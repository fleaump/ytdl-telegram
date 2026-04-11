import os
import time
import asyncio
import logging
from pathlib import Path
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable, List
import yt_dlp

logger = logging.getLogger(__name__)

# Download directory
DOWNLOAD_DIR = Path("/tmp/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class VideoFormat:
    """Information about a specific video format"""
    format_id: str
    format_note: str
    ext: str
    resolution: str
    filesize: Optional[int] = None
    vcodec: str = ''
    acodec: str = ''
    fps: Optional[float] = None
    format_str: str = ''  # yt-dlp format string


@dataclass
class VideoInfo:
    """Information about a downloaded video"""
    title: str
    filepath: Path
    file_size: int
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    formats: List[VideoFormat] = field(default_factory=list)
    url: str = ''


class ProgressHook:
    """Progress hook for yt-dlp that calls a callback on progress updates"""

    def __init__(self, progress_callback: Callable[[str], None]):
        self.progress_callback = progress_callback
        self.last_update = 0

    def __call__(self, d):
        if d['status'] == 'downloading':
            current_time = time.time()
            if current_time - self.last_update > 1:
                self.last_update = current_time

                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0) or 0
                eta = d.get('eta', 0) or 0

                if total > 0:
                    percent = (downloaded / total) * 100
                    speed_mb = speed / (1024 * 1024)
                    eta_min = eta // 60
                    eta_sec = eta % 60

                    text = (
                        f"📥 Скачивание видео...\n"
                        f"Прогресс: {percent:.1f}%\n"
                        f"Скорость: {speed_mb:.2f} МБ/с\n"
                        f"Осталось: {int(eta_min)}м {int(eta_sec)}с"
                    )
                    self.progress_callback(text)

        elif d['status'] == 'finished':
            self.progress_callback("✅ Скачивание завершено!\n📤 Отправляю в Telegram...")


class VideoDownloader(ABC):
    """Base class for video downloaders"""

    @abstractmethod
    def supports(self, url: str) -> bool:
        """Check if this downloader supports the given URL"""
        pass

    @abstractmethod
    async def download(self, url: str, progress_callback: Callable[[str], None]) -> VideoInfo:
        """Download video and return info"""
        pass


class YouTubeDownloader(VideoDownloader):
    """YouTube video downloader"""

    def supports(self, url: str) -> bool:
        return 'youtube.com' in url.lower() or 'youtu.be' in url.lower()

    def _extract_formats(self, info: dict) -> List[VideoFormat]:
        """Extract available video formats/qualities from video info"""
        formats = []
        seen = set()
        
        # Get individual formats
        for fmt in info.get('formats', []):
            # Skip audio-only formats
            if not fmt.get('vcodec') or fmt.get('vcodec') == 'none':
                continue
            
            # Skip duplicate resolutions
            resolution = fmt.get('format_note', '')
            if not resolution:
                height = fmt.get('height')
                if height:
                    resolution = f"{height}p"
                else:
                    continue
            
            if resolution in seen:
                continue
            seen.add(resolution)
            
            # Build format string for yt-dlp
            format_id = fmt.get('format_id', '')
            ext = fmt.get('ext', 'mp4')
            filesize = fmt.get('filesize')
            vcodec = fmt.get('vcodec', '')
            acodec = fmt.get('acodec', '')
            fps = fmt.get('fps')
            
            # Create format string for downloading
            # Prefer mp4 with m4a audio
            format_str = f"{format_id}+bestaudio[ext=m4a]/bestaudio/best"
            
            format_note = resolution
            if fps:
                format_note += f" {fps:.0f}fps"
            
            formats.append(VideoFormat(
                format_id=format_id,
                format_note=format_note,
                ext=ext,
                resolution=resolution,
                filesize=filesize,
                vcodec=vcodec,
                acodec=acodec,
                fps=fps,
                format_str=format_str
            ))
        
        # Sort by resolution (highest first)
        def sort_key(f):
            # Extract numeric value from resolution (e.g., "1080p" -> 1080, "4K" -> 4000)
            res = f.resolution.lower()
            if 'k' in res:
                return int(res.replace('k', '').replace('p', '')) * 1000
            try:
                return int(res.replace('p', ''))
            except:
                return 0
        
        formats.sort(key=sort_key, reverse=True)
        
        return formats

    async def get_video_info(self, url: str) -> VideoInfo:
        """Get video information without downloading"""
        cookies_file = os.environ.get('YOUTUBE_COOKIES_FILE')
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
        }
        
        if cookies_file:
            ydl_opts['cookies'] = cookies_file
            logger.info(f"Using cookies file: {cookies_file}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = self._extract_formats(info)
            
            return VideoInfo(
                title=info.get('title', 'Видео'),
                filepath=Path(''),
                file_size=0,
                duration=info.get('duration'),
                thumbnail=info.get('thumbnail'),
                formats=formats,
                url=url
            )

    async def download(self, url: str, progress_callback: Callable[[str], None]) -> VideoInfo:
        """Download video with best quality"""
        return await self.download_with_format(url, progress_callback, None)

    async def download_with_format(self, url: str, progress_callback: Callable[[str], None], format_str: Optional[str]) -> VideoInfo:
        """Download video with specific format"""
        cookies_file = os.environ.get('YOUTUBE_COOKIES_FILE')

        # Use specific format or fallback to best
        if format_str:
            format_option = format_str
        else:
            format_option = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'

        ydl_opts = {
            'format': format_option,
            'outtmpl': str(DOWNLOAD_DIR / '%(id)s.%(ext)s'),
            'progress_hooks': [ProgressHook(progress_callback)],
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
        }

        if cookies_file:
            ydl_opts['cookies'] = cookies_file
            logger.info(f"Using cookies file: {cookies_file}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            filepath = Path(filename)

            file_size = os.path.getsize(filepath)

            return VideoInfo(
                title=info.get('title', 'Видео'),
                filepath=filepath,
                file_size=file_size,
                duration=info.get('duration'),
                thumbnail=info.get('thumbnail'),
            )


class VideoDownloadManager:
    """Manages multiple video downloaders"""

    def __init__(self):
        self._downloaders: list[VideoDownloader] = []

    def register(self, downloader: VideoDownloader):
        """Register a video downloader"""
        self._downloaders.append(downloader)
        logger.info(f"Registered downloader: {downloader.__class__.__name__}")

    def get_downloader(self, url: str) -> Optional[VideoDownloader]:
        """Get the appropriate downloader for a URL"""
        for downloader in self._downloaders:
            if downloader.supports(url):
                return downloader
        return None

    async def get_video_info(self, url: str) -> VideoInfo:
        """Get video information without downloading"""
        downloader = self.get_downloader(url)
        if not downloader:
            raise ValueError("Неподдерживаемый сервис. Отправьте ссылку на YouTube видео.")
        
        # Check if downloader supports get_video_info
        if hasattr(downloader, 'get_video_info'):
            return await downloader.get_video_info(url)
        
        # Fallback: download with best quality
        return await downloader.download(url, lambda x: None)

    async def download(self, url: str, progress_callback: Callable[[str], None]) -> VideoInfo:
        """Download video from URL using the appropriate downloader"""
        downloader = self.get_downloader(url)
        if not downloader:
            raise ValueError("Неподдерживаемый сервис. Отправьте ссылку на YouTube видео.")

        return await downloader.download(url, progress_callback)

    async def download_with_format(self, url: str, progress_callback: Callable[[str], None], format_str: Optional[str]) -> VideoInfo:
        """Download video with specific format"""
        downloader = self.get_downloader(url)
        if not downloader:
            raise ValueError("Неподдерживаемый сервис. Отправьте ссылку на YouTube видео.")
        
        # Check if downloader supports download_with_format
        if hasattr(downloader, 'download_with_format'):
            return await downloader.download_with_format(url, progress_callback, format_str)
        
        # Fallback: use regular download
        return await downloader.download(url, progress_callback)

    def cleanup(self, video_info: VideoInfo):
        """Clean up downloaded file"""
        try:
            if video_info.filepath.exists():
                os.remove(video_info.filepath)
                logger.info(f"Cleaned up: {video_info.filepath}")
        except Exception as e:
            logger.warning(f"Failed to cleanup: {e}")


# Default manager with YouTube downloader pre-registered
download_manager = VideoDownloadManager()
download_manager.register(YouTubeDownloader())

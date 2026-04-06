import os
import time
import asyncio
import logging
from pathlib import Path
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable
import yt_dlp

logger = logging.getLogger(__name__)

# Download directory
DOWNLOAD_DIR = Path("/tmp/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class VideoInfo:
    """Information about a downloaded video"""
    title: str
    filepath: Path
    file_size: int
    duration: Optional[int] = None
    thumbnail: Optional[str] = None


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

    async def download(self, url: str, progress_callback: Callable[[str], None]) -> VideoInfo:
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': str(DOWNLOAD_DIR / '%(id)s.%(ext)s'),
            'progress_hooks': [ProgressHook(progress_callback)],
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
        }

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

    async def download(self, url: str, progress_callback: Callable[[str], None]) -> VideoInfo:
        """Download video from URL using the appropriate downloader"""
        downloader = self.get_downloader(url)
        if not downloader:
            raise ValueError("Неподдерживаемый сервис. Отправьте ссылку на YouTube видео.")

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

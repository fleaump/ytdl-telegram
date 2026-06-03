import re
import urllib.request
import urllib.parse
import html
import asyncio
import time
import logging
from pathlib import Path
from typing import Optional, Callable

from video_types import VideoFormat, VideoInfo, VideoDownloader, DOWNLOAD_DIR

logger = logging.getLogger(__name__)


class PikabuDownloader(VideoDownloader):
    """Downloader for pikabu.ru story pages containing <video> blocks"""

    def supports(self, url: str) -> bool:
        return 'pikabu.ru/story/' in url.lower()

    async def get_video_info(self, url: str) -> VideoInfo:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; yt-dl-bot/1.0)'
        }

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8', errors='ignore')

        # Try to extract a title
        title = None
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', raw, re.I)
        if m:
            title = html.unescape(m.group(1))
        else:
            m = re.search(r'<title>(.*?)</title>', raw, re.I|re.S)
            if m:
                title = html.unescape(m.group(1).strip())

        # Find first <video ...>...</video>
        vm = re.search(r'<video[^>]*>(.*?)</video>', raw, re.I|re.S)
        video_block = vm.group(0) if vm else None

        poster = None
        mp4_url = None
        width = None
        height = None
        filesize = None

        if video_block:
            pm = re.search(r'poster=["\']([^"\']+)["\']', video_block)
            if pm:
                poster = pm.group(1)

            wm = re.search(r'data-width=["\'](\d+)["\']', video_block)
            hm = re.search(r'data-height=["\'](\d+)["\']', video_block)
            if wm:
                width = wm.group(1)
            if hm:
                height = hm.group(1)

            # Prefer explicit <source src="...mp4">
            sm = re.search(r'<source[^>]+src=["\']([^"\']+\.mp4)["\']', video_block, re.I)
            if sm:
                mp4_url = sm.group(1)
            else:
                # fallback to data-source or data-video-url
                dm = re.search(r'data-source=["\']([^"\']+)["\']', video_block)
                if dm:
                    candidate = dm.group(1)
                    if candidate.endswith('.mp4'):
                        mp4_url = candidate
                    else:
                        # try adding .mp4
                        mp4_url = candidate + '.mp4'

        # If we still don't have mp4_url, try to search whole page for .mp4 links
        if not mp4_url:
            sm = re.search(r'https?://[^"\']+\.mp4', raw)
            if sm:
                mp4_url = sm.group(0)

        if mp4_url:
            # Make absolute if needed
            mp4_url = urllib.parse.urljoin(url, mp4_url)

            # Try HEAD to get content-length
            try:
                head = urllib.request.Request(mp4_url, method='HEAD', headers=headers)
                with urllib.request.urlopen(head, timeout=10) as hresp:
                    cl = hresp.getheader('Content-Length') or hresp.getheader('content-length')
                    if cl:
                        filesize = int(cl)
            except Exception:
                pass

        if not mp4_url:
            raise ValueError('Не удалось найти видео на странице Pikabu')

        # Build a single format entry
        fmt_note = 'original'
        if width and height:
            fmt_note = f"{width}x{height}"

        formats = [
            VideoFormat(
                format_id='mp4',
                format_note=fmt_note,
                ext='mp4',
                resolution=f"{width}x{height}" if width and height else '',
                filesize=filesize,
                vcodec='h264',
                acodec='aac',
                fps=None,
                format_str=mp4_url or 'mp4'
            )
        ]

        return VideoInfo(
            title=title or 'Pikabu Видео',
            filepath=Path(''),
            file_size=filesize or 0,
            duration=None,
            thumbnail=poster,
            formats=formats,
            url=url  # keep original story page URL
        )

    async def download_with_format(self, url: str, progress_callback, format_str: Optional[str]) -> VideoInfo:
        # url is the original story page URL; format_str may contain direct mp4 URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; yt-dl-bot/1.0)'
        }

        loop = asyncio.get_running_loop()

        # Determine direct mp4 URL: prefer format_str if it's an absolute URL
        mp4_url = None
        if format_str and isinstance(format_str, str) and format_str.startswith('http'):
            mp4_url = format_str
        else:
            # Parse the story page to find mp4
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8', errors='ignore')

            sm = re.search(r'<video[^>]*>(.*?)</video>', raw, re.I|re.S)
            video_block = sm.group(0) if sm else None
            if video_block:
                s2 = re.search(r'<source[^>]+src=["\']([^"\']+\.mp4)["\']', video_block, re.I)
                if s2:
                    mp4_url = s2.group(1)
                else:
                    dm = re.search(r'data-source=["\']([^"\']+)["\']', video_block)
                    if dm:
                        candidate = dm.group(1)
                        mp4_url = candidate if candidate.startswith('http') else urllib.parse.urljoin(url, candidate + ('.mp4' if not candidate.endswith('.mp4') else ''))

        if not mp4_url:
            raise ValueError('Не удалось определить прямой mp4 URL для загрузки')

        mp4_url = urllib.parse.urljoin(url, mp4_url)

        def blocking_download():
            req = urllib.request.Request(mp4_url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.getheader('Content-Length') or 0)
                path = urllib.parse.urlparse(mp4_url).path
                name = Path(path).name or f"pikabu_{int(time.time())}.mp4"
                outpath = DOWNLOAD_DIR / name

                downloaded = 0
                last_update = 0
                with open(outpath, 'wb') as fh:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_update > 1:
                            last_update = now
                            if total:
                                percent = (downloaded / total) * 100
                                msg = f"📥 Скачивание видео...\nПрогресс: {percent:.1f}%"
                            else:
                                msg = f"📥 Скачивание видео...\nСкачано: {downloaded // 1024}КБ"
                            try:
                                # Schedule progress callback in event loop
                                coro = progress_callback(msg)
                                asyncio.run_coroutine_threadsafe(coro, loop)
                            except Exception:
                                try:
                                    progress_callback(msg)
                                except Exception:
                                    pass

                return outpath

        outpath = await asyncio.to_thread(blocking_download)
        file_size = outpath.stat().st_size

        return VideoInfo(
            title=outpath.name,
            filepath=outpath,
            file_size=file_size,
            duration=None,
            thumbnail=None,
        )

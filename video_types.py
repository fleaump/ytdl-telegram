import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, List

logger = logging.getLogger(__name__)

# Download directory
DOWNLOAD_DIR = Path("/tmp/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class VideoFormat:
    format_id: str
    format_note: str
    ext: str
    resolution: str
    filesize: Optional[int] = None
    vcodec: str = ''
    acodec: str = ''
    fps: Optional[float] = None
    format_str: str = ''


@dataclass
class VideoInfo:
    title: str
    filepath: Path
    file_size: int
    description: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    formats: List[VideoFormat] = field(default_factory=list)
    url: str = ''


class VideoDownloader:
    def supports(self, url: str) -> bool:
        raise NotImplementedError()

    async def download(self, url: str, progress_callback: Callable[[str], None]) -> VideoInfo:
        raise NotImplementedError()

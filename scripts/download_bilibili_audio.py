"""Build and validate a portable Bilibili audio download workflow."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
import re


_BV_VIDEO_PATH = re.compile(r"^/video/BV[0-9A-Za-z]{10}(?:/|$)")
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}


@dataclass(frozen=True)
class MediaInfo:
    """The media properties required to accept a downloaded audio file."""

    duration: float
    audio_stream_count: int


def validate_url(url: str) -> str:
    """Return a public Bilibili BV video URL, or raise ``ValueError``."""
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in _BILIBILI_HOSTS
        or not _BV_VIDEO_PATH.match(parsed.path)
    ):
        raise ValueError("URL must be a public Bilibili BV video URL")
    return url


def build_download_command(url: str, output_directory: Path) -> list[str]:
    """Build a yt-dlp command for audio-only M4A output without running it."""
    validate_url(url)
    output_template = output_directory / "%(title)s.%(ext)s"
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        "ba[ext=m4a]/ba",
        "--remux-video",
        "m4a",
        "-o",
        str(output_template),
        url,
    ]


def inspect_media_json(payload: Mapping[str, Any]) -> MediaInfo:
    """Parse ffprobe JSON and require audio plus a positive duration."""
    format_data = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_data, Mapping) or not isinstance(streams, Sequence):
        raise ValueError("ffprobe output is missing format or streams data")

    try:
        duration = float(format_data["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("ffprobe output has no usable duration") from error
    if duration <= 0:
        raise ValueError("ffprobe duration must be positive")

    audio_stream_count = sum(
        isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
        for stream in streams
    )
    if not audio_stream_count:
        raise ValueError("ffprobe output has no audio stream")

    return MediaInfo(duration=duration, audio_stream_count=audio_stream_count)

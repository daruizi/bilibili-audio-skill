"""Download a public Bilibili video's best audio stream as a verified M4A file.

Usage:
    python download_bilibili_audio.py <bilibili-url> [--output-dir DIR]

On success prints one JSON record to stdout and exits 0. Exit codes:
    2  invalid URL
    3  missing dependency (yt-dlp, ffmpeg, ffprobe)
    4  Bilibili rejected the request with HTTP 412 -> use the browser fallback
    1  other yt-dlp failure
    5  downloaded file failed ffprobe verification
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlparse


_BV_VIDEO_PATH = re.compile(r"^/video/BV[0-9A-Za-z]{10}(?:/|$)")
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
_SHORT_LINK_HOSTS = {"b23.tv"}

EXIT_OK = 0
EXIT_DOWNLOAD_FAILED = 1
EXIT_INVALID_URL = 2
EXIT_MISSING_DEPENDENCY = 3
EXIT_HTTP_412 = 4
EXIT_VERIFICATION_FAILED = 5


@dataclass(frozen=True)
class MediaInfo:
    """The media properties required to accept a downloaded audio file."""

    duration: float
    audio_stream_count: int
    codec: Optional[str] = None
    bit_rate: Optional[int] = None


def validate_url(url: str) -> str:
    """Return a public Bilibili BV video URL or b23.tv short link, or raise ``ValueError``."""
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        if parsed.hostname in _SHORT_LINK_HOSTS and parsed.path.strip("/"):
            return url
        if parsed.hostname in _BILIBILI_HOSTS and _BV_VIDEO_PATH.match(parsed.path):
            return url
    raise ValueError("URL must be a public Bilibili BV video URL")


def find_missing_dependencies(
    which: Callable[[str], Optional[str]] = shutil.which,
    find_spec: Callable[[str], Any] = importlib.util.find_spec,
) -> list[str]:
    """Return the names of required tools that are not installed."""
    missing = []
    if find_spec("yt_dlp") is None:
        missing.append("yt-dlp")
    missing.extend(tool for tool in ("ffmpeg", "ffprobe") if which(tool) is None)
    return missing


def build_download_command(url: str, output_directory: Path) -> list[str]:
    """Build a yt-dlp command for audio-only M4A output without running it."""
    validate_url(url)
    output_template = output_directory / "%(title)s.%(ext)s"
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-progress",
        "-f",
        "ba[ext=m4a]/ba",
        "--remux-video",
        "m4a",
        "--print",
        "after_move:filepath",
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

    audio_streams = [
        stream
        for stream in streams
        if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
    ]
    if not audio_streams:
        raise ValueError("ffprobe output has no audio stream")

    first = audio_streams[0]
    return MediaInfo(
        duration=duration,
        audio_stream_count=len(audio_streams),
        codec=first.get("codec_name"),
        bit_rate=_to_int(first.get("bit_rate")) or _to_int(format_data.get("bit_rate")),
    )


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe_media(path: Path, run: Callable[..., Any] = subprocess.run) -> MediaInfo:
    """Run ffprobe on ``path`` and validate the result."""
    result = run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ValueError("ffprobe failed: %s" % (result.stderr or "").strip()[-300:])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("ffprobe returned invalid JSON") from error
    return inspect_media_json(payload)


def _default_output_directory() -> Path:
    return Path.home() / "Downloads"


def main(
    argv: Optional[Sequence[str]] = None,
    run: Callable[..., Any] = subprocess.run,
    which: Callable[[str], Optional[str]] = shutil.which,
    find_spec: Callable[[str], Any] = importlib.util.find_spec,
) -> int:
    parser = argparse.ArgumentParser(description="Download Bilibili audio as verified M4A.")
    parser.add_argument("url", help="Bilibili video URL (https://www.bilibili.com/video/BV...)")
    parser.add_argument("--output-dir", type=Path, default=_default_output_directory())
    args = parser.parse_args(argv)

    try:
        url = validate_url(args.url)
    except ValueError as error:
        print("error: %s" % error, file=sys.stderr)
        return EXIT_INVALID_URL

    missing = find_missing_dependencies(which=which, find_spec=find_spec)
    if missing:
        print("error: missing dependencies: %s" % ", ".join(missing), file=sys.stderr)
        return EXIT_MISSING_DEPENDENCY

    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = run(
        build_download_command(url, args.output_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    stderr = result.stderr or ""
    if result.returncode != 0:
        sys.stderr.write(stderr)
        if "412" in stderr:
            print(
                "error: Bilibili returned HTTP 412 (risk control); "
                "use the browser-assisted fallback in SKILL.md",
                file=sys.stderr,
            )
            return EXIT_HTTP_412
        return EXIT_DOWNLOAD_FAILED

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        sys.stderr.write(stderr)
        print("error: yt-dlp did not report an output file", file=sys.stderr)
        return EXIT_DOWNLOAD_FAILED
    output_path = Path(lines[-1])

    try:
        media = probe_media(output_path, run=run)
    except ValueError as error:
        print("error: %s (file kept at %s)" % (error, output_path), file=sys.stderr)
        return EXIT_VERIFICATION_FAILED

    record = {
        "status": "ok",
        "file": str(output_path),
        "codec": media.codec,
        "bit_rate": media.bit_rate,
        "duration": media.duration,
    }
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

"""Authoritative, read-only video metadata for editor presentation.

OpenCV's ``CAP_PROP_FPS`` is a decoder estimate and can be derived from frame
count/timestamps.  It is useful for seeking, but it must not be presented as
the saved stream's factual FPS or bitrate.  This module keeps that UI metadata
on FFprobe's stream/container semantics and makes missing values explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from core.ffmpeg_tools import FFmpegUnavailable, get_ffprobe_exe


_NO_WINDOW = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}


@dataclass(frozen=True)
class VideoMetadata:
    duration_seconds: float | None
    width: int | None
    height: int | None
    average_fps: float | None
    real_fps: float | None
    video_bitrate_bps: int | None
    total_bitrate_bps: int | None
    fps_source: str | None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _positive_int(value: Any) -> int | None:
    parsed = _positive_float(value)
    return int(parsed) if parsed is not None else None


def parse_frame_rate(value: Any) -> float | None:
    """Return a sane positive FPS from an FFprobe rational or decimal."""

    if value in (None, '', 'N/A', '0/0'):
        return None
    try:
        rate = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    # Anything beyond 1000 FPS is not useful clip metadata and is almost
    # certainly a codec time-base artifact rather than a presentation rate.
    return rate if math.isfinite(rate) and 0 < rate <= 1000 else None


def parse_ffprobe_video_metadata(
        payload: str | Mapping[str, Any], *, file_size: int | None = None,
) -> VideoMetadata | None:
    """Parse the first real video stream from an FFprobe JSON document."""

    try:
        document = json.loads(payload) if isinstance(payload, str) else payload
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(document, Mapping):
        return None
    streams = document.get('streams')
    if not isinstance(streams, list):
        return None
    video = next(
        (stream for stream in streams
         if isinstance(stream, Mapping) and stream.get('codec_type') == 'video'),
        None,
    )
    if video is None:
        return None

    average_fps = parse_frame_rate(video.get('avg_frame_rate'))
    real_fps = parse_frame_rate(video.get('r_frame_rate'))
    fps_source = 'avg_frame_rate' if average_fps is not None else (
        'r_frame_rate' if real_fps is not None else None)
    presented_fps = average_fps if average_fps is not None else real_fps

    format_data = document.get('format')
    if not isinstance(format_data, Mapping):
        format_data = {}
    duration = _positive_float(video.get('duration'))
    if duration is None:
        duration = _positive_float(format_data.get('duration'))

    total_bitrate = _positive_int(format_data.get('bit_rate'))
    if total_bitrate is None and duration is not None:
        size = _positive_int(format_data.get('size')) or _positive_int(file_size)
        if size is not None:
            # This fallback is explicitly TOTAL container bitrate. It is never
            # reused as video bitrate because FTHR clips may have many AAC stems.
            total_bitrate = int(round(size * 8 / duration))

    return VideoMetadata(
        duration_seconds=duration,
        width=_positive_int(video.get('width')),
        height=_positive_int(video.get('height')),
        average_fps=presented_fps,
        real_fps=real_fps,
        video_bitrate_bps=_positive_int(video.get('bit_rate')),
        total_bitrate_bps=total_bitrate,
        fps_source=fps_source,
    )


def probe_video_metadata(
        media_path: str | os.PathLike[str], *, timeout_seconds: float = 4.0,
) -> VideoMetadata | None:
    """Probe one file without guessing missing media facts."""

    try:
        probe = get_ffprobe_exe()
        result = subprocess.run(
            [
                probe, '-v', 'error',
                '-show_entries',
                'stream=index,codec_type,width,height,avg_frame_rate,'
                'r_frame_rate,duration,bit_rate:'
                'format=duration,size,bit_rate',
                '-of', 'json', os.fspath(media_path),
            ],
            capture_output=True, text=True, timeout=timeout_seconds,
            check=False, **_NO_WINDOW,
        )
    except (FFmpegUnavailable, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        file_size = Path(media_path).stat().st_size
    except OSError:
        file_size = None
    return parse_ffprobe_video_metadata(result.stdout, file_size=file_size)


def format_fps(value: float | None) -> str:
    if value is None or not math.isfinite(value) or value <= 0:
        return 'Unavailable'
    rounded_integer = round(value)
    if abs(value - rounded_integer) < 0.005:
        return f'{rounded_integer} FPS'
    return f'{value:.2f}'.rstrip('0').rstrip('.') + ' FPS'


def format_bitrate(value_bps: int | None) -> str:
    if value_bps is None or value_bps <= 0:
        return 'Unavailable'
    if value_bps >= 1_000_000:
        return f'{value_bps / 1_000_000:.2f}'.rstrip('0').rstrip('.') + ' Mbps'
    return f'{value_bps / 1000:.0f} kbps'

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

    stream_tags = video.get('tags')
    if not isinstance(stream_tags, Mapping):
        stream_tags = {}
    format_data = document.get('format')
    if not isinstance(format_data, Mapping):
        format_data = {}
    format_tags = format_data.get('tags')
    if not isinstance(format_tags, Mapping):
        format_tags = {}

    # FTHR writers publish the configured values explicitly. Prefer those
    # values over FFprobe's packet-derived estimates when present; the latter
    # can be skewed by hardware encoder timing or container interleave details.
    configured_fps = (
        parse_frame_rate(format_tags.get('fthr_frame_rate'))
        or parse_frame_rate(stream_tags.get('fthr_frame_rate')))
    average_fps = configured_fps or parse_frame_rate(video.get('avg_frame_rate'))
    real_fps = parse_frame_rate(video.get('r_frame_rate'))
    fps_source = 'fthr_frame_rate' if configured_fps is not None else (
        'avg_frame_rate' if average_fps is not None else (
            'r_frame_rate' if real_fps is not None else None))
    configured_bitrate = (
        _positive_int(format_tags.get('fthr_video_bitrate_bps'))
        or _positive_int(stream_tags.get('fthr_video_bitrate_bps')))
    presented_fps = average_fps if average_fps is not None else real_fps

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
        video_bitrate_bps=(configured_bitrate
                           or _positive_int(video.get('bit_rate'))),
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
                'format_tags=fthr_frame_rate,fthr_video_bitrate_bps:'
                'format=duration,size,bit_rate',
                '-of', 'json', os.fspath(media_path),
            ],
            capture_output=True, text=True, timeout=timeout_seconds,
            check=False, **_NO_WINDOW,
        )
    except (FFmpegUnavailable, OSError, subprocess.SubprocessError):
        # A failed timing probe is intentionally inconclusive; the caller
        # treats it as needing the conservative CFR repair path.
        return None
    if result.returncode != 0:
        return None
    try:
        file_size = Path(media_path).stat().st_size
    except OSError:
        file_size = None
    return parse_ffprobe_video_metadata(result.stdout, file_size=file_size)


def probe_video_cfr(
        media_path: str | os.PathLike[str], expected_fps: float, *,
        timeout_seconds: float = 30.0,
) -> bool | None:
    """Check whether every video sample has the requested CFR duration.

    Stream ``avg_frame_rate`` is not sufficient here: an MP4 can advertise a
    configured rate while its sample table still contains long gaps. Explorer
    uses that sample timing. ``True`` means the file is safe to publish,
    ``False`` means it needs a frame-rate repair, and ``None`` means probing
    failed and callers should take the repair path conservatively.
    """

    if (not math.isfinite(expected_fps)
            or expected_fps <= 0
            or expected_fps > 1000):
        return None
    try:
        probe = get_ffprobe_exe()
        result = subprocess.run(
            [
                probe, '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'packet=duration_time',
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
        packets = json.loads(result.stdout).get('packets')
    except (TypeError, json.JSONDecodeError, AttributeError):
        # Malformed probe output cannot prove CFR, so callers must repair.
        return None
    if not isinstance(packets, list) or not packets:
        return None

    expected_duration = 1.0 / expected_fps
    tolerance = max(0.00005, expected_duration * 0.002)
    for packet in packets:
        if not isinstance(packet, Mapping):
            return None
        duration = _positive_float(packet.get('duration_time'))
        if duration is None:
            return None
        if abs(duration - expected_duration) > tolerance:
            return False
    return True


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

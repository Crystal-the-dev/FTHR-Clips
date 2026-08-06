"""
ffmpeg_tools.py — one place that decides *which* ffmpeg we run and *which*
software video encoder we ask it for.

Why this module exists
----------------------
Two separate problems collapsed into one fix (AUDIT-005):

1. **Licensing.** FTHR used to shell out to the ffmpeg binary bundled inside
   the ``imageio-ffmpeg`` wheel. That binary is a gyan.dev "essentials" build
   configured with ``--enable-gpl --enable-version3 --enable-libx264
   --enable-libx265`` — i.e. **GPLv3**. Shipping it inside an AppImage or
   installer would place the whole distributed work under the GPL, which is
   incompatible with releasing FTHR's own code under MIT.

2. **It was already broken on Windows.** ``imageio_ffmpeg`` is not actually
   present in the frozen Windows bundle (no package directory, no binary), so
   every ``get_ffmpeg_exe()`` call raised and the watermark, auto-crop, webcam
   overlay and clip-export paths silently degraded in the shipped build.

Both are solved by using the **same LGPL FFmpeg that already ships next to the
capture engine**. One copy, one licence, ~87 MB less to download.

Consequence for encoders
------------------------
The LGPL build deliberately has no libx264/libx265. The software H.264 encoder
is Cisco **OpenH264** (BSD-2-Clause). It is not a drop-in for x264 on the
command line: it has no ``-preset`` and no ``-crf``, so the old
``-c:v libx264 -preset ultrafast -crf 18`` invocations would fail outright.
:func:`software_video_args` produces the correct arguments instead.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

# Suppress the console window ffmpeg would otherwise flash on Windows.
_NO_WINDOW = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}

_EXE_NAME = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'

# Resolved lazily, then cached — resolution touches the filesystem and the
# encoder probe spawns a process; neither should happen per clip.
_cached_exe: Optional[str] = None
_cached_encoder: Optional[str] = None


class FFmpegUnavailable(RuntimeError):
    """Raised when no usable ffmpeg binary can be found.

    Callers are expected to surface the message to the user — a missing ffmpeg
    disables watermark, crop, webcam overlay and export, and the old code
    reported that as nothing at all.
    """


def _candidate_paths() -> list[Path]:
    """Where to look, most-specific first.

    Note the deliberate absence of ``imageio_ffmpeg`` from the top of this
    list: it is a GPL build and must never be what a *release* uses.
    """
    here = Path(__file__).resolve()
    out: list[Path] = []

    # 1. Frozen bundle (PyInstaller). FTHR.spec places the engine and its
    #    FFmpeg DLLs — and now ffmpeg.exe — into an "engine" subdirectory.
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        out.append(Path(meipass) / 'engine' / _EXE_NAME)
        out.append(Path(meipass) / _EXE_NAME)

    # 2. Next to the executable (AppImage layout: engine sits beside AppRun).
    out.append(Path(sys.executable).parent / _EXE_NAME)
    out.append(Path(sys.executable).parent / 'engine' / _EXE_NAME)

    # 3. Development checkout: the LGPL build vendored for the C++ engine.
    #    here = <root>/FTHR_UI/core/ffmpeg_tools.py  →  parents[2] = <root>
    root = here.parents[2]
    out.append(root / 'FTHRcapture' / 'FTHRclips' / 'third_party' / 'ffmpeg' / 'bin' / _EXE_NAME)

    return out


def get_ffmpeg_exe() -> str:
    """Return a path to a usable ffmpeg binary.

    Resolution order: bundled LGPL build → system ``ffmpeg`` on PATH.

    The PATH fallback exists for Linux, where distributions ship their own
    FFmpeg and the user is expected to have one. That copy is *not* something
    FTHR distributes, so its licence is not FTHR's to answer for — but it is
    also not guaranteed to contain OpenH264, which is why
    :func:`software_video_args` probes rather than assumes.

    :raises FFmpegUnavailable: when nothing usable is found.
    """
    global _cached_exe
    if _cached_exe is not None:
        return _cached_exe

    for cand in _candidate_paths():
        try:
            if cand.is_file():
                _cached_exe = str(cand)
                return _cached_exe
        except OSError:
            continue

    # System ffmpeg (typical on Linux; also fine for a dev box on Windows).
    from shutil import which
    found = which('ffmpeg')
    if found:
        _cached_exe = found
        return _cached_exe

    raise FFmpegUnavailable(
        'No ffmpeg binary found. FTHR Clips ships one next to the capture '
        'engine; if this is a source checkout, run the build script or install '
        'ffmpeg and make sure it is on your PATH. Watermark, auto-crop, webcam '
        'overlay and clip export need it.'
    )


def _probe_encoders(ffmpeg: str) -> str:
    """Return the best available software H.264 encoder name.

    Probing beats hardcoding because the PATH fallback above can land on a
    distro FFmpeg whose encoder set we do not control.
    """
    try:
        res = subprocess.run(
            [ffmpeg, '-hide_banner', '-encoders'],
            capture_output=True, text=True, timeout=20, **_NO_WINDOW,
        )
        available = res.stdout or ''
    except (OSError, subprocess.SubprocessError):
        # Probe failed — assume the encoder our own bundled build carries.
        return 'libopenh264'

    # Preference order. libopenh264 is what we ship; the rest are graceful
    # degradations for third-party FFmpeg builds.
    for name in ('libopenh264', 'h264_mf', 'libx264'):
        if name in available:
            return name
    return 'libopenh264'


def software_h264_encoder(ffmpeg: Optional[str] = None) -> str:
    """Name of the software H.264 encoder this installation should use."""
    global _cached_encoder
    if _cached_encoder is None:
        _cached_encoder = _probe_encoders(ffmpeg or get_ffmpeg_exe())
    return _cached_encoder


def software_video_args(bitrate_kbps: int = 16000,
                        ffmpeg: Optional[str] = None) -> list[str]:
    """ffmpeg arguments selecting the software H.264 encoder and its quality.

    Replaces the hardcoded ``['-c:v', 'libx264', '-preset', X, '-crf', '18']``
    that appeared at six call sites.

    ``-crf`` is intentionally not used: OpenH264 has no constant-quality mode,
    so quality is expressed as a target bitrate. 16 Mbit/s is the engine's own
    default for 1080p and is visually close to the old ``-crf 18`` for the
    short, high-motion clips this tool produces.
    """
    enc = software_h264_encoder(ffmpeg)

    if enc == 'libopenh264':
        return [
            '-c:v', 'libopenh264',
            '-b:v', f'{bitrate_kbps}k',
            # Never drop frames to hit the bitrate: the clip would desync
            # against the audio track muxed in afterwards.
            '-allow_skip_frames', '0',
            '-profile:v', 'high',
        ]
    if enc == 'libx264':
        # Only reachable via a third-party/system FFmpeg that still has x264.
        # FTHR does not ship this path.
        return ['-c:v', 'libx264', '-preset', 'superfast', '-crf', '18']
    # h264_mf (Windows MediaFoundation) and anything else: bitrate only.
    return ['-c:v', enc, '-b:v', f'{bitrate_kbps}k']


def reset_cache() -> None:
    """Clear memoized resolution. Tests only."""
    global _cached_exe, _cached_encoder
    _cached_exe = None
    _cached_encoder = None

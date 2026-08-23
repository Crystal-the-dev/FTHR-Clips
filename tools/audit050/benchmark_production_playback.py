"""Measure FTHR's production in-process playback bridge on a real media file.

This intentionally does not synthesize results or invoke an FFmpeg decoder
subprocess.  Supply a clip created by the existing MP4 spike or a real FTHR
clip; the script uses the same ctypes bridge that the viewer uses.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'FTHR_UI'))

from core.ffmpeg_playback import InProcessFFmpegMixer, probe_audio_streams
from core.playback_mix_model import build_playback_sources


def _working_set_bytes() -> int | None:
    if sys.platform != 'win32':
        return None

    class Counters(ctypes.Structure):
        _fields_ = [
            ('cb', wintypes.DWORD),
            ('PageFaultCount', wintypes.DWORD),
            ('PeakWorkingSetSize', ctypes.c_size_t),
            ('WorkingSetSize', ctypes.c_size_t),
            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
            ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
            ('PagefileUsage', ctypes.c_size_t),
            ('PeakPagefileUsage', ctypes.c_size_t),
            ('PrivateUsage', ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ok = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return int(counters.WorkingSetSize) if ok else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('media', type=Path)
    parser.add_argument('--seeks', default='0,1000,3000,1000',
                        help='comma-separated seek positions in milliseconds')
    parser.add_argument('--max-tracks', type=int, default=0,
                        help='benchmark at most this many actual audio streams')
    args = parser.parse_args()
    if not args.media.is_file():
        parser.error(f'media does not exist: {args.media}')

    sources = build_playback_sources(None, probe_audio_streams(str(args.media)))
    if args.max_tracks:
        sources = sources[:args.max_tracks]
    if not sources:
        parser.error('media has no decodable audio stream')
    mixer = InProcessFFmpegMixer(str(args.media), sources)
    try:
        start_wall = time.perf_counter()
        start_cpu = time.process_time()
        total_frames = 0
        while True:
            block = mixer.pull(1024, [1.0] * len(sources), 1.0)
            if block is None:
                break
            total_frames += 1024
        decode_wall_ms = (time.perf_counter() - start_wall) * 1000.0
        decode_cpu_ms = (time.process_time() - start_cpu) * 1000.0
        seek_results = []
        for seek_ms in (int(value) for value in args.seeks.split(',') if value.strip()):
            start = time.perf_counter()
            mixer.seek(seek_ms)
            block = mixer.pull(1024, [1.0] * len(sources), 1.0)
            seek_results.append({
                'target_ms': seek_ms,
                'latency_ms': round((time.perf_counter() - start) * 1000.0, 3),
                'returned_audio': block is not None,
            })
        report = {
            'media': args.media.name,
            'selected_tracks': len(sources),
            'canonical_pcm': '48000 Hz float32 stereo',
            'decoder_worker_threads': 1,
            'decoded_frames': total_frames,
            'decoded_duration_seconds': round(total_frames / 48_000.0, 3),
            'decode_wall_ms': round(decode_wall_ms, 3),
            'decode_process_cpu_ms': round(decode_cpu_ms, 3),
            'working_set_bytes_after_decode': _working_set_bytes(),
            'seek_results': seek_results,
        }
        print(json.dumps(report, sort_keys=True))
    finally:
        mixer.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

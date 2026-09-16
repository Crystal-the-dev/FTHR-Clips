"""Build/run synthetic NVIDIA recording checks without capturing the desktop.

Exercises upscaling, downscaling, FIT, STRETCH and unchanged dimensions through
both NVIDIA input paths. Starts each recording mid-GOP, verifies the requested
keyframe, closes the file, and fully decodes it. Requires an NVIDIA GPU.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from run_windows_capture_smoke import ROOT, build_harness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows is required')
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error('--output must be outside the repository')
    output.mkdir(parents=True, exist_ok=True)
    executable = build_harness(
        output, ROOT / 'tools/windows_recording_smoke.cpp', 'recording-smoke')
    ffmpeg_bin = ROOT / 'FTHRcapture/FTHRclips/third_party/ffmpeg/bin'
    results = []
    for mode in ('cpu', 'gpu'):
        for fit, width, height in ((0, 1920, 1080), (1, 1920, 1080),
                                   (0, 640, 360), (0, 1280, 976)):
            name = f'{mode}-{fit}-{width}x{height}'
            clip = output / f'{name}.mp4'
            with (output / f'{name}.log').open('w', encoding='utf-8') as log:
                subprocess.run(
                    [str(executable), str(clip), str(fit), mode,
                     str(width), str(height)], stdout=log, stderr=subprocess.STDOUT,
                    timeout=30, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            subprocess.run(
                [str(ffmpeg_bin / 'ffmpeg.exe'), '-v', 'error', '-xerror',
                 '-i', str(clip), '-f', 'null', '-'], check=True,
                timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            probe = json.loads(subprocess.check_output(
                [str(ffmpeg_bin / 'ffprobe.exe'), '-v', 'error', '-select_streams',
                 'v:0', '-count_packets', '-show_entries',
                 'stream=width,height,nb_read_packets', '-of', 'json', str(clip)],
                timeout=30, creationflags=subprocess.CREATE_NO_WINDOW))
            stream = probe['streams'][0]
            if (stream['width'], stream['height'], int(stream['nb_read_packets'])) != (
                    width, height, 149):
                raise RuntimeError(f'{name}: unexpected recorded geometry/packet count')
            results.append({'case': name, 'full_decode': True, **stream})
            print(f'{name}: passed', flush=True)
    (output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f'{len(results)} recording scenarios passed. Evidence: {output}')


if __name__ == '__main__':
    main()

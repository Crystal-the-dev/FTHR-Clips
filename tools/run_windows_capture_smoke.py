"""Build and run CaptureEngine directly, without the app's shared-memory IPC.

Requires VS 2022 C++ tools and the repository's FFmpeg runtime. Captures the
explicitly selected monitor for 15 seconds and saves its last 10 seconds.
All generated projects, binaries, logs and recordings stay in --output.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def build_harness(output: Path, harness_source: Path, name: str) -> Path:
    """Build against the production engine sources in an isolated directory."""
    vswhere = (Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))
               / 'Microsoft Visual Studio/Installer/vswhere.exe')
    msbuild = subprocess.check_output(
        [str(vswhere), '-latest', '-products', '*', '-requires',
         'Microsoft.Component.MSBuild', '-find', 'MSBuild/**/Bin/MSBuild.exe'],
        text=True).strip().splitlines()[0]
    source_root = ROOT / 'FTHRcapture/FTHRclips'
    namespace = 'http://schemas.microsoft.com/developer/msbuild/2003'
    ET.register_namespace('', namespace)
    source = (source_root / 'FTHRclips.vcxproj').read_text(encoding='utf-8-sig')
    source = source.replace('$(ProjectDir)', str(source_root) + os.sep)
    project = ET.fromstring(source)
    for tag in ('ClCompile', 'ClInclude'):
        for item in project.iter(f'{{{namespace}}}{tag}'):
            include_name = item.get('Include')
            if include_name:
                path = (harness_source
                        if include_name == r'src\main.cpp' else source_root / include_name)
                item.set('Include', str(path.resolve()))
    project_path = output / f'{name}.vcxproj'
    ET.ElementTree(project).write(project_path, encoding='utf-8', xml_declaration=True)
    executable = output / f'bin/{name}.exe'
    with (output / 'build.log').open('w', encoding='utf-8') as log:
        subprocess.run(
            [msbuild, str(project_path), '/p:Configuration=Release', '/p:Platform=x64',
             f'/p:OutDir={output / "bin"}{os.sep}',
             f'/p:IntDir={output / "obj"}{os.sep}', '/v:minimal', '/nologo'],
            stdout=log, stderr=subprocess.STDOUT, check=True)
    return executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitor', required=True, help='stable Windows monitor device path')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cycles', type=int, default=3)
    parser.add_argument('--manual', action='store_true',
                        help='also start/stop a recording while replay capture continues')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows is required')
    if not 1 <= args.cycles <= 20:
        parser.error('--cycles must be between 1 and 20')
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error('--output must be outside the repository')
    output.mkdir(parents=True, exist_ok=True)
    executable = build_harness(
        output, ROOT / 'tools/windows_capture_smoke.cpp', 'capture-smoke')
    results = []
    ffmpeg_bin = ROOT / 'FTHRcapture/FTHRclips/third_party/ffmpeg/bin'
    for cycle in range(1, args.cycles + 1):
        clip = output / f'capture-{cycle}.mp4'
        with (output / f'capture-{cycle}.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(
                [str(executable), args.monitor, str(clip),
                 *(['--manual'] if args.manual else [])], stdout=log,
                stderr=subprocess.STDOUT, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(f'Capture cycle {cycle} failed: exit {result.returncode}')
        subprocess.run([str(ffmpeg_bin / 'ffmpeg.exe'), '-v', 'error', '-i',
                        str(clip), '-f', 'null', '-'], check=True,
                       timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        results.append({'cycle': cycle, 'saved': True, 'full_decode': True})
        if args.manual:
            recording = clip.with_suffix('.recording.mp4')
            subprocess.run([str(ffmpeg_bin / 'ffmpeg.exe'), '-v', 'error', '-i',
                            str(recording), '-f', 'null', '-'], check=True,
                           timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            results[-1]['manual_full_decode'] = True
    (output / 'result.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f'{len(results)} capture/save/decode cycles passed. Evidence: {output}')


if __name__ == '__main__':
    main()

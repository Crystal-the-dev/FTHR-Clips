#!/usr/bin/env python3
"""
verify_release_licenses.py — fail the build if a release artifact carries
GPL-licensed FFmpeg components, or is missing its licence paperwork.

Background: FTHR Clips used to bundle a GPLv3 FFmpeg build (with libx264 and
libx265) while presenting itself as MIT, and shipped no third-party licence
texts at all. That is a licence violation on the first download and cannot be
corrected after the fact. This script exists so it cannot silently come back.

Design notes
------------
Checks are deliberately *targeted*, not keyword grep over the whole tree:

  * "GPL" appearing in documentation is normal and must not fail the build —
    this file, THIRD_PARTY_NOTICES.md and the LGPL licence text all contain it.
  * Binaries are inspected for FFmpeg's embedded configuration string and the
    embedded ``libav* license:`` banner, which are authoritative.
  * ``ffmpeg -buildconf`` is consulted when an executable is present, since
    that is the build's own account of itself.

Usage
-----
    python tools/verify_release_licenses.py --tree .
    python tools/verify_release_licenses.py --windows-dist dist/FTHRClips
    python tools/verify_release_licenses.py --appdir build/AppDir
    python tools/verify_release_licenses.py --all          # tree + any artifacts found

Exit code 0 = pass, 1 = at least one FAIL.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Build flags that place an FFmpeg build under the GPL (or make it undistributable).
# --enable-version3 is deliberately NOT here: it yields LGPLv3, not GPL.
FORBIDDEN_FLAGS = (
    b'--enable-gpl',
    b'--enable-nonfree',
    b'--enable-libx264',
    b'--enable-libx265',
    b'--enable-libxvid',
    b'--enable-libxavs2',
)

# The library's own statement about its licence.
LICENSE_BANNER = re.compile(rb'libav\w+ license: ([ -!#-~]{1,40})')

FFMPEG_LIB_NAMES = ('avcodec', 'avformat', 'avutil', 'avfilter',
                    'avdevice', 'swresample', 'swscale')

REQUIRED_TREE_FILES = (
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    'licenses/FFmpeg-LICENSE.txt',
    'licenses/PyQt6-LICENSE.txt',
    'licenses/Qt6-LICENSE.txt',
    'tools/ffmpeg_manifest.json',
)

# What an installed/packaged artifact must carry for the end user.
REQUIRED_ARTIFACT_FILES = ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses')


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.checks = 0

    def ok(self, msg: str) -> None:
        self.checks += 1
        print(f'  [ OK ] {msg}')

    def fail(self, msg: str) -> None:
        self.checks += 1
        self.failures.append(msg)
        print(f'  [FAIL] {msg}')

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f'  [WARN] {msg}')


def _is_ffmpeg_binary(p: Path) -> bool:
    n = p.name.lower()
    if not (n.endswith('.dll') or n.endswith('.exe')
            or '.so' in n or n in ('ffmpeg', 'ffprobe')):
        return False
    return any(lib in n for lib in FFMPEG_LIB_NAMES) or n.startswith(('ffmpeg', 'ffprobe'))


def check_binary(path: Path, rep: Report) -> None:
    """Inspect one FFmpeg binary for GPL markers."""
    try:
        data = path.read_bytes()
    except OSError as e:
        rep.warn(f'{path}: unreadable ({e})')
        return

    hits = [f.decode() for f in FORBIDDEN_FLAGS if f in data]
    if hits:
        rep.fail(f'{path.name}: GPL build flags present -> {", ".join(hits)}')
    else:
        rep.ok(f'{path.name}: no GPL build flags')

    banners = {m.group(1).decode().strip() for m in LICENSE_BANNER.finditer(data)}
    for b in banners:
        # "LGPL version 3 or later" is fine. "GPL version 3 or later" is not.
        if re.match(r'^GPL', b):
            rep.fail(f'{path.name}: embedded licence banner says "{b}"')
        else:
            rep.ok(f'{path.name}: licence banner "{b}"')


def check_ffmpeg_executable(exe: Path, rep: Report) -> None:
    """Ask the binary itself. Most authoritative check available."""
    try:
        res = subprocess.run([str(exe), '-hide_banner', '-buildconf'],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        rep.warn(f'{exe.name}: could not run -buildconf ({e})')
        return

    conf = res.stdout or ''
    bad = [f.decode() for f in FORBIDDEN_FLAGS
           if re.search(rf'^\s*{re.escape(f.decode())}\s*$', conf, re.M)]
    if bad:
        rep.fail(f'{exe.name} -buildconf: {", ".join(bad)}')
    else:
        rep.ok(f'{exe.name} -buildconf: clean')

    try:
        enc = subprocess.run([str(exe), '-hide_banner', '-encoders'],
                             capture_output=True, text=True, timeout=30).stdout or ''
    except (OSError, subprocess.SubprocessError):
        return
    for gpl_enc in ('libx264', 'libx265'):
        if re.search(rf'^\s*V\S*\s+{gpl_enc}\b', enc, re.M):
            rep.fail(f'{exe.name}: GPL encoder {gpl_enc} is registered')
    if not re.search(r'^\s*V\S*\s+libopenh264\b', enc, re.M):
        rep.warn(f'{exe.name}: libopenh264 not available — '
                 'no LGPL-compatible software H.264 fallback')
    else:
        rep.ok(f'{exe.name}: libopenh264 present, no GPL encoders')


def scan_dir(root: Path, rep: Report, label: str) -> int:
    print(f'\n-- FFmpeg binaries in {label} --')
    found = 0
    for p in sorted(root.rglob('*')):
        if not p.is_file() or not _is_ffmpeg_binary(p):
            continue
        found += 1
        check_binary(p, rep)
        if p.suffix.lower() == '.exe' or p.name in ('ffmpeg', 'ffprobe'):
            if p.stem in ('ffmpeg', 'ffprobe') or p.name in ('ffmpeg', 'ffprobe'):
                check_ffmpeg_executable(p, rep)
    if found == 0:
        rep.warn(f'{label}: no FFmpeg binaries found — is this the right path?')
    return found


def _resolve_in_artifact(root: Path, rel: str) -> Path:
    """Find `rel` at the artifact root or in PyInstaller's `_internal/`.

    A onedir PyInstaller bundle puts everything declared in `datas` under
    `_internal/`, so a naive root-only check reports the licence files missing
    on a perfectly compliant build.
    """
    direct = root / rel
    if direct.exists():
        return direct
    nested = root / '_internal' / rel
    if nested.exists():
        return nested
    return direct   # report the expected location in the failure message


def check_files(root: Path, required, rep: Report, label: str) -> None:
    print(f'\n-- Licence paperwork in {label} --')
    for rel in required:
        p = _resolve_in_artifact(root, rel)
        if p.exists():
            if p.is_dir():
                n = len(list(p.glob('*')))
                if n == 0:
                    rep.fail(f'{label}: {rel}/ exists but is empty')
                else:
                    rep.ok(f'{label}: {rel}/ ({n} files)')
            elif p.stat().st_size == 0:
                rep.fail(f'{label}: {rel} is empty')
            else:
                rep.ok(f'{label}: {rel}')
        else:
            rep.fail(f'{label}: missing {rel}')


def check_manifest(root: Path, rep: Report) -> None:
    print('\n-- FFmpeg provenance manifest --')
    mf = root / 'tools' / 'ffmpeg_manifest.json'
    if not mf.is_file():
        rep.fail('tools/ffmpeg_manifest.json missing — FFmpeg origin is undocumented')
        return
    try:
        data = json.loads(mf.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        rep.fail(f'ffmpeg_manifest.json unreadable: {e}')
        return
    for key in ('version', 'license', 'source'):
        if not data.get(key):
            rep.fail(f'ffmpeg_manifest.json: "{key}" missing')
    src = data.get('source') or {}
    if not src.get('sha256'):
        rep.fail('ffmpeg_manifest.json: source checksum not recorded')
    else:
        rep.ok(f'FFmpeg {data.get("version")} documented, sha256 recorded')
    lic = str(data.get('license', ''))
    if 'LGPL' not in lic.upper():
        rep.fail(f'ffmpeg_manifest.json: license is "{lic}", expected LGPL')
    else:
        rep.ok(f'manifest licence: {lic}')


def check_python_sources(root: Path, rep: Report) -> None:
    """Nothing in the app may request a GPL-only encoder or import the GPL
    imageio-ffmpeg binary wrapper."""
    print('\n-- Python sources --')
    ui = root / 'FTHR_UI'
    if not ui.is_dir():
        rep.warn('FTHR_UI/ not found — skipping source scan')
        return
    enc_hits, imageio_hits = [], []
    for py in ui.rglob('*.py'):
        if py.name == 'ffmpeg_tools.py':
            continue   # documents the names in prose + one guarded fallback
        text = py.read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r"['\"]libx26[45]['\"]", text):
            enc_hits.append(f'{py.relative_to(root)}:{text[:m.start()].count(chr(10)) + 1}')
        if re.search(r'^\s*(import|from)\s+imageio_ffmpeg', text, re.M):
            imageio_hits.append(str(py.relative_to(root)))
    if enc_hits:
        rep.fail(f'GPL encoder hardcoded: {", ".join(enc_hits)}')
    else:
        rep.ok('no libx264/libx265 in Python sources')
    if imageio_hits:
        rep.fail(f'imageio_ffmpeg (GPL build) imported: {", ".join(imageio_hits)}')
    else:
        rep.ok('imageio_ffmpeg not imported')


def main() -> int:
    # Windows consoles default to cp1252, which cannot encode the dashes
    # used in these messages. Without this the script dies before printing
    # a single result -- including in CI.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tree', type=Path, help='source tree root')
    ap.add_argument('--windows-dist', type=Path, help='PyInstaller output dir (dist/FTHRClips)')
    ap.add_argument('--appdir', type=Path, help='Linux AppDir before packing')
    ap.add_argument('--all', action='store_true', help='check tree plus any artifacts present')
    args = ap.parse_args()

    if args.all and not args.tree:
        args.tree = Path('.')

    if not any((args.tree, args.windows_dist, args.appdir)):
        ap.error('nothing to check — pass --tree, --windows-dist, --appdir or --all')

    rep = Report()
    print('FTHR Clips — release licence verification')
    print('=' * 60)

    if args.tree:
        root = args.tree.resolve()
        check_files(root, REQUIRED_TREE_FILES, rep, 'tree')
        check_manifest(root, rep)
        check_python_sources(root, rep)
        vendored = root / 'FTHRcapture' / 'FTHRclips' / 'third_party' / 'ffmpeg'
        if vendored.is_dir():
            scan_dir(vendored, rep, 'vendored ffmpeg')
        else:
            rep.warn('no vendored ffmpeg directory (expected on a Linux checkout)')
        if args.all:
            for cand, kind in ((root / 'dist' / 'FTHRClips', 'windows dist'),
                               (root / 'build' / 'AppDir', 'appdir')):
                if cand.is_dir():
                    check_files(cand, REQUIRED_ARTIFACT_FILES, rep, kind)
                    scan_dir(cand, rep, kind)

    if args.windows_dist:
        d = args.windows_dist.resolve()
        check_files(d, REQUIRED_ARTIFACT_FILES, rep, 'windows dist')
        scan_dir(d, rep, 'windows dist')

    if args.appdir:
        d = args.appdir.resolve()
        check_files(d, REQUIRED_ARTIFACT_FILES, rep, 'appdir')
        scan_dir(d, rep, 'appdir')

    print('\n' + '=' * 60)
    print(f'{rep.checks} checks, {len(rep.failures)} failed, {len(rep.warnings)} warnings')
    if rep.warnings:
        print('\nWarnings:')
        for w in rep.warnings:
            print(f'  - {w}')
    if rep.failures:
        print('\nFAILURES — do not publish this build:')
        for f in rep.failures:
            print(f'  - {f}')
        return 1
    print('\nPASS — no GPL FFmpeg components, licence paperwork present.')
    print('Note: technical verification only, not legal advice.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

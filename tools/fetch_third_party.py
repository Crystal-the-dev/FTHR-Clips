#!/usr/bin/env python3
"""Download the third-party binaries that are not tracked in git.

These are kept out of the repository because they are large and
re-downloadable, but a working build needs them:

  1. Windows: the LGPL FFmpeg runtime (DLLs + ffmpeg.exe/ffprobe.exe, ~152 MB).
     Pinned by version AND sha256 in tools/ffmpeg_manifest.json. The exact
     build matters — AUDIT-005 was caused by a GPL FFmpeg being swapped in.
     The *headers* and *import libs* are tracked, so the engine compiles from a
     clean clone; this is only needed to link, run and package.
  2. Windows: the MSVC 2022 x64 redistributable the Inno Setup installer bundles.
  3. Linux: the LGPL FFmpeg the engine is COMPILED against and shipped with
     (~57 MB, headers + libs together). Pinned in
     tools/ffmpeg_manifest_linux.json. AUDIT-014: the distribution's FFmpeg is a
     GPL build on every mainstream distro, and it is a different SONAME
     generation, so headers and libraries must come from the same archive.

    python tools/fetch_third_party.py --ffmpeg          # Windows runtime
    python tools/fetch_third_party.py --ffmpeg-linux    # Linux headers + libs
    python tools/fetch_third_party.py --vcredist
    python tools/fetch_third_party.py --all             # what this platform needs

Every downloaded FFmpeg file is checked against the sha256 recorded in the
manifest. A mismatch aborts — it means either a corrupted download or a
different build than the one the licence paperwork describes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'tools' / 'ffmpeg_manifest.json'
FFMPEG_DIR = ROOT / 'FTHRcapture' / 'FTHRclips' / 'third_party' / 'ffmpeg'
REDIST_DIR = ROOT / 'redist'

# AUDIT-014: the Linux engine must NOT link the distribution's FFmpeg, which is
# a GPL build on every mainstream distro. This is the LGPL runtime it is built
# against and shipped with instead.
MANIFEST_LINUX = ROOT / 'tools' / 'ffmpeg_manifest_linux.json'
FFMPEG_LINUX_DIR = ROOT / 'FTHRcapture_linux' / 'third_party' / 'ffmpeg'

VCREDIST_URL = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    print(f'  downloading {url}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, dest.open('wb') as out:
        total = int(resp.headers.get('Content-Length') or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                print(f'\r  {pct:3d}%  {done / 1e6:.1f} / {total / 1e6:.1f} MB',
                      end='', flush=True)
        print()


def fetch_ffmpeg(force: bool) -> int:
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    src = manifest['source']
    expected = manifest['shipped_files_sha256']
    bin_dir = FFMPEG_DIR / 'bin'

    if bin_dir.is_dir() and not force:
        have = {p.name: p for p in bin_dir.iterdir() if p.is_file()}
        if set(expected) <= set(have):
            bad = [n for n, digest in expected.items()
                   if _sha256(have[n]) != digest]
            if not bad:
                print(f'FFmpeg {manifest["version"]} already present and verified.')
                return 0
            print(f'  present but {len(bad)} file(s) failed sha256: {bad}')

    print(f'FFmpeg {manifest["version"]} ({manifest["license"]}) '
          f'from {src["project"]}')

    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / src['asset']
        _download(src['url'], archive)

        digest = _sha256(archive)
        if digest != src['sha256']:
            print(f'ERROR: archive sha256 mismatch\n'
                  f'  expected {src["sha256"]}\n'
                  f'  got      {digest}\n'
                  f'This is NOT the build the licence paperwork describes. '
                  f'Refusing to install it.', file=sys.stderr)
            return 1
        print('  archive sha256 OK')

        with zipfile.ZipFile(archive) as zf:
            # BtbN archives nest everything under a single top-level directory.
            members = [n for n in zf.namelist() if not n.endswith('/')]
            root_prefix = members[0].split('/', 1)[0] + '/'
            staged = Path(td) / 'staged'
            for name in members:
                rel = name[len(root_prefix):] if name.startswith(root_prefix) else name
                if not rel:
                    continue
                target = staged / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as fsrc, target.open('wb') as fdst:
                    shutil.copyfileobj(fsrc, fdst)

        staged_bin = staged / 'bin'
        missing = [n for n in expected if not (staged_bin / n).is_file()]
        if missing:
            print(f'ERROR: archive is missing expected files: {missing}',
                  file=sys.stderr)
            return 1
        bad = [n for n, d in expected.items() if _sha256(staged_bin / n) != d]
        if bad:
            print(f'ERROR: per-file sha256 mismatch: {bad}', file=sys.stderr)
            return 1
        print(f'  all {len(expected)} binaries verified against the manifest')

        if bin_dir.exists():
            shutil.rmtree(bin_dir)
        shutil.copytree(staged_bin, bin_dir)

        # Headers and import libs are tracked in git; only replace them if the
        # clone is missing them (e.g. a shallow export).
        for sub in ('include', 'lib'):
            dest = FFMPEG_DIR / sub
            if not dest.exists() and (staged / sub).exists():
                shutil.copytree(staged / sub, dest)
                print(f'  restored missing {sub}/')

        lic = FFMPEG_DIR / 'LICENSE.txt'
        if not lic.exists():
            for cand in ('LICENSE.txt', 'LICENSE'):
                if (staged / cand).is_file():
                    shutil.copy2(staged / cand, lic)
                    break

    print(f'FFmpeg installed into {bin_dir.relative_to(ROOT)}')
    return 0


def fetch_ffmpeg_linux(force: bool) -> int:
    """Install the pinned LGPL FFmpeg the Linux engine builds against (AUDIT-014).

    Unlike the Windows side, this provides BOTH the headers/pkgconfig used at
    compile time and the shared libraries shipped in the AppImage. They have to
    come from the same archive: the replacement is a different SONAME generation
    from the distribution's (libavcodec.so.62 vs .so.60), so building against
    one and loading the other is not an option.
    """
    manifest = json.loads(MANIFEST_LINUX.read_text(encoding='utf-8'))
    src = manifest['source']
    expected = manifest['shipped_files_sha256']
    lib_dir = FFMPEG_LINUX_DIR / 'lib'

    if lib_dir.is_dir() and not force:
        have = {p.name: p for p in lib_dir.iterdir() if p.is_file()}
        if set(expected) <= set(have):
            bad = [n for n, d in expected.items() if _sha256(have[n]) != d]
            if not bad:
                print(f'LGPL FFmpeg {manifest["version"]} already present and verified.')
                return 0
            print(f'  present but {len(bad)} file(s) failed sha256: {bad}')

    print(f'FFmpeg {manifest["version"]} ({manifest["license"]}) '
          f'from {src["project"]} — Linux')

    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / src['asset']
        _download(src['url'], archive)

        digest = _sha256(archive)
        if digest != src['sha256']:
            print('ERROR: archive sha256 mismatch\n'
                  f'  expected {src["sha256"]}\n'
                  f'  got      {digest}\n'
                  'This is NOT the build the licence paperwork describes. '
                  'Refusing to install it.', file=sys.stderr)
            return 1
        print('  archive sha256 OK')

        staged = Path(td) / 'staged'
        with tarfile.open(archive) as tf:
            members = [m for m in tf.getmembers() if m.isfile() or m.issym()]
            root_prefix = members[0].name.split('/', 1)[0] + '/'
            for m in members:
                rel = m.name[len(root_prefix):] if m.name.startswith(root_prefix) else m.name
                if not rel or rel.startswith(('doc/', 'man/', 'presets/')):
                    continue
                m2 = tf.getmember(m.name)
                m2.name = rel
                # filter='data' refuses absolute paths and traversal (py3.12+).
                try:
                    tf.extract(m2, staged, filter='data')
                except TypeError:            # older tarfile without `filter`
                    tf.extract(m2, staged)

        staged_lib = staged / 'lib'
        missing = [n for n in expected if not (staged_lib / n).is_file()]
        if missing:
            print(f'ERROR: archive is missing expected libraries: {missing}',
                  file=sys.stderr)
            return 1
        bad = [n for n, d in expected.items() if _sha256(staged_lib / n) != d]
        if bad:
            print(f'ERROR: per-file sha256 mismatch: {bad}', file=sys.stderr)
            return 1
        print(f'  all {len(expected)} libraries verified against the manifest')

        if FFMPEG_LINUX_DIR.exists():
            shutil.rmtree(FFMPEG_LINUX_DIR)
        FFMPEG_LINUX_DIR.mkdir(parents=True)
        for sub in ('lib', 'include', 'bin'):
            if (staged / sub).exists():
                shutil.copytree(staged / sub, FFMPEG_LINUX_DIR / sub,
                                symlinks=True)
        for lic in ('LICENSE.txt', 'LICENSE'):
            if (staged / lic).is_file():
                shutil.copy2(staged / lic, FFMPEG_LINUX_DIR / 'LICENSE.txt')
                break

    print(f'LGPL FFmpeg installed into {FFMPEG_LINUX_DIR.relative_to(ROOT)}')
    print(f'  build the engine with: '
          f'-DFTHR_FFMPEG_ROOT={FFMPEG_LINUX_DIR.relative_to(ROOT)}')
    return 0


def fetch_vcredist(force: bool) -> int:
    dest = REDIST_DIR / 'vc_redist.x64.exe'
    if dest.is_file() and not force:
        print(f'vc_redist.x64.exe already present ({dest.stat().st_size / 1e6:.1f} MB).')
        return 0
    print('MSVC 2022 x64 redistributable (Microsoft, redistributable licence)')
    _download(VCREDIST_URL, dest)
    # Microsoft serves this from a permalink that follows the latest servicing
    # release, so there is no stable hash to pin. Sanity-check it is a PE.
    with dest.open('rb') as fh:
        if fh.read(2) != b'MZ':
            print('ERROR: downloaded file is not a Windows executable.',
                  file=sys.stderr)
            dest.unlink(missing_ok=True)
            return 1
    print(f'Installed into {dest.relative_to(ROOT)} '
          f'({dest.stat().st_size / 1e6:.1f} MB)')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ffmpeg', action='store_true',
                    help='Windows LGPL FFmpeg runtime')
    ap.add_argument('--ffmpeg-linux', action='store_true',
                    help='Linux LGPL FFmpeg (headers + libs) — AUDIT-014')
    ap.add_argument('--vcredist', action='store_true')
    ap.add_argument('--all', action='store_true',
                    help='everything for the current platform')
    ap.add_argument('--force', action='store_true',
                    help='re-download even if the files are already present')
    args = ap.parse_args()

    if not (args.ffmpeg or args.ffmpeg_linux or args.vcredist or args.all):
        ap.print_help()
        return 2

    # --all is platform-aware: fetching the Windows runtime on Linux (or the
    # reverse) downloads 150 MB nobody can use.
    on_windows = sys.platform == 'win32'
    rc = 0
    if args.ffmpeg or (args.all and on_windows):
        rc |= fetch_ffmpeg(args.force)
    if args.ffmpeg_linux or (args.all and not on_windows):
        rc |= fetch_ffmpeg_linux(args.force)
    if args.vcredist or (args.all and on_windows):
        rc |= fetch_vcredist(args.force)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

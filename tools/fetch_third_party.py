#!/usr/bin/env python3
"""Download the third-party binaries that are not tracked in git.

Two things are deliberately kept out of the repository because they are large
and re-downloadable, but both are required to produce a working Windows build:

  1. The LGPL FFmpeg runtime (DLLs + ffmpeg.exe/ffprobe.exe, ~152 MB).
     Pinned by version AND sha256 in tools/ffmpeg_manifest.json. The exact
     build matters — AUDIT-005 was caused by a GPL FFmpeg being swapped in.
  2. The MSVC 2022 x64 redistributable the Inno Setup installer bundles.

The FFmpeg *headers* and *import libs* ARE tracked, so the engine compiles
straight from a clean clone; you only need this to link, run and package.

    python tools/fetch_third_party.py --ffmpeg
    python tools/fetch_third_party.py --vcredist
    python tools/fetch_third_party.py --all

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
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'tools' / 'ffmpeg_manifest.json'
FFMPEG_DIR = ROOT / 'FTHRcapture' / 'FTHRclips' / 'third_party' / 'ffmpeg'
REDIST_DIR = ROOT / 'redist'

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
    ap.add_argument('--ffmpeg', action='store_true')
    ap.add_argument('--vcredist', action='store_true')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--force', action='store_true',
                    help='re-download even if the files are already present')
    args = ap.parse_args()

    if not (args.ffmpeg or args.vcredist or args.all):
        ap.print_help()
        return 2

    rc = 0
    if args.ffmpeg or args.all:
        rc |= fetch_ffmpeg(args.force)
    if args.vcredist or args.all:
        rc |= fetch_vcredist(args.force)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

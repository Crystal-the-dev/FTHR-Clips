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
import hashlib
import os
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

# Linux ships its own pinned LGPL FFmpeg (AUDIT-014) with its own manifest.
LINUX_MANIFEST_REL = 'tools/ffmpeg_manifest_linux.json'

# SONAMEs the distribution's (GPL) FFmpeg uses on current distros. Seeing one of
# these inside an artifact means a system build got collected, which is the
# AUDIT-014 failure mode.
# Exactly the FFmpeg library names. Anchored, because 'libav*' also matches
# libavif (the AV1 image codec) and libavc1394 (IEEE-1394) — treating those as
# FFmpeg produced false failures, and an earlier over-broad *delete* based on
# the same mistake removed libavif and broke the bundle's startup.
FFMPEG_SO_RE = re.compile(
    r'^lib(avcodec|avformat|avutil|avdevice|avfilter|swscale|swresample|postproc)'
    r'(-[0-9a-f]{8})?\.so[.0-9]*$')

SYSTEM_FFMPEG_SONAMES = frozenset((
    'libavcodec.so.60', 'libavformat.so.60', 'libavutil.so.58',
    'libavdevice.so.60', 'libavfilter.so.9',
    'libswscale.so.7', 'libswresample.so.4',
))

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
    env = os.environ.copy()
    if os.name != 'nt':
        # The pinned Linux CLI has no build-tree RPATH. Probe it against the
        # sibling libraries that ship with it; otherwise the loader exits 127
        # and an empty stdout used to be misreported as a clean configuration.
        candidates = (exe.parent / 'lib', exe.parent.parent / 'lib')
        lib_dir = next((p for p in candidates if p.is_dir()), None)
        if lib_dir is not None:
            previous = env.get('LD_LIBRARY_PATH', '')
            env['LD_LIBRARY_PATH'] = (
                str(lib_dir) if not previous else f'{lib_dir}{os.pathsep}{previous}')
    try:
        res = subprocess.run([str(exe), '-hide_banner', '-buildconf'],
                             capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        rep.warn(f'{exe.name}: could not run -buildconf ({e})')
        return

    if res.returncode != 0:
        rep.fail(f'{exe.name}: -buildconf exited {res.returncode}; runtime '
                 'capabilities were not verified')
        return

    conf = res.stdout or ''
    bad = [f.decode() for f in FORBIDDEN_FLAGS
           if re.search(rf'^\s*{re.escape(f.decode())}\s*$', conf, re.M)]
    if bad:
        rep.fail(f'{exe.name} -buildconf: {", ".join(bad)}')
    else:
        rep.ok(f'{exe.name} -buildconf: clean')

    try:
        enc_res = subprocess.run([str(exe), '-hide_banner', '-encoders'],
                                 capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return
    if enc_res.returncode != 0:
        rep.fail(f'{exe.name}: -encoders exited {enc_res.returncode}; encoder '
                 'capabilities were not verified')
        return
    enc = enc_res.stdout or ''
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



def _read_manifest(root, rel, rep):
    mf = root / rel
    if not mf.is_file():
        rep.fail(f'{rel} missing - the shipped FFmpeg origin is undocumented')
        return None
    try:
        return json.loads(mf.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        rep.fail(f'{rel} unreadable: {e}')
        return None


def check_linux_manifest(root, rep) -> None:
    """The Linux FFmpeg provenance manifest (AUDIT-014)."""
    print('\n-- Linux FFmpeg provenance manifest --')
    data = _read_manifest(root, LINUX_MANIFEST_REL, rep)
    if data is None:
        return
    for key in ('version', 'license', 'source', 'shipped_files_sha256'):
        if not data.get(key):
            rep.fail(f'{LINUX_MANIFEST_REL}: "{key}" missing')
    src = data.get('source') or {}
    if not src.get('sha256'):
        rep.fail(f'{LINUX_MANIFEST_REL}: source archive checksum not recorded')
    else:
        rep.ok(f'Linux FFmpeg {data.get("version")} documented, sha256 recorded')
    lic = str(data.get('license', ''))
    if 'LGPL' not in lic.upper():
        rep.fail(f'{LINUX_MANIFEST_REL}: license is "{lic}", expected LGPL')
    else:
        rep.ok(f'Linux manifest licence: {lic}')
    n = len(data.get('shipped_files_sha256') or {})
    if n < 7:
        rep.fail(f'{LINUX_MANIFEST_REL}: only {n} library hashes recorded, expected 7')
    else:
        rep.ok(f'{n} shipped library hashes recorded')


def _readelf_d(path):
    try:
        return subprocess.run(['readelf', '-d', str(path)],
                              capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _elf_needed(path):
    return re.findall(r'Shared library: \[([^\]]+)\]', _readelf_d(path))


def _elf_rpath(path):
    entries = re.findall(r'Library r(?:un)?path: \[([^\]]*)\]', _readelf_d(path))
    return [e for entry in entries for e in entry.split(':') if e]


def check_linux_ffmpeg_libs(root, rep, label, manifest_root=None) -> None:
    """Every FFmpeg .so in a Linux artifact must be a documented LGPL library.

    This is the check AUDIT-014 turns on. It is not enough that the engine links
    the right thing: the artifact must not *contain* the wrong thing. PyInstaller
    collects the system FFmpeg through cv2 and Qt whether or not anything uses it,
    and a GPL library sitting in the AppDir is a GPL library shipped.
    """
    print(f'\n-- Linux FFmpeg libraries in {label} --')
    data = _read_manifest(manifest_root or root, LINUX_MANIFEST_REL, rep)
    if data is None:
        rep.fail(f'{label}: cannot validate libraries without the Linux manifest')
        return
    hashes = data.get('shipped_files_sha256') or {}
    alias_sources = {}
    for versioned, soname in (data.get('soname_map') or {}).items():
        alias_sources[soname] = versioned
        alias_sources[soname.split('.so.', 1)[0] + '.so'] = versioned
    # Qt Multimedia ships its own FFmpeg inside the PyQt6-Qt6 wheel. It is a
    # different SONAME generation, the engine never loads it, and it is LGPL —
    # but it is still FFmpeg in the artifact, so it is checked rather than
    # ignored. Its bytes are not pinned (that is the PyQt6-Qt6 pin's job); its
    # licence is verified on every build.
    # Python wheels bring their own FFmpeg: Qt Multimedia inside PyQt6-Qt6, and
    # OpenCV inside opencv-python-headless (auditwheel renames those with an
    # 8-hex-digit suffix). Both are legitimate and neither is loaded by the
    # engine, but both are FFmpeg being distributed, so each is verified — by
    # licence rather than by hash, because their bytes follow the wheel version
    # rather than any decision made here.
    providers = data.get('additional_providers') or []
    provider_names = set()
    provider_prefixes = []
    for prov in providers:
        for n in prov.get('sonames') or []:
            provider_names.add(n)
        for pref in prov.get('name_prefixes') or []:
            provider_prefixes.append(pref)

    libs = [q for q in sorted(root.rglob('*'))
            if q.is_file() and not q.is_symlink()
            and FFMPEG_SO_RE.match(q.name)]
    if not libs:
        rep.warn(f'{label}: no FFmpeg shared libraries found')
        return

    for lib in libs:
        name = lib.name
        if name in hashes:
            digest = hashlib.sha256(lib.read_bytes()).hexdigest()
            if digest == hashes[name]:
                rep.ok(f'{name}: documented, sha256 matches the manifest')
            else:
                rep.fail(f'{name}: sha256 MISMATCH - expected {hashes[name][:16]}..., '
                         f'got {digest[:16]}... (not the library the manifest describes)')
        elif name in alias_sources:
            source_name = alias_sources[name]
            expected = hashes.get(source_name)
            digest = hashlib.sha256(lib.read_bytes()).hexdigest()
            if expected and digest == expected:
                rep.ok(f'{name}: verified alias of documented {source_name}')
            else:
                rep.fail(
                    f'{name}: alias sha256 does not match documented {source_name}')
        elif name in provider_names or any(name.startswith(pf)
                                           for pf in provider_prefixes):
            # Documented additional provider: verify the licence, not the hash.
            blob = lib.read_bytes()
            gpl = [f.decode() for f in FORBIDDEN_FLAGS if f in blob]
            banners = {m.group(1).decode().strip()
                       for m in LICENSE_BANNER.finditer(blob)}
            gpl_banner = [b for b in banners if re.match(r'^GPL', b)]
            if gpl:
                rep.fail(f'{name}: wheel-provided FFmpeg carries GPL flags '
                         f'({", ".join(gpl)}) - the wheel changed')
            elif gpl_banner:
                rep.fail(f'{name}: wheel-provided FFmpeg reports "{gpl_banner[0]}"')
            else:
                rep.ok(f'{name}: documented wheel-provided FFmpeg, LGPL')
        elif name in SYSTEM_FFMPEG_SONAMES:
            rep.fail(f'{name}: this is the distribution GPL FFmpeg - it must not '
                     f'be in the artifact (AUDIT-014)')
        else:
            rep.fail(f'{name}: FFmpeg library not listed in {LINUX_MANIFEST_REL} - '
                     f'undocumented provenance, most likely collected from the system')

        for need in _elf_needed(lib):
            if re.match(r'libx26[45]\.', need) or 'xvidcore' in need:
                rep.fail(f'{name}: links {need} - GPL codec dependency')

        for entry in _elf_rpath(lib):
            if not entry.startswith('$ORIGIN'):
                rep.fail(f'{name}: RPATH entry "{entry}" is an absolute host path')


def check_linux_engine(engine, rep) -> None:
    """The engine must load bundled FFmpeg, not whatever the system offers."""
    print(f'\n-- Linux engine linkage ({engine.name}) --')
    if not engine.is_file():
        rep.warn(f'{engine} not found - skipping linkage check')
        return
    needed = [n for n in _elf_needed(engine) if re.match(r'^lib(av|sw)', n)]
    if not needed:
        rep.warn(f'{engine.name}: no FFmpeg in DT_NEEDED')
    for n in needed:
        if n in SYSTEM_FFMPEG_SONAMES:
            rep.fail(f'{engine.name}: linked against the system GPL FFmpeg ({n}) - '
                     f'configure with -DFTHR_FFMPEG_ROOT')
        else:
            rep.ok(f'{engine.name}: needs {n}')
    rpath = _elf_rpath(engine)
    if not rpath:
        rep.fail(f'{engine.name}: no RPATH/RUNPATH - it would load system libraries')
    else:
        outside = [e for e in rpath if not e.startswith('$ORIGIN')]
        if outside:
            rep.fail(f'{engine.name}: RPATH leaks absolute host paths: {outside}')
        else:
            rep.ok(f'{engine.name}: RPATH is bundle-relative ({":".join(rpath)})')



def _repo_root() -> Path:
    """Repository root, so an --appdir check can still find the manifest.

    The AppDir is a build output; the manifest it must be validated against
    lives in the source tree next to this script.
    """
    return Path(__file__).resolve().parent.parent


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
        check_linux_manifest(root, rep)
        check_python_sources(root, rep)
        vendored = root / 'FTHRcapture' / 'FTHRclips' / 'third_party' / 'ffmpeg'
        if vendored.is_dir():
            scan_dir(vendored, rep, 'vendored ffmpeg')
        else:
            rep.warn('no vendored ffmpeg directory (expected on a Linux checkout)')
        vendored_linux = root / 'FTHRcapture_linux' / 'third_party' / 'ffmpeg'
        if vendored_linux.is_dir():
            scan_dir(vendored_linux, rep, 'vendored ffmpeg (linux)')
            check_linux_ffmpeg_libs(vendored_linux, rep,
                                    'vendored ffmpeg (linux)', manifest_root=root)
            check_linux_engine(
                root / 'FTHRcapture_linux' / 'build' / 'FTHRclips', rep)
        else:
            rep.warn('no vendored linux ffmpeg (run tools/fetch_third_party.py '
                     '--ffmpeg-linux before a Linux release build)')
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
        # AUDIT-014: the artifact is where it actually matters. A GPL library
        # the engine never loads is still a GPL library being distributed, and
        # PyInstaller collects the system FFmpeg through cv2 and Qt.
        check_linux_ffmpeg_libs(d, rep, 'appdir', manifest_root=_repo_root())
        engine = next(iter(sorted(d.rglob('FTHRclips'))), None)
        if engine is not None:
            check_linux_engine(engine, rep)
        else:
            rep.fail('appdir: no FTHRclips engine binary found')

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

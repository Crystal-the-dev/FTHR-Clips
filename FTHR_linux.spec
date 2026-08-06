# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for FTHR Clips — Linux AppImage bundle.
# Run on Linux: pyinstaller FTHR_linux.spec --clean
from pathlib import Path
import sys as _sys

ROOT       = Path(SPECPATH)
UI_DIR     = ROOT / 'FTHR_UI'
ASSETS_DIR = UI_DIR / 'assets'
ENGINE_BIN = ROOT / 'FTHRcapture_linux' / 'build' / 'FTHRclips'

# Product version comes from FTHR_UI/version.py — never retype it here.
_sys.path.insert(0, str(UI_DIR))
from version import APP_ID  # noqa: E402

# Qt6 plugin dirs — bundle Wayland + XCB so the app works on both.
# Arch puts them in /usr/lib/qt6; Debian/Ubuntu use a multiarch path.
def _first_existing(*candidates):
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p
    return Path(candidates[0])          # keep a stable value for error messages


import sysconfig as _sysconfig  # noqa: E402

_MULTIARCH = _sysconfig.get_config_var('MULTIARCH') or 'x86_64-linux-gnu'

_QT6_PLUG = _first_existing(
    '/usr/lib/qt6/plugins',
    f'/usr/lib/{_MULTIARCH}/qt6/plugins',
    '/usr/lib64/qt6/plugins',
)

def _so(subdir, dest):
    d = _QT6_PLUG / subdir
    return [(str(p), dest) for p in d.glob('*.so')] if d.exists() else []


def _find_lib(soname):
    """Locate a system shared library across distro layouts.

    A hardcoded '/usr/lib/libportaudio.so.2' used to sit in the binaries list.
    That path is correct on Arch and wrong on every Debian-family distro, which
    puts it under /usr/lib/<multiarch>/ — so `bash build_linux.sh` aborted with
    "Unable to find '/usr/lib/libportaudio.so.2'". Verified on Ubuntu 24.04.
    """
    import ctypes.util
    for cand in (f'/usr/lib/{_MULTIARCH}/{soname}',
                 f'/usr/lib/{soname}',
                 f'/usr/lib64/{soname}',
                 f'/lib/{_MULTIARCH}/{soname}'):
        if Path(cand).exists():
            return cand
    # Last resort: ask the dynamic linker.
    stem = soname.split('.so')[0].removeprefix('lib')
    found = ctypes.util.find_library(stem)
    if found and Path(found).exists():
        return found
    raise SystemExit(
        f'FTHR_linux.spec: required system library {soname!r} not found.\n'
        f'  Arch:          sudo pacman -S portaudio\n'
        f'  Debian/Ubuntu: sudo apt install libportaudio2\n'
        f'  Fedora:        sudo dnf install portaudio')


_PORTAUDIO = _find_lib('libportaudio.so.2')

# ---------------------------------------------------------------------------
# LGPL FFmpeg — AUDIT-014
#
# The engine is COMPILED against this tree (see FTHRcapture_linux/CMakeLists.txt,
# FTHR_FFMPEG_ROOT) and must load it at runtime. Its SONAMEs are a different
# generation from the distribution's (libavcodec.so.62 vs .so.60), so a system
# FFmpeg cannot accidentally satisfy the engine — but PyInstaller will happily
# collect the system copies as well, pulled in by cv2 and Qt, and the AppDir
# would then contain GPL libraries even though nothing links them.
#
# So: ship these deliberately, and drop the GPL strays in a post-processing
# step in build_linux.sh.
# ---------------------------------------------------------------------------
_FFMPEG_ROOT = ROOT / 'FTHRcapture_linux' / 'third_party' / 'ffmpeg'
_FFMPEG_LIB = _FFMPEG_ROOT / 'lib'

if not _FFMPEG_LIB.is_dir():
    raise SystemExit(
        'FTHR_linux.spec: the pinned LGPL FFmpeg is missing.\n'
        f'  Expected: {_FFMPEG_LIB}\n'
        '  Fetch it: python tools/fetch_third_party.py --ffmpeg-linux\n'
        '  Bundling the distribution FFmpeg instead is what AUDIT-014 records:\n'
        '  it is a GPL build and the licence gate will refuse to package it.')

# Real files only — the .so and .so.N entries are symlinks into these, and
# PyInstaller resolves and flattens them anyway.
_FFMPEG_LIBS = sorted(
    p for p in _FFMPEG_LIB.glob('lib*.so.*')
    if p.is_file() and not p.is_symlink()
)
if len(_FFMPEG_LIBS) < 7:
    raise SystemExit(
        f'FTHR_linux.spec: expected 7 FFmpeg libraries in {_FFMPEG_LIB}, '
        f'found {len(_FFMPEG_LIBS)}. Re-run '
        f'`python tools/fetch_third_party.py --ffmpeg-linux --force`.')

a = Analysis(
    [str(UI_DIR / 'main.py')],
    pathex=[str(UI_DIR)],
    binaries=[
        (str(ENGINE_BIN), '.'),
        (_PORTAUDIO, '.'),
        # The verified LGPL FFmpeg the engine was built against.
        *[(str(p), '.') for p in _FFMPEG_LIBS],
        *_so('platforms',                           'PyQt6/Qt6/plugins/platforms'),
        *_so('wayland-decoration-client',           'PyQt6/Qt6/plugins/wayland-decoration-client'),
        *_so('wayland-shell-integration',           'PyQt6/Qt6/plugins/wayland-shell-integration'),
        *_so('wayland-graphics-integration-client', 'PyQt6/Qt6/plugins/wayland-graphics-integration-client'),
        *_so('imageformats',                        'PyQt6/Qt6/plugins/imageformats'),
    ],
    datas=[
        (str(ASSETS_DIR / 'fthr_logo.png'),  'assets'),
        # Licence paperwork must travel INSIDE the bundle (AUDIT-005), so a
        # portable copy is as complete as an installed one.
        (str(ROOT / 'LICENSE'), '.'),
        (str(ROOT / 'THIRD_PARTY_NOTICES.md'), '.'),
        (str(ROOT / 'licenses'), 'licenses'),
        # LGPLv3 obliges us to ship FFmpeg's licence text with the binaries,
        # and the manifest is what tools/verify_release_licenses.py checks the
        # shipped libraries against.
        (str(_FFMPEG_ROOT / 'LICENSE.txt'), 'licenses/ffmpeg'),
        (str(ROOT / 'tools' / 'ffmpeg_manifest_linux.json'), 'licenses/ffmpeg'),
        (str(ASSETS_DIR / 'fonts'),          'assets/fonts'),
        (str(ASSETS_DIR / 'icons'),          'assets/icons'),
        (str(ASSETS_DIR / 'sounds'),         'assets/sounds'),
    ],
    hiddenimports=[
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
        'sounddevice',
        'numpy',
        'cv2',
        'keyboard',
        # UI submodules
        'ui.capture_card',
        'ui.capture_card_client',
        'ui.capture_card_process',
        'ui.capture_settings_widget',
        'ui.clip_grid',
        'ui.clip_viewer',
        'ui.customize_page',
        'ui.app_style',
        'ui.screenshot_editor',
        'ui.splash_screen',
        'ui.style',
        'ui.upload_settings_widget',
        # Core submodules
        'core.audio_mixer',
        'core.camera_recorder',
        'core.capture_bridge',
        'core.focus_monitor',
        'core.game_detector',
        'core.hotkey_manager',
        'core.mic_recorder',
        'core.presets_manager',
        'core.settings_manager',
        'core.theme_manager',
        'core.upload_manager',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # imageio_ffmpeg is deliberately EXCLUDED (AUDIT-005).
        # Its bundled binary is a gyan.dev build configured with
        # --enable-gpl --enable-libx264 --enable-libx265, i.e. GPLv3.
        # Shipping it would place this whole artifact under the GPL.
        # FTHR uses the LGPL FFmpeg next to the engine — see
        # FTHR_UI/core/ffmpeg_tools.py.
        'imageio_ffmpeg',
        # Qt modules we don't use (Widgets-only app)
        'PyQt6.QtQuick', 'PyQt6.QtQml', 'PyQt6.QtWebEngine',
        'PyQt6.QtWebEngineCore', 'PyQt6.QtBluetooth', 'PyQt6.QtPositioning',
        'PyQt6.QtSensors', 'PyQt6.QtLocation', 'PyQt6.Qt3D',
        'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets', 'PyQt6.QtNfc',
        # Standard library bloat
        'tkinter', 'unittest', 'html', 'xmlrpc',
        'xml', 'pydoc', 'doctest', 'difflib', 'ftplib', 'imaplib',
        'poplib', 'smtplib', 'telnetlib', 'nntplib',
        # Scientific stack not used
        'matplotlib', 'scipy', 'pandas', 'PIL', 'IPython',
        'sklearn', 'skimage', 'sympy',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_ID,
    debug=False,
    strip=True,
    upx=False,    # AppImage has its own compression; UPX on .so files can break things
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=True,
    upx=False,
    name=APP_ID,
)

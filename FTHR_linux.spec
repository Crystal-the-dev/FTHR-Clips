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

# Qt6 plugin dirs — bundle Wayland + XCB so the app works on both
_QT6_PLUG = Path('/usr/lib/qt6/plugins')

def _so(subdir, dest):
    d = _QT6_PLUG / subdir
    return [(str(p), dest) for p in d.glob('*.so')] if d.exists() else []

a = Analysis(
    [str(UI_DIR / 'main.py')],
    pathex=[str(UI_DIR)],
    binaries=[
        (str(ENGINE_BIN), '.'),
        ('/usr/lib/libportaudio.so.2', '.'),
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

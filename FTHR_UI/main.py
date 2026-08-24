"""
FTHR Clips - Main Application Entry Point

Layout:
    Top bar  — white, frameless drag region:
               Logo | ■ status | [CAPTURE▼] [SOURCE▼] [HOTKEYS▼] | – ×
    Body     — black, clip grid fills all remaining space
"""

import sys
import subprocess
import time
import math
import os
import json
import tempfile
import threading
from pathlib import Path
from datetime import datetime
_NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}


# ---------------------------------------------------------------------------
# File logging — every print() in the app also lands in ~/.fthr/logs/fthr.log.
# Essential for alpha builds: packaged Windows apps (pythonw) have NO console,
# so without this every diagnostic message vanishes into the void.
# ---------------------------------------------------------------------------

class _LogTee:
    """Mirror a stream (may be None in frozen GUI builds) into a log file."""
    _MAX_BYTES = 2 * 1024 * 1024  # rotate at 2 MB, keep one .old

    def __init__(self, stream, log_path):
        self._stream = stream
        self._log_path = log_path
        try:
            if log_path.exists() and log_path.stat().st_size > self._MAX_BYTES:
                log_path.replace(log_path.with_suffix('.log.old'))
            self._log = open(log_path, 'a', encoding='utf-8', errors='replace')
        except Exception:
            self._log = None

    def write(self, text):
        if self._stream is not None:
            try:
                self._stream.write(text)
            except Exception:
                pass
        if self._log is not None:
            try:
                self._log.write(text)
                self._log.flush()
            except Exception:
                pass

    def flush(self):
        for s in (self._stream, self._log):
            if s is not None:
                try:
                    s.flush()
                except Exception:
                    pass


def _setup_file_logging():
    try:
        log_dir = Path.home() / '.fthr' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / 'fthr.log'
        sys.stdout = _LogTee(sys.stdout, log_file)
        sys.stderr = _LogTee(sys.stderr, log_file)
        # Version goes in the banner so a pasted log identifies its build
        # without the reporter having to remember which one they installed.
        from version import __version__ as _ver
        print(f"\n===== FTHR Clips {_ver} started "
              f"{datetime.now():%Y-%m-%d %H:%M:%S} ({sys.platform}) =====")
        # Structured logging into the SAME file the tee writes to, so testers
        # keep sending one log. print() keeps working; new diagnostics go
        # through core.diagnostics, which timestamps them, names the thread and
        # redacts credentials on the way out (AUDIT-007).
        from core import diagnostics
        diagnostics.configure(log_file=log_file)
    except Exception as e:
        # Logging must never prevent startup — but a swallowed failure here is
        # why a tester's log can be empty with no explanation. Say it on
        # stderr, which is the one channel that has not been set up yet.
        print(f'[Startup] File logging unavailable: {type(e).__name__}: {e}',
              file=sys.__stderr__ or sys.stderr)


_setup_file_logging()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QScrollArea, QFrame, QMessageBox, QComboBox,
    QGraphicsOpacityEffect, QSizePolicy, QStackedWidget,
    QCheckBox, QSlider,
    QToolButton, QButtonGroup, QFileDialog, QLineEdit, QMenu,
    QSystemTrayIcon,
)
from PySide6.QtCore import (
    QTimer, Signal, Qt, QPoint, QPointF, QSize, QRect,
    QPropertyAnimation, QAbstractAnimation, QEasingCurve,
    QParallelAnimationGroup,
)
from PySide6.QtGui import QPixmap, QFontDatabase, QCursor, QPainter, QPen, QColor, QIcon, QBrush, QPolygonF, QPalette, QKeySequence, QAction

from version import (
    __version__ as APP_VERSION, APP_NAME, BUILD_DATE,
    SOURCE_LICENSE, DISTRIBUTION_LICENSE,
)
from core import linux_tools
from core.capture_bridge import CaptureBridge
from core.alpha_capabilities import (
    effective_multiband_audio_enabled,
    encoder_preset_supported,
    filter_alpha_preset,
    focus_pause_supported,
)
from core.capture_settings import (
    EXTENDED_CLIP_VALUES,
    FPS_VALUES,
    NORMAL_CLIP_VALUES,
    CaptureConfig,
    CaptureConfigTracker,
    compute_buffer_seconds,
    validate_extended_clip_length,
    validate_fps,
    validate_normal_clip_length,
)
from core.capture_health import (
    CaptureHealthMonitor,
    CaptureHealthState,
    evaluate_save_admission,
)
from core.diagnostics import get_logger
from core.engine_startup_diagnostics import (
    extract_startup_failure,
    extract_startup_warnings,
)
from core.clip_files import cleanup_stale_partial_clips, is_completed_video_path
from core.clip_readiness import get_clip_readiness_registry
from core.save_state import (SaveStateMachine, EngineEvent, OutcomeKind)
from core.hotkey_manager import HotkeyManager
from core.game_detector import GameDetector
from core.focus_monitor import FocusMonitor
from core.presets_manager import PresetsManager, PRESET_KEYS
from core.settings_manager import SettingsManager
from core.theme_manager import ThemeManager
from core.windows_monitor import (
    enumerate_windows_monitors,
    normalize_monitor_device_path,
)
from core.screenshot_target import build_grim_command, select_qt_screen
from core.screenshot_save import (
    ScreenshotPngSaveWorker,
    ScreenshotSaveError,
    reserve_screenshot_paths,
)
from core.library_ownership import add_import_root, remove_import_root
from core.mic_recorder import MicRecorder, write_wav
from core.windows_microphone_devices import (
    list_native_microphones,
    migrate_legacy_microphone_name,
)
from core.ffmpeg_tools import (
    get_ffmpeg_exe, software_video_args, FFmpegUnavailable)
from ui.capture_card_client import CaptureCardClient
from ui.error_bar import ErrorBar
from ui.clip_grid import ClipGrid
from ui.customize_page import CustomizePage
from ui.capture_settings_widget import (
    _enumerate_capturable_windows,
)
from ui.style import (
    Colors, Fonts, Sizes,
    label_display, label_uppercase, label_body,
    status_active_qss, status_idle_qss, status_warning_qss,
    combo_qss, button_primary_qss, button_outline_qss,
    button_secondary_qss, slider_qss, checkbox_qss, scrollbar_qss, tooltip_qss,
)
from ui.app_style import apply_app_style, configure_qt_for_linux_ui

try:
    import numpy as _np
    import sounddevice as _sd
    _SD_AVAILABLE = True
except Exception:
    _SD_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_RESOLUTION_DIMS = {
    '480p': (854, 480), '720p': (1280, 720),
    '1080p': (1920, 1080), '1440p': (2560, 1440), 'source': (0, 0),
}
_DIMS_TO_LABEL = {v: k.upper() for k, v in _RESOLUTION_DIMS.items()}

BITRATE_PRESETS = {
    '480p':   {'low': 2500,  'medium': 5000,  'high': 10000},
    '720p':   {'low': 5000,  'medium': 12000, 'high': 20000},
    '1080p':  {'low': 10000, 'medium': 25000, 'high': 50000},
    '1440p':  {'low': 15000, 'medium': 35000, 'high': 60000},
    'source': {'low': 10000, 'medium': 25000, 'high': 50000},
}

def _resolution_to_dims(name: str) -> tuple[int, int]:
    return _RESOLUTION_DIMS.get(name, (0, 0))


def _load_logo_asset(path: Path) -> QPixmap:
    """Load the provenance-gated project logo without altering its colors."""
    return QPixmap(str(path))


def _make_settings_icon(size: int = 18, color: str = Colors.TEXT) -> QIcon:
    """Draw a minimal 3-line sliders icon using QPainter."""
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    # Three horizontal lines (decreasing length → "filter/sliders" icon)
    p.drawLine(1, 4,  size - 1, 4)
    p.drawLine(1, 9,  size - 4, 9)
    p.drawLine(1, 14, size - 7, 14)
    p.end()
    return QIcon(pix)


def _icon_canvas(size: int):
    """Return (QPixmap, QPainter) ready for stroke drawing."""
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pix, p


def _make_general_icon(size: int = 22, color: str = Colors.TEXT) -> QIcon:
    """Gear / sun-burst — 8 pegs around a hollow ring."""
    pix, p = _icon_canvas(size)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx = cy = size / 2
    # Centre ring
    r1 = size * 0.20
    p.drawEllipse(int(cx - r1), int(cy - r1), int(r1 * 2), int(r1 * 2))
    # 8 pegs
    for i in range(8):
        a = i * math.pi / 4
        x1 = cx + math.cos(a) * (r1 + 1.5)
        y1 = cy + math.sin(a) * (r1 + 1.5)
        x2 = cx + math.cos(a) * (size * 0.46)
        y2 = cy + math.sin(a) * (size * 0.46)
        p.drawLine(int(x1), int(y1), int(x2), int(y2))
    p.end()
    return QIcon(pix)


def _make_clip_icon(size: int = 22, color: str = Colors.TEXT) -> QIcon:
    """Filmstrip — rounded rectangle with sprocket holes top + bottom."""
    pix, p = _icon_canvas(size)
    pen = QPen(QColor(color), 1.6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    pad = 2
    p.drawRect(pad, pad + 1, size - pad * 2, size - pad * 2 - 2)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.PenStyle.NoPen)
    hole_w = (size - pad * 2 - 8) / 3
    for i in range(3):
        x = pad + 4 + int(i * (hole_w + 2))
        p.drawRect(x, pad + 4, max(2, int(hole_w)), 2)
        p.drawRect(x, size - pad - 6, max(2, int(hole_w)), 2)
    p.end()
    return QIcon(pix)


def _make_audio_icon(size: int = 22, color: str = Colors.TEXT) -> QIcon:
    """Speaker with two emanation arcs."""
    pix, p = _icon_canvas(size)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(color)))
    # Speaker box
    p.drawRect(3, int(size * 0.38), 4, int(size * 0.24))
    # Speaker cone (triangle)
    cone = QPolygonF([
        QPointF(7, size * 0.38),
        QPointF(size * 0.55, size * 0.18),
        QPointF(size * 0.55, size * 0.82),
        QPointF(7, size * 0.62),
    ])
    p.drawPolygon(cone)
    # Sound arcs
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i, r in enumerate([3.5, 6.5]):
        rect_x = int(size * 0.55 + i * 1)
        rect_y = int(size / 2 - r)
        p.drawArc(rect_x, rect_y, int(r * 2), int(r * 2),
                  -60 * 16, 120 * 16)
    p.end()
    return QIcon(pix)


def _make_upload_icon(size: int = 22, color: str = Colors.TEXT) -> QIcon:
    """Upward arrow rising from a tray — upload icon."""
    pix, p = _icon_canvas(size)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx = size / 2
    # Tray (horizontal base line)
    p.drawLine(3, int(size * 0.78), size - 3, int(size * 0.78))
    # Shaft of arrow
    p.drawLine(int(cx), int(size * 0.62), int(cx), int(size * 0.22))
    # Arrow head
    p.drawLine(int(cx), int(size * 0.22), int(cx - 4), int(size * 0.40))
    p.drawLine(int(cx), int(size * 0.22), int(cx + 4), int(size * 0.40))
    p.end()
    return QIcon(pix)


def _make_visuals_icon(size: int = 22, color: str = Colors.TEXT) -> QIcon:
    """Monitor — rectangle on a stand."""
    pix, p = _icon_canvas(size)
    pen = QPen(QColor(color), 1.6)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(2, 4, size - 4, int(size * 0.55))
    # Stand
    p.drawLine(int(size / 2) - 3, int(size * 0.78), int(size / 2) + 3, int(size * 0.78))
    p.drawLine(int(size / 2),     int(size * 0.59),  int(size / 2),     int(size * 0.78))
    p.end()
    return QIcon(pix)


def _make_version_icon(size: int = 22, color: str = Colors.TEXT) -> QIcon:
    """Up-arrow inside a downloading-style ring — version & updates."""
    pix, p = _icon_canvas(size)
    pen = QPen(QColor(color), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Three-quarter ring
    p.drawArc(3, 3, size - 6, size - 6, 30 * 16, 300 * 16)
    # Down arrow (download)
    cx = size / 2
    p.drawLine(int(cx), int(size * 0.30), int(cx), int(size * 0.66))
    p.drawLine(int(cx), int(size * 0.66), int(cx - 3), int(size * 0.55))
    p.drawLine(int(cx), int(size * 0.66), int(cx + 3), int(size * 0.55))
    p.end()
    return QIcon(pix)


_icon_registry: list[tuple[object, str, int]] = []

# QPainter fallbacks for icons that don't have a PNG asset on disk.
# Maps icon filename → maker(size, color) → QIcon.
_PAINTER_ICON_FALLBACKS: dict[str, object] = {
    'upload.png': _make_upload_icon,
}


def _tint_pixmap(pixmap: QPixmap, color: QColor) -> QPixmap:
    """Recolor all opaque pixels to *color*, preserving alpha."""
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    p = QPainter(tinted)
    p.drawPixmap(0, 0, pixmap)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(tinted.rect(), color)
    p.end()
    return tinted


def _load_icon(name: str, size: int = 20) -> QIcon:
    """Load icon, preferring custom theme override over default asset.

    Custom (imported) icons are used as-is.
    Default icons are tinted with the icon tint color from the theme.
    """
    theme = ThemeManager()

    # Custom imported icon — use as-is, no tinting
    try:
        custom = theme.get_custom_icon_path(name)
        if custom and custom.exists():
            pix = QPixmap(str(custom)).scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            return QIcon(pix)
    except Exception:
        pass

    # Performance icon = updates.png flipped vertically (arrow points up instead of down)
    if name == 'performance.png':
        from PySide6.QtGui import QTransform
        src = Path(__file__).parent / 'assets' / 'icons' / 'updates.png'
        if src.exists():
            pix = QPixmap(str(src)).scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pix = pix.transformed(QTransform().scale(1, -1))
            tint_hex = theme.get_icon_tint('updates.png')
            pix = _tint_pixmap(pix, QColor(tint_hex))
            return QIcon(pix)

    # Default asset — apply icon tint color
    path = Path(__file__).parent / 'assets' / 'icons' / name
    if path.exists():
        pix = QPixmap(str(path)).scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        tint_hex = theme.get_icon_tint(name)
        pix = _tint_pixmap(pix, QColor(tint_hex))
        return QIcon(pix)
    # QPainter fallback (registered by feature modules for icons with no PNG)
    maker = _PAINTER_ICON_FALLBACKS.get(name)
    if maker:
        return maker(size)
    return QIcon()


def _register_icon_widget(widget, name: str, size: int):
    """Register a widget so its icon refreshes on Apply Theme."""
    _icon_registry.append((widget, name, size))


def _refresh_all_icons():
    """Reload all registered icon widgets from current theme state."""
    alive = []
    for widget, name, size in _icon_registry:
        try:
            _ = widget.objectName()
            icon = _load_icon(name, size)
            widget.setIcon(icon)
            widget.setIconSize(QSize(size, size))
            alive.append((widget, name, size))
        except (RuntimeError, AttributeError):
            pass
    _icon_registry.clear()
    _icon_registry.extend(alive)


def _dims_to_label(w: int, h: int) -> str:
    return _DIMS_TO_LABEL.get((w, h), f'{w}×{h}' if w else 'SOURCE')


def select_post_route(*, audio_on: bool, multiband_enabled: bool,
                      mic_running: bool, watermark: bool, auto_crop: bool,
                      camera: bool) -> tuple[str, bool]:
    """Decide which post-processing route a saved clip takes.

    Returns ``(route, has_async_mux)`` where route is exactly one of
    ``'mic'``, ``'multiband'`` or ``'finalize'``.

    The exclusivity matters: each route ends by setting the ``clip_ready``
    event, and ``clip_ready`` is the single gate the upload manager waits on.
    Two routes running for one clip set it twice, and the upload starts against
    a half-written file — the mic mux's os.replace() racing the watermark
    pass. This used to be three independent `if` blocks that could all fire;
    it is a pure function now so the exclusivity can actually be tested.

    ``has_async_mux`` says whether *any* route will touch the file after this
    returns, which is what the upload manager needs in order to wait.
    'finalize' only rewrites the file when there is something to apply, so a
    plain clip with no watermark/crop/camera is ready immediately.
    """
    multiband_on = audio_on and effective_multiband_audio_enabled(
        multiband_enabled)
    mic_active = audio_on and not multiband_on and mic_running

    if mic_active:
        return 'mic', True
    if multiband_on:
        return 'multiband', True
    # finalize is the fallback route and always runs, but it only rewrites the
    # file when one of these is on.
    return 'finalize', bool(watermark or auto_crop or camera)


def _sanitize_foldername(name: str) -> str:
    """Strip Windows-invalid chars from a window title to make a safe folder name."""
    invalid = r'\/:*?"<>|'
    cleaned = ''.join(c for c in name
                      if c not in invalid and ord(c) >= 32).strip('. ')
    cleaned = cleaned[:32].rstrip('. ')
    # Windows reserved device names can't be folders (CON, NUL, COM1, ...)
    if cleaned.upper() in {'CON', 'PRN', 'AUX', 'NUL',
                           *(f'COM{i}' for i in range(1, 10)),
                           *(f'LPT{i}' for i in range(1, 10))}:
        cleaned = f'{cleaned}_game'
    return cleaned or 'Unknown'

# Brand accent kept as a local alias for inline f-strings sprinkled below.
# Single source of truth lives in style.Colors.
FTHR_TEAL     = Colors.ACCENT
FTHR_TEAL_DIM = Colors.ACCENT_DIM

# Animation timing — kept module-level so popups, the settings page, and the
# clip viewer all converge on the same fade duration. 180 ms is brisk enough
# that the user perceives the panel as "snappy" but slow enough that opening
# / closing doesn't read as a jump-cut.
PANEL_FADE_MS = 180


def _check_linux_input_group() -> bool:
    """Return True if this process has /dev/input access for global hotkeys."""
    if sys.platform == 'win32':
        return True
    import grp
    try:
        input_gid = grp.getgrnam('input').gr_gid
        # Check both supplementary groups and the primary group
        return input_gid in os.getgroups() or input_gid == os.getgid()
    except Exception:
        return False


# Canonical QSS / label fragments come from style.py; aliased so call-sites stay short.
_COMBO_STYLE = combo_qss()
_LABEL_STYLE = label_uppercase(Colors.TEXT, Fonts.SIZE_MICRO, Fonts.TRACK_LABEL)


# ---------------------------------------------------------------------------
# Custom QComboBox — shows icons/dropdown.png as the arrow indicator and
# rotates it 180° while the popup is open, back to 0° when it closes.
# ---------------------------------------------------------------------------

class _DropdownCombo(QComboBox):
    _arrow_pix: 'QPixmap | None' = None  # class-level cache

    @classmethod
    def _get_arrow(cls) -> 'QPixmap | None':
        if cls._arrow_pix is None:
            path = Path(__file__).parent / 'assets' / 'icons' / 'dropdown.png'
            if path.exists():
                cls._arrow_pix = QPixmap(str(path))
        return cls._arrow_pix

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_open = False
        # Force the popup list to use our dark colors via palette, because on
        # Qt6/Linux the stylesheet alone doesn't reliably override the system
        # palette for the floating item view (white-on-white issue).
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base,            QColor(Colors.SURFACE_2))
        pal.setColor(QPalette.ColorRole.Text,            QColor(Colors.TEXT))
        pal.setColor(QPalette.ColorRole.Highlight,       QColor(Colors.SURFACE_3))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(Colors.ACCENT))
        pal.setColor(QPalette.ColorRole.Window,          QColor(Colors.SURFACE_2))
        pal.setColor(QPalette.ColorRole.WindowText,      QColor(Colors.TEXT))
        self.setPalette(pal)
        self.view().setPalette(pal)

    def showPopup(self):
        self._popup_open = True
        self.update()
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        self._popup_open = False
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        pix = self._get_arrow()
        if pix is None or pix.isNull():
            return
        sz = 14
        scaled = pix.scaled(sz, sz,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        x = self.width() - sz - 8
        y = (self.height() - sz) // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._popup_open:
            p.translate(x + sz / 2.0, y + sz / 2.0)
            p.rotate(180)
            p.drawPixmap(QRect(-sz // 2, -sz // 2, sz, sz), scaled)
        else:
            p.drawPixmap(x, y, scaled)
        p.end()


# ---------------------------------------------------------------------------
# Mic level meter — paints a horizontal RMS bar driven by a sounddevice stream
# ---------------------------------------------------------------------------

class _MicLevelMeter(QWidget):
    """Live mic-loudness bar. Updates at ~30Hz from an InputStream callback."""

    _level_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setMinimumWidth(120)
        self._level    = 0.0
        self._peak     = 0.0
        self._stream   = None
        self._gain     = 1.0
        self._shared_recorder = None
        self._level_changed.connect(self._on_level)

        self._peak_decay = QTimer(self)
        self._peak_decay.setInterval(60)
        self._peak_decay.timeout.connect(self._decay_peak)

    def set_gain(self, g: float):
        self._gain = max(0.0, g)

    def start(self, device_index):
        self.stop()
        if not _SD_AVAILABLE:
            return False

        # Prefer sharing MicRecorder's stream via RMS callback — avoids opening
        # a second capture device which can fail on exclusive-mode backends.
        recorder = MicRecorder()
        if recorder.is_running() and recorder._device_index == device_index:
            recorder.add_rms_listener(self._on_rms_from_recorder)
            self._shared_recorder = recorder
            self._peak_decay.start()
            return True

        # MicRecorder not running on this device — open a dedicated preview stream.
        self._shared_recorder = None

        def _cb(indata, frames, time_info, status):
            try:
                arr = indata if indata.ndim == 1 else indata[:, 0]
                rms = float(_np.sqrt(_np.mean(_np.square(arr, dtype=_np.float32))))
                self._level_changed.emit(min(rms * self._gain * 4.0, 1.0))
            except Exception:
                pass

        try:
            self._stream = _sd.InputStream(
                device=device_index,
                channels=1,
                dtype='float32',
                samplerate=44100,
                blocksize=1024,
                callback=_cb,
            )
            self._stream.start()
            self._peak_decay.start()
            return True
        except Exception as e:
            print(f'Mic meter start failed: {e}')
            self._stream = None
            return False

    def _on_rms_from_recorder(self, rms: float):
        """Called from MicRecorder audio thread when sharing its stream."""
        self._level_changed.emit(min(rms * self._gain * 4.0, 1.0))

    def stop(self):
        self._peak_decay.stop()
        if hasattr(self, '_shared_recorder') and self._shared_recorder is not None:
            self._shared_recorder.remove_rms_listener(self._on_rms_from_recorder)
            self._shared_recorder = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._level = 0.0
        self._peak  = 0.0
        self.update()

    def _on_level(self, v: float):
        self._level = v
        if v > self._peak:
            self._peak = v
        self.update()

    def _decay_peak(self):
        self._peak  = max(self._peak  - 0.04, self._level)
        self._level = max(self._level - 0.06, 0.0)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()

        p.fillRect(0, 0, w, h, QColor(Colors.BG))
        p.setPen(QPen(QColor(Colors.HAIRLINE), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        fill_w = max(int((w - 2) * self._level), 0)
        if fill_w > 0:
            p.fillRect(1, 1, fill_w, h - 2, QColor(Colors.ACCENT))

        peak_x = int((w - 2) * self._peak)
        if peak_x > 0:
            p.fillRect(1 + peak_x, 1, 2, h - 2, QColor(Colors.TEXT))


# ---------------------------------------------------------------------------
# RecordingDot — painted circle indicator; pulse driven externally via opacity
# ---------------------------------------------------------------------------

class RecordingDot(QWidget):
    """A small filled circle. Drawn with QPainter so it scales crisply at any
    DPI. Pulse is handled by the parent via QGraphicsOpacityEffect."""

    def __init__(self, diameter: int = 8, height: int = 52, parent=None):
        super().__init__(parent)
        self._diameter = diameter
        self._color = QColor(Colors.ACCENT)
        self.setFixedSize(diameter + 6, height)

    def set_color(self, hex_color: str) -> None:
        new_color = QColor(hex_color)
        if new_color != self._color:
            self._color = new_color
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        cx = self.width() // 2
        cy = self.height() // 2
        r = self._diameter // 2
        p.drawEllipse(cx - r, cy - r, self._diameter, self._diameter)
        p.end()


# ---------------------------------------------------------------------------
# Popup panel base — shared look for all dropdown panels
# ---------------------------------------------------------------------------

class _PopupPanel(QFrame):
    """Base class for top-bar dropdown panels (capture settings, source, hotkeys)."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName('popupPanel')
        self.setMinimumWidth(300)
        self.setStyleSheet(f'''
            QFrame#popupPanel {{
                background-color: {Colors.SURFACE_2};
                border: {Sizes.BORDER_W}px solid {Colors.BORDER_HI};
                border-radius: {Sizes.RADIUS_MD}px;
            }}
            QFrame#popupPanel QLabel {{
                color: {Colors.TEXT};
                background-color: transparent;
            }}
        ''')
        self._show_anim: QPropertyAnimation | None = None

    def show_below(self, button: QWidget):
        pos = button.mapToGlobal(QPoint(0, button.height()))
        self.move(pos)
        self.show()
        self.raise_()
        # setWindowOpacity on Popup windows is not supported on Wayland —
        # the compositor just ignores it and spams a warning every frame.
        # Skip the fade on Wayland; just show instantly. Looks fine.
        app = QApplication.instance()
        if app and app.platformName() != 'wayland':
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b'windowOpacity', self)
            anim.setDuration(PANEL_FADE_MS)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._show_anim = anim
            anim.start()


# ---------------------------------------------------------------------------
# Capture Settings popup
# ---------------------------------------------------------------------------

class CaptureSettingsPopup(_PopupPanel):
    """Dropdown panel: clip length, fps, resolution, quality."""

    clip_length_changed   = Signal(int)
    extended_clip_changed = Signal(int)
    framerate_changed     = Signal(int)
    resolution_changed  = Signal(int, int)
    bitrate_changed     = Signal(int)
    restart_needed      = Signal()
    summary_changed     = Signal(str)   # emitted whenever any value changes

    _CLIP_VALUES  = list(NORMAL_CLIP_VALUES)
    _CLIP_LABELS  = ['5s','10s','15s','30s','45s','1m','1m 30s','2m','3m','4m','5m']
    _EXT_VALUES   = list(EXTENDED_CLIP_VALUES)
    _EXT_LABELS   = ['30s','45s','1m','1m 30s','2m','3m','4m','5m']
    _FPS_VALUES   = list(FPS_VALUES)
    _RES_LABELS   = ['480p','720p','1080p','1440p','Source']
    _RES_KEYS     = ['480p','720p','1080p','1440p','source']
    _QUAL_LABELS  = ['Low','Medium','High']
    _QUAL_KEYS    = ['low','medium','high']

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self.sm = settings_manager
        self._restart_pending = False

        self.cur_clip   = self.sm.get('clip_length',    30)
        self.cur_ext    = self.sm.get('extended_clip_length',  60)
        self.cur_fps    = self.sm.get('framerate',       60)
        self.cur_res    = self.sm.get('resolution',   'source')
        self.cur_qual   = self.sm.get('bitrate_level', 'high')

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        def _row(label_text, combo):
            row = QHBoxLayout()
            row.setSpacing(16)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(_LABEL_STYLE)
            lbl.setFixedWidth(92)
            row.addWidget(lbl)
            row.addWidget(combo, stretch=1)
            return row

        # Clip length
        clip_idx = self._CLIP_VALUES.index(self.cur_clip) \
            if self.cur_clip in self._CLIP_VALUES else 3
        self.clip_combo = self._make_combo(self._CLIP_LABELS, clip_idx,
                                           self._on_clip_changed)
        layout.addLayout(_row('CLIP LENGTH', self.clip_combo))

        # Extended clip length
        ext_idx = self._EXT_VALUES.index(self.cur_ext) \
            if self.cur_ext in self._EXT_VALUES else 2  # default index for '1m'
        self.ext_combo = self._make_combo(self._EXT_LABELS, ext_idx,
                                          self._on_ext_clip_changed)
        layout.addLayout(_row('EXT. CLIP', self.ext_combo))

        # FPS
        fps_idx = self._FPS_VALUES.index(self.cur_fps) \
            if self.cur_fps in self._FPS_VALUES else 1
        self.fps_combo = self._make_combo(
            [str(v) for v in self._FPS_VALUES], fps_idx, self._on_fps_changed)
        layout.addLayout(_row('FRAMERATE', self.fps_combo))

        # Resolution
        res_idx = self._RES_KEYS.index(self.cur_res.lower()) \
            if self.cur_res.lower() in self._RES_KEYS else 4
        self.res_combo = self._make_combo(self._RES_LABELS, res_idx,
                                          self._on_res_changed)
        layout.addLayout(_row('RESOLUTION', self.res_combo))

        # Quality
        qual_idx = self._QUAL_KEYS.index(self.cur_qual) \
            if self.cur_qual in self._QUAL_KEYS else 2
        self.qual_combo = self._make_combo(self._QUAL_LABELS, qual_idx,
                                           self._on_qual_changed)
        layout.addLayout(_row('QUALITY', self.qual_combo))

        # Apply + Restart button (hidden until needed)
        self.restart_btn = QPushButton('APPLY + RESTART')
        self.restart_btn.setStyleSheet(button_primary_qss())
        self.restart_btn.setVisible(False)
        self.restart_btn.clicked.connect(self._on_restart)
        layout.addWidget(self.restart_btn)

    def _make_combo(self, items, idx, callback):
        c = _DropdownCombo()
        c.addItems(items)
        c.setCurrentIndex(idx)
        c.setStyleSheet(_COMBO_STYLE)
        c.currentIndexChanged.connect(callback)
        return c

    def _on_clip_changed(self, idx):
        self.cur_clip = self._CLIP_VALUES[idx]
        self.sm.set('clip_length', self.cur_clip)
        self.sm.save_settings()
        self._mark_restart()
        self.clip_length_changed.emit(self.cur_clip)
        self._update_summary()

    def _on_ext_clip_changed(self, idx):
        self.cur_ext = self._EXT_VALUES[idx]
        self.sm.set('extended_clip_length', self.cur_ext)
        self.sm.save_settings()
        self._mark_restart()
        self.extended_clip_changed.emit(self.cur_ext)
        self._update_summary()

    def reload_from_settings(self):
        self.cur_clip = self.sm.get('clip_length', 30)
        self.cur_ext  = self.sm.get('extended_clip_length', 60)
        self.cur_fps  = self.sm.get('framerate', 60)
        self.cur_res  = self.sm.get('resolution', 'source')
        self.cur_qual = self.sm.get('bitrate_level', 'high')

        for combo, values, val in [
            (self.clip_combo, self._CLIP_VALUES, self.cur_clip),
            (self.ext_combo,  self._EXT_VALUES,  self.cur_ext),
            (self.fps_combo,  self._FPS_VALUES,   self.cur_fps),
        ]:
            idx = values.index(val) if val in values else 0
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        res_idx = self._RES_KEYS.index(self.cur_res.lower()) \
            if self.cur_res.lower() in self._RES_KEYS else 4
        self.res_combo.blockSignals(True)
        self.res_combo.setCurrentIndex(res_idx)
        self.res_combo.blockSignals(False)

        qual_idx = self._QUAL_KEYS.index(self.cur_qual) \
            if self.cur_qual in self._QUAL_KEYS else 2
        self.qual_combo.blockSignals(True)
        self.qual_combo.setCurrentIndex(qual_idx)
        self.qual_combo.blockSignals(False)

        self._update_summary()

    def _on_fps_changed(self, idx):
        self.cur_fps = self._FPS_VALUES[idx]
        self.sm.set('framerate', self.cur_fps)
        self.sm.save_settings()
        self._mark_restart()
        self.framerate_changed.emit(self.cur_fps)
        self._update_summary()

    def _on_res_changed(self, idx):
        self.cur_res = self._RES_KEYS[idx]
        self.sm.set('resolution', self.cur_res)
        self.sm.save_settings()
        self._mark_restart()
        dims = _resolution_to_dims(self.cur_res)
        self.resolution_changed.emit(dims[0], dims[1])
        kbps = BITRATE_PRESETS[self.cur_res][self.cur_qual]
        self.bitrate_changed.emit(kbps)
        self._update_summary()

    def _on_qual_changed(self, idx):
        self.cur_qual = self._QUAL_KEYS[idx]
        self.sm.set('bitrate_level', self.cur_qual)
        self.sm.save_settings()
        self._mark_restart()
        kbps = BITRATE_PRESETS[self.cur_res][self.cur_qual]
        self.bitrate_changed.emit(kbps)
        self._update_summary()

    def _mark_restart(self):
        self._restart_pending = True
        self.restart_btn.setVisible(True)

    def _on_restart(self):
        self._restart_pending = False
        self.restart_btn.setVisible(False)
        self.restart_needed.emit()
        self.hide()

    def _update_summary(self):
        clip_label = self._CLIP_LABELS[self._CLIP_VALUES.index(self.cur_clip)] \
            if self.cur_clip in self._CLIP_VALUES else f'{self.cur_clip}s'
        res_label  = self._RES_LABELS[self._RES_KEYS.index(self.cur_res.lower())] \
            if self.cur_res.lower() in self._RES_KEYS else self.cur_res.upper()
        summary = f'{clip_label}  ·  {self.cur_fps}fps  ·  {res_label}  ·  {self.cur_qual.upper()}'
        self.summary_changed.emit(summary)

    def get_summary(self) -> str:
        clip_label = self._CLIP_LABELS[self._CLIP_VALUES.index(self.cur_clip)] \
            if self.cur_clip in self._CLIP_VALUES else f'{self.cur_clip}s'
        res_label  = self._RES_LABELS[self._RES_KEYS.index(self.cur_res.lower())] \
            if self.cur_res.lower() in self._RES_KEYS else self.cur_res.upper()
        return f'{clip_label}  ·  {self.cur_fps}fps  ·  {res_label}  ·  {self.cur_qual.upper()}'

    def get_bitrate(self) -> int:
        return BITRATE_PRESETS[self.cur_res][self.cur_qual]


# ---------------------------------------------------------------------------
# Source popup
# ---------------------------------------------------------------------------

class SourcePopup(_PopupPanel):
    """Dropdown panel: desktop / window selector."""

    source_changed = Signal(str, int)   # (mode, hwnd)
    restart_needed = Signal()
    summary_changed = Signal(str)

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self.sm = settings_manager
        self.cur_mode    = self.sm.get('capture_mode',    'desktop')
        self.cur_hwnd    = self.sm.get('target_hwnd',     0)
        self.cur_monitor = self.sm.get('capture_monitor', '')
        self._window_list: list = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        lbl = QLabel('CAPTURE SOURCE')
        lbl.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(lbl)

        self.mode_combo = _DropdownCombo()
        self.mode_combo.addItems(['Desktop', 'Window / Game'])
        self.mode_combo.setStyleSheet(_COMBO_STYLE)
        if self.cur_mode == 'window':
            self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.mode_combo)

        # Window list row
        win_row = QHBoxLayout()
        win_row.setSpacing(6)
        self.window_combo = _DropdownCombo()
        self.window_combo.setStyleSheet(_COMBO_STYLE)
        self.window_combo.setMinimumWidth(240)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        win_row.addWidget(self.window_combo)

        self.refresh_btn = QPushButton()
        _ref_ico = _load_icon('refresh.png', 14)
        if not _ref_ico.isNull():
            self.refresh_btn.setIcon(_ref_ico)
            self.refresh_btn.setIconSize(QSize(14, 14))
            _register_icon_widget(self.refresh_btn, 'refresh.png', 14)
        else:
            self.refresh_btn.setText('↺')
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setStyleSheet(f'''
            QPushButton {{
                background-color: {Colors.BG};
                border: {Sizes.BORDER_W}px solid {Colors.TEXT};
                color: {Colors.TEXT};
                font-size: 14px;
            }}
            QPushButton:hover {{
                border-color: {Colors.ACCENT};
                color: {Colors.ACCENT};
            }}
        ''')
        self.refresh_btn.clicked.connect(self._refresh_windows)
        win_row.addWidget(self.refresh_btn)
        layout.addLayout(win_row)

        # Monitor selector (desktop mode only)
        self.monitor_combo = _DropdownCombo()
        self.monitor_combo.setStyleSheet(_COMBO_STYLE)
        if sys.platform == 'win32':
            monitor_choices = enumerate_windows_monitors()
            screens_by_name = {
                screen.name().lower(): screen for screen in QApplication.screens()
            }
            for choice in monitor_choices:
                screen = screens_by_name.get(choice.gdi_name.lower())
                details = ''
                if screen is not None:
                    geometry = screen.availableGeometry()
                    details = (
                        f'  ({geometry.width()}×{geometry.height()} '
                        f'@ {int(screen.refreshRate())}Hz)'
                    )
                primary = ' · Primary' if choice.primary else ''
                self.monitor_combo.addItem(
                    f'{choice.friendly_name}{details}{primary}',
                    userData=choice.device_path,
                )

            saved_mon = normalize_monitor_device_path(self.cur_monitor)
            if saved_mon and self.monitor_combo.findData(saved_mon) < 0:
                # One-time migration from the former QScreen/GDI-name setting.
                legacy = next(
                    (choice for choice in monitor_choices
                     if choice.gdi_name.lower() == self.cur_monitor.lower()),
                    None,
                )
                saved_mon = legacy.device_path if legacy else saved_mon
            if self.monitor_combo.count() and self.monitor_combo.findData(saved_mon) < 0:
                primary_index = next(
                    (index for index, choice in enumerate(monitor_choices)
                     if choice.primary),
                    0,
                )
                saved_mon = self.monitor_combo.itemData(primary_index)
            if saved_mon != self.cur_monitor:
                self.cur_monitor = saved_mon
                self.sm.set('capture_monitor', saved_mon)
                self.sm.save_settings()
            if not monitor_choices:
                self.monitor_combo.addItem('No active Windows monitor found', userData='')
                self.monitor_combo.setEnabled(False)
        else:
            self.monitor_combo.addItem('First Screen (Default)', userData='')
            for screen in QApplication.screens():
                geometry = screen.availableGeometry()
                self.monitor_combo.addItem(
                    f'{screen.name()}  ({geometry.width()}×{geometry.height()} '
                    f'@ {int(screen.refreshRate())}Hz)',
                    userData=screen.name(),
                )
            saved_mon = self.cur_monitor
        idx = self.monitor_combo.findData(saved_mon)
        if idx >= 0:
            self.monitor_combo.setCurrentIndex(idx)
        self.monitor_combo.currentIndexChanged.connect(self._on_monitor_changed)
        layout.addWidget(self.monitor_combo)

        self.restart_btn = QPushButton('APPLY + RESTART')
        self.restart_btn.setStyleSheet(button_primary_qss())
        self.restart_btn.setVisible(False)
        self.restart_btn.clicked.connect(self._on_restart)
        layout.addWidget(self.restart_btn)

        self._update_window_visibility()
        if self.cur_mode == 'window':
            self._refresh_windows()

    def _update_window_visibility(self):
        show = (self.cur_mode == 'window')
        self.window_combo.setVisible(show)
        self.refresh_btn.setVisible(show)
        self.monitor_combo.setVisible(not show)

    def _on_monitor_changed(self, _idx: int):
        self.cur_monitor = self.monitor_combo.currentData()
        self.sm.set('capture_monitor', self.cur_monitor)
        self.sm.save_settings()
        self.restart_btn.setVisible(True)
        self._emit_summary()

    def _on_mode_changed(self, idx):
        self.cur_mode = 'window' if idx == 1 else 'desktop'
        self.sm.set('capture_mode', self.cur_mode)
        if self.cur_mode == 'desktop':
            self.cur_hwnd = 0
            self.sm.set('target_hwnd', 0)
        self._update_window_visibility()
        if self.cur_mode == 'window':
            self._refresh_windows()
        self.sm.save_settings()
        self.restart_btn.setVisible(True)
        self._emit_summary()

    def _refresh_windows(self):
        self._window_list = _enumerate_capturable_windows()
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        select_idx = 0
        for i, w in enumerate(self._window_list):
            label = ('★ ' if w['is_game'] else '') + w['display_name']
            if w.get('icon'):
                self.window_combo.addItem(w['icon'], label)
            else:
                self.window_combo.addItem(label)
            if w['hwnd'] == self.cur_hwnd:
                select_idx = i
        if self._window_list:
            self.window_combo.setCurrentIndex(select_idx)
            self.cur_hwnd = self._window_list[select_idx]['hwnd']
            self.sm.set('target_hwnd', self.cur_hwnd)
        self.window_combo.blockSignals(False)

    def _on_window_selected(self, idx):
        if 0 <= idx < len(self._window_list):
            self.cur_hwnd = self._window_list[idx]['hwnd']
            self.sm.set('target_hwnd', self.cur_hwnd)
            self.sm.save_settings()
            self.restart_btn.setVisible(True)
            self._emit_summary()

    def _on_restart(self):
        self.restart_btn.setVisible(False)
        self.restart_needed.emit()
        self.hide()

    def _emit_summary(self):
        if self.cur_mode == 'desktop':
            label = 'DESKTOP'
        elif self._window_list:
            idx = self.window_combo.currentIndex()
            if 0 <= idx < len(self._window_list):
                label = self._window_list[idx]['display_name'].upper()
            else:
                label = 'WINDOW'
        else:
            label = 'WINDOW'
        self.summary_changed.emit(label)

    def get_summary(self) -> str:
        if self.cur_mode == 'desktop':
            return 'DESKTOP'
        idx = self.window_combo.currentIndex()
        if 0 <= idx < len(self._window_list):
            return self._window_list[idx]['display_name'].upper()
        return 'WINDOW'


# ---------------------------------------------------------------------------
# Key-capture button — click it, press any key, done.
# ---------------------------------------------------------------------------

class _KeyCaptureButton(QPushButton):
    """Button that records a keypress as a hotkey when clicked."""

    key_captured = Signal(str)   # emits normalized key string e.g. 'F9', 'ctrl+shift+s'

    _BTN_NORMAL   = None  # set lazily from Colors
    _BTN_LISTEN   = None

    def __init__(self, initial_key: str = '', parent=None):
        super().__init__(parent)
        self._listening   = False
        self._current_key = initial_key
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(120)
        self.setFixedHeight(32)
        self._apply_style(listening=False)
        self._update_text()
        self.clicked.connect(self._start_listen)

    def set_key(self, key: str):
        self._current_key = key
        self._update_text()

    def _update_text(self):
        self.setText('…  Press a key' if self._listening else (self._current_key or 'Click to set'))

    def _apply_style(self, listening: bool):
        if listening:
            self.setStyleSheet(
                'QPushButton { background: #3a0020; border: 1px solid #ff2d78;'
                ' border-radius: 4px; color: #ff2d78;'
                ' font-size: 12px; font-weight: bold; padding: 0 12px; }'
            )
        else:
            self.setStyleSheet(
                f'QPushButton {{ background: {Colors.SURFACE_2}; border: 1px solid {Colors.BORDER_HI};'
                f' border-radius: 4px; color: {Colors.TEXT};'
                f' font-size: 12px; font-weight: bold; padding: 0 12px; }}'
                f'QPushButton:hover {{ border-color: {Colors.ACCENT}; color: {Colors.ACCENT}; }}'
            )

    def _start_listen(self):
        self._listening = True
        self._apply_style(listening=True)
        self._update_text()
        self.grabKeyboard()

    def _stop_listen(self, cancelled: bool = False):
        self._listening = False
        self.releaseKeyboard()
        self._apply_style(listening=False)
        self._update_text()

    def keyPressEvent(self, event):
        if not self._listening:
            super().keyPressEvent(event)
            return

        key = event.key()
        # Ignore bare modifier presses
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_unknown, 0):
            return

        # Escape cancels without changing anything
        if key == Qt.Key.Key_Escape:
            self._stop_listen(cancelled=True)
            return

        # Build a clean key string
        mods = event.modifiers()
        # Strip side-distinction flags Qt adds internally
        mods &= ~Qt.KeyboardModifier.KeypadModifier
        mods &= ~Qt.KeyboardModifier.GroupSwitchModifier

        seq = QKeySequence(int(mods) | key)
        raw = seq.toString(QKeySequence.SequenceFormat.PortableText)
        normalized = self._normalize(raw)

        self._current_key = normalized
        self._stop_listen()
        self.key_captured.emit(normalized)

    @staticmethod
    def _normalize(ks: str) -> str:
        """'Ctrl+Shift+S' → 'ctrl+shift+s', 'F9' → 'F9'."""
        parts = ks.split('+')
        out = []
        for p in parts:
            lower = p.lower()
            if lower in ('ctrl', 'shift', 'alt', 'meta'):
                out.append(lower)
            else:
                out.append(p)   # keep 'F9', 'S', etc. as-is from QKeySequence
        return '+'.join(out)

    def focusOutEvent(self, event):
        if self._listening:
            self._stop_listen(cancelled=True)
        super().focusOutEvent(event)


# ---------------------------------------------------------------------------
# Hotkey popup (compact version of existing HotkeyPanel)
# ---------------------------------------------------------------------------

class HotkeyPopup(_PopupPanel):

    def __init__(self, hotkey_manager: HotkeyManager, parent=None):
        super().__init__(parent)
        self.hotkey_manager = hotkey_manager
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        lbl = QLabel('HOTKEYS')
        lbl.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(lbl)

        hint = QLabel('Click a button, then press your desired key.')
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f'color: {Colors.TEXT_MUTED}; font-size: {Fonts.SIZE_MICRO}px; padding-bottom: 2px;'
        )
        layout.addWidget(hint)

        actions = [
            ('CAPTURE CLIP',  'save_clip'),
            ('EXTENDED CLIP', 'save_extended_clip'),
            ('SCREENSHOT',    'save_screenshot'),
        ]
        self._capture_btns = {}
        for label_text, action_key in actions:
            row = QHBoxLayout()
            row.setSpacing(12)
            lbl2 = QLabel(label_text)
            lbl2.setStyleSheet(
                label_uppercase(Colors.TEXT, Fonts.SIZE_LABEL, Fonts.TRACK_LABEL))
            lbl2.setFixedWidth(140)
            row.addWidget(lbl2)

            btn = _KeyCaptureButton(
                initial_key=self.hotkey_manager.get_hotkey(action_key)
            )
            btn.key_captured.connect(
                lambda key, a=action_key: self._on_key_captured(a, key))
            self._capture_btns[action_key] = btn
            row.addWidget(btn)
            row.addStretch()
            layout.addLayout(row)

        # -- nc dependency warning (Linux only) --
        import shutil as _shutil
        if sys.platform != 'win32' and not _shutil.which('nc'):
            nc_warn = QLabel(
                '⚠  netcat (nc) not found — hotkeys will not work.\n'
                'Install: sudo pacman -S openbsd-netcat'
            )
            nc_warn.setWordWrap(True)
            nc_warn.setStyleSheet(
                f'color: #ff9944; font-size: {Fonts.SIZE_MICRO}px;'
                f' background: #2a1800; border-radius: 4px; padding: 6px 8px;'
            )
            layout.addWidget(nc_warn)

        # -- Per-compositor hotkey setup instructions --
        from core.compositor import detect_compositor
        _comp = detect_compositor()

        if _comp == 'hyprland':
            _instr = '✓ Hotkeys are applied to your Hyprland config automatically.'
        else:
            _instr = self.hotkey_manager.setup_instructions(_comp)

        instr_lbl = QLabel(_instr)
        instr_lbl.setWordWrap(True)
        instr_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        instr_lbl.setStyleSheet(
            f'color: {Colors.TEXT_DIM}; font-size: {Fonts.SIZE_MICRO}px;'
            f' font-family: monospace; padding-top: 6px;'
        )
        layout.addWidget(instr_lbl)

    def _on_key_captured(self, action: str, key: str):
        self.hotkey_manager.set_hotkey(action, key)
        # Update all other buttons so they show the new key immediately
        if action in self._capture_btns:
            self._capture_btns[action].set_key(key)


# ---------------------------------------------------------------------------
# TopBarButton — a styled button for the top bar dropdowns
# ---------------------------------------------------------------------------

class TopBarButton(QPushButton):
    """Compact rounded-rect button for the dark status row that shows a summary + dropdown arrow."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setStyleSheet(f'''
            QPushButton {{
                background-color: {Colors.SURFACE_2};
                border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.TEXT};
                font-size: {Fonts.SIZE_LABEL}px;
                font-family: {Fonts.DISPLAY};
                letter-spacing: {Fonts.TRACK_LABEL}px;
                font-weight: bold;
                padding: 0px 14px;
                min-width: 60px;
            }}
            QPushButton:hover {{
                color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
            QPushButton:pressed {{
                color: {Colors.BG};
                background-color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
        ''')
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._base_text = text
        super().setText(f'{text}  ▾')

    def setText(self, text: str):
        self._base_text = text
        super().setText(f'{text}  ▾')


# ---------------------------------------------------------------------------
# Stats strip
# ---------------------------------------------------------------------------

class _StatsStrip(QFrame):
    """28px bar below the top bar: encoder type | frame count | buffer fill bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setObjectName('statsStrip')
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(Sizes.SPACE_6, 0, Sizes.SPACE_6, 0)
        layout.setSpacing(0)

        self._encoder_lbl = QLabel('—')
        self._encoder_lbl.setObjectName('statsEncoder')
        layout.addWidget(self._encoder_lbl)

        layout.addSpacing(10)
        sep = QLabel('·')
        sep.setObjectName('statsSep')
        layout.addWidget(sep)
        layout.addSpacing(10)

        self._frames_lbl = QLabel('0 frames')
        self._frames_lbl.setObjectName('statsFrames')
        layout.addWidget(self._frames_lbl)

        layout.addStretch()

        buf_text = QLabel('BUFFER')
        buf_text.setObjectName('statsBufText')
        layout.addWidget(buf_text)

        layout.addSpacing(12)

        # Thin progress bar for buffer fill — track is a quiet hairline,
        # fill is the brand accent so capture pressure reads at a glance.
        bar_wrap = QFrame()
        bar_wrap.setFixedSize(120, 3)
        bar_wrap.setStyleSheet(
            f'QFrame {{ background-color: {Colors.BORDER}; border: none; }}')
        self._buf_fill = QFrame(bar_wrap)
        self._buf_fill.setGeometry(0, 0, 0, 3)
        self._buf_fill.setStyleSheet(
            f'QFrame {{ background-color: {Colors.ACCENT}; border: none; }}')
        layout.addWidget(bar_wrap)

        layout.addSpacing(12)

        self._buf_time_lbl = QLabel('—')
        self._buf_time_lbl.setObjectName('statsBufTime')
        layout.addWidget(self._buf_time_lbl)

        self.setStyleSheet(f'''
            QFrame#statsStrip {{
                background-color: {Colors.BG};
                border-bottom: 1px solid {Colors.TEXT};
            }}
            QLabel#statsEncoder {{
                color: {Colors.ACCENT};
                font-size: {Fonts.SIZE_MICRO}px;
                font-weight: bold;
                font-family: {Fonts.DISPLAY};
                letter-spacing: {Fonts.TRACK_LABEL}px;
                background: transparent;
            }}
            QLabel#statsSep {{
                color: {Colors.TEXT_DIM};
                font-size: {Fonts.SIZE_MICRO}px;
                background: transparent;
            }}
            QLabel#statsFrames {{
                color: {Colors.TEXT};
                font-size: {Fonts.SIZE_MICRO}px;
                font-family: {Fonts.BODY};
                background: transparent;
            }}
            QLabel#statsBufText {{
                color: {Colors.TEXT_DIM};
                font-size: {Fonts.SIZE_MICRO}px;
                font-weight: bold;
                font-family: {Fonts.DISPLAY};
                letter-spacing: {Fonts.TRACK_LABEL}px;
                background: transparent;
            }}
            QLabel#statsBufTime {{
                color: {Colors.TEXT};
                font-size: {Fonts.SIZE_MICRO}px;
                font-family: {Fonts.BODY};
                background: transparent;
                min-width: 60px;
            }}
        ''')

    def update_stats(self, frames: int, fill_ratio: float, encoder: str,
                     connected: bool, buffer_sec: int):
        if not connected:
            self._encoder_lbl.setText('—')
            self._frames_lbl.setText('disconnected')
            self._buf_fill.setFixedWidth(0)
            self._buf_time_lbl.setText('—')
            return

        self._encoder_lbl.setText(encoder)
        self._frames_lbl.setText(f'{frames:,} frames')

        ratio   = min(max(fill_ratio, 0.0), 1.0)
        fill_px = int(ratio * 120)
        self._buf_fill.setFixedWidth(fill_px)

        filled_sec = int(ratio * buffer_sec)
        self._buf_time_lbl.setText(f'{filled_sec}s / {buffer_sec}s')



# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    clip_saved = Signal(str)
    # Cross-thread UI dispatcher. QTimer.singleShot(0, fn) from a plain
    # threading.Thread does NOT fire (no event loop in that thread) — emitting
    # this signal instead is guaranteed to queue fn onto the main thread.
    _ui_call = Signal(object)

    def __init__(self, *, background_start: bool = False):
        super().__init__()
        self._ui_call.connect(lambda fn: fn())
        self._background_start = background_start
        self._ui_ready = False
        self._background_services_started = False
        self._shutdown_requested = False
        self._shutdown_complete = False
        self._shutdown_timer_started = None
        self._tray_icon = None
        self._screenshot_inflight = False
        self._screenshot_save_worker = None
        self._lifecycle_log = get_logger('lifecycle')

        # -- Frameless window --
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.settings_manager = SettingsManager()

        from core.clip_metadata_manager import ClipMetadataManager
        self.clip_metadata_manager = ClipMetadataManager()

        from core.upload_manager import UploadManager
        self._clip_readiness = get_clip_readiness_registry()
        self.upload_manager = UploadManager(
            self.settings_manager, self._clip_readiness)
        # Give the settings widget a back-reference so its Save button can call
        # refresh_settings() without needing a direct signal connection.
        self.settings_manager._upload_manager_ref = self.upload_manager

        # Load custom theme colors before any UI is built so QSS uses them
        self._theme_mgr = ThemeManager()
        _theme_colors = self._theme_mgr.get_all_colors()
        for _tk, _val in _theme_colors.items():
            if hasattr(Colors, _tk):
                setattr(Colors, _tk, _val)

        # settings.json is user-editable. Invalid values are rejected and
        # replaced with the documented default, with a diagnostic, rather than
        # silently clamped to a value the user did not select.
        def _validated_setting(key, default, validator):
            try:
                value = int(self.settings_manager.get(key, default))
                return validator(value)
            except (TypeError, ValueError):
                bad = self.settings_manager.get(key, default)
                print(f'[Settings] Rejected invalid {key}={bad!r}; using {default}')
                self.settings_manager.set(key, default)
                return default

        self.clip_duration = _validated_setting(
            'clip_length', 30, validate_normal_clip_length)
        self.extended_clip_duration = _validated_setting(
            'extended_clip_length', 60, validate_extended_clip_length)
        self.capture_fps = _validated_setting(
            'framerate', 60, validate_fps)

        # These implementations remain in source for later work, but cannot
        # be revived by an old settings file during the alpha.
        if self.settings_manager.get('multiband_audio_enabled', False):
            print('[Settings] Multiband audio is disabled for the alpha')
            self.settings_manager.set('multiband_audio_enabled', False)
        if (not focus_pause_supported(sys.platform)
                and self.settings_manager.get('anticheat_detection_enabled', False)):
            print('[Settings] Focus pause is unavailable on Windows alpha')
            self.settings_manager.set('anticheat_detection_enabled', False)
        self.settings_manager.save_settings()

        saved_res  = self.settings_manager.get('resolution',   'source')
        saved_qual = self.settings_manager.get('bitrate_level', 'medium')
        # Derive the correct kbps from the saved resolution+quality preset so
        # the engine starts with the right bitrate even before any UI interaction
        # fires the bitrate_changed signal.
        self.capture_bitrate = BITRATE_PRESETS.get(
            saved_res, BITRATE_PRESETS['source']).get(saved_qual, 25000)

        self.capture_width, self.capture_height = _resolution_to_dims(saved_res)
        self.buffer_seconds = compute_buffer_seconds(
            self.clip_duration, self.extended_clip_duration)
        self._capture_config = CaptureConfigTracker()

        self.engine_process = None
        self._engine_startup_output = None
        self.bridge         = CaptureBridge()
        self._capture_health = CaptureHealthMonitor()
        self._capture_health_snapshot = None
        self._capture_health_log = get_logger('capture.health')

        if sys.platform == 'win32':
            if getattr(sys, 'frozen', False):
                possible_paths = [Path(sys._MEIPASS) / 'engine' / 'FTHRClips.exe']
            else:
                project_root   = Path(__file__).parent.parent / 'FTHRcapture'
                possible_paths = [
                    project_root / 'x64' / 'Release' / 'FTHRClips.exe',
                    project_root / 'x64' / 'Debug'   / 'FTHRClips.exe',
                    project_root / 'Release'          / 'FTHRClips.exe',
                    project_root / 'Debug'            / 'FTHRClips.exe',
                ]
        else:
            _env_engine = os.environ.get('FTHR_ENGINE', '')
            if getattr(sys, 'frozen', False):
                # In frozen PyInstaller build the engine is in _internal/ (_MEIPASS)
                possible_paths = [Path(sys._MEIPASS) / 'FTHRclips']
            else:
                linux_root = Path(__file__).parent.parent / 'FTHRcapture_linux'
                possible_paths = [
                    *([ Path(_env_engine) ] if _env_engine else []),
                    linux_root / 'build' / 'FTHRclips',
                ]
        self.engine_path = None
        for p in possible_paths:
            if p.exists():
                self.engine_path = p
                print(f"Engine found: {p.parent.name}/{p.name}")
                break
        if not self.engine_path:
            print("Engine not found.")

        # Pre-create the special folders so users can find them right away
        for _folder in ('Desktop', 'Exported', 'Shared', 'Screenshots'):
            (Path.home() / 'FTHR_Clips' / _folder).mkdir(parents=True, exist_ok=True)

        # A hard kill can leave the engine's same-directory transaction file.
        # Only old, FTHR-named partials are removed; fresh files may belong to a
        # still-running save and unrelated *.mp4.partial files are user-owned.
        partial_recovery = cleanup_stale_partial_clips(Path.home() / 'FTHR_Clips')
        if partial_recovery.removed:
            print(f'[Startup] Removed {len(partial_recovery.removed)} stale partial clip(s)')
        for partial_path, error in partial_recovery.failures:
            print(f'[Startup] Could not remove stale partial {partial_path.name}: {error}')

        self.hotkey_manager  = HotkeyManager()

        self._pending_game_window: dict | None = None
        self._active_game_hwnd:    int  | None = None
        self._game_dismiss_timer = QTimer(self)
        self._game_dismiss_timer.setSingleShot(True)
        self._game_dismiss_timer.timeout.connect(self._on_game_prompt_timeout)

        self._game_detector = GameDetector()
        self._game_detector.game_appeared.connect(self._on_game_appeared)
        self._game_detector.game_closed.connect(self._on_game_closed)

        if self.settings_manager.get('game_detection_enabled', False):
            self._game_detector.start()

        self._focus_monitor = FocusMonitor()
        self._focus_monitor.focus_lost.connect(self._on_focus_lost)
        self._focus_monitor.focus_regained.connect(self._on_focus_regained)

        if (focus_pause_supported(sys.platform)
                and self.settings_manager.get('anticheat_detection_enabled', False)
                and self.settings_manager.get('capture_mode', 'desktop') == 'window'):
            target = self.settings_manager.get('target_window_name', '')
            self._focus_monitor.set_target(target)
            self._focus_monitor.start()

        from core.camera_recorder import CameraRecorder
        if (CameraRecorder.is_available()
                and self.settings_manager.get('camera_enabled', False)):
            device_idx = self.settings_manager.get('camera_device_index', 0)
            # Async: opening a camera blocks for seconds on Windows/MSMF —
            # doing it inline froze the whole UI during startup.
            def _on_cam_result(ok):
                if not ok:
                    self._ui_call.emit(lambda: self.push_error(
                        'CAMERA UNAVAILABLE',
                        'Device not found or in use by another application.',
                        level='warning',
                        actions=[('OPEN CAMERA SETTINGS', self._toggle_settings_page)],
                    ))
            CameraRecorder().start_async(device_idx, on_result=_on_cam_result)

        self.is_capturing    = True
        self.capture_card    = CaptureCardClient(self.settings_manager)
        self._encoder_type   = 'DETECTING'
        self._startup_sound_played = False

        # Window drag state
        self._drag_pos: QPoint | None = None

        self.setWindowTitle(f'{APP_NAME} {APP_VERSION}')
        self.setMinimumSize(1100, 720)

        if not self._background_start:
            self.ensure_main_ui()
        self._setup_hotkeys()
        self._start_background_services()
        self._create_system_tray()
        if not _check_linux_input_group():
            QTimer.singleShot(1500, self._warn_input_group)

        # Start the always-on microphone recorder so saved clips can include
        # the user's voice. The C++ engine doesn't capture mic — we record
        # in Python and ffmpeg-mux it into each clip after save.
        if self.settings_manager.get('audio_capture_enabled', True):
            self._start_mic_recorder()

        # Launch the capture engine once the event loop is running. Deferring
        # past __init__ keeps the window responsive while the engine boots and
        # the bridge polls for its shared memory. Once running, the status timer
        # below handles transparent reconnection if the engine ever dies.
        QTimer.singleShot(0, self.start_engine)

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(500)

        # The save response channel. One state machine, one poller.
        #
        # The status timer's 500 ms is too coarse for save feedback — the ack
        # deadline is 1 s — so a save gets its own short timer that runs only
        # while something is outstanding. Each tick is a few shared-memory
        # reads: no sleeping, no I/O, no waiting on threads or subprocesses.
        # That is what keeps the event loop free (AUDIT-011).
        self._save_state = SaveStateMachine()
        self._save_poll_timer = QTimer(self)
        self._save_poll_timer.setInterval(50)
        self._save_poll_timer.timeout.connect(self._on_save_poll_tick)
        self._published_final_clips: set[str] = set()

    def ensure_main_ui(self) -> None:
        """Build the heavy library/settings UI only when a window is needed.

        The capture engine, hotkeys, upload queue and tray deliberately do not
        depend on this tree.  A Windows-login ``--background`` launch can
        therefore fill replay history without constructing thumbnails, editor
        controls or settings widgets first.
        """
        if self._ui_ready:
            return
        self._setup_ui()
        self._load_saved_theme()
        self._apply_styles()
        self._ui_ready = True
        is_connected = getattr(self.bridge, 'is_connected', lambda: False)
        if self.bridge and is_connected():
            QTimer.singleShot(
                0, self._settings_page_widget._populate_mic_devices)

    def _start_background_services(self) -> None:
        """Start services that must survive hiding or deferred UI creation."""
        if self._background_services_started:
            return
        self.upload_manager.upload_finished.connect(self._on_upload_finished)
        self.upload_manager.upload_error.connect(self._on_upload_error)
        self.upload_manager.start()
        self._background_services_started = True

    def _create_system_tray(self) -> bool:
        """Create one native Windows tray icon for the process lifetime."""
        if self._tray_icon is not None:
            return True
        if (sys.platform != 'win32'
                or not QSystemTrayIcon.isSystemTrayAvailable()):
            print('[Lifecycle] System tray unavailable; normal close remains enabled')
            return False

        icon_path = Path(__file__).parent / 'assets' / 'fthr_logo.ico'
        icon = QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon()
        if icon.isNull():
            print('[Lifecycle] System tray unavailable: FTHR icon could not load')
            return False

        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip('FTHR Clips — replay capture is running')
        menu = QMenu()
        open_action = QAction('Open FTHR', menu)
        open_action.triggered.connect(self.restore_main_window)
        save_action = QAction('Save Clip', menu)
        save_action.triggered.connect(self._on_hotkey_save_clip)
        library_action = QAction('Open Clips', menu)
        library_action.triggered.connect(self._open_clip_library)
        exit_action = QAction('Exit FTHR', menu)
        exit_action.triggered.connect(self.request_full_exit)
        menu.addAction(open_action)
        menu.addAction(save_action)
        menu.addAction(library_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray_icon = tray
        print('[Lifecycle] TrayReady')
        return True

    def _on_tray_activated(self, reason) -> None:
        if reason in {
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.restore_main_window()

    def restore_main_window(self) -> None:
        """Restore the existing UI without restarting the capture generation."""
        self.ensure_main_ui()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized)
        if self.isMaximized() or self._background_start:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self._background_start = False
        print('[Lifecycle] WindowRestored')

    def _open_clip_library(self) -> None:
        self.restore_main_window()
        self.main_stack.setCurrentIndex(0)

    # =======================================================================
    # UI layout
    # =======================================================================

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --
        # UNIFIED TOP BAR
        # logo  |  capture / source / hotkeys / gear  |  close-settings  |  — ✕
        # The mode-specific clusters (main vs settings) swap visibility when
        # _toggle_settings_page is called.
        # --
        top_bar = QFrame()
        top_bar.setObjectName('topBar')
        top_bar.setFixedHeight(Sizes.UNIFIED_BAR_H)
        tb = QHBoxLayout(top_bar)
        tb.setContentsMargins(16, 0, 0, 0)
        tb.setSpacing(10)

        # -- Logo — check theme override first, then fall back to default asset.
        #    Default asset is black-on-white so we invert RGB for the dark bar.
        #    Custom logos are used as-is (user provides the final look). --
        self._logo_label = QLabel()
        self._load_logo()
        self._logo_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        tb.addWidget(self._logo_label)

        tb.addStretch(1)

        # Hidden objects kept so internal status/dot methods don't crash
        self.rec_dot = RecordingDot(diameter=8, height=Sizes.UNIFIED_BAR_H)
        self.status_label = QLabel('CONNECTING')
        self.status_label.setStyleSheet(status_idle_qss())
        self.status_label.setObjectName('statusLabel')

        # -- Main-mode cluster: capture-settings dropdowns + gear --
        self.main_mode_cluster = QFrame()
        self.main_mode_cluster.setObjectName('topClusterMain')
        mc = QHBoxLayout(self.main_mode_cluster)
        mc.setContentsMargins(0, 0, 0, 0)
        mc.setSpacing(10)

        self.cap_settings_popup = CaptureSettingsPopup(self.settings_manager, self)
        self.cap_settings_popup.clip_length_changed.connect(self._on_clip_length_changed)
        self.cap_settings_popup.extended_clip_changed.connect(self._on_extended_clip_length_changed)
        self.cap_settings_popup.framerate_changed.connect(self._on_framerate_changed)
        self.cap_settings_popup.resolution_changed.connect(self._on_resolution_changed)
        self.cap_settings_popup.bitrate_changed.connect(self._on_bitrate_changed)
        self.cap_settings_popup.restart_needed.connect(self._restart_capture_engine)
        self.cap_settings_popup.summary_changed.connect(self._on_cap_summary_changed)

        self.cap_btn = TopBarButton(self.cap_settings_popup.get_summary())
        self.cap_btn.clicked.connect(self._toggle_cap_settings)
        mc.addWidget(self.cap_btn)

        self.source_popup = SourcePopup(self.settings_manager, self)
        self.source_popup.restart_needed.connect(self._restart_capture_engine)
        self.source_btn = TopBarButton('SOURCE')
        self.source_btn.clicked.connect(self._toggle_source)
        mc.addWidget(self.source_btn)

        self.hotkey_popup = HotkeyPopup(self.hotkey_manager, self)
        self.hotkey_btn = TopBarButton('HOTKEYS')
        self.hotkey_btn.clicked.connect(self._toggle_hotkeys)
        mc.addWidget(self.hotkey_btn)

        # NVENC / HW status label (hidden by default)
        self.hw_label = QLabel()
        self.hw_label.setObjectName('hwLabel')
        self.hw_label.setVisible(False)
        mc.addWidget(self.hw_label)

        self.settings_gear_btn = QPushButton()
        _gear_ico = _load_icon('settings(general).png', 18)
        self.settings_gear_btn.setIcon(_gear_ico if not _gear_ico.isNull() else _make_settings_icon(18, Colors.TEXT))
        self.settings_gear_btn.setIconSize(QSize(18, 18))
        _register_icon_widget(self.settings_gear_btn, 'settings(general).png', 18)
        self.settings_gear_btn.setObjectName('settingsGearBtn')
        self.settings_gear_btn.setFixedSize(36, 32)
        self.settings_gear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.settings_gear_btn.clicked.connect(self._toggle_settings_page)
        mc.addWidget(self.settings_gear_btn)

        tb.addWidget(self.main_mode_cluster)

        # -- Settings-mode cluster: just the close-settings button --
        self.settings_mode_cluster = QFrame()
        self.settings_mode_cluster.setObjectName('topClusterSettings')
        sc = QHBoxLayout(self.settings_mode_cluster)
        sc.setContentsMargins(0, 0, 0, 0)
        sc.setSpacing(10)

        self.close_settings_btn = QPushButton()
        self.close_settings_btn.setObjectName('homeBtn')
        self.close_settings_btn.setFixedSize(36, 32)
        _home_ico = _load_icon('home.png', 18)
        if not _home_ico.isNull():
            self.close_settings_btn.setIcon(_home_ico)
            self.close_settings_btn.setIconSize(QSize(18, 18))
            _register_icon_widget(self.close_settings_btn, 'home.png', 18)
        else:
            self.close_settings_btn.setText('⌂')
        self.close_settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_settings_btn.setToolTip('Back to clips')
        self.close_settings_btn.clicked.connect(self._toggle_settings_page)
        sc.addWidget(self.close_settings_btn)

        tb.addWidget(self.settings_mode_cluster)
        self.settings_mode_cluster.setVisible(False)

        # -- Window controls (always visible) --
        self.min_btn = QPushButton()
        self.min_btn.setObjectName('winBtn')
        self.min_btn.setFixedSize(46, Sizes.UNIFIED_BAR_H)
        _min_ico = _load_icon('minimize.png', 14)
        if not _min_ico.isNull():
            self.min_btn.setIcon(_min_ico)
            self.min_btn.setIconSize(QSize(14, 14))
            _register_icon_widget(self.min_btn, 'minimize.png', 14)
        else:
            self.min_btn.setText('—')
        self.min_btn.clicked.connect(self.showMinimized)
        tb.addWidget(self.min_btn)

        self._is_maximized = False
        self.max_btn = QPushButton()
        self.max_btn.setObjectName('winBtn')
        self.max_btn.setFixedSize(46, Sizes.UNIFIED_BAR_H)
        _max_ico = _load_icon('maximize.png', 14)
        if not _max_ico.isNull():
            self.max_btn.setIcon(_max_ico)
            self.max_btn.setIconSize(QSize(14, 14))
            _register_icon_widget(self.max_btn, 'maximize.png', 14)
        else:
            self.max_btn.setText('□')
        self.max_btn.clicked.connect(self._toggle_maximize)
        tb.addWidget(self.max_btn)

        self.close_btn = QPushButton()
        self.close_btn.setObjectName('closeBtn')
        self.close_btn.setFixedSize(46, Sizes.UNIFIED_BAR_H)
        _close_ico = _load_icon('close.png', 14)
        if not _close_ico.isNull():
            self.close_btn.setIcon(_close_ico)
            self.close_btn.setIconSize(QSize(14, 14))
            _register_icon_widget(self.close_btn, 'close.png', 14)
        else:
            self.close_btn.setText('✕')
        self.close_btn.clicked.connect(self.close)
        tb.addWidget(self.close_btn)

        root.addWidget(top_bar)

        # Drag/double-click on the bar background (the buttons absorb their own clicks)
        top_bar.mousePressEvent       = self._bar_mouse_press
        top_bar.mouseMoveEvent        = self._bar_mouse_move
        top_bar.mouseReleaseEvent     = self._bar_mouse_release
        top_bar.mouseDoubleClickEvent = self._bar_double_click
        self._top_bar = top_bar

        # -- Main content stack --
        self.main_stack = QStackedWidget()
        self.main_stack.setObjectName('mainStack')

        # Page 0: clip grid
        body_page = QWidget()
        body_page.setObjectName('body')
        body_layout = QVBoxLayout(body_page)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clip_grid = ClipGrid(settings_manager=self.settings_manager)
        self.clip_grid.set_readiness_checker(self._clip_readiness.can_access)
        self.clip_grid.clip_opened.connect(self._on_clip_opened)
        scroll.setWidget(self.clip_grid)
        body_layout.addWidget(scroll, stretch=1)

        self.main_stack.addWidget(body_page)

        # Page 1: full-screen settings
        self._settings_page_widget = _SettingsPage(self.settings_manager)
        self._settings_page_widget.close_requested.connect(
            self._toggle_settings_page)
        self._settings_page_widget.imported_folders_changed.connect(
            self.clip_grid.force_refresh)
        self._settings_page_widget.notification_monitor_changed.connect(
            self.capture_card.restart)
        self._settings_page_widget.encoder_config_changed.connect(
            self._on_encoder_config_changed)
        self._settings_page_widget.audio_capture_changed.connect(
            self._on_audio_capture_changed)
        self.main_stack.addWidget(self._settings_page_widget)

        # The manager itself starts before the optional UI exists, so background
        # replay and uploads do not depend on this screen.  These bindings are
        # the view-specific half and are created only with the library grid.
        self.clip_grid.clip_upload_requested.connect(self.upload_manager.enqueue_upload)
        self.clip_grid.set_upload_checker(self.upload_manager.is_uploaded)
        self.clip_grid.set_upload_enabled_checker(
            lambda: self.settings_manager.get('upload_enabled', False))

        root.addWidget(self.main_stack, stretch=1)

        # Error bar (shown at bottom of app for warnings/errors)
        self.error_bar = ErrorBar()
        root.addWidget(self.error_bar)

        # Start recording dot animation
        self._setup_rec_dot()

    # -- Drag support --

    def _bar_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _bar_mouse_move(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _bar_mouse_release(self, event):
        self._drag_pos = None

    def _bar_double_click(self, event):
        self._toggle_maximize()

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # -- Native Windows resize + Aero snap --

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_native_style_applied', False):
            self._native_style_applied = True
            # Defer SetWindowPos(SWP_FRAMECHANGED) to after the event loop starts.
            # Calling it synchronously inside showEvent sends WM_NCCALCSIZE back
            # into nativeEvent while Qt is mid-show, causing a crash.
            QTimer.singleShot(0, self._apply_native_style)
            # Pre-realize the settings page so the first time the user clicks
            # the gear button it doesn't pay for layout, font resolution, and
            # stylesheet compilation. The page is already constructed; we just
            # need Qt to do its first-show work for it.
            QTimer.singleShot(0, self._prerealize_settings_page)

    def _prerealize_settings_page(self):
        """Force Qt to do the deferred first-show work for the settings page.

        Adding a widget to a QStackedWidget doesn't trigger a full layout +
        style pass — that happens lazily on the first show. We pay that cost
        upfront here so the visible click-to-show transition is instant.

        ensurePolished() runs the QSS pass; adjustSize() forces a layout. We
        call them on the page itself and on every descendant widget so nested
        pages (the Audio sub-tab is the slowest) aren't deferred.
        """
        page = self._settings_page_widget
        try:
            page.ensurePolished()
            page.adjustSize()
            for child in page.findChildren(QWidget):
                child.ensurePolished()
        except Exception as e:
            print(f'[Prerealize] settings page warm-up failed: {e}')

    def _apply_native_style(self):
        """Apply WS_THICKFRAME so native resize/Aero-snap work on the frameless window."""
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_STYLE      = -16
            WS_THICKFRAME  = 0x00040000
            WS_MAXIMIZEBOX = 0x00010000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_MAXIMIZEBOX)
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            ctypes.windll.user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)
        except Exception:
            pass

    def nativeEvent(self, eventType, message):
        # Returning (False, 0) lets Qt's WndProc continue with default
        # processing after the read-only message inspection below.
        if eventType == b'windows_generic_MSG':
            try:
                import ctypes, ctypes.wintypes
                ptr = int(message)
                if ptr:
                    # Safe peek: read only the UINT message field.
                    # MSG layout on 64-bit Windows:
                    #   HWND   hwnd    (8 bytes)
                    #   UINT   message (4 bytes)  ← uint32 index [2]
                    #   ...
                    msg_type = ctypes.cast(
                        ptr, ctypes.POINTER(ctypes.c_uint32))[2]
                    if msg_type == 0x0084:  # WM_NCHITTEST — safe to read full MSG
                        msg = ctypes.wintypes.MSG.from_address(ptr)
                        lp  = msg.lParam
                        cx  = ctypes.c_short(lp & 0xFFFF).value
                        cy  = ctypes.c_short((lp >> 16) & 0xFFFF).value
                        g   = self.frameGeometry()
                        bw  = 6  # resize border width in pixels

                        left   = cx <  g.left()   + bw
                        right  = cx >= g.right()  - bw
                        top    = cy <  g.top()    + bw
                        bottom = cy >= g.bottom() - bw

                        if not self.isMaximized():
                            if top    and left:  return True, 13  # HTTOPLEFT
                            if top    and right: return True, 14  # HTTOPRIGHT
                            if bottom and left:  return True, 16  # HTBOTTOMLEFT
                            if bottom and right: return True, 17  # HTBOTTOMRIGHT
                            if top:              return True, 12  # HTTOP
                            if bottom:           return True, 15  # HTBOTTOM
                            if left:             return True, 10  # HTLEFT
                            if right:            return True, 11  # HTRIGHT

                        # Caption area: enables Aero snap & native drag
                        if cy < g.top() + Sizes.UNIFIED_BAR_H:
                            local = self.mapFromGlobal(QPoint(cx, cy))
                            w = self.childAt(local)
                            if w is None or not isinstance(w, (QPushButton, QComboBox)):
                                return True, 2  # HTCAPTION
            except Exception:
                pass
        return False, 0  # not handled — Qt WndProc continues normally

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj.objectName() == 'dragArea':
            if event.type() == QEvent.Type.MouseButtonPress:
                self._bar_mouse_press(event)
            elif event.type() == QEvent.Type.MouseMove:
                self._bar_mouse_move(event)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._bar_mouse_release(event)
            elif event.type() == QEvent.Type.MouseButtonDblClick:
                self._bar_double_click(event)
        return super().eventFilter(obj, event)

    # -- Helpers --

    def _vsep(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(
            f'QFrame {{ color: {Colors.BORDER_HI};'
            f' max-width: 1px; margin: 12px 4px; }}'
        )
        return sep

    # -- Popup toggles --

    def _toggle_cap_settings(self):
        if self.cap_settings_popup.isVisible():
            self.cap_settings_popup.hide()
        else:
            self.source_popup.hide()
            self.hotkey_popup.hide()
            self.cap_settings_popup.show_below(self.cap_btn)

    def _toggle_source(self):
        if self.source_popup.isVisible():
            self.source_popup.hide()
        else:
            self.cap_settings_popup.hide()
            self.hotkey_popup.hide()
            self.source_popup.show_below(self.source_btn)

    def _toggle_hotkeys(self):
        if self.hotkey_popup.isVisible():
            self.hotkey_popup.hide()
        else:
            self.cap_settings_popup.hide()
            self.source_popup.hide()
            self.hotkey_popup.show_below(self.hotkey_btn)

    def _on_cap_summary_changed(self, text: str):
        self.cap_btn.setText(text)

    def _toggle_settings_page(self):
        if self.main_stack.currentIndex() == 1:
            # Fade out settings, then switch back to clip grid
            effect = self._settings_page_widget.graphicsEffect()
            if effect is None:
                effect = QGraphicsOpacityEffect(self._settings_page_widget)
                self._settings_page_widget.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b'opacity', self)
            anim.setDuration(PANEL_FADE_MS)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
            anim.finished.connect(lambda: self.main_stack.setCurrentIndex(0))
            self._settings_fade_out = anim
            try:
                self._settings_page_widget._mappings_timer.stop()
            except AttributeError:
                pass
            anim.start()
            self.main_mode_cluster.setVisible(True)
            self.settings_mode_cluster.setVisible(False)
        else:
            self.main_stack.setCurrentIndex(1)
            if effective_multiband_audio_enabled(
                    self.settings_manager.get('multiband_audio_enabled', False)):
                try:
                    self._settings_page_widget._mappings_timer.start()
                except AttributeError:
                    pass
            self.main_mode_cluster.setVisible(False)
            self.settings_mode_cluster.setVisible(True)
            # Close any open dropdowns from main mode
            self.cap_settings_popup.hide()
            self.source_popup.hide()
            self.hotkey_popup.hide()
            # Fade in settings page
            effect = QGraphicsOpacityEffect(self._settings_page_widget)
            self._settings_page_widget.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            anim = QPropertyAnimation(effect, b'opacity', self)
            anim.setDuration(PANEL_FADE_MS)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._settings_fade_in = anim
            anim.start()

    # -- Recording dot --

    def _setup_rec_dot(self):
        self._dot_effect = QGraphicsOpacityEffect(self.rec_dot)
        self.rec_dot.setGraphicsEffect(self._dot_effect)
        self._dot_anim = QPropertyAnimation(self._dot_effect, b'opacity', self)
        self._dot_anim.setDuration(900)
        self._dot_anim.setStartValue(1.0)
        self._dot_anim.setEndValue(0.15)
        self._dot_anim.setEasingCurve(QEasingCurve.Type.SineCurve)
        self._dot_anim.setLoopCount(-1)
        self._dot_anim.start()

    def _set_rec_dot_state(self, state: str):
        if not hasattr(self, 'rec_dot'):
            return
        # Top bar is white, so the muted-state color must be a dark tone — using
        # white here was the source of the "no dot visible while disconnected" bug.
        colors = {
            'capturing':    Colors.ACCENT,
            'stopped':      Colors.TEXT_MUTED,
            'disconnected': Colors.TEXT_MUTED,
        }
        self.rec_dot.set_color(colors.get(state, Colors.TEXT_MUTED))
        if state == 'capturing':
            if self._dot_anim.state() != QAbstractAnimation.State.Running:
                self._dot_anim.start()
        else:
            self._dot_anim.stop()
            self._dot_effect.setOpacity(1.0)

    # =======================================================================
    # Hotkeys
    # =======================================================================

    def _warn_input_group(self):
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle('Hotkeys Disabled')
        msg.setText(
            'Global hotkeys are disabled because your user is not in the <b>input</b> group.<br><br>'
            'Run this command, then log out and back in:<br>'
            '<code>sudo usermod -aG input $USER</code>'
        )
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.exec()

    def _warn_no_engine(self):
        if getattr(sys, 'frozen', False):
            detail = ('FTHRcapture binary is missing from the installation.'
                      ' Re-download the latest release.')
        else:
            detail = ('FTHRcapture binary not found. Build it:'
                      ' cd FTHRcapture_linux && bash build_linux.sh')
        self.push_error(
            'ENGINE NOT FOUND',
            detail,
            level='error',
            actions=[('OPEN SETTINGS', self._toggle_settings_page)],
        )

    def _setup_hotkeys(self):
        self.hotkey_manager.save_clip_triggered.connect(self._on_hotkey_save_clip)
        self.hotkey_manager.save_extended_clip_triggered.connect(
            self._on_hotkey_save_extended_clip)
        self.hotkey_manager.save_screenshot_triggered.connect(
            self._on_hotkey_save_screenshot)
        self.hotkey_manager.confirm_game_detection_triggered.connect(
            self._on_confirm_game_detection)
        self.hotkey_manager.dismiss_game_detection_triggered.connect(
            self._on_dismiss_game_detection)
        self.hotkey_manager.error_occurred.connect(
            lambda title, detail, level: self.push_error(
                title, detail, level,
                actions=[('OPEN HOTKEYS', self._toggle_hotkeys)],
            )
        )
        self.hotkey_manager.register_all()
        print("Hotkeys registered.")

        # Warn if key features are limited on the current compositor
        from core.compositor import detect_compositor as _dc, has_xtools as _hx
        if _dc() not in ('hyprland', 'x11') and not _hx():
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(2000, self._show_compositor_warning)

    def _on_hotkey_save_clip(self):
        config = self._capture_config.active
        self._save_clip(config.normal_clip_seconds if config else self.clip_duration)

    def _on_hotkey_save_extended_clip(self):
        config = self._capture_config.active
        self._save_clip(
            config.extended_clip_seconds
            if config else self.extended_clip_duration)

    def _on_hotkey_save_screenshot(self):
        if self._screenshot_inflight:
            print('[Screenshot] Ignored duplicate request while a screenshot is saving.')
            return

        config = self._capture_config.active
        selected_monitor = (
            config.monitor if config is not None
            else self.settings_manager.get('capture_monitor', ''))
        try:
            paths = reserve_screenshot_paths(
                Path.home() / 'FTHR_Clips' / 'Screenshots')
        except ScreenshotSaveError as error:
            self._show_screenshot_error(error.code, error.detail)
            return

        if sys.platform != 'win32':
            # Preserve the established Wayland capture backend.  Its explicit
            # -o output is the same selected monitor identity, and it writes
            # only to the non-library staging path before the editor publishes.
            grim = linux_tools.path('grim')
            if grim:
                result = subprocess.run(
                    build_grim_command(grim, str(paths.staged), selected_monitor),
                    capture_output=True,
                    **_NO_WINDOW,
                )
                if result.returncode == 0 and paths.staged.is_file():
                    self._screenshot_inflight = True
                    self._open_screenshot_editor(paths)
                    return
                paths.staged.unlink(missing_ok=True)
                detail = result.stderr.decode(errors='replace').strip()
                print(
                    '[Screenshot] grim capture failed; trying the selected Qt '
                    f'screen instead: {detail or "no diagnostic"}')

        # Resolve the configured monitor afresh for every screenshot.  Windows
        # receives the same stable DISPLAYCONFIG device path that starts replay;
        # no QScreen/DXGI enumeration index is persisted or reused.
        screen = select_qt_screen(
            selected_monitor,
            QApplication.screens(),
            platform=sys.platform,
            windows_monitors=(
                enumerate_windows_monitors() if sys.platform == 'win32' else ()),
            primary=QApplication.primaryScreen(),
        )
        if screen is None:
            paths.staged.unlink(missing_ok=True)
            self._show_screenshot_error(
                'MONITOR_NOT_FOUND',
                'The configured capture monitor is unavailable. FTHR did not '
                'fall back to another display.',
            )
            return

        try:
            pixmap = screen.grabWindow(0)
        except Exception as error:
            paths.staged.unlink(missing_ok=True)
            self._show_screenshot_error(
                'CAPTURE_UNAVAILABLE',
                f'Could not capture the configured monitor: {error}',
            )
            return
        if pixmap.isNull():
            paths.staged.unlink(missing_ok=True)
            self._show_screenshot_error(
                'CAPTURE_UNAVAILABLE',
                'The configured monitor returned an empty screenshot.',
            )
            return

        image = pixmap.toImage()
        if image.isNull():
            paths.staged.unlink(missing_ok=True)
            self._show_screenshot_error(
                'CAPTURE_UNAVAILABLE',
                'The configured monitor could not provide an image.',
            )
            return

        self._screenshot_inflight = True
        worker = ScreenshotPngSaveWorker(image, paths.staged, self)
        self._screenshot_save_worker = worker
        worker.succeeded.connect(
            lambda _staged, target=paths: self._open_screenshot_editor(target))
        worker.failed.connect(
            lambda code, detail, target=paths: self._on_screenshot_save_failed(
                target, code, detail))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _open_screenshot_editor(self, paths) -> None:
        """Offer crop/full save only after the complete staged PNG exists."""

        self._screenshot_save_worker = None
        from ui.screenshot_editor import ScreenshotEditor
        from PySide6.QtCore import QDialog

        if QPixmap(str(paths.staged)).isNull():
            paths.staged.unlink(missing_ok=True)
            self._screenshot_inflight = False
            self._show_screenshot_error(
                'IMAGE_ENCODE_FAILED',
                'The screenshot backend returned an unreadable PNG.',
            )
            return

        # Background/tray mode opens only this standalone dialog; it never
        # builds or restores the deferred library/settings widget tree.
        parent = self if self._ui_ready else None
        editor = ScreenshotEditor(str(paths.staged), str(paths.final), parent)
        try:
            accepted = editor.exec() == QDialog.DialogCode.Accepted
            if accepted and paths.final.is_file():
                self.capture_card.show_screenshot()
            elif accepted:
                self._show_screenshot_error(
                    'WRITE_FAILED',
                    'The screenshot editor closed without publishing a PNG.',
                )
            else:
                paths.staged.unlink(missing_ok=True)
        finally:
            self._screenshot_inflight = False

    def _on_screenshot_save_failed(self, paths, code: str, detail: str) -> None:
        paths.staged.unlink(missing_ok=True)
        self._screenshot_save_worker = None
        self._screenshot_inflight = False
        self._show_screenshot_error(code, detail)

    def _show_screenshot_error(self, code: str, detail: str) -> None:
        """Keep screenshot failures actionable when the main window is hidden."""

        print(f'[Screenshot] {code}: {detail}')
        self.push_error('SCREENSHOT FAILED', f'{code}: {detail}', level='error')
        QMessageBox.warning(
            self if self.isVisible() else None,
            'Screenshot Failed',
            f'{code}\n\n{detail}',
        )

    def _show_compositor_warning(self):
        from PySide6.QtWidgets import QMessageBox
        from core.compositor import detect_compositor
        comp = detect_compositor()
        comp_name = {
            'kwin':            'KDE Plasma',
            'gnome':           'GNOME',
            'wayland-unknown': 'your Wayland compositor',
        }.get(comp, comp)
        msg = QMessageBox(self)
        msg.setWindowTitle('Limited Feature Support')
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f'<b>FTHR Clips is running on {comp_name}.</b><br><br>'
            f'Game detection and focus monitoring require <b>xdotool</b>.<br><br>'
            f'Install it with your package manager:<br>'
            f'<code>sudo pacman -S xdotool</code>  (Arch)<br>'
            f'<code>sudo apt install xdotool</code>  (Debian/Ubuntu)<br>'
            f'<code>sudo dnf install xdotool</code>  (Fedora)<br><br>'
            f'Hotkeys work via the Unix socket — see Settings → Hotkeys for setup.'
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _on_game_appeared(self, window: dict):
        self._pending_game_window = window
        game_name = window.get('display_name', 'Game')
        hotkey = self.hotkey_manager.hotkeys.get('confirm_game_detection', 'F8')
        dismiss = self.hotkey_manager.hotkeys.get('dismiss_game_detection', 'F7')
        self.capture_card.show_prompt(
            f'{game_name} detected — [{hotkey}] Record  [{dismiss}] Dismiss')
        self._game_dismiss_timer.stop()   # cancel any existing prompt timer first
        self._game_dismiss_timer.start(15000)

    def _on_game_closed(self, hwnd: int):
        if hwnd != self._active_game_hwnd:
            return
        self._active_game_hwnd = None
        self.settings_manager.set('capture_mode', 'desktop')
        self.settings_manager.set('target_hwnd', 0)
        self.settings_manager.set('target_window_name', '')
        self.settings_manager.save_settings()
        # Stop the focus monitor — no game to track anymore
        self._focus_monitor.stop()
        if focus_pause_supported(sys.platform) and self.bridge.is_connected():
            self.bridge.resume_recording()
        self._restart_capture_engine()
        self.capture_card.show_prompt('Game closed — switched back to Desktop')

    def _on_confirm_game_detection(self):
        if self._pending_game_window is None:
            return
        hwnd = self._pending_game_window['hwnd']
        window_name = self._pending_game_window.get('display_name', '')
        self._active_game_hwnd = hwnd
        self.settings_manager.set('capture_mode', 'window')
        self.settings_manager.set('target_hwnd', hwnd)
        # Save the window title so FocusMonitor can identify it for anticheat.
        self.settings_manager.set('target_window_name', window_name)
        self.settings_manager.save_settings()
        # Update the focus monitor's target immediately if anticheat is enabled.
        if (focus_pause_supported(sys.platform)
                and self.settings_manager.get('anticheat_detection_enabled', False)
                and window_name):
            self._focus_monitor.set_target(window_name)
            self._focus_monitor.start()
        self._pending_game_window = None
        self._game_dismiss_timer.stop()
        self._restart_capture_engine()

    def _on_dismiss_game_detection(self):
        self._pending_game_window = None
        self._game_dismiss_timer.stop()

    def _on_game_prompt_timeout(self):
        self._pending_game_window = None

    def _on_focus_lost(self):
        if focus_pause_supported(sys.platform) and self.bridge.is_connected():
            self.bridge.pause_recording()
            self._set_status('PAUSED — GAME UNFOCUSED', status_warning_qss())

    def _on_focus_regained(self):
        if focus_pause_supported(sys.platform) and self.bridge.is_connected():
            self.bridge.resume_recording()
            # Resume is a request, not proof that fresh frames have returned.
            self._set_status('CAPTURE STARTING', status_idle_qss())

    # =======================================================================
    # Settings handlers
    # =======================================================================

    def _on_clip_length_changed(self, duration: int):
        self.clip_duration  = duration
        self.buffer_seconds = compute_buffer_seconds(
            duration, self.extended_clip_duration)

    def _on_extended_clip_length_changed(self, duration: int):
        self.extended_clip_duration = duration
        self.buffer_seconds = compute_buffer_seconds(
            self.clip_duration, duration)

    def _on_framerate_changed(self, fps: int):        self.capture_fps = fps
    def _on_resolution_changed(self, w: int, h: int): self.capture_width, self.capture_height = w, h
    def _on_bitrate_changed(self, kbps: int):         self.capture_bitrate = kbps

    def _on_audio_capture_changed(self, _enabled: bool):
        self._restart_capture_engine()

    def _sync_requested_capture_settings(self) -> bool:
        try:
            clip = validate_normal_clip_length(
                int(self.settings_manager.get('clip_length', 30)))
            extended = validate_extended_clip_length(
                int(self.settings_manager.get('extended_clip_length', 60)))
            fps = validate_fps(int(self.settings_manager.get('framerate', 60)))
        except (TypeError, ValueError) as exc:
            self.push_error(
                'INVALID CAPTURE PRESET',
                f'The preset was not applied: {exc}',
                level='warning',
            )
            return False
        resolution = self.settings_manager.get('resolution', 'source')
        quality = self.settings_manager.get('bitrate_level', 'medium')
        if resolution not in BITRATE_PRESETS or quality not in {'low', 'medium', 'high'}:
            self.push_error(
                'INVALID CAPTURE PRESET',
                'The preset contains an unsupported resolution or quality.',
                level='warning',
            )
            return False
        self.clip_duration = clip
        self.extended_clip_duration = extended
        self.capture_fps = fps
        self.capture_width, self.capture_height = _resolution_to_dims(resolution)
        self.capture_bitrate = BITRATE_PRESETS[resolution][quality]
        self.buffer_seconds = compute_buffer_seconds(clip, extended)
        return True

    def _requested_capture_config(self) -> CaptureConfig:
        return CaptureConfig(
            fps=self.capture_fps,
            buffer_seconds=compute_buffer_seconds(
                self.clip_duration, self.extended_clip_duration),
            width=self.capture_width,
            height=self.capture_height,
            bitrate_kbps=self.capture_bitrate,
            codec=self.settings_manager.get('codec_pref', 'auto'),
            preset=int(self.settings_manager.get('encoder_preset', 4)),
            monitor=self.settings_manager.get('capture_monitor', ''),
            scaling=self.settings_manager.get('scaling_mode', 'stretch'),
            audio_enabled=bool(
                self.settings_manager.get('audio_capture_enabled', True)),
            microphone_endpoint_id=str(
                self.settings_manager.get('mic_device_id') or ''),
            normal_clip_seconds=self.clip_duration,
            extended_clip_seconds=self.extended_clip_duration,
        )

    def _apply_active_audio_state(self, config: CaptureConfig) -> None:
        # Windows now owns the microphone natively inside the capture engine.
        # The legacy Python recorder remains only for the Linux fallback path.
        if sys.platform == 'win32':
            MicRecorder().stop()
            return
        recorder = MicRecorder()
        if config.audio_enabled:
            if not recorder.is_running():
                self._start_mic_recorder()
        else:
            recorder.stop()

    # =======================================================================
    # Engine lifecycle
    # =======================================================================

    def connect_to_engine(self) -> bool:
        max_retries = 10
        for attempt in range(1, max_retries + 1):
            if self.bridge.initialize():
                print(f"Connected to capture engine (attempt {attempt}).")
                self._set_status('CAPTURE STARTING', status_idle_qss())
                self._set_rec_dot_state('disconnected')
                QTimer.singleShot(2000, self._check_hardware_encoding_status)
                return True
            if attempt < max_retries:
                print(f"Connection attempt {attempt}/{max_retries} failed, retrying...")
                self._set_status(f'CONNECTING {attempt}/{max_retries}', status_idle_qss())
                self.bridge.shutdown()
                self.bridge = CaptureBridge()
                time.sleep(0.5)

        print("Bridge connection failed after all retries.")
        self._set_status('DISCONNECTED', status_warning_qss())
        return False

    def start_engine(self) -> bool:
        launch_config = self._requested_capture_config()
        self._capture_config.request(launch_config)
        self._capture_config.begin_apply()
        if not self.engine_path or not self.engine_path.exists():
            print("Engine executable not found.")
            self._set_status('NO ENGINE', status_warning_qss())
            QTimer.singleShot(500, self._warn_no_engine)
            self._capture_config.fail('engine executable not found')
            self._restart_pending = False
            return False

        # Preserve the legacy raw-capacity budget as a diagnostic input. Public
        # alpha startup refuses hardware failure, but the engine reports how
        # short the old raw history would have been instead of claiming 30/60s.
        actual_w = launch_config.width if launch_config.width else 1920
        actual_h = launch_config.height if launch_config.height else 1080
        bytes_per_frame = actual_w * actual_h * 4
        frames_needed   = launch_config.buffer_seconds * launch_config.fps
        mb_needed       = max(64, (frames_needed * bytes_per_frame + (1024*1024-1)) // (1024*1024))
        max_buffer_mb   = min(int(mb_needed) + 64, 2048)   # hard 2 GB ceiling

        capture_mode    = self.settings_manager.get('capture_mode',    'desktop')
        target_hwnd     = self.settings_manager.get('target_hwnd',     0)
        capture_monitor = launch_config.monitor
        # target_hwnd comes from user-editable settings.json — never trust it.
        try:
            target_hwnd = int(target_hwnd)
        except (TypeError, ValueError):
            target_hwnd = 0
        mode_arg        = '1' if (capture_mode == 'window' and target_hwnd) else '0'
        hwnd_arg        = str(target_hwnd)

        # 0 = stretch (default), 1 = fit (letterbox/pillarbox)
        scaling_mode = launch_config.scaling
        scale_arg    = '1' if scaling_mode == 'fit' else '0'

        print(f"Starting engine  |  FPS={launch_config.fps}  "
              f"Buffer={launch_config.buffer_seconds}s  "
              f"Bitrate={launch_config.bitrate_kbps}kbps  Pool={max_buffer_mb}MB  "
              f"Scale={scaling_mode}"
              + (f"  Monitor={capture_monitor}" if capture_monitor else ""))
        codec_pref_int = {
            'auto': 0, 'h264': 1, 'hevc': 2, 'av1': 3
        }.get(launch_config.codec, 0)
        encoder_preset = launch_config.preset
        multiband_arg = '0'
        audio_arg = '1' if launch_config.audio_enabled else '0'
        microphone_id_arg = launch_config.microphone_endpoint_id
        try:
            microphone_gain = int(self.settings_manager.get('mic_volume', 100))
        except (TypeError, ValueError):
            microphone_gain = 100
        microphone_gain_arg = str(max(0, min(200, microphone_gain)))
        try:
            # A process/backend restart starts a new replay generation. Keep the
            # one-per-incident recovery budget, but never carry stale buffer age.
            self._capture_health.reset(preserve_recovery_budget=True)
            popen_options = dict(_NO_WINDOW)
            self._close_engine_startup_output()
            self._engine_startup_output = tempfile.TemporaryFile(
                mode='w+', encoding='utf-8', errors='replace')
            popen_options.update(
                stdout=self._engine_startup_output,
                stderr=subprocess.STDOUT,
            )
            self.engine_process = subprocess.Popen(
                [str(self.engine_path),
                 str(launch_config.fps), str(launch_config.buffer_seconds),
                 str(launch_config.width), str(launch_config.height),
                 str(launch_config.bitrate_kbps), str(max_buffer_mb),
                 mode_arg, hwnd_arg, scale_arg, capture_monitor,
                 str(codec_pref_int), str(encoder_preset),
                 multiband_arg, audio_arg,
                 microphone_id_arg, microphone_gain_arg],
                **popen_options
            )
            # Poll for connection in a background thread so the UI stays responsive.
            # Up to 3s total (20 × 150ms). On success, fire UI updates back on
            # the main thread via the _ui_call signal (queued cross-thread).
            #
            # Generation guard: a restart while an old poll is still running
            # must not let the stale thread touch the new bridge — its failure
            # path would kill the freshly started engine.
            self._engine_gen = getattr(self, '_engine_gen', 0) + 1
            _my_gen = self._engine_gen

            def _poll_connect():
                for _ in range(20):
                    time.sleep(0.15)
                    if self._engine_gen != _my_gen:
                        return   # superseded by a newer start/restart
                    if self.bridge.initialize():
                        startup_output = self._read_engine_startup_output()
                        startup_warnings = extract_startup_warnings(startup_output)
                        if startup_output.strip():
                            print(startup_output.rstrip())
                        self._close_engine_startup_output()
                        def _on_connected(
                                startup_warnings=startup_warnings):
                            # Connection proves IPC only. Promote requested to
                            # active after capture-health observes fresh frames.
                            self._pending_launch_config = launch_config
                            self._pending_launch_generation = _my_gen
                            self._set_status('CAPTURE STARTING', status_idle_qss())
                            self._set_rec_dot_state('disconnected')
                            # Native endpoint discovery is intentionally
                            # deferred until the normal engine is already
                            # running. Settings-page construction must not
                            # launch a second capture executable merely to
                            # enumerate microphones.
                            if self._ui_ready:
                                QTimer.singleShot(
                                    0, self._settings_page_widget._populate_mic_devices)
                            QTimer.singleShot(2000, self._check_hardware_encoding_status)
                            if not self._startup_sound_played:
                                self._startup_sound_played = True
                                from ui.capture_card import _play_sound, _SND_STARTUP
                                vol = self.settings_manager.get('sound_volume_startup', 100)
                                _play_sound(_SND_STARTUP, vol)
                            for warning in startup_warnings:
                                self.push_error(
                                    warning.title,
                                    warning.detail,
                                    level='warning',
                                    actions=[('OPEN AUDIO SETTINGS',
                                              self._toggle_settings_page)],
                                )
                        self._ui_call.emit(_on_connected)
                        print("Connected to capture engine.")
                        return
                    if (self.engine_process is not None
                            and self.engine_process.poll() is not None):
                        break
                # initialize() never succeeded — kill the orphaned process
                if self._engine_gen != _my_gen:
                    return   # a newer start owns the engine now — don't kill it
                failure = extract_startup_failure(
                    self._read_engine_startup_output())
                print(f"Engine did not respond — {failure.code}: "
                      f"{failure.detail}")
                def _on_failed():
                    self.stop_engine()
                    self._capture_config.fail(failure.detail)
                    self._restart_pending = False
                    self._set_status('DISCONNECTED', status_warning_qss())
                    self.push_error(
                        failure.title,
                        failure.detail,
                        level='error',
                        actions=[('RESTART ENGINE', self._restart_capture_engine)],
                    )
                self._ui_call.emit(_on_failed)

            threading.Thread(target=_poll_connect, daemon=True,
                             name='fthr-engine-connect').start()
            return True
        except Exception as e:
            print(f"Engine start error: {e}")
            self.stop_engine()
            self._set_status('ERROR', status_warning_qss())
            self.push_error(
                'ENGINE NOT RESPONDING',
                f'Engine failed to start: {e}',
                level='error',
                actions=[('RESTART ENGINE', self._restart_capture_engine)],
            )
            self._capture_config.fail(str(e))
            self._restart_pending = False
            return False

    def _read_engine_startup_output(self) -> str:
        stream = self._engine_startup_output
        if stream is None:
            return ''
        try:
            stream.flush()
            stream.seek(0)
            return stream.read()
        except (OSError, ValueError):
            return ''

    def _close_engine_startup_output(self):
        stream = self._engine_startup_output
        self._engine_startup_output = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def stop_engine(self):
        process = self.engine_process
        graceful_requested = False
        request_shutdown = getattr(self.bridge, 'request_engine_shutdown', None)
        if sys.platform == 'win32' and callable(request_shutdown):
            graceful_requested = request_shutdown()
        if process:
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                print('[Lifecycle] Engine graceful shutdown timed out; escalating')
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=0.5)  # reap — no zombie on Linux
                    except subprocess.TimeoutExpired:
                        print('[Lifecycle] Engine could not be reaped before exit')
            print('[Lifecycle] EngineStopped '
                  f'graceful={graceful_requested} code={process.returncode}')
            self.engine_process = None
        if self.bridge:
            self.bridge.shutdown()
        self._close_engine_startup_output()

    def _restart_capture_engine(self):
        if hasattr(self, '_save_state') and self._save_state.is_busy():
            self._set_status('APPLYING AFTER CURRENT SAVE', status_idle_qss())
            if not getattr(self, '_restart_deferred_for_save', False):
                self._restart_deferred_for_save = True
                def _retry_after_save():
                    self._restart_deferred_for_save = False
                    self._restart_capture_engine()
                QTimer.singleShot(500, _retry_after_save)
            return
        # Guard against button spam: each unguarded click would spawn another
        # engine process fighting over the same shared memory.
        if getattr(self, '_restart_pending', False):
            print('[Engine] Restart already in progress — ignored')
            return
        self._restart_pending = True
        self._set_status('RESTARTING', status_idle_qss())
        self.stop_engine()
        QTimer.singleShot(1000, self._finish_restart)

    def _on_encoder_config_changed(self):
        self._set_status('APPLYING / RESTARTING CAPTURE', status_idle_qss())
        self._restart_capture_engine()

    def _write_audio_categories_json(self):
        """Write ~/.fthr/audio_categories.json for the C++ engine to read at startup."""
        cats = self.settings_manager.get('audio_categories', [])
        path = Path.home() / '.fthr' / 'audio_categories.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for cat in cats:
            sink_name = 'fthr_' + ''.join(
                c if c.isalnum() else '_' for c in cat['name'].lower())
            obj = {
                'name':     cat['name'],
                'sink':     sink_name,
                'patterns': cat.get('patterns', []),
            }
            # Use compact separators (no spaces after : or ,) so the C++ parser
            # can find keys with strstr('\"name\":\"') — it does not handle spaces.
            lines.append(json.dumps(obj, ensure_ascii=True, separators=(',', ':')))
        tmp = path.with_suffix('.json.tmp')
        try:
            tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            os.replace(str(tmp), str(path))
        except OSError as e:
            print(f'[Config] Failed to write audio_categories.json: {e}')
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _finish_restart(self):
        self.bridge = CaptureBridge()
        if not self.start_engine():
            self._restart_pending = False

    # =======================================================================
    # Microphone recorder
    # =======================================================================

    def _start_mic_recorder(self):
        if sys.platform == 'win32':
            # Native WASAPI capture starts with the engine so microphone and
            # video share one generation-local QPC timeline. Do not create a
            # second PortAudio ring that could later post-mix stale audio.
            return
        if not MicRecorder.is_available():
            print('[Mic] sounddevice not installed — mic-in-clips disabled')
            self.push_error(
                'MIC NOT FOUND',
                'No recording device detected. Check audio settings or permissions.',
                level='warning',
                actions=[('OPEN AUDIO SETTINGS', self._toggle_settings_page)],
            )
            return
        device_index = self._resolved_mic_device_index()
        gain = float(self.settings_manager.get('mic_volume', 100)) / 100.0
        if MicRecorder().start(device_index, gain=gain):
            print(f'[Mic] Recorder started (device={device_index}, gain={gain:.2f})')
        else:
            print('[Mic] Recorder failed to start — clips will have no mic audio')
            self.push_error(
                'MIC NOT FOUND',
                'Microphone failed to start. Clips will have no mic audio.',
                level='warning',
                actions=[('OPEN AUDIO SETTINGS', self._toggle_settings_page)],
            )

    def _resolved_mic_device_index(self):
        """Translate the saved mic_device_name into a sounddevice index, or None."""
        if not MicRecorder.is_available():
            return None
        name = self.settings_manager.get('mic_device_name')
        if not name:
            return None
        try:
            import sounddevice as _sd
            for i, dev in enumerate(_sd.query_devices()):
                if dev.get('max_input_channels', 0) > 0 and dev.get('name') == name:
                    return i
        except Exception:
            pass
        return None

    # =======================================================================
    # Save clip
    # =======================================================================

    def _save_clip(self, duration_seconds: int = 30):
        is_connected = getattr(self.bridge, 'is_connected', lambda: False)
        if not self.bridge or not is_connected():
            self.push_error(
                'NOTHING TO CLIP',
                'Buffer is empty — let the engine run for at least 5 seconds first.',
                level='warning',
            )
            return

        admission = evaluate_save_admission(
            self._capture_health_snapshot, duration_seconds)
        if not admission.allowed:
            self.capture_card.show_error()
            self.push_error(
                'CLIP NOT SAVED',
                admission.reason,
                level='warning',
                actions=[('RESTART CAPTURE', self._restart_capture_engine)],
            )
            self._capture_health_log.warning(
                'Save rejected by capture health: state=%s reason=%s',
                (self._capture_health_snapshot.state.value
                 if self._capture_health_snapshot else 'unknown'),
                admission.reason,
            )
            return
        if admission.reason:
            self.push_error('REPLAY BUFFER WARMING', admission.reason, level='warning')
        duration_seconds = admission.duration_seconds

        # Two admission gates, in order of cheapness:
        #
        # 1. The original 1-second debounce. Two saves in the same second
        #    produce the same timestamped filename → the engine writes the same
        #    file twice and two mux workers fight over it.
        # 2. Single-flight. The engine reports through one response slot with no
        #    correlation id, so a second concurrent save is untrackable: its
        #    CLIP_SAVED would be indistinguishable from the first one's. Refuse
        #    it rather than guess.
        #
        # Both refusals give the same visible feedback the debounce always gave
        # — a silently dropped hotkey reads as "the extended-clip key sometimes
        # doesn't work". Neither ever confirms a second clip.
        now = time.monotonic()
        if now - getattr(self, '_last_save_request', 0.0) < 1.0:
            print('[Save] Ignored — save already in progress (spam guard)')
            self._show_save_busy_feedback()
            return
        if self._save_state.is_busy():
            print(f'[Save] Ignored — engine save still in flight '
                  f'({self._save_state.state.value})')
            self._show_save_busy_feedback()
            return
        self._last_save_request = now

        timestamp    = datetime.now().strftime('%d%b%Y_%H-%M-%S')
        clips_root   = Path.home() / 'FTHR_Clips'
        capture_mode = self.settings_manager.get('capture_mode', 'desktop')

        if capture_mode == 'window':
            if hasattr(self, 'source_popup'):
                idx = self.source_popup.window_combo.currentIndex()
                if 0 <= idx < len(self.source_popup._window_list):
                    raw_name = self.source_popup._window_list[idx]['display_name']
                else:
                    raw_name = 'Unknown'
            else:
                # Background replay has no source popup.  Its launch settings
                # remain the capture authority, including the saved window name.
                raw_name = self.settings_manager.get('target_window_name', 'Unknown')
            game_name    = _sanitize_foldername(raw_name)
            clips_folder = clips_root / game_name
            filename     = f'{game_name}_clip_from_{timestamp}.mp4'
        else:
            clips_folder = clips_root / 'Desktop'
            filename     = f'desktop_clip_from_{timestamp}.mp4'

        try:
            clips_folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # Read-only home / OneDrive lock / full disk — without this the
            # exception escapes the hotkey slot and the user sees nothing.
            self.capture_card.show_error()
            self.push_error(
                'CLIP SAVE FAILED',
                f'Cannot create {clips_folder}: {e}',
                level='error',
            )
            return
        output_path  = clips_folder / filename
        # Belt and suspenders: never overwrite an existing clip.
        _base_stem = output_path.stem
        n = 2
        while output_path.exists():
            output_path = clips_folder / f'{_base_stem}_{n}.mp4'
            n += 1
        # The Windows shared-memory field is c_wchar * 256, so the bridge
        # refuses anything longer. Catch it here instead: from inside the
        # bridge the failure is indistinguishable from a write error, and the
        # user got told to check disk space while the real cause was a deep
        # folder (a long game name under a long user profile path is enough).
        if sys.platform == 'win32' and len(str(output_path)) > 255:
            self.capture_card.show_error()
            self.push_error(
                'CLIP PATH TOO LONG',
                f'The target path is {len(str(output_path))} characters; the '
                'capture engine accepts at most 255. Choose a shorter clip '
                'folder or a shorter capture-source name.',
                level='error',
                actions=[('OPEN FOLDER', self._open_clips_folder)],
            )
            return

        print(f"Saving clip: {output_path.name}  ({duration_seconds}s)")

        # Capture the mic-window timestamp before requesting the save so the
        # post-mux thread can pull the matching mic segment from the ring.
        #
        # Engine and microphone rings now use the accepted save instant as the
        # common end boundary. The engine's PCM ring is mutex-protected, so no
        # artificial 0.5-second audio tail trim is needed.
        mic_end_time = time.monotonic()

        try:
            # A response left over from the previous save may still be sitting
            # in the field. Drain it *before* submitting, so its completion is
            # attributed to the save it belongs to. Overwriting it — which the
            # bridge used to do — silently lost that save's verdict (AUDIT-017).
            self._pump_save_responses()

            if not self.bridge.save_clip(str(output_path), duration_seconds):
                self.capture_card.show_error()
                self.push_error(
                    'CLIP SAVE FAILED',
                    'The capture engine did not accept the save command. '
                    'Check disk space at ~/FTHR_Clips.',
                    level='error',
                    actions=[('OPEN FOLDER', self._open_clips_folder)],
                )
                return

            # Submitted — NOT saved. Everything that asserts a clip exists
            # (grid entry, clip_saved, upload, "SAVED") now waits for
            # CLIP_SAVED in _on_save_outcome(). All the UI may claim here is
            # that something is in progress.
            submit = self._save_state.submit(
                str(output_path), duration_seconds, time.monotonic(),
                mic_end_time=mic_end_time,
            )
            if not submit.accepted:
                # is_busy() was checked above; losing the race means another
                # save slipped in. Refuse loudly rather than track two.
                print('[Save] Submit rejected by state machine after the '
                      'command was written — response will be attributed to '
                      'the operation already in flight')
                return

            self._set_status('SAVING…', status_idle_qss())
            self._save_poll_timer.start()

        except Exception as e:
            print(f"Save error: {e}")
            self.capture_card.show_error()
            self.push_error(
                'CLIP SAVE FAILED',
                f'Unexpected error: {e}',
                level='error',
            )

    def _show_save_busy_feedback(self):
        """Visible answer to a hotkey we refused. Never confirms a clip."""
        self._set_status('SAVING…', status_idle_qss())
        QTimer.singleShot(1000, self._update_status)

    # -----------------------------------------------------------------------
    # The save response channel — single reader, single interpreter
    # -----------------------------------------------------------------------

    def _pump_save_responses(self):
        """Read at most one engine save response, interpret it, consume it.

        This is the ONLY consumer of engine_response/engine_string. It reads
        the response and its string together as one event, hands them to the
        state machine, and clears the field only after the machine has
        accepted the interpretation.
        """
        if not self.bridge or not self.bridge.is_connected():
            return
        peeked = self.bridge.peek_save_response()
        now = time.monotonic()

        if peeked is None:
            outcome = self._save_state.on_tick(now)
            if outcome is not None:
                self._on_save_outcome(outcome)
            return

        kind, detail = peeked
        event = {
            'started': EngineEvent.SAVE_STARTED,
            'saved':   EngineEvent.CLIP_SAVED,
            'error':   EngineEvent.ERROR_OCCURRED,
        }[kind]
        outcome = self._save_state.on_event(event, detail, now)
        # Consume only now: the event has been interpreted and attributed.
        self.bridge.consume_save_response()
        if outcome is not None:
            self._on_save_outcome(outcome)

    def _on_save_poll_tick(self):
        """Dedicated short-interval poll while a save is outstanding.

        Cheap by construction: a handful of shared-memory reads and no I/O.
        It must never sleep, wait on a subprocess, join a thread or touch the
        encoder — that is the whole point of AUDIT-011.
        """
        self._pump_save_responses()
        if not self._save_state.is_busy():
            # Nothing left in flight. A timed-out operation keeps its late
            # window open via the 500 ms status poll, which also calls
            # _pump_save_responses(), so stopping the fast timer here does not
            # lose a late result.
            self._save_poll_timer.stop()

    def _on_save_outcome(self, outcome):
        """Act on exactly one state-machine outcome."""
        op = outcome.operation

        if outcome.kind is OutcomeKind.ACCEPTED:
            # Engine has the job. Still not saved — no grid, no upload.
            self._set_status('SAVING…', status_idle_qss())
            return

        if outcome.kind is OutcomeKind.TIMEOUT_NOTICE:
            # Not a verdict: the engine is slow, not proven broken. Warn, keep
            # watching, and do not emit a failure the late result would then
            # contradict.
            self._set_status('SAVE SLOW…', status_warning_qss())
            return

        if outcome.kind is OutcomeKind.FAILED:
            self.capture_card.show_error()
            self._set_status('SAVE FAILED', status_warning_qss())
            QTimer.singleShot(3000, self._update_status)
            self.push_error(
                'CLIP WAS NOT SAVED',
                outcome.detail or
                'The capture engine failed while writing the clip. '
                'Check free disk space at ~/FTHR_Clips.',
                level='error',
                actions=[('OPEN FOLDER', self._open_clips_folder)],
            )
            return

        if outcome.kind is OutcomeKind.COMPLETED:
            self._on_clip_written(op, late=outcome.late)

    def _on_clip_written(self, op, late: bool = False):
        """CLIP_SAVED for `op`. Runs exactly once per operation.

        The state machine guarantees single delivery (`result_emitted`), so the
        post-processing route below is started once and only once.
        """
        output_path = Path(op.output_path)

        if not is_completed_video_path(output_path):
            print(f'[Save] Refused non-final clip path from engine: {output_path.name}')
            self.capture_card.show_error()
            self._set_status('SAVE FAILED', status_warning_qss())
            self.push_error(
                'CLIP WAS NOT SAVED',
                'The capture engine returned an incomplete clip path. The file '
                'was not added to the library or post-processing pipeline.',
                level='error',
            )
            return

        # The engine said it wrote the file. Verify before telling the user —
        # a CLIP_SAVED for a file that is not there is a bug we want to see,
        # not a broken grid entry the user discovers days later.
        try:
            if not output_path.exists() or output_path.stat().st_size == 0:
                print(f'[Save] CLIP_SAVED but the file is missing or empty: '
                      f'{output_path.name}')
                self.capture_card.show_error()
                self._set_status('SAVE FAILED', status_warning_qss())
                QTimer.singleShot(3000, self._update_status)
                self.push_error(
                    'CLIP WAS NOT SAVED',
                    'The capture engine reported success but no clip file was '
                    'written. Check free disk space at ~/FTHR_Clips.',
                    level='error',
                    actions=[('OPEN FOLDER', self._open_clips_folder)],
                )
                return
        except OSError as e:
            print(f'[Save] Could not stat the saved clip: {e}')

        duration_seconds = op.duration_seconds
        mic_end_time = op.context.get('mic_end_time', op.requested_at)

        if late:
            print(f'[Save] Late success accepted for {output_path.name} — the '
                  f'timeout warning was premature')

        print(f'Base clip committed: {output_path.name}')
        if hasattr(self, 'clip_grid'):
            self.clip_grid._known_files = None
            self.clip_grid._load_clips()

        # Pick the post-processing route. Exactly one runs — see
        # select_post_route() for why that exclusivity is load-bearing.
        active_config = self._capture_config.active
        route, has_async_mux = select_post_route(
            audio_on=(active_config.audio_enabled if active_config else
                      self.settings_manager.get('audio_capture_enabled', True)),
            multiband_enabled=False,
            mic_running=(sys.platform != 'win32' and MicRecorder.is_available()
                         and MicRecorder().is_running()),
            watermark=self.settings_manager.get('watermark_enabled', False),
            auto_crop=self.settings_manager.get('auto_crop_enabled', False),
            camera=self.settings_manager.get('camera_enabled', False),
        )

        # Notify the upload manager and get the clip-ready event.
        # The event is set immediately if there's no mux pending; otherwise the
        # worker sets it after os.replace() completes. clip_ready stays the
        # single upload gatekeeper.
        clip_ready = self.upload_manager.notify_clip_saved(
            str(output_path), has_mic_mux=has_async_mux)

        if has_async_mux:
            self._set_status('FINALIZING…', status_idle_qss())

        args = (str(output_path), duration_seconds, mic_end_time, clip_ready)
        if route == 'mic':
            self._mux_mic_into_clip(*args)
        elif route == 'multiband':
            self._mux_multiband_into_clip(*args)
        else:
            self._finalize_clip(*args)
        if not has_async_mux:
            self._publish_final_clip(str(output_path), duration_seconds)

    def _record_finalization_warning(self, clip_path: str, message: str) -> None:
        self._clip_readiness.record_warning(clip_path, message)

    def _complete_clip_finalization(
            self, clip_path: str, duration_seconds: int) -> None:
        try:
            usable = os.path.isfile(clip_path) and os.path.getsize(clip_path) > 0
        except OSError:
            usable = False
        if usable:
            self._clip_readiness.complete(clip_path)
        else:
            self._clip_readiness.finalization_failed(
                clip_path,
                'The final clip file is missing or empty.',
                base_clip_usable=False,
            )
        self._ui_call.emit(
            lambda: self._publish_final_clip(clip_path, duration_seconds))

    def _publish_final_clip(self, clip_path: str, duration_seconds: int) -> None:
        key = os.path.normcase(os.path.abspath(clip_path))
        if key in self._published_final_clips:
            return
        self._published_final_clips.add(key)
        if not self._clip_readiness.can_access(clip_path):
            self._set_status('FINALIZATION FAILED', status_warning_qss())
            self.push_error(
                'CLIP FINALIZATION FAILED',
                'The final clip file is unavailable. The base save was not '
                'reported as a completed clip.',
                level='error',
            )
            return

        warnings = self._clip_readiness.warnings(clip_path)
        print(f'Clip ready: {os.path.basename(clip_path)}')
        self.clip_saved.emit(clip_path)
        if hasattr(self, 'clip_grid'):
            self.clip_grid._known_files = None
            self.clip_grid._load_clips()
        if warnings:
            self._set_status('SAVED WITH WARNING', status_warning_qss())
            self.push_error(
                'CLIP SAVED WITH WARNING',
                warnings[-1],
                level='warning',
            )
        else:
            self._set_status('SAVED', status_active_qss())
        QTimer.singleShot(2000, self._update_status)
        config = self._capture_config.active
        fps = config.fps if config else self.capture_fps
        width = config.width if config else self.capture_width
        height = config.height if config else self.capture_height
        self.capture_card.show_clip(
            duration_seconds, fps, _dims_to_label(width, height))

    def _open_clips_folder(self):
        """Open ~/FTHR_Clips in the platform file manager."""
        folder = Path.home() / 'FTHR_Clips'
        try:
            if sys.platform == 'win32':
                os.startfile(str(folder))  # os.startfile exists on Windows only
            else:
                opener = linux_tools.path('xdg-open')
                if opener:
                    subprocess.Popen([opener, str(folder)])
                else:
                    print(f'[UI] {linux_tools.missing_message("xdg-open")}')
        except Exception as e:
            print(f'[UI] Could not open clips folder: {e}')

    # -- Upload finished callback --

    def _on_upload_finished(self, path: str, success: bool, msg: str):
        if success:
            self._set_status('UPLOADED', status_active_qss())
            QTimer.singleShot(2000, self._update_status)
            self.capture_card.show_upload(os.path.basename(path))
            # Refresh the badge on the matching clip card if it's visible
            widget = (self.clip_grid._thumb_widgets.get(path)
                      if hasattr(self, 'clip_grid') else None)
            if widget:
                widget.set_uploaded(True)
        else:
            print(f'[Upload] Failed — {msg}  ({path})')

    def _on_upload_error(
            self, title: str, detail: str, level: str, clip_path: str) -> None:
        if title == 'UPLOAD NOT CONFIGURED':
            actions = [('OPEN UPLOAD SETTINGS', self.restore_main_window)]
        elif title == 'UPLOAD FAILED' and clip_path:
            actions = [
                ('RETRY NOW',
                 lambda p=clip_path: self.upload_manager.enqueue_upload(p))]
        else:
            actions = []
        self.push_error(title, detail, level, actions=actions)

    # -- Mic post-mux --

    @staticmethod
    def _allow_completed_clip_pipeline(clip_path: str, clip_ready=None) -> bool:
        """Reject partial paths before any post-processing worker is started."""
        if is_completed_video_path(clip_path):
            return True
        print(f'[Post] Refused incomplete clip path: {os.path.basename(clip_path)}')
        if clip_ready is not None:
            clip_ready.set()
        return False

    def _mux_mic_into_clip(self, clip_path: str,
                           duration_seconds: int,
                           mic_end_time: float,
                           clip_ready=None):
        """
        Wait for the engine to finish writing the clip, then mix the matching
        mic-recording segment into the clip's audio track. Runs in a daemon
        thread so the UI stays responsive.

        clip_ready is a threading.Event returned by UploadManager.notify_clip_saved().
        The mux worker sets it after os.replace() so the upload can start.
        If there is no mic to mux, the event is set here before returning.
        """
        if not self._allow_completed_clip_pipeline(clip_path, clip_ready):
            return
        # When multiband is active, _multiband_mux_worker owns clip_ready.
        # Don't touch the event here — it will be set in that worker's finally block.
        if effective_multiband_audio_enabled(
                self.settings_manager.get('multiband_audio_enabled', False)):
            return

        if not MicRecorder.is_available() or not MicRecorder().is_running():
            self._record_finalization_warning(
                clip_path, 'Microphone capture stopped before finalization; '
                'the base clip was retained.')
            self._complete_clip_finalization(clip_path, duration_seconds)
            return

        self._spawn_mux_thread(
            target=self._mic_mux_worker,
            args=(clip_path, duration_seconds, mic_end_time, clip_ready),
        )

    def _spawn_mux_thread(self, target, args):
        """Start a tracked post-processing worker (mic mux / multiband /
        finalize). Tracked so closeEvent can wait for pending ones — daemon
        threads killed mid-ffmpeg/os.replace leave a corrupt clip behind."""
        if not hasattr(self, '_mux_threads'):
            self._mux_threads = []
        self._mux_threads = [t for t in self._mux_threads if t.is_alive()]
        def _run_and_publish():
            try:
                target(*args)
            except Exception as exc:
                clip_path = str(args[0])
                print(f'[Finalize] Unhandled worker error: {exc}')
                self._record_finalization_warning(
                    clip_path, f'Optional clip processing failed: {exc}')
            finally:
                self._complete_clip_finalization(str(args[0]), int(args[1]))

        t = threading.Thread(target=_run_and_publish, daemon=True)
        self._mux_threads.append(t)
        t.start()
        return t

    def _mic_mux_worker(self, clip_path: str,
                        duration_seconds: int,
                        mic_end_time: float,
                        clip_ready=None):
        try:
            ffmpeg = get_ffmpeg_exe()
        except FFmpegUnavailable as e:
            print(f'[Mic] {e} — skipping mic mux')
            self._record_finalization_warning(
                clip_path, 'Microphone mix was skipped because FFmpeg is unavailable.')
            if clip_ready is not None:
                clip_ready.set()
            return

        try:
            # Wait for the clip file to actually appear and stabilize. The engine
            # runs SaveClipThread in the background; for short clips this is
            # usually <1s, but worst-case x264 path can take ~clip-duration.
            deadline = time.monotonic() + max(duration_seconds * 2, 15)
            last_size = -1
            while time.monotonic() < deadline:
                try:
                    if os.path.exists(clip_path):
                        size = os.path.getsize(clip_path)
                        if size > 0 and size == last_size:
                            break
                        last_size = size
                except OSError:
                    pass
                time.sleep(0.25)
            else:
                print(f'[Mic] Clip {clip_path} did not stabilize — skipping mux')
                self._record_finalization_warning(
                    clip_path, 'Microphone mix was skipped because the clip did not stabilize.')
                return

            # Try to mix mic audio into the clip. Any failure is non-fatal:
            # watermark/crop/camera still apply to the original clip below.
            samples = MicRecorder().extract_segment(mic_end_time, duration_seconds)
            if samples is not None and samples.size > 0:
                with tempfile.TemporaryDirectory(
                        dir=os.path.dirname(clip_path)) as td:
                    mic_wav = os.path.join(td, 'mic.wav')
                    mixed_mp4 = os.path.join(td, 'mixed.mp4')
                    if write_wav(mic_wav, samples):
                        cmd = [
                            ffmpeg, '-y',
                            '-i', clip_path,
                            '-i', mic_wav,
                            '-filter_complex',
                            '[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[aout]',
                            '-map', '0:v',
                            '-map', '[aout]',
                            '-c:v', 'copy',
                            '-c:a', 'aac', '-b:a', '192k',
                            '-shortest',
                            mixed_mp4,
                        ]
                        try:
                            result = subprocess.run(
                                cmd, capture_output=True, timeout=120,
                                **_NO_WINDOW,
                            )
                            if result.returncode == 0:
                                try:
                                    os.replace(mixed_mp4, clip_path)
                                    print(f'[Mic] Mixed mic into {os.path.basename(clip_path)}')
                                except OSError as e:
                                    print(f'[Mic] Could not replace clip: {e}')
                                    self._record_finalization_warning(
                                        clip_path, 'Microphone mix could not replace the base clip.')
                            else:
                                err = result.stderr.decode(errors='replace').strip().splitlines()
                                print(f'[Mic] ffmpeg failed: {err[-1] if err else "(no stderr)"}')
                                self._record_finalization_warning(
                                    clip_path, 'Microphone track could not be merged; '
                                    'the base clip was retained.')
                                self._ui_call.emit(lambda: self.push_error(
                                    'MIC AUDIO FAILED',
                                    'Microphone track could not be merged.'
                                    ' Clip saved without mic audio.',
                                    level='warning',
                                    actions=[('OPEN AUDIO SETTINGS',
                                              self._toggle_settings_page)],
                                ))
                        except subprocess.TimeoutExpired:
                            print('[Mic] ffmpeg timed out after 120s — skipping mux')
                            self._record_finalization_warning(
                                clip_path, 'Microphone mix timed out; the base clip was retained.')
                            self._ui_call.emit(lambda: self.push_error(
                                'MIC AUDIO FAILED',
                                'ffmpeg timed out. Clip saved without mic audio.',
                                level='warning',
                            ))
                        except Exception as e:
                            print(f'[Mic] ffmpeg mux error: {e}')
                            self._record_finalization_warning(
                                clip_path, 'Microphone mix failed; the base clip was retained.')
            else:
                print('[Mic] No mic samples for this clip window')
                self._record_finalization_warning(
                    clip_path, 'No microphone samples were available for this clip.')

            # Always apply post-processing to whatever clip exists now
            # (either the muxed version or the original if mux failed).
            self._apply_crop(clip_path, ffmpeg)
            self._apply_watermark(clip_path, ffmpeg)
            self._apply_camera_overlay(clip_path, ffmpeg, mic_end_time, duration_seconds)
        finally:
            # Always unblock the upload worker, regardless of success or failure.
            if clip_ready is not None:
                clip_ready.set()

    def _mux_multiband_into_clip(self, clip_path: str, duration_seconds: int,
                                  audio_end_time: float, clip_ready=None):
        """Start a background thread to mix per-category WAVs into the clip."""
        if not self._allow_completed_clip_pipeline(clip_path, clip_ready):
            return
        self._spawn_mux_thread(
            target=self._multiband_mux_worker,
            args=(clip_path, duration_seconds, audio_end_time, clip_ready),
        )

    def _multiband_mux_worker(self, clip_path: str, duration_seconds: int,
                               audio_end_time: float, clip_ready=None):
        try:
            ffmpeg = get_ffmpeg_exe()
        except FFmpegUnavailable as e:
            print(f'[MultiAudio] {e} — skipping multiband mix')
            if clip_ready is not None:
                clip_ready.set()
            return

        from core.audio_mixer import mix_multiband_clip

        try:
            # Wait for clip file to stabilize (same pattern as mic mux)
            deadline = time.monotonic() + max(duration_seconds * 2, 15)
            last_size = -1
            while time.monotonic() < deadline:
                try:
                    if os.path.exists(clip_path):
                        size = os.path.getsize(clip_path)
                        if size > 0 and size == last_size:
                            break
                        last_size = size
                except OSError:
                    pass
                time.sleep(0.25)
            else:
                print(f'[MultiAudio] Clip {clip_path} did not stabilize — skipping mix')
                return

            # Find per-category WAV files written by the C++ engine next to the clip
            base = os.path.splitext(clip_path)[0]
            cats = self.settings_manager.get('audio_categories', [])
            category_wavs = {}
            volumes = {}
            for cat in cats:
                sink_name = 'fthr_' + ''.join(
                    c if c.isalnum() else '_' for c in cat['name'].lower())
                wav_path = f'{base}_{sink_name}.wav'
                if os.path.exists(wav_path):
                    category_wavs[cat['name']] = wav_path
                    volumes[cat['name']] = cat.get('volume', 100) / 100.0

            if not category_wavs:
                print('[MultiAudio] No category WAVs found — skipping mix')
                return

            # Include mic audio in the multiband mix if mic recorder is active
            mic_wav_path = None
            try:
                from core.mic_recorder import MicRecorder, write_wav
                if MicRecorder.is_available() and MicRecorder().is_running():
                    samples = MicRecorder().extract_segment(audio_end_time, duration_seconds)
                    if samples is not None and samples.size > 0:
                        import tempfile as _tf
                        mic_tmp = _tf.NamedTemporaryFile(suffix='_mic.wav', delete=False)
                        mic_wav_path = mic_tmp.name
                        mic_tmp.close()
                        if write_wav(mic_wav_path, samples):
                            category_wavs['Mikrofon'] = mic_wav_path
                            volumes['Mikrofon'] = self.settings_manager.get('mic_volume', 100) / 100.0
            except Exception as e:
                print(f'[MultiAudio] Mic include error: {e}')

            ok = mix_multiband_clip(clip_path, category_wavs, volumes, ffmpeg)

            # Clean up WAV files regardless of mix result
            for wav in category_wavs.values():
                try:
                    os.remove(wav)
                except OSError:
                    pass

            # Clean up mic temp file
            if mic_wav_path and os.path.exists(mic_wav_path):
                try:
                    os.remove(mic_wav_path)
                except OSError:
                    pass

            if not ok:
                print('[MultiAudio] Mix failed')
                self._ui_call.emit(lambda: self.push_error(
                    'MULTIBAND MIX FAILED',
                    'Channel separation failed. Clip saved with default audio mix.',
                    level='warning',
                ))
            self._apply_crop(clip_path, ffmpeg)
            self._apply_watermark(clip_path, ffmpeg)
            self._apply_camera_overlay(clip_path, ffmpeg, audio_end_time, duration_seconds)
        finally:
            if clip_ready is not None:
                clip_ready.set()

    def _apply_camera_overlay(self, clip_path: str, ffmpeg: str,
                               clip_end_time: float, duration_sec: int) -> None:
        if not self.settings_manager.get('camera_enabled', False):
            return
        from core.camera_recorder import CameraRecorder
        if not CameraRecorder.is_available() or not CameraRecorder().is_running():
            self._record_finalization_warning(
                clip_path, 'Camera overlay was requested but the camera was unavailable.')
            return
        import re as _re
        import tempfile as _tf

        cam_tmp = _tf.NamedTemporaryFile(
            suffix='.mp4', dir=os.path.dirname(clip_path), delete=False)
        cam_path = cam_tmp.name
        cam_tmp.close()

        if not CameraRecorder().write_segment(cam_path, clip_end_time, duration_sec, 30.0):
            self._record_finalization_warning(
                clip_path, 'Camera overlay could not be rendered; the base clip was retained.')
            try:
                os.remove(cam_path)
            except FileNotFoundError:
                pass
            return

        info = subprocess.run([ffmpeg, '-i', clip_path],
                              capture_output=True, timeout=30, **_NO_WINDOW)
        dim = _re.search(r'(\d{3,5})x(\d{3,5})', info.stderr.decode(errors='replace'))
        clip_w = int(dim.group(1)) if dim else 1920

        size_map  = {'small': 0.20, 'medium': 0.25, 'large': 0.33}
        factor    = size_map.get(self.settings_manager.get('camera_size', 'medium'), 0.25)
        cam_w     = int(clip_w * factor)

        pos = self.settings_manager.get('camera_position', 'bottom-right')
        pos_map = {
            'top-left':     '10:10',
            'top-right':    'W-w-10:10',
            'bottom-left':  '10:H-h-10',
            'bottom-right': 'W-w-10:H-h-10',
        }
        overlay_pos = pos_map.get(pos, 'W-w-10:H-h-10')

        out_tmp = _tf.NamedTemporaryFile(
            suffix='.mp4', dir=os.path.dirname(clip_path), delete=False)
        out_path = out_tmp.name
        out_tmp.close()

        try:
            result = subprocess.run(
                [ffmpeg, '-y',
                 '-i', clip_path,
                 '-i', cam_path,
                 '-filter_complex',
                 f'[1:v]scale={cam_w}:-2[cam];[0:v][cam]overlay={overlay_pos}',
                 *software_video_args(),
                 '-c:a', 'copy',
                 out_path],
                capture_output=True, timeout=120, **_NO_WINDOW,
            )
            if result.returncode == 0:
                os.replace(out_path, clip_path)
                print(f'[Camera] Overlay applied to {os.path.basename(clip_path)}')
            else:
                err = result.stderr.decode(errors='replace').strip().splitlines()
                print(f'[Camera] ffmpeg failed: {err[-1] if err else "(no stderr)"}')
                self._record_finalization_warning(
                    clip_path, 'Camera overlay failed; the base clip was retained.')
                self._ui_call.emit(lambda: self.push_error(
                    'CAMERA OVERLAY FAILED',
                    'FFmpeg error. Clip saved without camera overlay.',
                    level='warning',
                ))
        except Exception as e:
            print(f'[Camera] Error: {e}')
            self._record_finalization_warning(
                clip_path, 'Camera overlay failed; the base clip was retained.')
        finally:
            for p in (cam_path, out_path):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass

    def _apply_watermark(self, clip_path: str, ffmpeg: str) -> None:
        if not self.settings_manager.get('watermark_enabled', False):
            return
        raw_text = self.settings_manager.get('watermark_text', 'FTHR') or 'FTHR'
        # ffmpeg drawtext text option is single-quoted at the filter-parse level.
        # Single quotes cannot appear inside single-quoted strings in ffmpeg
        # (backslash is not an escape char at this level). Replace ' with the
        # typographic apostrophe so user text is preserved visually.
        # Then escape \ for drawtext's own text-expander level.
        # Also escape % to prevent drawtext sprintf expansion (%{pts:hms} etc.).
        text = (raw_text
                .replace("'", '\u2019')
                .replace('\\', '\\\\')
                .replace('%', '%%'))
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(
            suffix='.mp4',
            dir=os.path.dirname(clip_path),
            delete=False,
        )
        tmp_path = tmp.name
        tmp.close()
        try:
            result = subprocess.run(
                [
                    ffmpeg, '-y',
                    '-i', clip_path,
                    '-vf', (
                        f"drawtext=text='{text}'"
                        ":fontsize=28:fontcolor=white@0.5"
                        ":x=w-tw-16:y=h-th-16"
                    ),
                    *software_video_args(),
                    '-c:a', 'copy',
                    tmp_path,
                ],
                capture_output=True, timeout=120,
                **_NO_WINDOW,
            )
            if result.returncode == 0:
                os.replace(tmp_path, clip_path)
                print(f'[Watermark] Applied to {os.path.basename(clip_path)}')
            else:
                err = result.stderr.decode(errors='replace').strip().splitlines()
                print(f'[Watermark] ffmpeg failed: {err[-1] if err else "(no stderr)"}')
                self._record_finalization_warning(
                    clip_path, 'Watermark failed; the base clip was retained.')
                self._ui_call.emit(lambda: self.push_error(
                    'WATERMARK FAILED',
                    'Watermark could not be applied. Clip saved without it.',
                    level='warning',
                ))
        except Exception as e:
            print(f'[Watermark] Error: {e}')
            self._record_finalization_warning(
                clip_path, 'Watermark failed; the base clip was retained.')
            self._ui_call.emit(lambda: self.push_error(
                'WATERMARK FAILED',
                'Watermark could not be applied. Clip saved without it.',
                level='warning',
            ))
        finally:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

    def _apply_crop(self, clip_path: str, ffmpeg: str) -> None:
        if not self.settings_manager.get('auto_crop_enabled', False):
            return
        import re as _re
        try:
            probe = subprocess.run(
                [ffmpeg, '-i', clip_path,
                 '-vf', 'cropdetect=limit=24:round=16:reset=0',
                 '-frames:v', '60', '-f', 'null', '-'],
                capture_output=True, timeout=60, **_NO_WINDOW,
            )
        except Exception as e:
            print(f'[AutoCrop] cropdetect probe failed: {e} — skipping')
            self._record_finalization_warning(
                clip_path, 'Auto-crop analysis failed; the base clip was retained.')
            return
        matches = _re.findall(r'crop=(\d+:\d+:\d+:\d+)',
                              probe.stderr.decode(errors='replace'))
        if not matches:
            print('[AutoCrop] cropdetect found nothing — skipping')
            return
        crop = matches[-1]
        w, h, x, y = (int(v) for v in crop.split(':'))
        try:
            info = subprocess.run([ffmpeg, '-i', clip_path],
                                  capture_output=True, timeout=30, **_NO_WINDOW)
            dim = _re.search(r'(\d{3,5})x(\d{3,5})',
                             info.stderr.decode(errors='replace'))
        except Exception:
            dim = None
        if dim:
            src_w, src_h = int(dim.group(1)), int(dim.group(2))
            if x <= 8 and y <= 8 and src_w - (x + w) <= 8 and src_h - (y + h) <= 8:
                print('[AutoCrop] No significant bars detected — skipping')
                return
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(
            suffix='.mp4', dir=os.path.dirname(clip_path), delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            result = subprocess.run(
                [ffmpeg, '-y', '-i', clip_path,
                 '-vf', f'crop={crop}',
                 *software_video_args(),
                 '-c:a', 'copy', tmp_path],
                capture_output=True, timeout=120, **_NO_WINDOW,
            )
            if result.returncode == 0:
                os.replace(tmp_path, clip_path)
                print(f'[AutoCrop] crop={crop} applied to {os.path.basename(clip_path)}')
            else:
                err = result.stderr.decode(errors='replace').strip().splitlines()
                print(f'[AutoCrop] ffmpeg failed: {err[-1] if err else "(no stderr)"}')
                self._record_finalization_warning(
                    clip_path, 'Auto-crop failed; the base clip was retained.')
                self._ui_call.emit(lambda: self.push_error(
                    'AUTO-CROP FAILED',
                    'Auto-crop could not be applied. Clip saved uncropped.',
                    level='warning',
                ))
        except Exception as e:
            print(f'[AutoCrop] Error: {e}')
            self._record_finalization_warning(
                clip_path, 'Auto-crop failed; the base clip was retained.')
            self._ui_call.emit(lambda: self.push_error(
                'AUTO-CROP FAILED',
                'Auto-crop could not be applied. Clip saved uncropped.',
                level='warning',
            ))
        finally:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

    def _finalize_clip(self, clip_path: str, duration_seconds: int,
                       clip_end_time: float = 0.0, clip_ready=None):
        if not self._allow_completed_clip_pipeline(clip_path, clip_ready):
            return
        if not (self.settings_manager.get('watermark_enabled', False)
                or self.settings_manager.get('auto_crop_enabled', False)
                or self.settings_manager.get('camera_enabled', False)):
            if clip_ready is not None:
                clip_ready.set()
            return
        self._spawn_mux_thread(
            target=self._finalize_clip_worker,
            args=(clip_path, duration_seconds, clip_end_time, clip_ready),
        )

    def _finalize_clip_worker(self, clip_path: str, duration_seconds: int,
                               clip_end_time: float = 0.0, clip_ready=None):
        try:
            try:
                ffmpeg = get_ffmpeg_exe()
            except FFmpegUnavailable as e:
                print(f'[Finalize] {e} — skipping post-processing')
                self._record_finalization_warning(
                    clip_path, 'Optional processing was skipped because FFmpeg is unavailable.')
                return
            deadline = time.monotonic() + max(duration_seconds * 2, 15)
            last_size = -1
            while time.monotonic() < deadline:
                try:
                    if os.path.exists(clip_path):
                        size = os.path.getsize(clip_path)
                        if size > 0 and size == last_size:
                            break
                        last_size = size
                except OSError:
                    pass
                time.sleep(0.25)
            else:
                print('[Finalize] Clip did not stabilize — skipping watermark')
                self._record_finalization_warning(
                    clip_path, 'Optional processing was skipped because the clip did not stabilize.')
                return
            self._apply_crop(clip_path, ffmpeg)
            self._apply_watermark(clip_path, ffmpeg)
            self._apply_camera_overlay(clip_path, ffmpeg, clip_end_time, duration_seconds)
        finally:
            if clip_ready is not None:
                clip_ready.set()

    # =======================================================================
    # UI state
    # =======================================================================

    def _update_status(self):
        # Detect an engine crash. The shared-memory mapping outlives the
        # process (is_initialized stays true), so is_connected() keeps lying
        # after a crash: the UI shows CAPTURING forever and every hotkey
        # burns the 1 s save timeout with a generic error.
        proc = self.engine_process
        if proc is not None and proc.poll() is not None:
            code = proc.returncode
            self.engine_process = None
            self._capture_config.deactivate(
                f'capture engine exited unexpectedly (code {code})')
            self._pending_launch_config = None
            self._pending_launch_generation = None
            self._restart_pending = False
            if self.bridge:
                self.bridge.shutdown()
            self.bridge = CaptureBridge()
            self._capture_health_snapshot = self._capture_health.observe(
                connected=False, frame_count=0, engine_flags=0)
            self._set_status('ENGINE STOPPED', status_warning_qss())
            self.push_error(
                'ENGINE STOPPED',
                f'The capture engine exited unexpectedly (code {code}). '
                'Clips cannot be saved until it is restarted.',
                level='error',
                actions=[('RESTART ENGINE', self._restart_capture_engine)],
            )
            return
        is_connected = getattr(self.bridge, 'is_connected', lambda: False)
        if not self.bridge or not is_connected():
            self._reconnect_counter = getattr(self, '_reconnect_counter', 0) + 1
            # Only try to reconnect while an engine process actually exists —
            # on Linux a crashed engine leaves its /dev/shm segment behind
            # with is_initialized still true, so initialize() would happily
            # "reconnect" to a corpse.
            if self.engine_process is not None and self._reconnect_counter % 3 == 1:
                if self.bridge:
                    self.bridge.shutdown()
                self.bridge = CaptureBridge()
                if self.bridge.initialize():
                    print("Reconnected to capture engine.")
                    self._set_status('CAPTURE STARTING', status_idle_qss())
                    self._set_rec_dot_state('disconnected')
                    self._reconnect_counter = 0
                    QTimer.singleShot(2000, self._check_hardware_encoding_status)
                    return
            self._set_status('CONNECTING', status_idle_qss())
            self._set_rec_dot_state('disconnected')
            return

        status = self.bridge.get_status()
        if not status.get('connected'):
            self._capture_health_snapshot = self._capture_health.observe(
                connected=False, frame_count=0, engine_flags=0)
            self._set_status('DISCONNECTED', status_warning_qss())
            self._set_rec_dot_state('disconnected')
            return

        # Second driver of the same single reader. The fast save timer stops as
        # soon as nothing is in flight; this keeps a *timed-out* operation's
        # late-result window open, and catches a response for a save the fast
        # timer had already given up on. Both call the same method, so there is
        # still exactly one consumer.
        self._pump_save_responses()
        codec = self.bridge.get_active_codec()
        if codec:
            if encoder_preset_supported(sys.platform):
                preset = self.bridge.get_active_preset()
                new_enc_text = f'{codec} — P{preset}'
            else:
                new_enc_text = codec
            if self._ui_ready:
                lbl = self._settings_page_widget.active_encoder_lbl
                if lbl.text() != new_enc_text:
                    lbl.setText(new_enc_text)
        if self.is_capturing:
            frames = status.get('frames_captured', 0)
            snapshot = self._capture_health.observe(
                connected=True,
                frame_count=frames,
                engine_flags=status.get('capture_health_flags', 0),
                generation=status.get('capture_generation', 0),
            )
            self._capture_health_snapshot = snapshot
            pending_config = getattr(self, '_pending_launch_config', None)
            pending_generation = getattr(
                self, '_pending_launch_generation', None)
            if (pending_config is not None
                    and pending_generation == self._engine_gen):
                if snapshot.state in {
                        CaptureHealthState.HEALTHY,
                        CaptureHealthState.CONTENT_SUSPECT}:
                    self._capture_config.succeed()
                    self._restart_pending = False
                    self._apply_active_audio_state(pending_config)
                    self._pending_launch_config = None
                    self._pending_launch_generation = None
                elif snapshot.state in {
                        CaptureHealthState.STALLED,
                        CaptureHealthState.FAILED}:
                    self._capture_config.fail(snapshot.reason)
                    self._restart_pending = False
                    self._pending_launch_config = None
                    self._pending_launch_generation = None
            if snapshot.changed:
                self._capture_health_log.info(
                    'Capture health: %s -> %s reason=%s frame_count=%d generation=%d '
                    'sample_sequence=%d suspicious_streak=%d luma_mean=%.2f '
                    'luma_variance=%.2f',
                    snapshot.previous_state.value.upper(),
                    snapshot.state.value.upper(), snapshot.reason, frames,
                    status.get('capture_generation', 0),
                    status.get('content_sample_sequence', 0),
                    status.get('content_suspicious_streak', 0),
                    status.get('content_luma_mean', 0.0),
                    status.get('content_luma_variance', 0.0),
                )

            state_text = {
                CaptureHealthState.INITIALIZING: 'CAPTURE STARTING',
                CaptureHealthState.HEALTHY: f'CAPTURING  {frames:,}f',
                CaptureHealthState.DEGRADED: 'CAPTURE DEGRADED',
                CaptureHealthState.CONTENT_SUSPECT: 'CAPTURE DEGRADED — DARK / UNIFORM',
                CaptureHealthState.STALLED: 'CAPTURE STALLED',
                CaptureHealthState.FAILED: 'CAPTURE FAILED',
                CaptureHealthState.RECOVERING: 'RECOVERING CAPTURE',
                CaptureHealthState.STOPPED: 'CAPTURE STOPPED',
            }
            new_text = state_text[snapshot.state]
            style = (status_active_qss()
                     if snapshot.state is CaptureHealthState.HEALTHY
                     else status_idle_qss()
                     if snapshot.state in {
                         CaptureHealthState.INITIALIZING,
                         CaptureHealthState.RECOVERING,
                     }
                     else status_warning_qss())
            if self._ui_ready and self.status_label.text() != new_text:
                self.status_label.setText(new_text)
                # setStyleSheet triggers a full re-style of the label and is
                # expensive (~1ms). Only call it on actual style transitions —
                # the frame count text updates twice/sec but the style stays
                # status_active_qss() the whole time we're capturing.
                if getattr(self, '_last_status_style', None) != style:
                    self.status_label.setStyleSheet(style)
                    self._last_status_style = style
            self._set_rec_dot_state(
                'capturing' if snapshot.state is CaptureHealthState.HEALTHY
                else 'disconnected')

            if snapshot.changed and snapshot.state is CaptureHealthState.CONTENT_SUSPECT:
                self.push_error(
                    'CAPTURE CONTENT LOOKS UNUSUAL',
                    'Fresh frames are arriving, but the image has remained black '
                    'or uniform. A dark or static scene may be intentional; check '
                    'the capture source if it is not.',
                    level='warning',
                    actions=[('RESTART CAPTURE', self._restart_capture_engine)],
                )
            elif snapshot.changed and snapshot.state in {
                    CaptureHealthState.STALLED, CaptureHealthState.FAILED}:
                self.push_error(
                    'CAPTURE IS NOT HEALTHY',
                    'The capture engine is not receiving new frames. Clips are '
                    'disabled until capture recovers.',
                    level='error',
                    actions=[('RESTART CAPTURE', self._restart_capture_engine)],
                )

            if snapshot.request_recovery:
                self._capture_health_log.warning(
                    'Capture stalled; bounded automatic backend recovery is '
                    'engine-owned and a manual process restart is available')

    def _set_status(self, text: str, style: str):
        if not hasattr(self, 'status_label'):
            print(f'[Lifecycle] Status={text}')
            return
        self.status_label.setText(text)
        # Skip the QSS reapply when the style didn't change (CONNECTING ticks
        # every 500ms during reconnect would otherwise re-style on every tick).
        if getattr(self, '_last_status_style', None) != style:
            self.status_label.setStyleSheet(style)
            self._last_status_style = style

    def _on_clip_opened(self, clip_path: str, thumb_pixmap: QPixmap, card_global_rect: QRect):
        if not self._clip_readiness.can_access(clip_path):
            self.push_error(
                'CLIP STILL FINALIZING',
                'Playback, editing and upload become available after optional '
                'processing has finished.',
                level='warning',
            )
            return
        # The file may have been deleted/renamed in the file manager while its
        # card was still visible — opening the viewer on a dead path gives a
        # black player window with a cryptic media error.
        if not os.path.isfile(clip_path):
            self.push_error(
                'CLIP NOT FOUND',
                f'{os.path.basename(clip_path)} was moved or deleted outside the app.',
                level='warning',
            )
            self.clip_grid._known_files = None   # force rescan
            self.clip_grid._load_clips()
            return
        from ui.clip_viewer import ClipViewer
        upload_on = self.settings_manager.get('upload_enabled', False)
        viewer = ClipViewer(clip_path, self.bridge, self, thumb_pixmap=thumb_pixmap,
                            settings_manager=self.settings_manager,
                            upload_enabled=upload_on,
                            metadata_manager=self.clip_metadata_manager,
                            linked_import=self.clip_grid.is_linked_import(clip_path))
        viewer.upload_requested.connect(self.upload_manager.enqueue_upload)
        viewer.export_error.connect(self.push_error)
        viewer.showMaximized()
        viewer.exec()

    # =======================================================================
    # Error bar
    # =======================================================================

    def push_error(self, title: str, detail: str,
                   level: str = 'error',
                   actions: list[tuple[str, callable]] | None = None) -> None:
        if hasattr(self, 'error_bar'):
            self.error_bar.push(title, detail, level, actions or [])
        else:
            print(f'[Lifecycle] {level.upper()}: {title}: {detail}')

    # =======================================================================
    # Hardware encoding detection
    # =======================================================================

    def _check_hardware_encoding_status(self):
        if not self.bridge or not self.bridge.is_connected():
            return
        codec = self.bridge.get_active_codec()
        hw_keywords = ('nvenc', 'amf', 'qsv', 'vaapi')
        hw_active = any(k in codec.lower() for k in hw_keywords) if codec else False

        try:
            nvenc_active = self.bridge._layout.nvenc_active
        except Exception:
            nvenc_active = False

        is_hw = hw_active or nvenc_active
        self._encoder_type = codec.upper() if codec else ('NVENC' if nvenc_active else 'SW')

        if not is_hw:
            self.push_error(
                'HARDWARE ENCODING UNAVAILABLE',
                'No hardware replay encoder is active. Capture must be restarted; '
                'FTHR does not silently switch to a shorter raw replay buffer.',
                level='error',
                actions=[('RESTART ENGINE', self._restart_capture_engine)],
            )
            print(f"[UI] Hardware encoding not available (codec: {codec or 'unknown'})")
        else:
            print(f"[UI] Hardware encoding active: {codec or 'NVENC'}")

    # =======================================================================
    # Styles
    # =======================================================================

    def _load_saved_theme(self):
        """Patch Colors class with saved theme so all QSS uses custom values."""
        theme = ThemeManager()
        if not theme.has_any_customization():
            return
        colors = theme.get_all_colors()
        from ui.style import Colors as C
        for token, value in colors.items():
            if hasattr(C, token):
                setattr(C, token, value)

    def _apply_styles(self):
        self.setStyleSheet(f'''
            /* -- Window canvas -- */
            QMainWindow {{ background-color: {Colors.BG}; }}
            QWidget {{
                background-color: {Colors.BG};
                color: {Colors.TEXT};
                font-family: {Fonts.BODY};
            }}

            /* -- Unified top bar -- */
            QFrame#topBar {{
                background-color: {Colors.SHELL_BG};
                border-bottom: 1px solid {Colors.SHELL_DIVIDER};
            }}
            QFrame#topBar QLabel {{
                background-color: transparent;
                color: {Colors.TEXT};
            }}
            QFrame#topClusterMain,
            QFrame#topClusterSettings {{
                background: transparent;
                border: none;
            }}
            QLabel#statusLabel {{ background: transparent; }}

            /* Settings gear — quiet square button */
            QPushButton#settingsGearBtn {{
                background-color: {Colors.SURFACE_2};
                border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
            }}
            QPushButton#settingsGearBtn:hover {{
                border-color: {Colors.ACCENT};
            }}

            /* Home button — square icon, matches the settings gear style */
            QPushButton#homeBtn {{
                background-color: {Colors.SURFACE_2};
                border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
            }}
            QPushButton#homeBtn:hover {{
                border-color: {Colors.ACCENT};
            }}

            /* Min / close — flat against the dark bar */
            QPushButton#winBtn {{
                background-color: transparent;
                border: none;
                color: {Colors.TEXT_DIM};
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#winBtn:hover {{
                background-color: {Colors.SURFACE_3};
                color: {Colors.TEXT};
            }}
            QPushButton#closeBtn {{
                background-color: transparent;
                border: none;
                color: {Colors.TEXT_DIM};
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#closeBtn:hover {{
                background-color: {Colors.ERROR};
                color: {Colors.TEXT};
            }}

            {scrollbar_qss()}

            {tooltip_qss()}
        ''')

    def _load_logo(self):
        """Load the top-bar logo, preferring a custom theme override."""
        theme = ThemeManager()
        custom = theme.get_custom_icon_path('fthr_logo.png')
        if custom and custom.exists():
            pix = QPixmap(str(custom))
            self._logo_label.setPixmap(
                pix.scaledToHeight(28, Qt.TransformationMode.SmoothTransformation))
            return
        logo_path = Path(__file__).parent / 'assets' / 'fthr_logo.png'
        if logo_path.exists():
            pix = _load_logo_asset(logo_path)
            self._logo_label.setPixmap(
                pix.scaledToHeight(28, Qt.TransformationMode.SmoothTransformation))
        else:
            self._logo_label.setText('FTHR')
            self._logo_label.setStyleSheet(
                label_display(Colors.TEXT, Fonts.SIZE_H2, Fonts.TRACK_HEADING))

    def _apply_theme(self):
        """Rebuild the main window stylesheet from current Colors class values.
        Called by the Customize page after the user clicks Apply Theme."""
        self._apply_styles()
        _refresh_all_icons()
        self._load_logo()
        if hasattr(self, 'clip_grid'):
            self.clip_grid.refresh_theme()
        self.update()
        QApplication.processEvents()

    # =======================================================================
    # Shutdown
    # =======================================================================

    _FINALIZATION_GRACE_SECONDS = 1.5

    def request_full_exit(self) -> None:
        """Make full exit visually immediate, then clean up on the event loop."""
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._shutdown_timer_started = time.monotonic()
        print('[Lifecycle] ShutdownRequested')
        self._lifecycle_log.info('ShutdownRequested')

        # The user must never watch a frozen main window while a bounded engine
        # or finalization cleanup is in progress. Hiding is intentionally
        # separate from cleanup; X remains a tray action, and only this path
        # reaches QApplication.quit().
        self.hide()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        self._shutdown_mark('UIHidden')
        QTimer.singleShot(0, self._perform_full_shutdown)

    def _shutdown_mark(self, phase: str) -> None:
        started = self._shutdown_timer_started
        elapsed = (float(time.monotonic()) - float(started)
                   if started is not None else 0.0)
        elapsed_text = f'{elapsed:.3f}'
        print(f'[Lifecycle] {phase} +{elapsed:.3f}s')
        self._lifecycle_log.info('%s +%ss', phase, elapsed_text)

    def _wait_for_finalization_grace(self) -> None:
        """Give atomic clip finalization a short shared grace period.

        Workers write through staging paths, so a worker that cannot finish in
        this grace window cannot publish a corrupt final-looking file. The
        next startup retains its normal stale-partial recovery responsibility.
        """
        deadline = time.monotonic() + self._FINALIZATION_GRACE_SECONDS
        for worker in list(getattr(self, '_mux_threads', ())):
            if not worker.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)
        still_running = sum(
            worker.is_alive() for worker in getattr(self, '_mux_threads', ()))
        if still_running:
            print('[Lifecycle] FinalizationGraceExpired '
                  f'workers={still_running}; staged work was not published')
        self._shutdown_mark('FinalizationGraceComplete')

    def _perform_full_shutdown(self) -> None:
        """One idempotent, bounded shutdown sequence for tray and app exit."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.is_capturing = False

        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
        if hasattr(self, '_save_poll_timer'):
            self._save_poll_timer.stop()
        if hasattr(self, '_save_state'):
            self._save_state.cancel_active(time.monotonic(), reason='shutdown')
        self._shutdown_mark('SaveCommandsStopped')

        self.hotkey_manager.cleanup()
        self._shutdown_mark('HotkeysStopped')
        self._wait_for_finalization_grace()
        try:
            if MicRecorder.is_available():
                MicRecorder().stop()
        except Exception:
            pass
        self._shutdown_mark('MicrophoneStopped')

        self.upload_manager.stop()
        self._shutdown_mark('UploadsStopped')
        self.capture_card.close()
        self._shutdown_mark('CaptureCardStopped')
        self.stop_engine()
        self._shutdown_mark('EngineStopped')

        if self._tray_icon is not None:
            self._tray_icon.hide()
            self._tray_icon.deleteLater()
            self._tray_icon = None
        self._shutdown_mark('TrayRemoved')
        self._shutdown_mark('ShutdownComplete')
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event):
        if self._shutdown_requested:
            event.accept()
            return
        if self._tray_icon is not None and self._tray_icon.isVisible():
            event.ignore()
            self.hide()
            print('[Lifecycle] WindowHiddenToTray')
            self._lifecycle_log.info('WindowHiddenToTray')
            return
        # A system with no tray cannot recover a hidden window; use the same
        # immediate-visual-response full-exit path instead.
        event.ignore()
        self.request_full_exit()


# ---------------------------------------------------------------------------
# Settings page — shared layout helpers
# ---------------------------------------------------------------------------

def _flat_section_header(title: str) -> QWidget:
    """Accent uppercase label with a thin extending line to the right."""
    row = QWidget()
    row.setStyleSheet('background: transparent;')
    hl = QHBoxLayout(row)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(10)
    lbl = QLabel(title.upper())
    lbl.setStyleSheet(
        f'color: {Colors.ACCENT}; font-size: {Fonts.SIZE_BODY_L}px; font-weight: 700;'
        f' letter-spacing: 2px; background: transparent; border: none;'
        f' font-family: {Fonts.DISPLAY};'
    )
    hl.addWidget(lbl)
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f'background: {Colors.SHELL_DIVIDER}; border: none;')
    hl.addWidget(line, 1)
    return row


def _settings_hsep() -> QFrame:
    """Thin horizontal divider between settings sections."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f'background: {Colors.SHELL_DIVIDER}; border: none;')
    return line


def _settings_vsep() -> QFrame:
    """Thin vertical divider between settings columns."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFixedWidth(1)
    line.setStyleSheet(f'background: {Colors.SHELL_DIVIDER}; border: none;')
    return line


# ---------------------------------------------------------------------------
# Sliding stacked widget — gives the settings page its swipe animation
# ---------------------------------------------------------------------------

class SlidingStackedWidget(QWidget):
    """
    Drop-in replacement for QStackedWidget that slides pages horizontally
    when the index changes. Forward = slides left, back = slides right.
    Rapid clicks queue to the most recent target so the UI never desyncs.
    """

    _DURATION = 260   # ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pages = []
        self._current = -1
        self._animating = False
        self._pending = None
        self._anim_group = None

    # API mirrors QStackedWidget ------------------------------------------------

    def addWidget(self, widget):
        widget.setParent(self)
        idx = len(self._pages)
        self._pages.append(widget)
        if idx == 0:
            self._current = 0
            widget.setGeometry(0, 0, self.width(), self.height())
            widget.show()
        else:
            widget.setGeometry(0, 0, self.width(), self.height())
            widget.hide()
        return idx

    def setCurrentIndex(self, new_idx):
        if new_idx < 0 or new_idx >= len(self._pages):
            return
        if new_idx == self._current and not self._animating:
            return
        if self._animating:
            self._pending = new_idx
            return
        self._slide(new_idx)

    def currentIndex(self):
        return self._current

    def currentWidget(self):
        if 0 <= self._current < len(self._pages):
            return self._pages[self._current]
        return None

    def widget(self, idx):
        if 0 <= idx < len(self._pages):
            return self._pages[idx]
        return None

    def count(self):
        return len(self._pages)

    # Sizing --------------------------------------------------------------------

    def sizeHint(self):
        s = QSize(0, 0)
        for p in self._pages:
            s = s.expandedTo(p.sizeHint())
        return s

    def minimumSizeHint(self):
        s = QSize(0, 0)
        for p in self._pages:
            s = s.expandedTo(p.minimumSizeHint())
        return s

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._animating and self._current >= 0:
            w, h = self.width(), self.height()
            self._pages[self._current].setGeometry(0, 0, w, h)

    # Animation -----------------------------------------------------------------

    def _slide(self, new_idx):
        old_idx = self._current
        direction = 1 if new_idx > old_idx else -1   # 1 = new from right
        w, h = self.width(), self.height()

        old_page = self._pages[old_idx]
        new_page = self._pages[new_idx]

        new_page.setGeometry(direction * w, 0, w, h)
        new_page.show()
        new_page.raise_()

        self._current = new_idx
        self._animating = True

        anim_out = QPropertyAnimation(old_page, b'geometry', self)
        anim_out.setDuration(self._DURATION)
        anim_out.setStartValue(QRect(0, 0, w, h))
        anim_out.setEndValue(QRect(-direction * w, 0, w, h))
        anim_out.setEasingCurve(QEasingCurve.Type.OutCubic)

        anim_in = QPropertyAnimation(new_page, b'geometry', self)
        anim_in.setDuration(self._DURATION)
        anim_in.setStartValue(QRect(direction * w, 0, w, h))
        anim_in.setEndValue(QRect(0, 0, w, h))
        anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_out)
        group.addAnimation(anim_in)
        group.finished.connect(self._finish)
        self._anim_group = group
        group.start()

    def _finish(self):
        w, h = self.width(), self.height()
        for i, page in enumerate(self._pages):
            if i != self._current:
                page.hide()
                page.setGeometry(0, 0, w, h)
        self._anim_group = None
        self._animating = False
        if self._pending is not None:
            nxt = self._pending
            self._pending = None
            if nxt == self._current:
                self._pages[self._current].show()
            else:
                self._slide(nxt)


# ---------------------------------------------------------------------------
# Full-screen settings page (embedded in main content stack)
# ---------------------------------------------------------------------------

class _SettingsPage(QWidget):
    close_requested           = Signal()
    imported_folders_changed  = Signal()
    notification_monitor_changed = Signal()
    encoder_config_changed    = Signal()
    audio_capture_changed     = Signal(bool)

    def _init_autostart_checkbox(self):
        from core.windows_autostart import is_packaged_launch, read_enabled
        packaged = is_packaged_launch()
        self.autostart_check.setEnabled(packaged)
        self.autostart_check.setToolTip(
            'Available in installed FTHR builds.' if packaged else
            'Development runs never modify Windows startup registration.')
        self.autostart_check.setChecked(read_enabled())

    def _on_autostart_changed(self, state):
        from core.windows_autostart import read_enabled, set_enabled
        requested = state == Qt.CheckState.Checked.value
        if set_enabled(requested):
            print(f'[Lifecycle] AutostartChanged enabled={requested}')
            return
        # Registry write failures and an externally stale entry are reflected
        # immediately instead of leaving a checkbox that lies about Windows.
        self.autostart_check.blockSignals(True)
        self.autostart_check.setChecked(read_enabled())
        self.autostart_check.blockSignals(False)
        print('[Lifecycle] Autostart registration could not be changed')

    def __init__(self, settings_manager: SettingsManager = None, parent=None):
        super().__init__(parent)
        self.sm = settings_manager
        self.setObjectName('settingsPage')
        self._loopback_stream = None
        self._presets_mgr = PresetsManager()
        self._setup_ui()
        self._apply_styles()
        self._load_audio_settings()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -- Top category tab bar --
        tab_bar = QFrame()
        tab_bar.setObjectName('settingsTabBar')
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(24, 0, 24, 0)
        tab_layout.setSpacing(0)
        tab_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        self._cat_titles = ['General', 'Clip', 'Audio', 'Visuals', 'Customize', 'Performance', 'Version & Updates']
        _tab_icons = [
            'settings(general).png',
            'clip.png',
            'sound.png',
            'visuals.png',
            'personalize.png',
            'performance.png',
            'updates.png',
        ]

        self._tab_buttons: list = []
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        # strict=False keeps the existing behaviour: if the icon list and the
        # title list ever fall out of step, the extra tabs are dropped rather
        # than raising in the middle of building the settings page.
        for i, (title, icon_file) in enumerate(
                zip(self._cat_titles, _tab_icons, strict=False)):
            btn = QToolButton()
            btn.setText(title.upper())
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            icon = _load_icon(icon_file, 36)
            btn.setIcon(icon)
            btn.setIconSize(QSize(36, 36))
            _register_icon_widget(btn, icon_file, 36)
            btn.setCheckable(True)
            btn.setObjectName('settingsTabBtn')
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self._tab_group.addButton(btn, i)
            tab_layout.addWidget(btn)
            self._tab_buttons.append(btn)

        self._tab_group.idClicked.connect(self._on_category_changed)
        main_layout.addWidget(tab_bar)

        # Thin separator line
        divider = QFrame()
        divider.setObjectName('settingsDivider')
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        main_layout.addWidget(divider)

        # -- Content area --
        content = QWidget()
        content.setObjectName('settingsContent')
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(36, 22, 36, 22)
        content_layout.setSpacing(0)

        self.stack = SlidingStackedWidget()
        self.stack.addWidget(self._make_general_page())     # 0: General (includes upload)
        self.stack.addWidget(self._make_clip_page())        # 1: Clip
        self.stack.addWidget(self._make_audio_page())       # 2: Audio
        self.stack.addWidget(self._make_visuals_page())     # 3: Visuals
        self._customize_page = CustomizePage()
        self._customize_page.theme_applied.connect(self._on_theme_applied)
        self.stack.addWidget(self._customize_page)          # 4: Customize
        self.stack.addWidget(self._make_performance_page()) # 5: Performance
        self.stack.addWidget(self._make_version_page())     # 6: Version & Updates
        content_layout.addWidget(self.stack, stretch=1)

        main_layout.addWidget(content, stretch=1)

        # Select first tab
        self._tab_buttons[0].setChecked(True)

    def _on_category_changed(self, idx):
        self.stack.setCurrentIndex(idx)
        # Audio sub-page is index 2 — start the live meter only there
        if hasattr(self, 'mic_level_meter'):
            if idx == 2 and self.isVisible():
                self.mic_level_meter.set_gain(self.mic_vol_slider.value() / 100.0)
                self.mic_level_meter.start(self._selected_mic_index())
            else:
                self.mic_level_meter.stop()
                self._stop_loopback()
                if hasattr(self, 'mic_loopback_check'):
                    self.mic_loopback_check.blockSignals(True)
                    self.mic_loopback_check.setChecked(False)
                    self.mic_loopback_check.blockSignals(False)

    def _make_general_page(self):
        from ui.upload_settings_widget import UploadSettingsWidget

        page = QWidget()
        page.setStyleSheet('background: transparent;')
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 16, 32)
        layout.setSpacing(0)

        # -- System --
        layout.addWidget(_flat_section_header('System'))
        layout.addSpacing(12)
        self.autostart_check = QCheckBox('Autostart with Windows')
        if sys.platform != 'win32':
            self.autostart_check.setVisible(False)
        layout.addWidget(self.autostart_check)
        if sys.platform == 'win32':
            self._init_autostart_checkbox()
        if sys.platform == 'win32':
            self.autostart_check.stateChanged.connect(self._on_autostart_changed)

        # -- Game Detection --
        layout.addSpacing(28)
        layout.addWidget(_flat_section_header('Game Detection'))
        layout.addSpacing(12)

        self.game_detection_check = QCheckBox('Detect games and suggest recording')
        self.game_detection_check.setStyleSheet(checkbox_qss())
        self.game_detection_check.setChecked(self.sm.get('game_detection_enabled', False))
        self.game_detection_check.toggled.connect(self._on_game_detection_toggled)
        layout.addWidget(self.game_detection_check)
        layout.addSpacing(4)

        _gd_hint = QLabel('Press F8 to start recording when a game is detected.')
        _gd_hint.setWordWrap(True)
        _gd_hint.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        layout.addWidget(_gd_hint)

        # -- Import Clips --
        layout.addSpacing(28)
        layout.addWidget(_flat_section_header('Import Clips'))
        layout.addSpacing(10)

        desc = QLabel(
            'Add folders from other clipping software so their clips '
            'appear alongside your FTHR recordings.'
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        layout.addWidget(desc)
        layout.addSpacing(12)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        add_btn = QPushButton('ADD FOLDER')
        add_btn.setStyleSheet(button_outline_qss())
        add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_btn.clicked.connect(self._on_import_folder_add)
        btn_row.addWidget(add_btn)

        self._scan_btn = QPushButton('SCAN FOR CLIPS')
        self._scan_btn.setStyleSheet(button_secondary_qss())
        self._scan_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._scan_btn.clicked.connect(self._on_import_folder_scan)
        btn_row.addWidget(self._scan_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addSpacing(12)

        # Added-folders list
        self._import_folders_container = QWidget()
        self._import_folders_container.setStyleSheet('background: transparent;')
        self._import_folders_layout = QVBoxLayout(self._import_folders_container)
        self._import_folders_layout.setContentsMargins(0, 0, 0, 0)
        self._import_folders_layout.setSpacing(4)
        layout.addWidget(self._import_folders_container)

        # Scan results (hidden until scan runs)
        layout.addSpacing(4)
        self._scan_results_container = QWidget()
        self._scan_results_container.setVisible(False)
        self._scan_results_container.setStyleSheet('background: transparent;')
        self._scan_results_layout = QVBoxLayout(self._scan_results_container)
        self._scan_results_layout.setContentsMargins(0, 0, 0, 0)
        self._scan_results_layout.setSpacing(4)
        layout.addWidget(self._scan_results_container)

        # -- Upload --
        layout.addSpacing(28)
        self._upload_settings_widget = UploadSettingsWidget(self.sm, no_scroll=True)
        layout.addWidget(self._upload_settings_widget)

        # Focus pause has no Windows replay implementation. Do not expose a
        # control that would route through the unrelated legacy record toggle.
        if focus_pause_supported(sys.platform):
            layout.addSpacing(28)
            layout.addWidget(_flat_section_header('Anticheat Detection'))
            layout.addSpacing(12)

            self.anticheat_check = QCheckBox(
                'Pause recording when game is unfocused')
            self.anticheat_check.setStyleSheet(checkbox_qss())
            self.anticheat_check.setChecked(
                self.sm.get('anticheat_detection_enabled', False))
            self.anticheat_check.toggled.connect(self._on_anticheat_toggled)
            layout.addWidget(self.anticheat_check)
            layout.addSpacing(4)

            _at_hint = QLabel(
                'Window capture only. Pauses the ring buffer when the game '
                'is not in the foreground. Off by default.'
            )
            _at_hint.setWordWrap(True)
            _at_hint.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
            layout.addWidget(_at_hint)

        # -- Settings Presets --
        layout.addSpacing(28)
        layout.addWidget(_flat_section_header('Settings Presets'))
        layout.addSpacing(12)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)

        self.preset_combo = _DropdownCombo()
        self.preset_combo.setStyleSheet(_COMBO_STYLE)
        self.preset_combo.setMinimumWidth(160)
        self._refresh_preset_combo()
        preset_row.addWidget(self.preset_combo, 1)

        load_btn = QPushButton('LOAD')
        load_btn.setStyleSheet(button_primary_qss())
        load_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        load_btn.clicked.connect(self._on_preset_load)
        preset_row.addWidget(load_btn)

        save_btn = QPushButton('SAVE')
        save_btn.setStyleSheet(button_outline_qss())
        save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_btn.clicked.connect(self._on_preset_save)
        preset_row.addWidget(save_btn)

        del_btn = QPushButton('DELETE')
        del_btn.setStyleSheet(button_outline_qss())
        del_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        del_btn.clicked.connect(self._on_preset_delete)
        preset_row.addWidget(del_btn)

        layout.addLayout(preset_row)

        layout.addStretch()

        # Wrap everything in a scroll area so the combined content fits
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(scrollbar_qss())
        scroll.setWidget(page)

        wrapper = QWidget()
        wrapper.setStyleSheet('background: transparent;')
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        wl.addWidget(scroll)

        self._scan_pending: list[tuple[str, str]] = []
        self._refresh_import_folders_list()
        return wrapper

    # -- Import Clips helpers --

    def _refresh_import_folders_list(self):
        while self._import_folders_layout.count():
            item = self._import_folders_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        folders = self.sm.get('imported_clip_folders', []) if self.sm else []

        if not folders:
            lbl = QLabel('No import folders added yet.')
            lbl.setStyleSheet(label_body(Colors.TEXT_MUTED, Fonts.SIZE_BODY))
            self._import_folders_layout.addWidget(lbl)
            return

        for folder in folders:
            self._import_folders_layout.addWidget(self._make_import_folder_row(folder))

    def _make_import_folder_row(self, path: str) -> QFrame:
        row = QFrame()
        row.setStyleSheet(
            f'QFrame {{ background: {Colors.SURFACE_1};'
            f' border: 1px solid {Colors.BORDER}; }}'
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 6, 6, 6)
        rl.setSpacing(8)

        display_path = path
        if not os.path.isdir(path):
            display_path += '  —  MISSING (remove link or reconnect drive)'
        path_lbl = QLabel(display_path)
        path_lbl.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        path_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        rl.addWidget(path_lbl, 1)

        remove_btn = QPushButton('×')
        remove_btn.setFixedSize(22, 22)
        remove_btn.setToolTip('Remove this folder')
        remove_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        remove_btn.setStyleSheet(
            f'QPushButton {{ background: transparent;'
            f' border: 1px solid {Colors.BORDER}; color: {Colors.TEXT_DIM};'
            f' font-size: 14px; font-weight: bold; }}'
            f' QPushButton:hover {{ border-color: {Colors.ERROR}; color: {Colors.ERROR}; }}'
        )
        remove_btn.clicked.connect(lambda _, p=path: self._remove_import_folder(p))
        rl.addWidget(remove_btn)
        return row

    def _remove_import_folder(self, path: str):
        if self.sm is None:
            return
        folders = remove_import_root(
            self.sm.get('imported_clip_folders', []), path)
        self.sm.set('imported_clip_folders', folders)
        self.sm.save_settings()
        self._refresh_import_folders_list()
        self.imported_folders_changed.emit()

    def _on_import_folder_add(self):
        folder = QFileDialog.getExistingDirectory(
            self, 'Select Clips Folder',
            str(Path.home() / 'Videos'),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder or self.sm is None:
            return
        folders = add_import_root(
            self.sm.get('imported_clip_folders', []), folder)
        self.sm.set('imported_clip_folders', folders)
        self.sm.save_settings()
        self._refresh_import_folders_list()
        # Remove from scan results if it was pending there
        self._scan_pending = [(n, p) for n, p in self._scan_pending if p != folder]
        self._rebuild_scan_results()
        self.imported_folders_changed.emit()

    def _on_import_folder_scan(self):
        _known = [
            ('Medal.tv',             Path.home() / 'Videos' / 'Medal'),
            ('Xbox Game Bar',        Path.home() / 'Videos' / 'Captures'),
            ('Outplayed',            Path.home() / 'Videos' / 'Outplayed'),
            ('Plays.tv',             Path.home() / 'Videos' / 'Plays.tv'),
            ('AMD ReLive',           Path.home() / 'Videos' / 'AMD' / 'ReLive'),
            ('Nvidia ShadowPlay',    Path.home() / 'Videos' / 'Shadowplay Clips'),
            ('GeForce Experience',   Path.home() / 'Videos' / 'NVIDIA'),
            ('Nvidia Highlights',    Path.home() / 'Videos' / 'Nvidia Highlights'),
        ]
        existing = {
            os.path.normcase(os.path.realpath(path))
            for path in (
                self.sm.get('imported_clip_folders', []) if self.sm else [])
        }
        found = [
            (name, str(path))
            for name, path in _known
            if (path.exists()
                and os.path.normcase(os.path.realpath(path)) not in existing)
        ]

        # Clear scan results area for fresh output
        while self._scan_results_layout.count():
            item = self._scan_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not found:
            lbl = QLabel('No clip folders from other software were found on your system.')
            lbl.setWordWrap(True)
            lbl.setStyleSheet(label_body(Colors.TEXT_MUTED, Fonts.SIZE_BODY))
            self._scan_results_layout.addWidget(lbl)
            self._scan_results_container.setVisible(True)
            self._scan_pending = []
            return

        self._scan_pending = found
        self._rebuild_scan_results()

    def _rebuild_scan_results(self):
        while self._scan_results_layout.count():
            item = self._scan_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._scan_pending:
            self._scan_results_container.setVisible(False)
            return

        count = len(self._scan_pending)
        header = _flat_section_header(
            f'Found {count} folder{"s" if count != 1 else ""}'
        )
        self._scan_results_layout.addWidget(header)
        self._scan_results_layout.addSpacing(8)

        for name, path in self._scan_pending:
            self._scan_results_layout.addWidget(self._make_scan_result_row(name, path))

        self._scan_results_container.setVisible(True)

    def _make_scan_result_row(self, software_name: str, path: str) -> QFrame:
        row = QFrame()
        row.setStyleSheet(
            f'QFrame {{ background: {Colors.SURFACE_1};'
            f' border: 1px solid {Colors.BORDER}; }}'
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 8, 8, 8)
        rl.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(1)
        name_lbl = QLabel(software_name)
        name_lbl.setStyleSheet(
            f'color: {Colors.TEXT}; font-size: {Fonts.SIZE_BODY}px;'
            f' font-family: {Fonts.DISPLAY}; font-weight: bold;'
            f' background: transparent;'
        )
        path_lbl = QLabel(path)
        path_lbl.setStyleSheet(label_body(Colors.TEXT_MUTED, Fonts.SIZE_LABEL))
        info.addWidget(name_lbl)
        info.addWidget(path_lbl)
        rl.addLayout(info, 1)

        add_btn = QPushButton('ADD')
        add_btn.setFixedWidth(60)
        add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_btn.setStyleSheet(button_primary_qss())
        add_btn.clicked.connect(lambda _, n=software_name, p=path: self._scan_add(n, p))
        rl.addWidget(add_btn)

        dismiss_btn = QPushButton('DISMISS')
        dismiss_btn.setFixedWidth(76)
        dismiss_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        dismiss_btn.setStyleSheet(button_outline_qss())
        dismiss_btn.clicked.connect(lambda _, n=software_name, p=path: self._scan_dismiss(n, p))
        rl.addWidget(dismiss_btn)
        return row

    def _scan_add(self, software_name: str, path: str):
        if self.sm is not None:
            folders = add_import_root(
                self.sm.get('imported_clip_folders', []), path)
            self.sm.set('imported_clip_folders', folders)
            self.sm.save_settings()
            self._refresh_import_folders_list()
        self._scan_pending = [(n, p) for n, p in self._scan_pending if p != path]
        self._rebuild_scan_results()
        self.imported_folders_changed.emit()

    def _scan_dismiss(self, software_name: str, path: str):
        self._scan_pending = [(n, p) for n, p in self._scan_pending if p != path]
        self._rebuild_scan_results()

    def _make_clip_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 16, 32)
        layout.setSpacing(0)

        layout.addWidget(_flat_section_header('Clip Settings'))
        layout.addSpacing(12)
        hint = QLabel(
            'Clip length, frame rate, resolution and bitrate are\n'
            'configured via the top bar capture button (▶ CAP).'
        )
        hint.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        layout.addWidget(hint)

        # -- Watermark --
        layout.addSpacing(28)
        layout.addWidget(_flat_section_header('Watermark'))
        layout.addSpacing(12)

        self.watermark_check = QCheckBox('Burn watermark into clips')
        self.watermark_check.setStyleSheet(checkbox_qss())
        self.watermark_check.setChecked(self.sm.get('watermark_enabled', False))
        self.watermark_check.toggled.connect(self._on_watermark_toggled)
        layout.addWidget(self.watermark_check)
        layout.addSpacing(8)

        wm_row = QHBoxLayout()
        wm_row.setSpacing(8)
        wm_lbl = QLabel('TEXT')
        wm_lbl.setStyleSheet(_LABEL_STYLE)
        wm_lbl.setFixedWidth(60)
        wm_row.addWidget(wm_lbl)
        self.watermark_text_edit = QLineEdit(self.sm.get('watermark_text', 'FTHR'))
        self.watermark_text_edit.setMaxLength(30)
        self.watermark_text_edit.setStyleSheet(_COMBO_STYLE)
        self.watermark_text_edit.textChanged.connect(self._on_watermark_text_changed)
        wm_row.addWidget(self.watermark_text_edit)
        layout.addLayout(wm_row)
        layout.addSpacing(4)

        _wm_hint = QLabel('Small text overlay in the bottom-right corner. Off by default.')
        _wm_hint.setWordWrap(True)
        _wm_hint.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        layout.addWidget(_wm_hint)

        # -- Auto-Crop --
        layout.addSpacing(28)
        layout.addWidget(_flat_section_header('Auto-Crop'))
        layout.addSpacing(12)

        self.auto_crop_check = QCheckBox('Automatically remove black bars')
        self.auto_crop_check.setStyleSheet(checkbox_qss())
        self.auto_crop_check.setChecked(self.sm.get('auto_crop_enabled', False))
        self.auto_crop_check.toggled.connect(self._on_auto_crop_toggled)
        layout.addWidget(self.auto_crop_check)
        layout.addSpacing(4)

        _crop_hint = QLabel(
            'Detects letterbox/pillarbox bars after recording and removes them. '
            'Off by default. Slows down clip processing.'
        )
        _crop_hint.setWordWrap(True)
        _crop_hint.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        layout.addWidget(_crop_hint)

        layout.addStretch()
        return page

    def _make_audio_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        cols = QHBoxLayout()
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(0)

        # -- Left: Microphone --
        left = QWidget()
        left.setStyleSheet('background: transparent;')
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 28, 0)
        left_layout.setSpacing(0)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        left_layout.addWidget(_flat_section_header('Microphone'))
        left_layout.addSpacing(12)

        # Device row
        dev_row = QHBoxLayout()
        dev_row.setSpacing(8)
        dev_lbl = QLabel('Input device')
        dev_lbl.setFixedWidth(100)
        dev_row.addWidget(dev_lbl)
        self.mic_combo = _DropdownCombo()
        self._mic_combo_connected = False
        dev_row.addWidget(self.mic_combo, 1)
        refresh = QPushButton()
        refresh.setObjectName('micRefreshBtn')
        refresh.setFixedSize(26, 26)
        refresh.setToolTip('Re-scan input devices')
        _ref_ico2 = _load_icon('refresh.png', 13)
        if not _ref_ico2.isNull():
            refresh.setIcon(_ref_ico2)
            refresh.setIconSize(QSize(13, 13))
            _register_icon_widget(refresh, 'refresh.png', 13)
        else:
            refresh.setText('↻')
        refresh.clicked.connect(self._populate_mic_devices)
        dev_row.addWidget(refresh)
        left_layout.addLayout(dev_row)
        left_layout.addSpacing(8)

        # Volume row
        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        vol_lbl = QLabel('Loudness')
        vol_lbl.setFixedWidth(100)
        vol_row.addWidget(vol_lbl)
        self.mic_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_vol_slider.setRange(0, 200)
        self.mic_vol_slider.setValue(100)
        vol_row.addWidget(self.mic_vol_slider, 1)
        self.mic_vol_value = QLabel('100%')
        self.mic_vol_value.setFixedWidth(42)
        self.mic_vol_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.mic_vol_slider.valueChanged.connect(self._on_mic_volume_changed)
        vol_row.addWidget(self.mic_vol_value)
        left_layout.addLayout(vol_row)
        left_layout.addSpacing(8)

        # Live level meter row
        meter_row = QHBoxLayout()
        meter_row.setSpacing(8)
        meter_lbl = QLabel('Live level')
        meter_lbl.setFixedWidth(100)
        meter_row.addWidget(meter_lbl)
        self.mic_level_meter = _MicLevelMeter()
        meter_row.addWidget(self.mic_level_meter, 1)
        left_layout.addLayout(meter_row)
        left_layout.addSpacing(10)

        # Loopback checkbox
        self.mic_loopback_check = QCheckBox(
            'Monitor (route mic to speakers so I can hear it)')
        self.mic_loopback_check.setToolTip(
            'Plays your microphone back through your speakers in real time so '
            'you can judge volume. Stops when you leave this page.')
        self.mic_loopback_check.toggled.connect(self._on_loopback_toggled)
        left_layout.addWidget(self.mic_loopback_check)

        if not _SD_AVAILABLE:
            warn = QLabel(
                '⚠  Mic features need the "sounddevice" Python package.\n'
                '   Install it from your venv:  pip install sounddevice numpy')
            warn.setStyleSheet(label_body(Colors.ERROR, Fonts.SIZE_BODY))
            left_layout.addSpacing(6)
            left_layout.addWidget(warn)

        left_layout.addStretch()
        cols.addWidget(left, 1)

        cols.addWidget(_settings_vsep())

        # -- Right: FTHR Sounds --
        right = QWidget()
        right.setStyleSheet('background: transparent;')
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(28, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        right_layout.addWidget(_flat_section_header('FTHR Sounds'))
        right_layout.addSpacing(12)

        self.sound_sliders = {}
        self.sound_value_labels = {}
        for label_text, key in [
            ('Clip Captured',       'clip'),
            ('Screenshot Captured', 'screenshot'),
            ('Error Sound',         'error'),
            ('Startup Sound',       'startup'),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(f'{label_text}:')
            lbl.setFixedWidth(150)
            row.addWidget(lbl)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(100)
            self.sound_sliders[key] = slider
            row.addWidget(slider, 1)
            val_lbl = QLabel('100%')
            val_lbl.setFixedWidth(38)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.sound_value_labels[key] = val_lbl
            slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(f'{v}%'))
            slider.valueChanged.connect(lambda v, k=key: self._on_sound_volume_changed(k, v))
            row.addWidget(val_lbl)
            right_layout.addLayout(row)
            right_layout.addSpacing(8)

        right_layout.addSpacing(20)
        right_layout.addWidget(_flat_section_header('Notifications'))
        right_layout.addSpacing(12)

        mon_row = QHBoxLayout()
        mon_row.setSpacing(8)
        mon_lbl = QLabel('Monitor:')
        mon_lbl.setFixedWidth(150)
        mon_row.addWidget(mon_lbl)
        self.notif_monitor_combo = _DropdownCombo()
        self.notif_monitor_combo.addItem('Auto (highest Hz)', userData='auto')
        for s in QApplication.screens():
            g = s.availableGeometry()
            self.notif_monitor_combo.addItem(
                f'{s.name()}  ({g.width()}×{g.height()} @ {int(s.refreshRate())}Hz)',
                userData=s.name(),
            )
        self.notif_monitor_combo.currentIndexChanged.connect(self._on_notif_monitor_changed)
        mon_row.addWidget(self.notif_monitor_combo, 1)
        right_layout.addLayout(mon_row)

        right_layout.addStretch()
        cols.addWidget(right, 1)

        outer.addLayout(cols)

        # Mic enumeration was synchronous here, which made the entire
        # settings page wait on sounddevice.query_devices(). On systems with
        # many audio devices this added 100–400 ms to the first open. We
        # populate a placeholder now and run the real scan on the next event
        # loop tick so widget construction stays I/O-free.
        self.mic_combo.addItem('System Default', userData=None)
        QTimer.singleShot(0, self._populate_mic_devices)

        # -- Multiband Audio --
        outer.addSpacing(24)
        outer.addWidget(_settings_hsep())
        outer.addSpacing(20)
        mb_header = _flat_section_header('Multiband Audio')
        outer.addWidget(mb_header)
        outer.addSpacing(8)

        mb_desc = QLabel(
            'Records each app category separately and bakes volume levels into the clip. '
            'Disable for maximum performance.')
        mb_desc.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        mb_desc.setWordWrap(True)
        outer.addWidget(mb_desc)
        outer.addSpacing(12)

        self.multiband_check = QCheckBox('Enable Multiband Audio')
        self.multiband_check.setStyleSheet(checkbox_qss())
        self.multiband_check.setChecked(False)
        self.multiband_check.toggled.connect(self._on_multiband_toggled)
        outer.addWidget(self.multiband_check)
        outer.addSpacing(12)

        # Category list container — shown only when multiband is enabled
        self.multiband_container = QWidget()
        mb_inner = QVBoxLayout(self.multiband_container)
        mb_inner.setContentsMargins(0, 0, 0, 0)
        mb_inner.setSpacing(6)
        self._cat_rows = []
        self._rebuild_category_rows(mb_inner)
        outer.addWidget(self.multiband_container)
        self.multiband_container.setVisible(False)

        # Recognised app→category label (updated by timer when page is visible)
        self.mappings_lbl = QLabel('Detected apps: —')
        self.mappings_lbl.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        self.mappings_lbl.setWordWrap(True)
        outer.addWidget(self.mappings_lbl)
        outer.addSpacing(8)

        # + Add category row
        add_row = QHBoxLayout()
        self.new_cat_name = QLineEdit()
        self.new_cat_name.setPlaceholderText('Name (e.g. "Music")')
        self.new_cat_name.setStyleSheet(combo_qss())
        self.new_cat_patterns = QLineEdit()
        self.new_cat_patterns.setPlaceholderText('Patterns: spotify,Spotify,vlc')
        self.new_cat_patterns.setStyleSheet(combo_qss())
        add_btn = QPushButton('+ Add')
        add_btn.setStyleSheet(button_outline_qss())
        add_btn.clicked.connect(self._on_add_category)
        add_row.addWidget(self.new_cat_name, 1)
        add_row.addWidget(self.new_cat_patterns, 2)
        add_row.addWidget(add_btn)
        outer.addLayout(add_row)

        # Retain the implementation for the planned audio rework, but remove
        # every activation surface from the public alpha.
        for widget in (
                mb_header, mb_desc, self.multiband_check,
                self.multiband_container, self.mappings_lbl,
                self.new_cat_name, self.new_cat_patterns, add_btn):
            widget.setVisible(False)

        # Timer to refresh mappings label every 2s while page is visible
        self._mappings_timer = QTimer(self)
        self._mappings_timer.setInterval(2000)
        self._mappings_timer.timeout.connect(self._update_mappings_label)

        # -- Audio Capture --
        outer.addSpacing(28)
        outer.addWidget(_flat_section_header('Audio Capture'))
        outer.addSpacing(12)

        self.audio_capture_check = QCheckBox('Enable audio capture')
        self.audio_capture_check.setStyleSheet(checkbox_qss())
        self.audio_capture_check.setChecked(self.sm.get('audio_capture_enabled', True))
        self.audio_capture_check.toggled.connect(self._on_audio_capture_toggled)
        outer.addWidget(self.audio_capture_check)
        outer.addSpacing(4)

        _aud_hint = QLabel('Disabling saves CPU. Takes effect on next engine restart.')
        _aud_hint.setWordWrap(True)
        _aud_hint.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        outer.addWidget(_aud_hint)

        return page

    # -- Multiband Audio helpers --

    def _rebuild_category_rows(self, layout: QVBoxLayout):
        """Clear and repopulate the category volume rows."""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cat_rows = []

        for i, cat in enumerate(self.sm.get('audio_categories', [])):
            row_w = QWidget()
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(0, 0, 0, 0)
            row_h.setSpacing(8)

            name_lbl = QLabel(cat['name'])
            name_lbl.setFixedWidth(100)
            name_lbl.setStyleSheet(label_body(Colors.TEXT, Fonts.SIZE_BODY))

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setStyleSheet(slider_qss())
            slider.setRange(0, 100)
            slider.setValue(cat.get('volume', 100))

            val_lbl = QLabel(f"{cat.get('volume', 100)}%")
            val_lbl.setFixedWidth(38)
            val_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            slider.valueChanged.connect(
                lambda v, idx=i, lbl=val_lbl: self._on_cat_volume(idx, v, lbl))

            del_btn = QPushButton('✕')
            del_btn.setFixedSize(24, 24)
            del_btn.setStyleSheet(button_outline_qss())
            del_btn.clicked.connect(lambda _, idx=i: self._on_delete_category(idx))

            row_h.addWidget(name_lbl)
            row_h.addWidget(slider, 1)
            row_h.addWidget(val_lbl)
            row_h.addWidget(del_btn)
            layout.addWidget(row_w)
            self._cat_rows.append((name_lbl, slider, val_lbl, del_btn))

    def _on_multiband_toggled(self, checked: bool):
        checked = effective_multiband_audio_enabled(checked)
        self.sm.set('multiband_audio_enabled', checked)
        self.sm.save_settings()
        self.multiband_container.setVisible(checked)
        if checked:
            self._mappings_timer.start()
        else:
            self._mappings_timer.stop()

    def _on_game_detection_toggled(self, checked: bool):
        self.sm.set('game_detection_enabled', checked)
        self.sm.save_settings()
        main_win = self.window()
        if hasattr(main_win, '_game_detector'):
            if checked:
                main_win._game_detector.start()
            else:
                main_win._game_detector.stop()

    def _on_audio_capture_toggled(self, checked: bool):
        self.sm.set('audio_capture_enabled', checked)
        self.sm.save_settings()
        self.audio_capture_changed.emit(checked)

    def _on_watermark_toggled(self, checked: bool):
        self.sm.set('watermark_enabled', checked)
        self.sm.save_settings()

    def _on_watermark_text_changed(self, text: str):
        self.sm.set('watermark_text', text)
        self.sm.save_settings()

    def _on_auto_crop_toggled(self, checked: bool):
        self.sm.set('auto_crop_enabled', checked)
        self.sm.save_settings()

    def _on_anticheat_toggled(self, checked: bool):
        self.sm.set('anticheat_detection_enabled', checked)
        self.sm.save_settings()
        main_win = self.window()
        if not hasattr(main_win, '_focus_monitor'):
            return
        if checked and self.sm.get('capture_mode', 'desktop') == 'window':
            target = self.sm.get('target_window_name', '')
            main_win._focus_monitor.set_target(target)
            main_win._focus_monitor.start()
        else:
            main_win._focus_monitor.stop()
            if hasattr(main_win, 'bridge') and main_win.bridge.is_connected():
                main_win.bridge.resume_recording()

    def _populate_camera_devices(self):
        from core.camera_recorder import CameraRecorder
        self.camera_device_combo.clear()
        if not CameraRecorder.is_available():
            self.camera_device_combo.addItem('cv2 not available')
            return
        # Populate statically — blocking cv2.VideoCapture scan at startup freezes UI
        # for ~5s per device. User can pick index and test via the toggle.
        self.camera_device_combo.addItems(
            [f'Camera {i}' for i in range(4)])
        saved = self.sm.get('camera_device_index', 0)
        self.camera_device_combo.setCurrentIndex(min(saved, 3))

    def _on_camera_toggled(self, checked: bool):
        self.sm.set('camera_enabled', checked)
        self.sm.save_settings()
        from core.camera_recorder import CameraRecorder
        if checked and CameraRecorder.is_available():
            idx = self.sm.get('camera_device_index', 0)
            CameraRecorder().start_async(idx)   # blocking open would freeze the UI
            self._camera_preview_timer.start()
        else:
            CameraRecorder().stop()
            self._camera_preview_timer.stop()
            self.camera_preview_lbl.setPixmap(QPixmap())
            self.camera_preview_lbl.setText('Camera disabled')

    def _on_camera_device_changed(self, idx: int):
        self.sm.set('camera_device_index', idx)
        self.sm.save_settings()
        if self.sm.get('camera_enabled', False):
            from core.camera_recorder import CameraRecorder
            CameraRecorder().start_async(idx)   # blocking open would freeze the UI

    def _on_camera_pos_changed(self, idx: int):
        keys = ['bottom-right', 'bottom-left', 'top-right', 'top-left']
        self.sm.set('camera_position', keys[idx] if idx < len(keys) else 'bottom-right')
        self.sm.save_settings()

    def _on_camera_size_changed(self, idx: int):
        keys = ['small', 'medium', 'large']
        self.sm.set('camera_size', keys[idx] if idx < len(keys) else 'medium')
        self.sm.save_settings()

    def _update_camera_preview(self):
        from core.camera_recorder import CameraRecorder
        frame = CameraRecorder().latest_frame
        if frame is None:
            return
        import cv2 as _cv2
        from PySide6.QtGui import QImage, QPixmap as _QPixmap
        rgb = _cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        # Use bytes() to copy the data — QImage constructed from a memoryview
        # holds a reference to the buffer, but rgb may be GC'd before Qt renders.
        qi = QImage(rgb.tobytes(), w, h, ch * w, QImage.Format.Format_RGB888)
        pix = _QPixmap.fromImage(qi).scaled(
            160, 90,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.camera_preview_lbl.setPixmap(pix)
        self.camera_preview_lbl.setText('')

    def _refresh_preset_combo(self):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        names = self._presets_mgr.names()
        if names:
            self.preset_combo.addItems(names)
        else:
            self.preset_combo.addItem('— no presets —')
        self.preset_combo.blockSignals(False)

    def _on_preset_save(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, 'Save Preset', 'Name:',
            text=self.preset_combo.currentText() if self._presets_mgr.names() else '')
        if not ok or not name.strip():
            return
        name = name.strip()
        data = {k: self.sm.get(k) for k in PRESET_KEYS}
        self._presets_mgr.save(name, data)
        self._refresh_preset_combo()
        idx = self.preset_combo.findText(name)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)

    def _on_preset_load(self):
        name = self.preset_combo.currentText()
        data = self._presets_mgr.load(name)
        if data is None:
            return
        filtered = filter_alpha_preset(data)
        previous = {key: self.sm.get(key) for key in filtered}
        for k, v in filtered.items():
            self.sm.set(k, v)
        main_win = self.window()
        if (hasattr(main_win, '_sync_requested_capture_settings')
                and not main_win._sync_requested_capture_settings()):
            for key, value in previous.items():
                self.sm.set(key, value)
            if hasattr(main_win, 'cap_settings_popup'):
                main_win.cap_settings_popup.reload_from_settings()
            return
        self.sm.save_settings()
        if hasattr(main_win, 'cap_settings_popup'):
            main_win.cap_settings_popup.reload_from_settings()
        if hasattr(main_win, '_restart_capture_engine'):
            main_win._restart_capture_engine()

    def _on_preset_delete(self):
        name = self.preset_combo.currentText()
        if not self._presets_mgr.names():
            return
        self._presets_mgr.delete(name)
        self._refresh_preset_combo()

    def _on_cat_volume(self, idx: int, vol: int, lbl: QLabel):
        lbl.setText(f'{vol}%')
        cats = self.sm.get('audio_categories', [])
        if 0 <= idx < len(cats):
            cats[idx]['volume'] = vol
            self.sm.set('audio_categories', cats)
            self.sm.save_settings()

    def _on_delete_category(self, idx: int):
        cats = self.sm.get('audio_categories', [])
        if 0 <= idx < len(cats):
            cats.pop(idx)
            self.sm.set('audio_categories', cats)
            self.sm.save_settings()
            mb_inner = self.multiband_container.layout()
            self._rebuild_category_rows(mb_inner)

    def _on_add_category(self):
        name = self.new_cat_name.text().strip()
        if not name:
            return
        patterns = [p.strip() for p in self.new_cat_patterns.text().split(',')
                    if p.strip()]
        cats = self.sm.get('audio_categories', [])
        cats.append({'name': name, 'volume': 100, 'patterns': patterns})
        self.sm.set('audio_categories', cats)
        self.sm.save_settings()
        self.new_cat_name.clear()
        self.new_cat_patterns.clear()
        self._rebuild_category_rows(self.multiband_container.layout())

    def _update_mappings_label(self):
        """Refresh the recognised-apps label from the bridge."""
        try:
            # Walk up Qt parent hierarchy: SettingsPage → QStackedWidget → MainWindow
            main_win = self.parent()
            while main_win is not None and not hasattr(main_win, 'bridge'):
                main_win = main_win.parent()
            if main_win is None:
                return
            mappings = main_win.bridge.get_audio_mappings()
            if not mappings:
                self.mappings_lbl.setText('Detected apps: (none)')
            else:
                parts = [f'{app} → {cat} ✓' for app, cat in mappings.items()]
                self.mappings_lbl.setText('Detected apps: ' + '   '.join(parts))
        except Exception:
            pass

    # -- Microphone helpers --

    def _populate_mic_devices(self):
        """Re-scan input devices while preserving a stable native ID on Windows."""
        saved_id = self.sm.get('mic_device_id') if self.sm is not None else None
        saved_name = self.sm.get('mic_device_name') if self.sm is not None else None
        previous_name = self.mic_combo.currentText() if self.mic_combo.count() else None

        native_endpoints = []
        if sys.platform == 'win32':
            main_window = self.window()
            engine_path = getattr(main_window, 'engine_path', None)
            if engine_path and getattr(main_window, 'engine_process', None) is not None:
                try:
                    native_endpoints = list_native_microphones(engine_path)
                except RuntimeError as exc:
                    print(f'[Mic] Native endpoint scan failed: {exc}')

        legacy_indices = {}
        if _SD_AVAILABLE:
            try:
                for index, device in enumerate(_sd.query_devices()):
                    if device.get('max_input_channels', 0) > 0:
                        legacy_indices.setdefault(device['name'], []).append(index)
            except Exception as exc:
                print(f'Mic scan failed: {exc}')

        if sys.platform == 'win32' and not saved_id:
            migrated_id = migrate_legacy_microphone_name(saved_name, native_endpoints)
            if migrated_id and self.sm is not None:
                self.sm.set('mic_device_id', migrated_id)
                self.sm.save_settings()
                saved_id = migrated_id

        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        default_data = {'endpoint_id': None, 'legacy_index': None,
                        'display_name': 'System Default'}
        self.mic_combo.addItem('System Default', userData=default_data)
        if native_endpoints:
            active_ids = set()
            for endpoint in native_endpoints:
                if not endpoint.is_active:
                    continue
                active_ids.add(endpoint.endpoint_id)
                indices = legacy_indices.get(endpoint.display_name, [])
                legacy_index = indices[0] if len(indices) == 1 else None
                self.mic_combo.addItem(endpoint.display_name, userData={
                    'endpoint_id': endpoint.endpoint_id,
                    'legacy_index': legacy_index,
                    'display_name': endpoint.display_name,
                })
            # Explicit selections remain explicit even after a USB/Bluetooth
            # device disappears. Passing this ID to the engine produces a
            # clear native failure instead of silently following Default.
            if saved_id and saved_id not in active_ids:
                self.mic_combo.addItem(f'{saved_name or "Selected microphone"} (unavailable)',
                                       userData={
                                           'endpoint_id': saved_id,
                                           'legacy_index': None,
                                           'display_name': saved_name or 'Microphone',
                                       })
        elif _SD_AVAILABLE:
            # Linux remains on the legacy PortAudio path for this phase.
            for name, indices in legacy_indices.items():
                if len(indices) == 1:
                    self.mic_combo.addItem(name, userData={
                        'endpoint_id': None,
                        'legacy_index': indices[0],
                        'display_name': name,
                    })

        selected = 0
        for index in range(self.mic_combo.count()):
            data = self.mic_combo.itemData(index)
            if isinstance(data, dict) and saved_id and data.get('endpoint_id') == saved_id:
                selected = index
                break
            if (not saved_id and saved_name and isinstance(data, dict)
                    and data.get('display_name') == saved_name):
                selected = index
                break
            if not saved_id and not saved_name and previous_name == self.mic_combo.itemText(index):
                selected = index
        self.mic_combo.setCurrentIndex(selected)
        self.mic_combo.blockSignals(False)
        if not self._mic_combo_connected:
            self.mic_combo.currentIndexChanged.connect(
                self._on_mic_device_changed)
            self._mic_combo_connected = True

    def _load_audio_settings(self):
        if self.sm is None:
            return
        # Block signals so loading values doesn't trigger saves mid-load.
        self.mic_combo.blockSignals(True)
        self.mic_vol_slider.blockSignals(True)
        self.mic_loopback_check.blockSignals(True)
        self.notif_monitor_combo.blockSignals(True)
        for slider in self.sound_sliders.values():
            slider.blockSignals(True)
        try:
            endpoint_id = self.sm.get('mic_device_id')
            name = self.sm.get('mic_device_name')
            for idx in range(self.mic_combo.count()):
                data = self.mic_combo.itemData(idx)
                if isinstance(data, dict) and endpoint_id and data.get('endpoint_id') == endpoint_id:
                    self.mic_combo.setCurrentIndex(idx)
                    break
                if (isinstance(data, dict) and not endpoint_id and name
                        and data.get('display_name') == name):
                    self.mic_combo.setCurrentIndex(idx)
                    break
            vol = int(self.sm.get('mic_volume', 100))
            self.mic_vol_slider.setValue(vol)
            self.mic_vol_value.setText(f'{vol}%')
            self.mic_level_meter.set_gain(vol / 100.0)
            self.mic_loopback_check.setChecked(bool(self.sm.get('mic_loopback', False)))
            saved_mon = self.sm.get('notification_monitor', 'auto')
            idx = self.notif_monitor_combo.findData(saved_mon)
            if idx >= 0:
                self.notif_monitor_combo.setCurrentIndex(idx)
            for key, slider in self.sound_sliders.items():
                sv = int(self.sm.get(f'sound_volume_{key}', 100))
                slider.setValue(sv)
                self.sound_value_labels[key].setText(f'{sv}%')
        finally:
            self.mic_combo.blockSignals(False)
            self.mic_vol_slider.blockSignals(False)
            self.mic_loopback_check.blockSignals(False)
            self.notif_monitor_combo.blockSignals(False)
            for slider in self.sound_sliders.values():
                slider.blockSignals(False)

    def _on_notif_monitor_changed(self, _idx: int):
        if self.sm is None:
            return
        val = self.notif_monitor_combo.currentData()
        self.sm.set('notification_monitor', val)
        self.sm.save_settings()
        self.notification_monitor_changed.emit()

    def _save_audio_settings(self):
        if self.sm is None:
            return
        data = self.mic_combo.currentData()
        endpoint_id = data.get('endpoint_id') if isinstance(data, dict) else None
        display_name = data.get('display_name') if isinstance(data, dict) else None
        self.sm.set('mic_device_id', endpoint_id)
        self.sm.set('mic_device_name', display_name if endpoint_id else None)
        self.sm.set('mic_volume', self.mic_vol_slider.value())
        self.sm.set('mic_loopback', self.mic_loopback_check.isChecked())
        self.sm.save_settings()
        # Linux still owns its temporary legacy recorder. Windows applies the
        # requested endpoint/input gain only on the next engine generation.
        if sys.platform == 'win32':
            return
        try:
            from core.mic_recorder import MicRecorder
            if MicRecorder.is_available():
                rec = MicRecorder()
                rec.set_gain(self.mic_vol_slider.value() / 100.0)
                rec.start(self._selected_mic_index(),
                          gain=self.mic_vol_slider.value() / 100.0)
        except Exception as e:
            print(f'[Mic] settings push failed: {e}')

    def _selected_mic_index(self):
        data = self.mic_combo.currentData() if self.mic_combo.count() else None
        return data.get('legacy_index') if isinstance(data, dict) else data

    def _selected_mic_endpoint_id(self):
        data = self.mic_combo.currentData() if self.mic_combo.count() else None
        return data.get('endpoint_id') if isinstance(data, dict) else None

    def _on_mic_volume_changed(self, v: int):
        self.mic_vol_value.setText(f'{v}%')
        self.mic_level_meter.set_gain(v / 100.0)
        if self._loopback_stream is not None:
            # Volume is read live by the loopback callback closure
            pass
        self._save_audio_settings()

    def _on_sound_volume_changed(self, key: str, v: int):
        if self.sm is None:
            return
        self.sm.set(f'sound_volume_{key}', v)
        self.sm.save_settings()

    def _on_mic_device_changed(self, _idx: int):
        idx = self._selected_mic_index()
        # Restart meter on the new device
        self.mic_level_meter.stop()
        if self.isVisible() and self.stack.currentIndex() == 2:
            self.mic_level_meter.set_gain(self.mic_vol_slider.value() / 100.0)
            self.mic_level_meter.start(idx)
        # Restart loopback if currently on
        if self.mic_loopback_check.isChecked():
            self._stop_loopback()
            self._start_loopback(idx)
        self._save_audio_settings()
        if sys.platform == 'win32':
            # Explicit/default microphone changes intentionally create a new
            # capture generation. A running native source is never silently
            # rebound to another endpoint or clock domain.
            self.audio_capture_changed.emit(True)

    def _start_loopback(self, device_index):
        self._stop_loopback()
        if not _SD_AVAILABLE:
            return
        try:
            out_dev = None
            try:
                default_out = _sd.default.device[1]
                if default_out is not None and default_out >= 0:
                    out_dev = default_out
            except Exception:
                out_dev = None

            def _passthrough(indata, outdata, frames, time_info, status):
                vol = self.mic_vol_slider.value() / 100.0
                outdata[:] = indata * vol

            self._loopback_stream = _sd.Stream(
                device=(device_index, out_dev),
                channels=1,
                dtype='float32',
                samplerate=44100,
                blocksize=1024,
                callback=_passthrough,
            )
            self._loopback_stream.start()
        except Exception as e:
            print(f'Loopback start failed: {e}')
            self._loopback_stream = None
            self.mic_loopback_check.blockSignals(True)
            self.mic_loopback_check.setChecked(False)
            self.mic_loopback_check.blockSignals(False)

    def _stop_loopback(self):
        if self._loopback_stream is not None:
            try:
                self._loopback_stream.stop()
                self._loopback_stream.close()
            except Exception:
                pass
            self._loopback_stream = None

    def _on_loopback_toggled(self, checked: bool):
        if checked:
            self._start_loopback(self._selected_mic_index())
        else:
            self._stop_loopback()
        self._save_audio_settings()

    # Page lifecycle — start/stop the live meter as the page comes/goes
    def showEvent(self, event):
        super().showEvent(event)
        # Only run the meter when the audio sub-page is selected
        if hasattr(self, 'stack') and self.stack.currentIndex() == 2:
            self.mic_level_meter.set_gain(self.mic_vol_slider.value() / 100.0)
            self.mic_level_meter.start(self._selected_mic_index())

    def hideEvent(self, event):
        self._stop_loopback()
        if hasattr(self, 'mic_level_meter'):
            self.mic_level_meter.stop()
        super().hideEvent(event)

    def _make_visuals_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 16, 32)
        layout.setSpacing(0)

        # -- Camera Overlay --
        layout.addWidget(_flat_section_header('Camera Overlay'))
        layout.addSpacing(12)

        self.camera_check = QCheckBox('Burn camera overlay into clips')
        self.camera_check.setStyleSheet(checkbox_qss())
        self.camera_check.setChecked(self.sm.get('camera_enabled', False))
        self.camera_check.toggled.connect(self._on_camera_toggled)
        layout.addWidget(self.camera_check)
        layout.addSpacing(8)

        cam_dev_row = QHBoxLayout()
        cam_dev_row.setSpacing(8)
        _dev_lbl = QLabel('DEVICE')
        _dev_lbl.setStyleSheet(_LABEL_STYLE)
        _dev_lbl.setFixedWidth(80)
        cam_dev_row.addWidget(_dev_lbl)
        self.camera_device_combo = _DropdownCombo()
        self.camera_device_combo.setStyleSheet(_COMBO_STYLE)
        self._populate_camera_devices()
        self.camera_device_combo.currentIndexChanged.connect(self._on_camera_device_changed)
        cam_dev_row.addWidget(self.camera_device_combo, 1)
        layout.addLayout(cam_dev_row)
        layout.addSpacing(6)

        cam_pos_row = QHBoxLayout()
        cam_pos_row.setSpacing(8)
        _pos_lbl = QLabel('POSITION')
        _pos_lbl.setStyleSheet(_LABEL_STYLE)
        _pos_lbl.setFixedWidth(80)
        cam_pos_row.addWidget(_pos_lbl)
        self.camera_pos_combo = _DropdownCombo()
        self.camera_pos_combo.addItems(['Bottom Right', 'Bottom Left', 'Top Right', 'Top Left'])
        self.camera_pos_combo.setStyleSheet(_COMBO_STYLE)
        _pos_keys = ['bottom-right', 'bottom-left', 'top-right', 'top-left']
        saved_pos = self.sm.get('camera_position', 'bottom-right')
        self.camera_pos_combo.setCurrentIndex(
            _pos_keys.index(saved_pos) if saved_pos in _pos_keys else 0)
        self.camera_pos_combo.currentIndexChanged.connect(self._on_camera_pos_changed)
        cam_pos_row.addWidget(self.camera_pos_combo)

        _sz_lbl = QLabel('SIZE')
        _sz_lbl.setStyleSheet(_LABEL_STYLE)
        _sz_lbl.setFixedWidth(48)
        cam_pos_row.addWidget(_sz_lbl)
        self.camera_size_combo = _DropdownCombo()
        self.camera_size_combo.addItems(['Small', 'Medium', 'Large'])
        self.camera_size_combo.setStyleSheet(_COMBO_STYLE)
        _sz_keys = ['small', 'medium', 'large']
        saved_sz = self.sm.get('camera_size', 'medium')
        self.camera_size_combo.setCurrentIndex(
            _sz_keys.index(saved_sz) if saved_sz in _sz_keys else 1)
        self.camera_size_combo.currentIndexChanged.connect(self._on_camera_size_changed)
        cam_pos_row.addWidget(self.camera_size_combo)
        layout.addLayout(cam_pos_row)
        layout.addSpacing(8)

        self.camera_preview_lbl = QLabel('Camera disabled')
        self.camera_preview_lbl.setFixedSize(160, 90)
        self.camera_preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_preview_lbl.setStyleSheet(
            f'background: {Colors.CARD_BG}; color: {Colors.TEXT_DIM}; '
            f'border: 1px solid {Colors.BORDER};')
        layout.addWidget(self.camera_preview_lbl)
        layout.addSpacing(4)

        self._camera_preview_timer = QTimer(self)
        self._camera_preview_timer.setInterval(100)
        self._camera_preview_timer.timeout.connect(self._update_camera_preview)
        if self.sm.get('camera_enabled', False):
            self._camera_preview_timer.start()

        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(scrollbar_qss())
        scroll.setWidget(page)

        wrapper = QWidget()
        wrapper.setStyleSheet('background: transparent;')
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        wl.addWidget(scroll)
        return wrapper

    def _on_theme_applied(self):
        """Re-apply the full app stylesheet using current theme colors."""
        theme = ThemeManager()
        colors = theme.get_all_colors()
        # Patch the Colors class at runtime so all future QSS references use
        # the custom values. This is a one-time operation per Apply click.
        from ui.style import Colors as C
        for token, value in colors.items():
            if hasattr(C, token):
                setattr(C, token, value)
        # Re-apply styles for this settings page
        self._apply_styles()
        # Refresh the customize page swatches and preview
        self._customize_page._refresh_all()
        # Signal the main window to rebuild its stylesheet
        top = self.window()
        if hasattr(top, '_apply_theme'):
            top._apply_theme()

    def _make_performance_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(_flat_section_header('Video Encoding'))
        layout.addSpacing(12)

        def _row(label_text, widget):
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl = QLabel(label_text)
            lbl.setFixedWidth(140)
            lbl.setStyleSheet(_LABEL_STYLE)
            row.addWidget(lbl)
            row.addWidget(widget, 1)
            return row

        # Codec
        self.codec_combo = _DropdownCombo()
        self.codec_combo.addItems(['Auto', 'H.264', 'HEVC', 'AV1'])
        self.codec_combo.setStyleSheet(_COMBO_STYLE)
        saved_codec = self.sm.get('codec_pref', 'auto')
        self.codec_combo.setCurrentIndex(
            {'auto': 0, 'h264': 1, 'hevc': 2, 'av1': 3}.get(saved_codec, 0))
        layout.addLayout(_row('CODEC', self.codec_combo))
        layout.addSpacing(8)

        # Preset
        self.encoder_preset_combo = _DropdownCombo()
        self.encoder_preset_combo.addItems([
            'P1 — Fastest', 'P2', 'P3', 'P4 — Balanced',
            'P5', 'P6', 'P7 — Best Quality',
        ])
        self.encoder_preset_combo.setStyleSheet(_COMBO_STYLE)
        saved_preset = self.sm.get('encoder_preset', 4)
        self.encoder_preset_combo.setCurrentIndex(max(0, min(6, saved_preset - 1)))
        if not encoder_preset_supported(sys.platform):
            self.encoder_preset_combo.setEnabled(False)
            self.encoder_preset_combo.setToolTip(
                'Windows encoder presets are not configurable in this alpha.')
        layout.addLayout(_row('PRESET', self.encoder_preset_combo))
        layout.addSpacing(8)

        # Active encoder (read-only label)
        self.active_encoder_lbl = QLabel('—')
        self.active_encoder_lbl.setStyleSheet(
            label_body(Colors.ACCENT, Fonts.SIZE_BODY))
        layout.addLayout(_row('ACTIVE ENCODER', self.active_encoder_lbl))
        layout.addSpacing(12)

        # Apply button (hidden until user changes something)
        self.encoder_apply_btn = QPushButton('APPLY')
        self.encoder_apply_btn.setStyleSheet(button_primary_qss())
        self.encoder_apply_btn.setVisible(False)
        self.encoder_apply_btn.clicked.connect(self._on_encoder_apply)
        layout.addWidget(self.encoder_apply_btn)

        self.codec_combo.currentIndexChanged.connect(self._on_encoder_setting_changed)
        self.encoder_preset_combo.currentIndexChanged.connect(self._on_encoder_setting_changed)

        note_text = 'Applying restarts capture and clears the current replay history.'
        if not encoder_preset_supported(sys.platform):
            note_text += ' Windows encoder preset selection is unavailable.'
        enc_note = QLabel(note_text)
        enc_note.setStyleSheet(label_body(Colors.TEXT_DIM,
            Fonts.SIZE_SMALL if hasattr(Fonts, 'SIZE_SMALL') else Fonts.SIZE_BODY))
        layout.addWidget(enc_note)

        if sys.platform == 'win32':
            qualification = QLabel(
                'Alpha hardware qualification: NVIDIA same-adapter verified. '
                'AMD and Intel are code-ready but hardware-unverified. '
                'Hybrid/cross-adapter capture is not supported.')
            qualification.setWordWrap(True)
            qualification.setStyleSheet(
                label_body(Colors.TEXT_MUTED, Fonts.SIZE_BODY))
            layout.addWidget(qualification)

        layout.addSpacing(28)
        layout.addWidget(_settings_hsep())
        layout.addSpacing(20)

        layout.addWidget(_flat_section_header('Capture Buffer'))
        layout.addSpacing(12)

        buf_note = QLabel(
            'Buffer length is set via the top bar capture button.\n'
            'A larger buffer uses more RAM but lets you save longer clips.'
        )
        buf_note.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        layout.addWidget(buf_note)

        layout.addStretch()
        return page

    def _on_encoder_setting_changed(self, _idx: int):
        self.encoder_apply_btn.setVisible(True)

    def _on_encoder_apply(self):
        codec_map = {0: 'auto', 1: 'h264', 2: 'hevc', 3: 'av1'}
        codec  = codec_map.get(self.codec_combo.currentIndex(), 'auto')
        preset = self.encoder_preset_combo.currentIndex() + 1
        if not encoder_preset_supported(sys.platform):
            preset = int(self.sm.get('encoder_preset', 4))
        self.sm.set('codec_pref',     codec)
        self.sm.set('encoder_preset', preset)
        self.sm.save_settings()
        self.encoder_apply_btn.setVisible(False)
        self.encoder_config_changed.emit()

    def _make_version_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(_flat_section_header('Current Installation'))
        layout.addSpacing(12)

        # Must match installer_windows.iss / build_linux.sh / setApplicationVersion.
        # Bug reports quoting "v1.0.0" were ambiguous about which build was meant.
        ver_lbl = QLabel(f'FTHR Clips v{QApplication.applicationVersion()}')
        ver_lbl.setStyleSheet(label_display(Colors.TEXT, Fonts.SIZE_H3, 3))
        layout.addWidget(ver_lbl)

        # LGPL/GPL components are bundled; the notices must be reachable from
        # inside the app, not only from the repository.
        # FTHR's source stays MIT; bundled PySide6/Qt and media components keep
        # their own licences, documented in the installed notices.
        lic_lbl = QLabel(
            f'FTHR Clips source: {SOURCE_LICENSE} · this build as distributed: '
            f'{DISTRIBUTION_LICENSE} — bundled components: see '
            f'THIRD_PARTY_NOTICES.md in the install folder')
        lic_lbl.setWordWrap(True)
        lic_lbl.setStyleSheet(label_body(Colors.TEXT_MUTED, Fonts.SIZE_BODY))
        layout.addWidget(lic_lbl)
        layout.addSpacing(4)

        build_lbl = QLabel(f'Build: {BUILD_DATE}')
        build_lbl.setStyleSheet(label_body(Colors.TEXT_DIM, Fonts.SIZE_BODY))
        layout.addWidget(build_lbl)
        layout.addSpacing(24)

        layout.addWidget(_settings_hsep())
        layout.addSpacing(20)

        layout.addWidget(_flat_section_header('Available Versions'))
        layout.addSpacing(12)

        update_lbl = QLabel(
            'Updates: Manual\nAutomatic update checks are not available yet.')
        update_lbl.setStyleSheet(label_body(Colors.TEXT_MUTED, Fonts.SIZE_BODY_L))
        layout.addWidget(update_lbl)

        layout.addStretch()
        return page

    def _apply_styles(self):
        self.setStyleSheet(f'''
            QWidget#settingsPage    {{ background-color: {Colors.BG}; }}
            QWidget#settingsContent {{ background-color: {Colors.BG}; }}

            QFrame#settingsTabBar {{
                background-color: {Colors.SHELL_BG};
                border-bottom: 1px solid {Colors.SHELL_DIVIDER};
            }}

            QFrame#settingsDivider {{
                background-color: {Colors.SHELL_DIVIDER};
                border: none;
            }}

            QToolButton#settingsTabBtn {{
                background-color: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                color: {Colors.TEXT_DIM};
                font-size: {Fonts.SIZE_LABEL}px;
                font-family: {Fonts.DISPLAY};
                letter-spacing: {Fonts.TRACK_LABEL}px;
                font-weight: bold;
                padding: 14px 20px 10px 20px;
                min-width: 72px;
            }}
            QToolButton#settingsTabBtn:hover {{
                background-color: {Colors.SURFACE_3};
                color: {Colors.TEXT};
            }}
            QToolButton#settingsTabBtn:checked {{
                color: {Colors.ACCENT};
                border-bottom: 2px solid {Colors.ACCENT};
                background-color: transparent;
            }}

            QPushButton#micRefreshBtn {{
                background-color: {Colors.SURFACE_2};
                border: {Sizes.BORDER_W}px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.TEXT};
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#micRefreshBtn:hover {{
                border-color: {Colors.ACCENT};
                color: {Colors.ACCENT};
            }}

            {checkbox_qss()}
            {combo_qss()}
            {slider_qss()}

            QLabel {{
                color: {Colors.TEXT};
                font-size: {Fonts.SIZE_BODY_L}px;
                font-family: {Fonts.BODY};
                background: transparent;
            }}

            /* -- Accordion sections (Customize tab) -- */
            QFrame#accordionSection {{
                background-color: {Colors.SURFACE_1};
                border: 1px solid {Colors.BORDER};
            }}
            QPushButton#accordionHeader {{
                background-color: transparent;
                border: none;
                border-bottom: 1px solid {Colors.BORDER};
                text-align: left;
            }}
            QPushButton#accordionHeader:hover {{
                background-color: {Colors.SURFACE_3};
            }}
            QWidget#accordionBody {{
                background-color: {Colors.SURFACE_1};
            }}
            QFrame#colorPreview {{
                background-color: {Colors.BG};
                border: 1px solid {Colors.BORDER};
            }}
        ''')


# ===========================================================================
# Entry point
# ===========================================================================

def _load_fonts():
    """Register bundled fonts with Qt. Falls back silently if files missing."""
    fonts_dir = Path(__file__).parent / 'assets' / 'fonts'
    for ttf in fonts_dir.glob('*.ttf'):
        fid = QFontDatabase.addApplicationFont(str(ttf))
        if fid >= 0:
            families = QFontDatabase.applicationFontFamilies(fid)
            print(f"Font loaded: {ttf.name} -> {families}")
        else:
            print(f"Font failed to load: {ttf.name}")


def _prewarm_heavy_modules():
    """Eagerly initialize heavy libs that the editor would otherwise pay for
    on the first clip open.

    On first use, each of these triggers expensive one-time work:
      - cv2: loads the OpenCV C++ extension and FFmpeg demux backend.
      - QMediaPlayer: spins up Windows MediaFoundation and the H.264 codec.
      - ffmpeg_tools: locates the bundled ffmpeg binary on disk.

    Doing this at app startup (where the splash hides the latency) makes the
    first ClipViewer open feel instantaneous. None of these are user-visible
    in the warming step — the QMediaPlayer is constructed and immediately
    deleted, just to pay the MediaFoundation initialization cost once.
    """
    try:
        import cv2  # noqa: F401  — import alone is enough to load the .pyd
        # Touch a method so any lazy module-level init also runs.
        _ = cv2.__version__
    except Exception as e:
        print(f'[Prewarm] cv2 unavailable: {e}')

    try:
        from core.ffmpeg_tools import get_ffmpeg_exe as _warm_ffmpeg
        _warm_ffmpeg()
    except Exception as e:
        print(f'[Prewarm] ffmpeg unavailable: {e}')

    if sys.platform == 'win32':
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            # Pays the one-time cost of MediaFoundation init on Windows.
            _warm_player = QMediaPlayer()
            _warm_audio  = QAudioOutput()
            _warm_player.setAudioOutput(_warm_audio)
        except Exception as e:
            print(f'[Prewarm] Qt multimedia init failed: {e}')


def main():
    # When the frozen Windows exe is relaunched as the capture-card subprocess,
    # route into the card process instead of the main application.
    if '--card-process' in sys.argv:
        from ui.capture_card_process import main as _card_main
        _card_main()
        return

    background_start = '--background' in sys.argv
    print(f'Main.py successfully initiated background={background_start}')

    # Which external helpers resolved to what, and from which PATH. Bug reports
    # saying "screenshots don't work" used to arrive with nothing to go on.
    if sys.platform != 'win32':
        print(linux_tools.report())

    # A second instance is destructive, not just redundant: two capture
    # engines fight over NVENC and over the single-writer shared-memory
    # command fields. Refuse before anything is started, but ask the owner to
    # restore its existing window so a normal second launch feels native.
    from core.single_instance import SingleInstance
    instance_guard = SingleInstance()
    if not instance_guard.acquire():
        print('[FTHR] Another instance is already running — exiting.')
        configure_qt_for_linux_ui()
        _app = QApplication(sys.argv)
        from core.instance_activation import request_existing_instance_activation
        if request_existing_instance_activation():
            print('[Lifecycle] ExistingInstanceActivated')
            return 0
        # The owner may be starting up or its local activation endpoint may
        # have failed. Do not start a competing capture engine; retain a clear
        # fallback explanation instead.
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            None,
            'FTHR Clips is already running',
            'FTHR Clips is already open.\n\n'
            'Look for the window on your other monitors or in the system tray. '
            'If you believe this is wrong, end the running FTHRClips process '
            'and start it again.',
        )
        return 1

    configure_qt_for_linux_ui()
    app = QApplication(sys.argv)
    apply_app_style(app)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    app_icon = Path(__file__).parent / 'assets' / 'fthr_logo.ico'
    if app_icon.exists():
        app.setWindowIcon(QIcon(str(app_icon)))

    _load_fonts()
    _prewarm_heavy_modules()

    from core.instance_activation import InstanceActivationServer
    activation_server = InstanceActivationServer(parent=app)
    activation_server.start()

    window = MainWindow(background_start=background_start)
    activation_server.activation_requested.connect(window.restore_main_window)
    if not background_start:
        window.showMaximized()
    else:
        print('[Lifecycle] BackgroundStartup')

    try:
        return app.exec()
    finally:
        window._perform_full_shutdown()
        activation_server.stop()
        instance_guard.release()


if __name__ == '__main__':
    sys.exit(main())

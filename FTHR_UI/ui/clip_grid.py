# clip_grid.py - responsive clip and screenshot library grid.
#
# Big picture: this scans a few folders for video/image files, groups them into
# "TUE, APR 28" style date sections, and lays each section out as a responsive
# grid of cards. The number of columns recalculates from the window width.
#
# The primary constraint is to avoid blocking the main thread. Decoding a video
# frame for a thumbnail with cv2 is slow (20-100ms each), and statting hundreds
# of files isn't free either. So:
#   - file scanning + sorting happens on a worker thread (_FileCollectWorker)
#   - thumbnail generation happens on a worker thread (_ThumbnailWorker)
#   - thumbnails + duration are cached to disk so we only pay that cost once
# Unexpected scroll stalls should be checked for decoding or filesystem work
# that has moved back onto the main thread.
import os
import sys
import subprocess
from core import linux_tools
import hashlib
from pathlib import Path
import cv2
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout,
    QGraphicsOpacityEffect, QMenu, QApplication,
    QPushButton, QSizePolicy,
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QRunnable, QThreadPool, QObject,
    QFileSystemWatcher, QPropertyAnimation, QEasingCurve, QRect, QPoint,
    QUrl,
)
from PySide6.QtGui import QDesktopServices, QPixmap, QPainter, QColor

from core.clip_files import (
    IMAGE_SUFFIXES,
    VIDEO_SUFFIXES,
    is_completed_video_path,
    is_fthr_temporary_dir,
    is_library_media_path,
)
from core.library_ownership import MediaOwnership, classify_media_path
from core.media_metadata import probe_video_metadata
from core.settings_manager import clips_directory_from
from ui.style import WheelSafeComboBox, paint_dropdown_arrow


class _DropdownCombo(WheelSafeComboBox):
    """QComboBox that shows icons/dropdown.png as its arrow, rotated when open."""

    _custom_arrow_managed = True

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_open = False

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
        p = QPainter(self)
        paint_dropdown_arrow(p, self.rect(), self._popup_open)
        p.end()

from ui.style import (
    Colors, Fonts, Sizes,
    label_display,
    context_menu_qss, combo_qss,
)
from ui.dialogs import FthrMessageDialog


THUMB_CACHE_DIR = os.path.join(os.path.expanduser('~'), '.fthr', 'thumbnails')

# These folders hold processed/shared clips — not shown in the main library grid
_GRID_EXCLUDED_DIRS = {'Exported', 'Shared'}
_VIDEO_EXTS = VIDEO_SUFFIXES
_IMAGE_EXTS = IMAGE_SUFFIXES


def _rgba(hex_color: str, alpha: int) -> str:
    """Turn a theme hex token into a QSS rgba color with the given alpha."""
    color = QColor(hex_color)
    return f'rgba({color.red()},{color.green()},{color.blue()},{alpha})'


def _is_grid_excluded_dir(path: str) -> bool:
    """Keep app-owned post-processing directories out of the library scan."""

    name = os.path.basename(os.fspath(path))
    return name in _GRID_EXCLUDED_DIRS or is_fthr_temporary_dir(name)


def _show_in_file_manager(file_path: str) -> None:
    """Open the containing folder and highlight ``file_path`` when possible."""

    path = os.path.abspath(os.path.normpath(os.fspath(file_path)))
    if sys.platform == 'win32':
        # Explorer expects /select,"path" as a single command-line expression.
        # Passing the switch as a list argument makes subprocess quote the whole
        # /select expression, which causes Explorer to ignore the selection and
        # only open its default location.
        subprocess.Popen(f'explorer.exe /select,"{path}"')
        return

    _opener = linux_tools.path('xdg-open')
    if _opener:
        subprocess.Popen([_opener, os.path.dirname(path)])
    else:
        print(f'[Clips] {linux_tools.missing_message("xdg-open")}')


# ─── Card geometry ──────────────────────────────────────────────────
# Cards flex with the viewport. The thumbnail *surface* remains 16:9, while
# source pixels are fitted inside it without cropping or distortion.
_CARD_W      = 320  # preferred / cache sizing reference
_CARD_MIN_W  = 240
_CARD_MAX_W  = 420
_THUMB_H     = 180
_CARD_BODY_H = 104
_CARD_H      = _THUMB_H + _CARD_BODY_H


def _get_cached_thumb_path(file_path: str) -> str:
    """Return a cache path based on file path + mtime so stale caches auto-invalidate.

    Including mtime in the hash invalidates the cache automatically when a file
    is overwritten without requiring a separate cache index.
    """
    mtime = os.path.getmtime(file_path)
    # v2 caches preserve the source aspect ratio.  Including the cache format
    # here prevents an older, force-stretched 16:9 thumbnail from surviving an
    # application update.
    key = f'{file_path}|{mtime}|aspect-v2'.encode(
        'utf-8', errors='surrogateescape')
    return os.path.join(THUMB_CACHE_DIR, hashlib.md5(key).hexdigest() + '.jpg')


def _get_cached_duration_path(thumb_path: str) -> str:
    """Sidecar storing authoritative clip metadata for thumbnail/viewer reuse.

    The v2 filename deliberately invalidates old OpenCV-derived FPS caches.
    Format: ``duration width height fps video_bitrate total_bitrate``.
    """
    return thumb_path[:-4] + '.meta-v2'


def _read_cached_duration(dur_path: str) -> int:
    """Backwards-compatible reader that returns just the duration."""
    meta = read_cached_metadata(dur_path)
    return int(meta[0]) if meta else 0


def read_cached_metadata(dur_path: str):
    """Return factual cached metadata, or ``None`` when unavailable."""
    try:
        with open(dur_path, 'r') as f:
            parts = f.read().strip().split()
        if not parts:
            return None
        duration = float(parts[0])
        width    = int(parts[1]) if len(parts) > 1 else 0
        height   = int(parts[2]) if len(parts) > 2 else 0
        fps      = float(parts[3]) if len(parts) > 3 else 0.0
        video_bitrate = int(parts[4]) if len(parts) > 4 else 0
        total_bitrate = int(parts[5]) if len(parts) > 5 else 0
        return (duration, width, height, fps,
                video_bitrate, total_bitrate)
    except (OSError, ValueError):
        return None


def _write_cached_duration(dur_path: str, duration: float,
                            width: int = 0, height: int = 0, fps: float = 0.0,
                            video_bitrate: int = 0,
                            total_bitrate: int = 0):
    """Persist factual FFprobe metadata so ClipViewer can skip a second probe."""
    try:
        with open(dur_path, 'w') as f:
            f.write(
                f'{float(duration):.6f} {int(width)} {int(height)} '
                f'{float(fps):.6f} {int(video_bitrate)} {int(total_bitrate)}')
    except OSError:
        pass


# Public lookup used by ClipViewer to avoid blocking cv2.VideoCapture on first
# open. Returns (duration_sec, width, height, fps) or None on any miss / error.
def get_cached_clip_metadata(file_path: str):
    try:
        cache_path = _get_cached_thumb_path(file_path)
    except OSError:
        return None
    return read_cached_metadata(_get_cached_duration_path(cache_path))


def _humanize_relative(ts: float) -> str:
    """Turn a file mtime into a 'X days ago' / 'just now' style string."""
    now = datetime.now()
    when = datetime.fromtimestamp(ts)
    delta = now - when
    secs = int(delta.total_seconds())
    if secs < 60:
        return 'just now'
    mins = secs // 60
    if mins < 60:
        return f'{mins} min ago'
    hours = mins // 60
    if hours < 24:
        return f'{hours} hr ago'
    days = hours // 24
    if days == 1:
        return 'yesterday'
    if days < 7:
        return f'{days} days ago'
    weeks = days // 7
    if weeks < 5:
        return f'{weeks} wk ago'
    months = days // 30
    if months < 12:
        return f'{months} mo ago'
    return f'{days // 365} yr ago'


def _section_label_for(ts: float) -> str:
    """Group label like 'TUE, APR 28' for grouping cards under date headers."""
    return datetime.fromtimestamp(ts).strftime('%a, %b %d').upper()


def _game_name_from_path(file_path: str, clips_root: str | None = None) -> str:
    """Best-effort game / source name from the parent folder."""
    parent = os.path.basename(os.path.dirname(file_path))
    root_name = os.path.basename(os.path.normpath(
        clips_root or os.path.expanduser('~/FTHR_Clips')))
    if parent in (root_name, ''):
        return 'DESKTOP'
    return parent.upper()


def _clip_title_from_filename(file_path: str) -> str:
    """Friendly title — strip the timestamp tail from the filename."""
    base = os.path.splitext(os.path.basename(file_path))[0]
    # Drop trailing pattern like  *_clip_from_28Apr2026_22-30
    for suffix_marker in ('_clip_from_', '_screenshot_from_', '_from_'):
        idx = base.find(suffix_marker)
        if idx > 0:
            base = base[:idx]
            break
    base = base.replace('_', ' ').replace('-', ' ').strip()
    return base[:1].upper() + base[1:] if base else 'Clip'


class _ThumbnailSignals(QObject):
    finished = Signal(str, str, int)  # file_path, cache_path, duration_sec


class _ThumbnailWorker(QRunnable):
    """Generate and cache a video thumbnail off the main thread."""

    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.signals = _ThumbnailSignals()

    def run(self):
        if not is_completed_video_path(self.file_path):
            self.signals.finished.emit(self.file_path, '', 0)
            return
        cache_path = _get_cached_thumb_path(self.file_path)
        dur_path   = _get_cached_duration_path(cache_path)

        # Cache hit: image AND duration sidecar both exist.
        # This skips opening the video file entirely (cv2.VideoCapture +
        # FFmpeg demux is the expensive part — typically 20-100ms per file).
        if os.path.exists(cache_path) and os.path.exists(dur_path):
            duration = _read_cached_duration(dur_path)
            self.signals.finished.emit(self.file_path, cache_path, duration)
            return

        cap = cv2.VideoCapture(self.file_path)
        if not cap.isOpened():
            self.signals.finished.emit(self.file_path, '', 0)
            return

        ret, frame = cap.read()
        decoder_fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        decoder_duration = frames / decoder_fps if decoder_fps > 0 else 0.0
        cap.release()

        probed = probe_video_metadata(self.file_path)
        duration = (probed.duration_seconds
                    if probed and probed.duration_seconds is not None
                    else decoder_duration)
        width = probed.width if probed and probed.width else width
        height = probed.height if probed and probed.height else height
        # Never persist CAP_PROP_FPS as factual media metadata. It is commonly
        # reconstructed from approximate frame counts/timestamps.
        fps = probed.average_fps if probed and probed.average_fps else 0.0
        video_bitrate = (
            probed.video_bitrate_bps if probed and probed.video_bitrate_bps else 0)
        total_bitrate = (
            probed.total_bitrate_bps if probed and probed.total_bitrate_bps else 0)

        if not ret or frame is None:
            self.signals.finished.emit(self.file_path, '', int(duration))
            return

        os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
        if not os.path.exists(cache_path):
            # Cache the real aspect ratio. The old forced 320×180 resize baked
            # distortion into portrait, ultrawide, and cropped thumbnails even
            # before Qt displayed them.
            source_h, source_w = frame.shape[:2]
            scale = min(1.0, 640 / max(source_w, 1), 360 / max(source_h, 1))
            if scale < 1.0:
                thumb = cv2.resize(
                    frame,
                    (max(1, int(source_w * scale)), max(1, int(source_h * scale))),
                    interpolation=cv2.INTER_AREA)
            else:
                thumb = frame
            cv2.imwrite(cache_path, thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
        # Persist full metadata so ClipViewer can skip its own cv2.VideoCapture
        # on subsequent opens — this is what removes the first-launch lag for
        # clips that already appear on the grid.
        _write_cached_duration(
            dur_path, duration, width, height, fps,
            video_bitrate, total_bitrate)
        self.signals.finished.emit(
            self.file_path, cache_path, int(duration))


class _FileCollectSignals(QObject):
    # raw_files: set[str], sorted_pairs: list[(mtime, path)], imported: set[str], subdirs: list[str]
    finished = Signal(object, object, object, object)


class _FileCollectWorker(QRunnable):
    """Scans folders, applies filter, sorts by mtime — all off the main thread."""

    def __init__(self, clips_dir: str, import_dirs: list,
                 filter_: str, sort_: str):
        super().__init__()
        self.clips_dir   = clips_dir
        self.import_dirs = import_dirs
        self.filter_     = filter_
        self.sort_       = sort_
        self.signals     = _FileCollectSignals()

    def run(self):
        found:    set  = set()
        imported: set  = set()
        subdirs:  list = []

        try:
            entries = os.listdir(self.clips_dir)
        except OSError:
            entries = []
        for name in entries:
            full = os.path.join(self.clips_dir, name)
            if os.path.isfile(full) and is_library_media_path(name):
                found.add(full)
            elif os.path.isdir(full) and not _is_grid_excluded_dir(name):
                subdirs.append(full)
                try:
                    for sub in os.listdir(full):
                        sf = os.path.join(full, sub)
                        if os.path.isfile(sf) and is_library_media_path(sub):
                            found.add(sf)
                except OSError:
                    pass

        for folder in self.import_dirs:
            if not os.path.isdir(folder):
                continue
            subdirs.append(folder)
            try:
                for name in os.listdir(folder):
                    full = os.path.join(folder, name)
                    if os.path.isfile(full) and is_library_media_path(name):
                        found.add(full)
                        imported.add(full)
                    elif os.path.isdir(full) and not _is_grid_excluded_dir(full):
                        subdirs.append(full)
                        try:
                            for sub in os.listdir(full):
                                sf = os.path.join(full, sub)
                                if os.path.isfile(sf) and is_library_media_path(sub):
                                    found.add(sf)
                                    imported.add(sf)
                        except OSError:
                            pass
            except OSError:
                pass

        files: list = list(found)
        if self.filter_ == 'clips':
            files = [f for f in files if is_completed_video_path(f)]
        elif self.filter_ == 'screenshots':
            files = [f for f in files if f.lower().endswith(_IMAGE_EXTS)]
        elif self.filter_ == 'imported':
            files = [f for f in files if f in imported]

        # Bulk-stat here (off main thread) so the main thread never calls getmtime
        mtimes = {}
        for f in files:
            try:
                mtimes[f] = os.path.getmtime(f)
            except OSError:
                mtimes[f] = 0.0
        files.sort(key=lambda f: mtimes.get(f, 0.0), reverse=(self.sort_ != 'oldest'))
        pairs = [(mtimes.get(f, 0.0), f) for f in files]

        self.signals.finished.emit(found, pairs, imported, subdirs)


# ──────────────────────────────────────────────────────────────────────
# ClipThumbnail — Medal-style card: thumb on top, info row below
# ──────────────────────────────────────────────────────────────────────

class ClipThumbnail(QFrame):
    clicked          = Signal(str)
    opened           = Signal(str, QPixmap, QRect)   # video-only: path, thumb, global card rect
    deleted          = Signal(str)
    upload_requested = Signal(str)

    def __init__(self, file_path: str, is_video: bool = True, imported: bool = False,
                  upload_enabled: bool = False, uploaded: bool = False,
                  upload_info: dict | None = None, ready: bool = True,
                  card_width: int = _CARD_W, parent=None,
                  clips_root: str | None = None):
        super().__init__(parent)
        self.file_path      = file_path
        self.is_video       = is_video
        self.imported       = imported
        self.upload_enabled = upload_enabled
        self.uploaded       = uploaded
        self.upload_info    = dict(upload_info or {})
        self._clips_root    = clips_root or os.path.expanduser('~/FTHR_Clips')
        self.upload_link    = ''
        self.ready          = ready
        # A cold thumbnail decode also produces the metadata used by the
        # editor.  Do not let a click race that worker and force ClipViewer
        # back onto its synchronous OpenCV fallback on the UI thread.
        self._thumbnail_ready = not is_video
        self._open_pending = False
        self._card_width = max(_CARD_MIN_W, min(_CARD_MAX_W, int(card_width)))
        self._thumb_height = max(1, round(self._card_width * 9 / 16))
        self._thumb_pixmap = QPixmap()
        self.setObjectName('clipCard')
        self.setFixedSize(
            self._card_width, self._thumb_height + _CARD_BODY_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._fade_anim: QPropertyAnimation | None = None
        self._setup_ui()
        if not self.ready:
            self.share_btn.setEnabled(False)
            self.menu_btn.setEnabled(False)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if not is_video:
            self._load_image_thumbnail()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Thumbnail container ───────────────────────────────────────────
        thumb = QFrame(self)
        self._thumb_frame = thumb
        thumb.setObjectName('cardThumb')
        thumb.setFixedSize(self._card_width, self._thumb_height)

        self.thumb_label = QLabel(thumb)
        self.thumb_label.setGeometry(0, 0, self._card_width, self._thumb_height)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setScaledContents(False)
        self.thumb_label.setStyleSheet('background: transparent; border: none;')

        # Center play glyph (video only)
        if self.is_video:
            self._play_icon = QLabel('▶', thumb)
            self._play_icon.setGeometry(
                0, 0, self._card_width, self._thumb_height)
            self._play_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._play_icon.setStyleSheet(
                f'color: {_rgba(Colors.TEXT, 210)}; font-size: 36px;'
                ' background: transparent; border: none;'
            )
            self._play_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._play_icon.setVisible(False)

        # Top-right duration badge
        if self.is_video:
            self.duration_label = QLabel('0:00', thumb)
            self.duration_label.setObjectName('cardDurationBadge')
            self.duration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.duration_label.setStyleSheet(
                f'QLabel#cardDurationBadge {{'
                f' color: {Colors.TEXT};'
                f' background: {_rgba(Colors.SURFACE_1, 170)};'
                f' border-radius: 0px; padding: 2px 9px;'
                f' font-family: {Fonts.BODY}; font-size: {Fonts.SIZE_LABEL}px;'
                f' font-weight: bold; letter-spacing: 1px;'
                f'}}'
            )
            self.duration_label.adjustSize()
            self.duration_label.move(
                self._card_width - self.duration_label.width() - 10, 10)

        # Bottom-left imported badge (only for clips from imported folders)
        self._imported_badge_h = 0
        self._imported_badge = None
        if self.imported:
            imp = QLabel('IMPORTED', thumb)
            self._imported_badge = imp
            imp.setObjectName('cardImportedBadge')
            imp.setStyleSheet(
                f'QLabel#cardImportedBadge {{'
                f' color: {Colors.ACCENT};'
                f' background: {_rgba(Colors.SURFACE_1, 180)};'
                f' border-radius: 0px; padding: 2px 7px;'
                f' font-family: {Fonts.DISPLAY}; font-size: {Fonts.SIZE_MICRO}px;'
                f' font-weight: bold; letter-spacing: 2px;'
                f'}}'
            )
            imp.adjustSize()
            imp.move(8, self._thumb_height - imp.height() - 8)
            imp.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._imported_badge_h = imp.height() + 4   # used to stack UPLOADED above it

        self._finalizing_badge = None
        if not self.ready:
            finalizing = QLabel('FINALIZING', thumb)
            self._finalizing_badge = finalizing
            finalizing.setStyleSheet(
                f'color: {Colors.TEXT}; '
                f'background: {_rgba(Colors.WARNING, 220)}; '
                f'font-family: {Fonts.DISPLAY}; '
                f'font-size: {Fonts.SIZE_MICRO}px; '
                'padding: 3px 7px; font-weight: bold;')
            finalizing.adjustSize()
            finalizing.move(8, 8)

        # "UPLOADED" badge — pre-created, shown/hidden dynamically
        self._upload_badge = QLabel('UPLOADED', thumb)
        self._upload_badge.setObjectName('cardUploadedBadge')
        self._upload_badge.setStyleSheet(
            f'QLabel#cardUploadedBadge {{'
            f' color: {Colors.BG};'
            f' background: {_rgba(Colors.SUCCESS, 210)};'
            f' border-radius: 0px; padding: 2px 7px;'
            f' font-family: {Fonts.DISPLAY}; font-size: {Fonts.SIZE_MICRO}px;'
            f' font-weight: bold; letter-spacing: 2px;'
            f'}}'
        )
        self._upload_badge.adjustSize()
        _badge_bottom = self._thumb_height - 8 - self._imported_badge_h
        self._upload_badge.move(8, _badge_bottom - self._upload_badge.height())
        self._upload_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._upload_badge.setVisible(self.uploaded)

        layout.addWidget(thumb)

        # ── Card body (game / title / share+menu / time-ago) ─────────────
        body = QFrame(self)
        body.setObjectName('cardBody')
        self._body_frame = body
        body.setFixedSize(self._card_width, _CARD_BODY_H)

        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 8, 8, 8)
        bl.setSpacing(2)

        # Top row: game name (small dim) + share + menu
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.game_label = QLabel(
            _game_name_from_path(self.file_path, self._clips_root))
        self.game_label.setObjectName('cardGame')
        top_row.addWidget(self.game_label)
        top_row.addStretch(1)

        self.share_btn = QPushButton('⤴')
        self.share_btn.setObjectName('cardIconBtn')
        self.share_btn.setFixedSize(22, 22)
        self.share_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.share_btn.setToolTip('Share')
        self.share_btn.clicked.connect(self._on_share_click)
        top_row.addWidget(self.share_btn)

        self.menu_btn = QPushButton('⋯')
        self.menu_btn.setObjectName('cardIconBtn')
        self.menu_btn.setFixedSize(22, 22)
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_btn.clicked.connect(self._show_menu)
        top_row.addWidget(self.menu_btn)
        bl.addLayout(top_row)

        # Title row
        self.title_label = QLabel(_clip_title_from_filename(self.file_path))
        self.title_label.setObjectName('cardTitle')
        self.title_label.setWordWrap(False)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bl.addWidget(self.title_label)

        # Time-ago row
        try:
            mtime = os.path.getmtime(self.file_path)
        except OSError:
            mtime = 0.0
        self.time_label = QLabel(_humanize_relative(mtime))
        self.time_label.setObjectName('cardTime')
        bl.addWidget(self.time_label)

        # Returned provider links are the useful post-upload action. Keep the
        # action row out of the card entirely until history has a valid URL.
        bl.addSpacing(3)
        self._link_actions = QWidget(body)
        link_row = QHBoxLayout(self._link_actions)
        link_row.setContentsMargins(0, 0, 0, 0)
        link_row.setSpacing(5)

        self.copy_link_btn = QPushButton('COPY LINK')
        self.copy_link_btn.setObjectName('cardLinkBtn')
        self.copy_link_btn.setFixedHeight(22)
        self.copy_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_link_btn.setToolTip('Copy the uploaded file link')
        self.copy_link_btn.clicked.connect(self._copy_upload_link)
        link_row.addWidget(self.copy_link_btn, 1)

        self.open_link_btn = QPushButton('OPEN LINK')
        self.open_link_btn.setObjectName('cardLinkBtn')
        self.open_link_btn.setFixedHeight(22)
        self.open_link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_link_btn.setToolTip('Open the uploaded file link')
        self.open_link_btn.clicked.connect(self._open_upload_link)
        link_row.addWidget(self.open_link_btn, 1)
        bl.addWidget(self._link_actions)

        layout.addWidget(body)

        self.setStyleSheet(self._card_qss())
        self.set_upload_info(self.upload_info)

    @staticmethod
    def _card_qss() -> str:
        return f'''
            QFrame#clipCard {{
                background-color: {Colors.CARD_BG};
                border: 1px solid {Colors.CARD_BORDER};
                border-radius: {Sizes.RADIUS_CARD}px;
            }}
            QFrame#clipCard:hover {{
                background-color: {Colors.CARD_BG_HI};
                border-color: {Colors.BORDER_HI};
            }}
            QFrame#cardThumb {{
                background-color: {Colors.SURFACE_3};
                border: none;
                border-top-left-radius: {Sizes.RADIUS_CARD}px;
                border-top-right-radius: {Sizes.RADIUS_CARD}px;
            }}
            QFrame#cardBody {{
                background-color: transparent;
                border: none;
            }}
            QLabel#cardGame {{
                color: {Colors.TEXT_DIM};
                font-family: {Fonts.DISPLAY};
                font-size: {Fonts.SIZE_MICRO}px;
                font-weight: bold;
                letter-spacing: 2px;
                background: transparent;
            }}
            QLabel#cardTitle {{
                color: {Colors.TEXT};
                font-family: {Fonts.BODY};
                font-size: {Fonts.SIZE_BODY_L}px;
                font-weight: bold;
                background: transparent;
            }}
            QLabel#cardTime {{
                color: {Colors.TEXT_MUTED};
                font-family: {Fonts.BODY};
                font-size: {Fonts.SIZE_LABEL}px;
                background: transparent;
            }}
            QPushButton#cardIconBtn {{
                background: transparent;
                border: none;
                color: {Colors.TEXT_DIM};
                font-size: 16px;
                font-weight: bold;
            }}
            QPushButton#cardIconBtn:hover {{
                color: {Colors.ACCENT};
            }}
            QPushButton#cardLinkBtn {{
                background-color: {Colors.SURFACE_2};
                border: 1px solid {Colors.BORDER};
                color: {Colors.TEXT_DIM};
                font-family: {Fonts.DISPLAY};
                font-size: {Fonts.SIZE_MICRO}px;
                font-weight: bold;
                letter-spacing: 1px;
                padding: 0 4px;
            }}
            QPushButton#cardLinkBtn:hover {{
                background-color: {Colors.SURFACE_3};
                border-color: {Colors.ACCENT};
                color: {Colors.ACCENT};
            }}
            QPushButton#cardLinkBtn:disabled {{
                background-color: transparent;
                border-color: {Colors.CARD_BORDER};
                color: {Colors.TEXT_MUTED};
            }}
        '''

    def set_uploaded(self, val: bool):
        self.uploaded = val
        self._upload_badge.setVisible(val)

    @staticmethod
    def _upload_url(info: dict | None) -> str:
        if not isinstance(info, dict):
            return ''
        for key in ('url', 'raw_url'):
            value = str(info.get(key, '') or '').strip()
            if value.startswith(('https://', 'http://')):
                return value
        return ''

    def set_upload_info(self, info: dict | None):
        """Update the link actions from the provider's persisted upload result."""
        self.upload_info = dict(info or {})
        self.upload_link = self._upload_url(self.upload_info)
        enabled = bool(self.upload_link)
        self._link_actions.setVisible(enabled)
        self.copy_link_btn.setEnabled(enabled)
        self.open_link_btn.setEnabled(enabled)

    def _copy_upload_link(self):
        if self.upload_link:
            QApplication.clipboard().setText(self.upload_link)

    def _open_upload_link(self):
        if self.upload_link:
            QDesktopServices.openUrl(QUrl(self.upload_link))

    def resize_card(self, width: int):
        """Resize a card without recreating it or distorting its media."""
        width = max(_CARD_MIN_W, min(_CARD_MAX_W, int(width)))
        if width == self._card_width:
            return
        self._card_width = width
        self._thumb_height = max(1, round(width * 9 / 16))
        self.setFixedSize(width, self._thumb_height + _CARD_BODY_H)
        self._thumb_frame.setFixedSize(width, self._thumb_height)
        self.thumb_label.setGeometry(0, 0, width, self._thumb_height)
        self._body_frame.setFixedSize(width, _CARD_BODY_H)
        if hasattr(self, '_play_icon'):
            self._play_icon.setGeometry(0, 0, width, self._thumb_height)
        if hasattr(self, 'duration_label'):
            self.duration_label.move(
                width - self.duration_label.width() - 10, 10)
        if self._imported_badge is not None:
            self._imported_badge.move(
                8, self._thumb_height - self._imported_badge.height() - 8)
        badge_bottom = self._thumb_height - 8 - self._imported_badge_h
        self._upload_badge.move(
            8, badge_bottom - self._upload_badge.height())
        self._render_thumbnail()

    def _render_thumbnail(self):
        """Fit media inside the 16:9 surface; never stretch or crop it."""
        if self._thumb_pixmap.isNull():
            self.thumb_label.clear()
            return
        fitted = self._thumb_pixmap.scaled(
            self._card_width,
            self._thumb_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.thumb_label.setPixmap(fitted)

    def refresh_theme(self):
        self.setStyleSheet(self._card_qss())
        if hasattr(self, '_play_icon'):
            self._play_icon.setStyleSheet(
                f'color: {_rgba(Colors.TEXT, 210)}; font-size: 36px;'
                ' background: transparent; border: none;')
        if hasattr(self, 'duration_label'):
            self.duration_label.setStyleSheet(
                f'QLabel#cardDurationBadge {{'
                f' color: {Colors.TEXT};'
                f' background: {_rgba(Colors.SURFACE_1, 170)};'
                f' border-radius: 0px; padding: 2px 9px;'
                f' font-family: {Fonts.BODY}; font-size: {Fonts.SIZE_LABEL}px;'
                f' font-weight: bold; letter-spacing: 1px; }}')
        if self._imported_badge is not None:
            self._imported_badge.setStyleSheet(
                f'QLabel#cardImportedBadge {{'
                f' color: {Colors.ACCENT};'
                f' background: {_rgba(Colors.SURFACE_1, 180)};'
                f' border-radius: 0px; padding: 2px 7px;'
                f' font-family: {Fonts.DISPLAY}; font-size: {Fonts.SIZE_MICRO}px;'
                f' font-weight: bold; letter-spacing: 2px; }}')
        if self._finalizing_badge is not None:
            self._finalizing_badge.setStyleSheet(
                f'color: {Colors.TEXT}; '
                f'background: {_rgba(Colors.WARNING, 220)}; '
                f'font-family: {Fonts.DISPLAY}; '
                f'font-size: {Fonts.SIZE_MICRO}px; '
                'padding: 3px 7px; font-weight: bold;')
        self._upload_badge.setStyleSheet(
            f'QLabel#cardUploadedBadge {{'
            f' color: {Colors.BG};'
            f' background: {_rgba(Colors.SUCCESS, 210)};'
            f' border-radius: 0px; padding: 2px 7px;'
            f' font-family: {Fonts.DISPLAY}; font-size: {Fonts.SIZE_MICRO}px;'
            f' font-weight: bold; letter-spacing: 2px; }}')

    def _on_share_click(self):
        # Share is wired through ClipViewer for now — open the viewer
        if not (self.is_video and self.ready):
            return
        if not self._thumbnail_ready:
            self._open_pending = True
            return
        self._emit_opened()

    def _emit_opened(self):
        if not (self.is_video and self.ready):
            return
        # Pass the cached source thumbnail to the editor, not the pixmap that
        # has already been fitted to this card.  The latter is intentionally
        # display-sized and can be much smaller than the editor preview, so it
        # made the clip look soft until QMediaPlayer delivered its first frame
        # (and remained soft while the editor was paused before that happened).
        px = self._thumb_pixmap if not self._thumb_pixmap.isNull() else QPixmap()
        global_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        self.opened.emit(self.file_path, px, global_rect)

    def _show_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(context_menu_qss())
        open_act = menu.addAction('Open in viewer')
        explorer_act = menu.addAction('Show in Explorer')
        copy_act = menu.addAction('Copy path')
        if self.upload_enabled:
            menu.addSeparator()
            upload_act = menu.addAction('Upload')
        else:
            upload_act = None
        menu.addSeparator()
        del_act = menu.addAction(
            'Linked original — managed outside FTHR'
            if self.imported else 'Delete')
        del_act.setEnabled(not self.imported)
        action = menu.exec(self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height())))
        if action == open_act:
            self._on_share_click()
        elif action == explorer_act:
            _show_in_file_manager(self.file_path)
        elif action == copy_act:
            QApplication.clipboard().setText(self.file_path)
        elif upload_act and action == upload_act:
            self.upload_requested.emit(self.file_path)
        elif action == del_act:
            self._confirm_delete()

    def _confirm_delete(self):
        if not self.ready:
            return
        ownership = classify_media_path(self.file_path, self._clips_root, [])
        if self.imported or ownership is not MediaOwnership.FTHR_OWNED:
            FthrMessageDialog.information(
                self,
                'Linked Original Protected',
                'Imported files stay in their original folder. FTHR will not '
                'delete this file with the generic Delete action.',
            )
            return
        reply = FthrMessageDialog.question(
            self, 'Delete',
            f'Delete {os.path.basename(self.file_path)}?',
        )
        if not reply:
            return

        # Resolve cache paths BEFORE the delete — _get_cached_thumb_path needs
        # the file's mtime, which won't be available once the file is gone.
        cache_path = None
        dur_path = None
        try:
            cache_path = _get_cached_thumb_path(self.file_path)
            dur_path = _get_cached_duration_path(cache_path)
        except OSError:
            pass

        import time as _time
        for attempt in range(3):
            try:
                os.remove(self.file_path)
                # Prune thumbnail cache so orphaned .jpg/.dur files don't pile up.
                for p in (cache_path, dur_path):
                    if p:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                self.deleted.emit(self.file_path)
                return
            except OSError as e:
                # Win32 error 32 = "file in use by another process".
                # Common cause: clip still being encoded/played.
                # Retry twice with a short delay before giving up.
                if sys.platform == 'win32' and getattr(e, 'winerror', 0) == 32:
                    if attempt < 2:
                        _time.sleep(0.2)
                        continue
                    FthrMessageDialog.warning(
                        self, 'Delete Failed',
                        f'"{os.path.basename(self.file_path)}" is still in use.\n\n'
                        'Close the clip viewer and wait for any active clip save\n'
                        'or upload to finish, then try again.',
                    )
                    return
                FthrMessageDialog.warning(self, 'Delete Failed', str(e))
                return

    # ── Hover (large play glyph + slight border highlight) ───────────────

    def enterEvent(self, event):
        if self.is_video and hasattr(self, '_play_icon'):
            self._play_icon.setVisible(True)

    def leaveEvent(self, event):
        if self.is_video and hasattr(self, '_play_icon'):
            self._play_icon.setVisible(False)

    def contextMenuEvent(self, event):
        # Right-click anywhere on card — same options as the ⋯ button
        menu = QMenu(self)
        menu.setStyleSheet(context_menu_qss())
        open_act = menu.addAction('Open in viewer')
        explorer_act = menu.addAction('Show in Explorer')
        copy_act = menu.addAction('Copy path')
        if self.upload_enabled:
            menu.addSeparator()
            upload_act = menu.addAction('Upload')
        else:
            upload_act = None
        menu.addSeparator()
        del_act = menu.addAction(
            'Linked original — managed outside FTHR'
            if self.imported else 'Delete')
        del_act.setEnabled(not self.imported)
        action = menu.exec(event.globalPos())
        if action == open_act:
            self._on_share_click()
        elif action == explorer_act:
            _show_in_file_manager(self.file_path)
        elif action == copy_act:
            QApplication.clipboard().setText(self.file_path)
        elif upload_act and action == upload_act:
            self.upload_requested.emit(self.file_path)
        elif action == del_act:
            self._confirm_delete()

    def fade_in(self, delay_ms: int = 0):
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        self._fade_effect = effect

        def _start():
            # A library refresh may replace/delete this card before its
            # staggered delay expires. Do not animate the old Qt effect.
            if getattr(self, '_fade_effect', None) is not effect:
                return
            anim = QPropertyAnimation(effect, b'opacity', self)
            anim.setDuration(450)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _finish():
                if getattr(self, '_fade_effect', None) is effect:
                    self.setGraphicsEffect(None)
                    self._fade_effect = None

            anim.finished.connect(_finish)
            self._fade_anim = anim
            anim.start()

        if delay_ms > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(_start)
            self._fade_timer = timer
            timer.start(delay_ms)
        else:
            _start()

    def _load_image_thumbnail(self):
        self._thumb_pixmap = QPixmap(self.file_path)
        self._render_thumbnail()

    def set_video_thumbnail(self, cache_path: str, duration: int):
        if cache_path and os.path.exists(cache_path):
            self._thumb_pixmap = QPixmap(cache_path)
            self._render_thumbnail()
        if self.is_video and hasattr(self, 'duration_label'):
            mins, secs = divmod(duration, 60)
            self.duration_label.setText(f'{mins}:{secs:02d}')
            self.duration_label.adjustSize()
            self.duration_label.move(
                self._card_width - self.duration_label.width() - 10, 10)
        if self.is_video:
            self._thumbnail_ready = True
            if self._open_pending:
                self._open_pending = False
                QTimer.singleShot(0, self._emit_opened)

    def mousePressEvent(self, event):
        if not self.ready:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Don't trigger card-open if the click was on a child button —
            # those handle their own actions. We can detect by checking the
            # local target widget.
            local = event.position().toPoint()
            child = self.childAt(local)
            if isinstance(child, QPushButton):
                return
            if self.is_video:
                if not self._thumbnail_ready:
                    self._open_pending = True
                    return
            self.clicked.emit(self.file_path)
            if self.is_video:
                self._emit_opened()


# ──────────────────────────────────────────────────────────────────────
# ClipGrid — filter bar + date sections + grid of cards
# ──────────────────────────────────────────────────────────────────────

class ClipGrid(QWidget):
    clip_clicked          = Signal(str)
    clip_opened           = Signal(str, QPixmap, QRect)
    screenshot_clicked    = Signal(str)
    clip_upload_requested = Signal(str)

    # Compact gutters keep the media surface visually dominant while still
    # separating cards at desktop widths.
    _H_MARGIN = 48
    _GRID_SPACING = 12

    def __init__(self, settings_manager=None, parent=None):
        super().__init__(parent)
        self._sm              = settings_manager
        self._upload_checker  = None   # callable(path) -> bool
        self._upload_info_checker = None  # callable(path) -> dict | None
        self._upload_enabled  = None   # callable() -> bool
        self._readiness_checker = None  # callable(path) -> bool
        self.clips_dir      = str(clips_directory_from(self._sm))
        self.thumbnails     = []
        self._thumb_widgets = {}
        self._known_files   = set()
        self._imported_files: set = set()
        self._thread_pool   = QThreadPool()
        # Cap at 2 workers. cv2/ffmpeg thumbnail decode is already heavy on disk
        # and CPU; throwing 16 threads at it just thrashes and makes everything
        # slower. 400ms of profiling led me here. don't touch this.
        self._thread_pool.setMaxThreadCount(2)
        # File scans must not sit behind a long queue of video decoders.  A
        # separate single-worker pool makes filter/sort changes respond as soon
        # as the current scan (if any) completes.
        self._scan_thread_pool = QThreadPool()
        self._scan_thread_pool.setMaxThreadCount(1)
        # Pre-compute the column count from the primary screen's available
        # width so the first paint already matches the maximized window.
        # Without this, the grid renders at 3 cols then snaps to N cols once
        # the resize event fires after showMaximized() — visible lag.
        initial_width = self._initial_viewport_width()
        (self._current_columns,
         self._current_card_widths) = self._layout_metrics(initial_width)

        self._filter = 'all'
        self._sort = 'newest'
        self._background_paused = False
        self._background_refresh_pending = False
        self._in_transition = False
        self._transition_anim = None
        # Keep transition workers (and, critically, their signal objects) alive
        # until the queued completion callback has run on the GUI thread.
        self._transition_workers: dict[int, _FileCollectWorker] = {}

        # Watcher must exist before _load_clips() runs
        self._watcher = QFileSystemWatcher()
        if os.path.exists(self.clips_dir):
            self._watcher.addPath(self.clips_dir)
        self._watcher.directoryChanged.connect(self._on_dir_changed)

        self._setup_ui()
        self._load_clips()

        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._load_clips)

        # Debounced resize — only rebuilds the grid when columns would change.
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_settled)

        self.refresh_timer = QTimer()
        self.refresh_timer.setInterval(30000)
        self.refresh_timer.timeout.connect(self._load_clips)
        self.refresh_timer.start()

    def is_linked_import(self, path: str) -> bool:
        target = os.path.normcase(os.path.realpath(path))
        return any(
            os.path.normcase(os.path.realpath(candidate)) == target
            for candidate in self._imported_files
        )

    def set_upload_checker(self, checker):
        """checker(path: str) -> bool  — True if the clip has been uploaded."""
        self._upload_checker = checker
        self._refresh_upload_states()

    def set_upload_info_checker(self, checker):
        """checker(path: str) -> dict | None — persisted provider result."""
        self._upload_info_checker = checker
        self._refresh_upload_states()

    def set_upload_enabled_checker(self, checker):
        """checker() -> bool  — True if uploads are enabled (shows Upload menu item)."""
        self._upload_enabled = checker
        enabled = bool(checker and checker())
        for card in self._thumb_widgets.values():
            card.upload_enabled = enabled

    def set_readiness_checker(self, checker):
        self._readiness_checker = checker

    def _refresh_upload_states(self):
        """Refresh badges and returned-link actions on cards already in the grid."""
        for path, card in self._thumb_widgets.items():
            uploaded = bool(self._upload_checker and self._upload_checker(path))
            info = (
                self._upload_info_checker(path)
                if uploaded and self._upload_info_checker else None)
            card.set_upload_info(info)
            card.set_uploaded(uploaded)

    def refresh_theme(self):
        for card in self._thumb_widgets.values():
            card.refresh_theme()

    def _viewport_width(self) -> int:
        """The scroll-area viewport is our parent — read its width so column
        calculation works even when the grid layout is wider than the viewport."""
        vp = self.parentWidget()
        return vp.width() if vp else self.width()

    def _initial_viewport_width(self) -> int:
        """Best-guess viewport width before the parent has been laid out.

        This lets the first build match the maximized window and avoids a
        visible reflow during startup.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return 1100
        return screen.availableGeometry().width()

    @classmethod
    def _layout_metrics(cls, viewport_width: int) -> tuple[int, list[int]]:
        """Return column count and exact per-column widths for a viewport."""
        available = max(_CARD_MIN_W, int(viewport_width) - cls._H_MARGIN)
        cols = max(
            1,
            int((available + cls._GRID_SPACING)
                // (_CARD_W + cls._GRID_SPACING)),
        )
        while cols > 1:
            card_width = (
                available - cls._GRID_SPACING * (cols - 1)) // cols
            if card_width >= _CARD_MIN_W:
                break
            cols -= 1
        while True:
            card_width = (
                available - cls._GRID_SPACING * (cols - 1)) // cols
            if card_width <= _CARD_MAX_W:
                break
            cols += 1

        usable = available - cls._GRID_SPACING * (cols - 1)
        base, remainder = divmod(usable, cols)
        widths = [base + (1 if col < remainder else 0)
                  for col in range(cols)]
        return cols, widths

    def _initial_columns_from_screen(self) -> int:
        """Compatibility helper used by a few downstream integrations."""
        return self._layout_metrics(self._initial_viewport_width())[0]

    def _compute_columns(self) -> int:
        return self._layout_metrics(self._viewport_width())[0]

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_vp_filter', False):
            vp = self.parentWidget()
            if vp:
                vp.installEventFilter(self)
                self._vp_filter = True

    def eventFilter(self, obj, event):
        # Resize events fire like a machine gun while you drag a window edge.
        # Debounce so we only rebuild the grid 200ms after you STOP dragging,
        # not 60 times a second mid-drag. Your CPU thanks you.
        if event.type() == event.Type.Resize:
            self._resize_timer.start(200)
        return super().eventFilter(obj, event)

    def _on_resize_settled(self):
        new_cols, new_widths = self._layout_metrics(self._viewport_width())
        if (new_cols != self._current_columns
                or new_widths != self._current_card_widths):
            self._current_columns = new_cols
            self._current_card_widths = new_widths
            self._relayout_grids()

    def _on_dir_changed(self, path: str):
        if self._background_paused:
            self._background_refresh_pending = True
            return
        self._debounce_timer.start(500)

    def set_background_paused(self, paused: bool) -> None:
        """Suspend library scans while the containing app is in background.

        Capture/save completion can still invalidate the library through
        ``force_refresh``. While paused that work is coalesced into one refresh
        when the UI becomes active again.
        """
        paused = bool(paused)
        if paused == self._background_paused:
            return
        self._background_paused = paused
        if paused:
            # Drop queued decodes immediately. At most the two already-running
            # workers finish; the next foreground refresh reuses any cache they
            # produced and rebuilds the remaining queue.
            self._background_refresh_pending = True
            self._thread_pool.clear()
            self.refresh_timer.stop()
            self._debounce_timer.stop()
            return

        self.refresh_timer.start()
        if self._background_refresh_pending:
            self._background_refresh_pending = False
            self._known_files = None
            self._load_clips()

    # ── UI ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 18, 24, 24)
        self.layout.setSpacing(0)

        # ── Filter bar ────────────────────────────────────────────────────
        filt = QFrame()
        filt.setObjectName('filterBar')
        filt.setFixedHeight(Sizes.FILTER_H)
        fl = QHBoxLayout(filt)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(12)

        self._all_count_label = QLabel('ALL CLIPS')
        self._all_count_label.setObjectName('allCountLabel')
        fl.addWidget(self._all_count_label)

        fl.addStretch(1)

        self.filter_combo = _DropdownCombo()
        self.filter_combo.addItems(['All clips', 'Clips only',
                                    'Screenshots', 'Imported clips'])
        self.filter_combo.setStyleSheet(combo_qss())
        self.filter_combo.setMinimumWidth(120)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        fl.addWidget(self.filter_combo)

        self.sort_combo = _DropdownCombo()
        self.sort_combo.addItems(['Newest', 'Oldest'])
        self.sort_combo.setStyleSheet(combo_qss())
        self.sort_combo.setMinimumWidth(110)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        fl.addWidget(self.sort_combo)

        self.layout.addWidget(filt)
        self.layout.addSpacing(8)

        # ── Section host (date-grouped grids live here) ───────────────────
        self._sections_host = QWidget()
        self._sections_host.setStyleSheet('background: transparent;')
        self._sections_layout = QVBoxLayout(self._sections_host)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(20)
        self.layout.addWidget(self._sections_host)

        # ── Empty state ───────────────────────────────────────────────────
        self._empty_widget = QWidget()
        self._empty_widget.setStyleSheet('background: transparent;')
        ev_layout = QVBoxLayout(self._empty_widget)
        ev_layout.setContentsMargins(0, 96, 0, 0)
        ev_layout.setSpacing(0)
        ev_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self._no_clips_lbl = QLabel('NO CLIPS YET')
        self._no_clips_lbl.setStyleSheet(label_display(Colors.TEXT_GHOST, Fonts.SIZE_H1, 8))
        self._no_clips_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev_layout.addWidget(self._no_clips_lbl)

        self._empty_detail_lbl = QLabel()
        self._empty_detail_lbl.setStyleSheet(
            label_display(Colors.TEXT_DIM, Fonts.SIZE_LABEL, Fonts.TRACK_LABEL))
        self._empty_detail_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_detail_lbl.setVisible(False)
        ev_layout.addSpacing(Sizes.SPACE_2)
        ev_layout.addWidget(self._empty_detail_lbl)

        ev_layout.addSpacing(Sizes.SPACE_4)

        mark_row = QHBoxLayout()
        mark_row.setContentsMargins(0, 0, 0, 0)
        mark_row.addStretch()
        mark = QFrame()
        mark.setFixedSize(56, 2)
        mark.setStyleSheet(f'background: {Colors.ACCENT}; border: none;')
        mark_row.addWidget(mark)
        mark_row.addStretch()
        ev_layout.addLayout(mark_row)

        self.layout.addWidget(self._empty_widget)
        self.layout.addStretch()

        self.setStyleSheet(self._grid_qss())

    @staticmethod
    def _grid_qss() -> str:
        return f'''
            QFrame#filterBar {{
                background: transparent;
                border-bottom: 1px solid {Colors.SHELL_DIVIDER};
            }}
            QLabel#allCountLabel {{
                color: {Colors.TEXT};
                font-family: {Fonts.DISPLAY};
                font-weight: bold;
                font-size: {Fonts.SIZE_LABEL}px;
                letter-spacing: {Fonts.TRACK_LABEL}px;
                background: transparent;
            }}
            QPushButton#viewToggle, QPushButton#viewToggleActive {{
                background-color: {Colors.SURFACE_2};
                border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.TEXT_DIM};
                font-size: 14px;
            }}
            QPushButton#viewToggle:hover, QPushButton#viewToggleActive:hover {{
                color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
            QPushButton#viewToggleActive {{
                color: {Colors.ACCENT};
                border-color: {Colors.ACCENT};
            }}
            QLabel.sectionHeader {{
                color: {Colors.TEXT};
                font-family: {Fonts.DISPLAY};
                font-weight: bold;
                font-size: {Fonts.SIZE_BODY_L}px;
                letter-spacing: {Fonts.TRACK_LABEL}px;
                background: transparent;
            }}
            QLabel.sectionSub {{
                color: {Colors.TEXT_DIM};
                font-family: {Fonts.BODY};
                font-size: {Fonts.SIZE_BODY}px;
                background: transparent;
            }}
        '''

    # ── Filter / sort handlers ──────────────────────────────────────────

    def _on_filter_changed(self, idx: int):
        mapping = ['all', 'clips', 'screenshots', 'imported']
        self._filter = mapping[idx] if idx < len(mapping) else 'all'
        self._known_files = set()
        self._fade_out_then_reload()

    def _on_sort_changed(self, idx: int):
        self._sort = ['newest', 'oldest'][idx]
        self._known_files = set()
        self._fade_out_then_reload()

    def _fade_out_then_reload(self):
        """Refresh a category through a background scan without hiding its cards.

        QGraphicsOpacityEffect occasionally left the section host fully
        transparent after a category change on Windows.  The cards remained
        interactive, but the library looked like a black, empty surface.  Keep
        the current result visible until the replacement is ready instead.
        """
        if self._transition_anim is not None:
            self._transition_anim.stop()
            self._transition_anim = None

        self._in_transition = True
        # Sequence guard: if you spam the filter dropdown, multiple background
        # workers race. Each gets a seq number and only the latest one's result
        # is allowed to touch the UI — older ones finish and get thrown away.
        # Otherwise a slow earlier scan could overwrite a newer filter's results.
        self._transition_seq = getattr(self, '_transition_seq', 0) + 1
        seq = self._transition_seq

        def _launch_worker():
            import_dirs = self._sm.get('imported_clip_folders', []) if self._sm else []
            worker = _FileCollectWorker(self.clips_dir, import_dirs, self._filter, self._sort)
            self._transition_workers[seq] = worker

            def _on_done(raw, pairs, imported, subdirs):
                self._transition_workers.pop(seq, None)
                if self._transition_seq == seq:
                    self._on_files_collected_for_transition(raw, pairs, imported, subdirs)

            worker.signals.finished.connect(_on_done)
            self._scan_thread_pool.start(worker)

        # Do not use a graphics effect here. Besides being unnecessary for the
        # scan, it can leave Qt's backing store transparent on some Windows
        # GPU/driver combinations.
        self._sections_host.setGraphicsEffect(None)
        _launch_worker()

    def _on_files_collected_for_transition(self, raw_files, sorted_pairs, imported_files, subdirs):
        """Runs on the main thread once the background worker finishes."""
        for path in subdirs:
            self._watch_subdir(path)

        self._known_files    = raw_files
        self._imported_files = imported_files

        self._clear_sections()
        self.thumbnails.clear()
        self._thumb_widgets.clear()

        if not sorted_pairs:
            self._show_empty(True)
            self._all_count_label.setText(self._filter_heading())
        else:
            self._show_empty(False)
            self._all_count_label.setText(f'{self._filter_heading()}  ({len(sorted_pairs)})')
            groups: dict = {}
            order:  list = []
            for mtime, f in sorted_pairs:
                key = _section_label_for(mtime)
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(f)
            global_idx = 0
            for section_key in order:
                section_files = groups[section_key]
                self._add_section(section_key, section_files, global_idx)
                global_idx += len(section_files)

        self._sections_host.setGraphicsEffect(None)
        self._in_transition = False
        self._transition_anim = None

    def _filter_heading(self) -> str:
        if self._filter == 'clips':
            return 'CLIPS'
        if self._filter == 'screenshots':
            return 'SCREENSHOTS'
        if self._filter == 'imported':
            return 'IMPORTED CLIPS'
        return 'ALL CLIPS'

    # ── Filesystem walking ──────────────────────────────────────────────

    def _collect_media_files(self) -> set:
        found = set()
        self._imported_files = set()

        # Primary FTHR_Clips folder
        try:
            entries = os.listdir(self.clips_dir)
        except OSError:
            entries = []
        for name in entries:
            full = os.path.join(self.clips_dir, name)
            if os.path.isfile(full):
                if is_library_media_path(name):
                    found.add(full)
            elif os.path.isdir(full) and not _is_grid_excluded_dir(name):
                self._watch_subdir(full)
                try:
                    for sub in os.listdir(full):
                        sub_full = os.path.join(full, sub)
                        if os.path.isfile(sub_full) and is_library_media_path(sub):
                            found.add(sub_full)
                except OSError:
                    pass

        # Imported folders from settings
        import_dirs = self._sm.get('imported_clip_folders', []) if self._sm else []
        for folder in import_dirs:
            if not os.path.isdir(folder):
                continue
            self._watch_subdir(folder)
            try:
                for name in os.listdir(folder):
                    full = os.path.join(folder, name)
                    if os.path.isfile(full) and is_library_media_path(name):
                        found.add(full)
                        self._imported_files.add(full)
                    elif os.path.isdir(full) and not _is_grid_excluded_dir(full):
                        self._watch_subdir(full)
                        try:
                            for sub in os.listdir(full):
                                sub_full = os.path.join(full, sub)
                                if os.path.isfile(sub_full) and is_library_media_path(sub):
                                    found.add(sub_full)
                                    self._imported_files.add(sub_full)
                        except OSError:
                            pass
            except OSError:
                pass

        return found

    def _watch_subdir(self, path: str):
        if path not in self._watcher.directories():
            self._watcher.addPath(path)

    # ── Build the date-grouped layout ───────────────────────────────────

    def _load_clips(self):
        if self._background_paused:
            self._background_refresh_pending = True
            return
        if self._in_transition:
            return
        if not os.path.isdir(self.clips_dir):
            os.makedirs(self.clips_dir, exist_ok=True)
            if self.clips_dir not in self._watcher.directories():
                self._watcher.addPath(self.clips_dir)
            self._show_empty(True)
            return

        current_files = self._collect_media_files()

        if current_files == self._known_files:
            return
        self._known_files = current_files

        self._clear_sections()
        self.thumbnails.clear()
        self._thumb_widgets.clear()

        # Apply filter
        files = list(current_files)
        if self._filter == 'clips':
            files = [f for f in files if is_completed_video_path(f)]
        elif self._filter == 'screenshots':
            files = [f for f in files if f.lower().endswith(_IMAGE_EXTS)]
        elif self._filter == 'imported':
            files = [f for f in files if f in self._imported_files]
        # 'all' shows everything

        # Sort — wrap stat calls so a file deleted between scan and sort
        # doesn't crash the key function.
        def _mtime(f):
            try:
                return os.path.getmtime(f)
            except OSError:
                return 0.0

        def _fsize(f):
            try:
                return os.path.getsize(f)
            except OSError:
                return 0

        if self._sort == 'oldest':
            files.sort(key=_mtime)
        elif self._sort == 'longest':
            # Without per-clip duration here we approximate by file size
            # (longer clips ≈ bigger files). Real duration comes via worker.
            files.sort(key=_fsize, reverse=True)
        else:
            files.sort(key=_mtime, reverse=True)

        if not files:
            self._show_empty(True)
            self._all_count_label.setText(self._filter_heading())
            return

        self._show_empty(False)
        self._all_count_label.setText(f'{self._filter_heading()}  ({len(files)})')

        # Group by date
        groups: dict[str, list[str]] = {}
        order: list[str] = []
        for f in files:
            try:
                key = _section_label_for(os.path.getmtime(f))
            except OSError:
                key = 'OTHER'
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(f)

        global_idx = 0
        for section_key in order:
            section_files = groups[section_key]
            self._add_section(section_key, section_files, global_idx)
            global_idx += len(section_files)

    def _add_section(self, header_text: str, files: list[str], starting_idx: int):
        """One block: [date header] + [game subtitle] + [grid of cards]."""
        section = QFrame()
        section.setStyleSheet('background: transparent;')
        sl = QVBoxLayout(section)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)

        # Header row: date + dropdown caret (visual)
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.setSpacing(10)

        date_lbl = QLabel(f'{header_text}  ▾')
        date_lbl.setProperty('class', 'sectionHeader')
        date_lbl.setStyleSheet(
            f'color: {Colors.TEXT};'
            f' font-family: {Fonts.DISPLAY}; font-weight: bold;'
            f' font-size: {Fonts.SIZE_BODY_L}px;'
            f' letter-spacing: {Fonts.TRACK_LABEL}px;'
            f' background: transparent;'
        )
        hdr_row.addWidget(date_lbl)

        # Game/source name comes from the most-recent file's parent folder
        sample_game = (
            _game_name_from_path(files[0], self.clips_dir) if files else '')
        if sample_game:
            sub_lbl = QLabel(f'·  {sample_game}')
            sub_lbl.setStyleSheet(
                f'color: {Colors.TEXT_DIM}; font-family: {Fonts.BODY};'
                f' font-size: {Fonts.SIZE_BODY}px; background: transparent;'
            )
            hdr_row.addWidget(sub_lbl)

        hdr_row.addStretch(1)
        sl.addLayout(hdr_row)

        cols = self._current_columns
        widths = self._current_card_widths
        grid = QGridLayout()
        grid.setHorizontalSpacing(self._GRID_SPACING)
        grid.setVerticalSpacing(self._GRID_SPACING)
        grid.setContentsMargins(0, 0, 0, 0)

        upload_enabled = bool(self._upload_enabled and self._upload_enabled())
        for i, fp in enumerate(files):
            is_video = is_completed_video_path(fp)
            uploaded = bool(self._upload_checker and self._upload_checker(fp))
            upload_info = (
                self._upload_info_checker(fp)
                if uploaded and self._upload_info_checker else None)
            ready = bool(
                self._readiness_checker(fp)
                if self._readiness_checker else True)
            thumb = ClipThumbnail(
                fp, is_video=is_video,
                imported=fp in self._imported_files,
                upload_enabled=upload_enabled,
                uploaded=uploaded,
                upload_info=upload_info,
                ready=ready,
                card_width=widths[i % cols],
                clips_root=self.clips_dir,
            )
            thumb.opened.connect(self.clip_opened.emit)
            thumb.clicked.connect(
                self.clip_clicked.emit if is_video else self.screenshot_clicked.emit)
            thumb.deleted.connect(self._on_clip_deleted)
            thumb.upload_requested.connect(self.clip_upload_requested.emit)
            # Cards have a fixed width.  Explicit left/top alignment prevents
            # Qt from centering a short final row (especially a one-card date
            # section) inside a column that received surplus layout space.
            grid.addWidget(
                thumb, i // cols, i % cols,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            )
            self.thumbnails.append(thumb)
            self._thumb_widgets[fp] = thumb
            if not self._in_transition:
                thumb.fade_in(delay_ms=min((starting_idx + i) * 35, 600))

            if is_video and ready:
                worker = _ThumbnailWorker(fp)
                worker.signals.finished.connect(self._on_thumb_ready)
                self._thread_pool.start(worker)

        for col, width in enumerate(widths):
            grid.setColumnMinimumWidth(col, width)
        sl.addLayout(grid)
        self._sections_layout.addWidget(section)

    def _relayout_grids(self):
        """Re-position existing cards into the new column count without
        destroying and recreating widgets.

        Key word: WITHOUT recreating. We could just nuke everything and rebuild
        on every resize, but that re-runs all the thumbnail workers and flickers
        the whole grid. Instead we yank each card out of the grid and drop it
        back at its new (row, col). Same widgets, new positions. works on my
        machine ✓ (and yours, hopefully)."""
        cols = self._current_columns
        widths = self._current_card_widths
        for si in range(self._sections_layout.count()):
            section_widget = self._sections_layout.itemAt(si).widget()
            if section_widget is None:
                continue
            section_layout = section_widget.layout()
            if section_layout is None:
                continue
            for li in range(section_layout.count()):
                item = section_layout.itemAt(li)
                if item is None:
                    continue
                grid = item.layout()
                if not isinstance(grid, QGridLayout):
                    continue
                widgets = []
                while grid.count():
                    child = grid.takeAt(0)
                    w = child.widget()
                    if w:
                        widgets.append(w)
                for i, w in enumerate(widgets):
                    if isinstance(w, ClipThumbnail):
                        w.resize_card(widths[i % cols])
                    grid.addWidget(
                        w, i // cols, i % cols,
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    )
                for col, width in enumerate(widths):
                    grid.setColumnMinimumWidth(col, width)

    def set_clips_directory(self, path: str) -> None:
        """Switch the primary library root and refresh the visible media."""
        resolved = str(Path(path).expanduser().resolve(strict=False))
        if resolved == self.clips_dir:
            self.force_refresh()
            return

        self._transition_seq = getattr(self, '_transition_seq', 0) + 1
        if self._transition_anim is not None:
            self._transition_anim.stop()
            self._transition_anim = None
        self._in_transition = False
        self._sections_host.setGraphicsEffect(None)

        old_root = Path(self.clips_dir).resolve(strict=False)
        for watched in list(self._watcher.directories()):
            try:
                Path(watched).resolve(strict=False).relative_to(old_root)
            except ValueError:
                continue
            self._watcher.removePath(watched)

        self.clips_dir = resolved
        self._known_files = set()
        self._imported_files = set()
        Path(resolved).mkdir(parents=True, exist_ok=True)
        if resolved not in self._watcher.directories():
            self._watcher.addPath(resolved)
        self._load_clips()

    def force_refresh(self):
        """Clear the file cache and immediately reload — called when import folders change."""
        if self._background_paused:
            self._background_refresh_pending = True
            return
        self._known_files = set()
        self._load_clips()

    def _clear_sections(self):
        while self._sections_layout.count():
            item = self._sections_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _show_empty(self, show: bool):
        if show:
            detail = ''
            if self._filter == 'screenshots':
                self._no_clips_lbl.setText('NO SCREENSHOTS YET')
                hotkeys = self._sm.get('hotkeys', {}) if self._sm else {}
                screenshot_key = hotkeys.get('save_screenshot', 'F12')
                detail = f'PRESS {screenshot_key} TO TAKE A SCREENSHOT'
            elif self._filter == 'clips':
                self._no_clips_lbl.setText('NO CLIPS YET')
            elif self._filter == 'imported':
                self._no_clips_lbl.setText('NO IMPORTED CLIPS')
            else:
                self._no_clips_lbl.setText('NO CLIPS YET')
            self._empty_detail_lbl.setText(detail)
            self._empty_detail_lbl.setVisible(bool(detail))
        self._empty_widget.setVisible(show)
        self._sections_host.setVisible(not show)

    def _on_clip_deleted(self, file_path: str):
        self._known_files = set()
        self._load_clips()

    def _on_thumb_ready(self, file_path: str, cache_path: str, duration: int):
        widget = self._thumb_widgets.get(file_path)
        if widget:
            widget.set_video_thumbnail(cache_path, duration)

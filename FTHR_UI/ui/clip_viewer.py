# clip_viewer.py - FTHR clip editor
import os, sys, threading, subprocess
from dataclasses import replace
from core.ffmpeg_tools import (
    get_ffmpeg_exe, software_video_args, FFmpegUnavailable)
from core.library_ownership import MediaOwnership, classify_media_path
from core.ffmpeg_playback import (
    FFmpegPlaybackController, PlaybackError, discover_playback_sources,
)
from core.playback_mix_model import (
    PlaybackSource, SourceMixState, ffmpeg_mix_filter, source_display_name,
    source_icon_key,
)
from core.transactional_output import (
    commit_staged_output,
    create_staged_output_path,
    discard_staged_output,
)
from pathlib import Path
from datetime import datetime

_NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}

import cv2

from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QFrame, QSizePolicy, QGraphicsOpacityEffect, QLineEdit,
)
from PySide6.QtCore import (
    Qt, Signal, QUrl, QTimer, QSize, QRect, QPoint, QEvent,
    QPropertyAnimation, QEasingCurve, QMimeData,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import (
    QPainter, QBrush, QPen, QColor, QFont, QPixmap, QImage, QPolygon, QDrag, QIcon,
    QFontMetrics,
)

from ui.style import Colors, Fonts, Sizes


# ---------------------------------------------------------------------------
# TrimSlider — two-handle trim bar with draggable playhead
# ---------------------------------------------------------------------------

class TrimSlider(QFrame):
    """Two-handle trim bar with live, draggable playhead indicator."""
    range_changed  = Signal(float, float)
    seek_requested = Signal(float)

    def __init__(self, duration_ms: int, parent=None):
        super().__init__(parent)
        self.duration_ms  = max(duration_ms, 1)
        self.start_pct    = 0.0
        self.end_pct      = 1.0
        self.playhead_pct = 0.0
        self.dragging     = None
        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet('QFrame { background-color: #000000; border: none; }')

    def set_playhead(self, pct: float):
        self.playhead_pct = max(0.0, min(1.0, pct))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad  = 14
        tw   = w - pad * 2
        ty   = h // 2 - 4
        th   = 8

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor('#ffffff')))
        painter.drawRect(pad, ty, tw, th)

        sx = pad + int(tw * self.start_pct)
        ex = pad + int(tw * self.end_pct)

        painter.setBrush(QBrush(QColor(Colors.ACCENT)))
        painter.drawRect(sx, ty, ex - sx, th)

        painter.setOpacity(0.30)
        painter.setBrush(QBrush(QColor('#000000')))
        painter.drawRect(pad, ty, sx - pad, th)
        painter.drawRect(ex, ty, pad + tw - ex, th)
        painter.setOpacity(1.0)

        ph_x = pad + int(tw * self.playhead_pct)
        painter.setPen(QPen(QColor(Colors.ACCENT), 1.5))
        painter.drawLine(ph_x, ty - 8, ph_x, ty + th + 8)
        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(QBrush(QColor(Colors.ACCENT)))
        ks = 6
        ky = ty - 10
        painter.drawPolygon(QPolygon([
            QPoint(ph_x,      ky),
            QPoint(ph_x + ks, ky + ks),
            QPoint(ph_x,      ky + ks * 2),
            QPoint(ph_x - ks, ky + ks),
        ]))

        hh, hw = 22, 5
        mid_y = ty + th // 2 - hh // 2
        painter.setBrush(QBrush(QColor('#ffffff')))
        painter.drawRect(sx - hw // 2, mid_y, hw, hh)
        painter.drawRect(ex - hw // 2, mid_y, hw, hh)

        font = QFont('Segoe UI', 8)
        painter.setFont(font)
        painter.setPen(QPen(QColor('#ffffff')))
        painter.drawText(sx - 24, ty + th + 6, 48, 14,
                         Qt.AlignmentFlag.AlignCenter, self._fmt(self.start_pct * self.duration_ms))
        painter.drawText(ex - 24, ty + th + 6, 48, 14,
                         Qt.AlignmentFlag.AlignCenter, self._fmt(self.end_pct * self.duration_ms))

    def _pct_from_x(self, x: int) -> float:
        tw = self.width() - 28
        return max(0.0, min(1.0, (x - 14) / max(tw, 1)))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pct     = self._pct_from_x(event.pos().x())
        dist_s  = abs(pct - self.start_pct)
        dist_e  = abs(pct - self.end_pct)
        dist_ph = abs(pct - self.playhead_pct)
        if dist_ph < 0.03 and dist_ph <= dist_s and dist_ph <= dist_e:
            self.dragging = 'playhead'
        elif dist_s <= dist_e:
            self.dragging = 'start'
        else:
            self.dragging = 'end'

    def mouseMoveEvent(self, event):
        if not self.dragging:
            return
        pct = self._pct_from_x(event.pos().x())
        if self.dragging == 'playhead':
            self.playhead_pct = pct
            self.seek_requested.emit(pct)
            self.update()
        elif self.dragging == 'start':
            self.start_pct = min(pct, self.end_pct - 0.04)
            self.range_changed.emit(self.start_pct, self.end_pct)
            self.update()
        else:
            self.end_pct = max(pct, self.start_pct + 0.04)
            self.range_changed.emit(self.start_pct, self.end_pct)
            self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = None

    def _fmt(self, ms: float) -> str:
        s = int(ms / 1000)
        return f'{s // 60}:{s % 60:02d}'


# ---------------------------------------------------------------------------
# CropOverlay — transparent drag-handle crop net
# ---------------------------------------------------------------------------

class CropOverlay(QWidget):
    """
    Transparent overlay on top of a frame label.
    8 drag handles (corners + edge midpoints), rule-of-thirds grid,
    click-and-drag to create a new crop.
    """
    crop_changed = Signal(QRect)

    _HS = 9    # handle square size px
    _HZ = 14   # hit-zone radius px

    # 0=TL 1=T 2=TR 3=R 4=BR 5=B 6=BL 7=L
    _CURSORS = [
        Qt.CursorShape.SizeFDiagCursor,
        Qt.CursorShape.SizeVerCursor,
        Qt.CursorShape.SizeBDiagCursor,
        Qt.CursorShape.SizeHorCursor,
        Qt.CursorShape.SizeFDiagCursor,
        Qt.CursorShape.SizeVerCursor,
        Qt.CursorShape.SizeBDiagCursor,
        Qt.CursorShape.SizeHorCursor,
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._crop        = QRect()
        self._bounds      = QRect()
        self._drag        = None
        self._drag_origin = QPoint()
        self._drag_rect   = QRect()

    def set_bounds(self, rect: QRect):
        self._bounds = QRect(rect)
        self.update()

    def set_crop(self, rect: QRect):
        self._crop = QRect(rect)
        self.update()

    def clear(self):
        self._crop = QRect()
        self.update()

    def has_crop(self) -> bool:
        return self._crop.isValid() and self._crop.width() > 4 and self._crop.height() > 4

    def _handle_pts(self):
        r  = self._crop
        cx = r.center().x()
        cy = r.center().y()
        return [
            QPoint(r.left(),  r.top()),
            QPoint(cx,        r.top()),
            QPoint(r.right(), r.top()),
            QPoint(r.right(), cy),
            QPoint(r.right(), r.bottom()),
            QPoint(cx,        r.bottom()),
            QPoint(r.left(),  r.bottom()),
            QPoint(r.left(),  cy),
        ]

    def _hit(self, pos: QPoint):
        if not self.has_crop():
            return None
        hz = self._HZ
        for i, p in enumerate(self._handle_pts()):
            if abs(pos.x() - p.x()) <= hz and abs(pos.y() - p.y()) <= hz:
                return i
        if self._crop.contains(pos):
            return 'move'
        return None

    def _clamp(self, rect: QRect) -> QRect:
        if self._bounds.isNull():
            return rect
        r = QRect(rect)
        if r.left()   < self._bounds.left():   r.setLeft(self._bounds.left())
        if r.top()    < self._bounds.top():    r.setTop(self._bounds.top())
        if r.right()  > self._bounds.right():  r.setRight(self._bounds.right())
        if r.bottom() > self._bounds.bottom(): r.setBottom(self._bounds.bottom())
        return r

    def paintEvent(self, event):
        if not self.has_crop():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        r = self._crop

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 148)))
        painter.drawRect(0, 0, w, r.top())
        painter.drawRect(0, r.bottom() + 1, w, h - r.bottom() - 1)
        painter.drawRect(0, r.top(), r.left(), r.height() + 1)
        painter.drawRect(r.right() + 1, r.top(), w - r.right() - 1, r.height() + 1)

        painter.setPen(QPen(QColor('#ffffff'), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)

        painter.setPen(QPen(QColor(255, 255, 255, 42), 1))
        t3w = r.width()  // 3
        t3h = r.height() // 3
        painter.drawLine(r.left() + t3w,     r.top(), r.left() + t3w,     r.bottom())
        painter.drawLine(r.left() + t3w * 2, r.top(), r.left() + t3w * 2, r.bottom())
        painter.drawLine(r.left(), r.top() + t3h,     r.right(), r.top() + t3h)
        painter.drawLine(r.left(), r.top() + t3h * 2, r.right(), r.top() + t3h * 2)

        hs = self._HS
        painter.setPen(QPen(QColor('#000000'), 1))
        painter.setBrush(QBrush(QColor('#ffffff')))
        for p in self._handle_pts():
            painter.drawRect(p.x() - hs // 2, p.y() - hs // 2, hs, hs)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.pos()
        hit = self._hit(pos)
        if hit is not None:
            self._drag        = hit
            self._drag_origin = QPoint(pos)
            self._drag_rect   = QRect(self._crop)
        elif self._bounds.isNull() or self._bounds.contains(pos):
            self._drag        = 'new'
            self._drag_origin = QPoint(pos)
            self._crop        = QRect(pos.x(), pos.y(), 0, 0)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._drag is None:
            hit = self._hit(pos)
            if isinstance(hit, int):
                self.setCursor(self._CURSORS[hit])
            elif hit == 'move':
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif self._bounds.isNull() or self._bounds.contains(pos):
                self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        dx = pos.x() - self._drag_origin.x()
        dy = pos.y() - self._drag_origin.y()
        r  = QRect(self._drag_rect)

        if self._drag == 'new':
            ox, oy = self._drag_origin.x(), self._drag_origin.y()
            self._crop = self._clamp(QRect(
                min(ox, pos.x()), min(oy, pos.y()),
                abs(pos.x() - ox), abs(pos.y() - oy),
            ))
        elif self._drag == 'move':
            moved = r.translated(dx, dy)
            if not self._bounds.isNull():
                moved.moveLeft(max(self._bounds.left(),
                                   min(moved.left(), self._bounds.right()  - moved.width())))
                moved.moveTop( max(self._bounds.top(),
                                   min(moved.top(),  self._bounds.bottom() - moved.height())))
            self._crop = moved
        else:
            i  = self._drag
            nr = QRect(r)
            if i in (0, 6, 7): nr.setLeft(r.left()     + dx)
            if i in (2, 3, 4): nr.setRight(r.right()   + dx)
            if i in (0, 1, 2): nr.setTop(r.top()       + dy)
            if i in (4, 5, 6): nr.setBottom(r.bottom() + dy)
            nr = nr.normalized()
            if nr.width() > 4 and nr.height() > 4:
                self._crop = self._clamp(nr)

        self.update()
        if self.has_crop():
            self.crop_changed.emit(QRect(self._crop))

    def mouseReleaseEvent(self, event):
        self._drag = None


# ---------------------------------------------------------------------------
# CropPreviewOverlay — shows active crop region on top of QVideoWidget
# ---------------------------------------------------------------------------

class CropPreviewOverlay(QWidget):
    """
    Transparent overlay placed over QVideoWidget.
    Draws a teal rectangle around the crop region so the user can see exactly
    what will be exported while still seeing the rest of the video around it.
    WA_TransparentForMouseEvents so playback controls still work.
    """

    def __init__(self, src_w: int, src_h: int, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._src_w     = max(src_w, 1)
        self._src_h     = max(src_h, 1)
        self._crop_rect = None   # (x, y, w, h) in source pixels, or None

    def set_crop(self, crop_rect):
        self._crop_rect = crop_rect
        self.update()

    def _video_display_rect(self) -> QRect:
        """Compute where Qt renders the video inside QVideoWidget (letterboxed)."""
        vw, vh = self.width(), self.height()
        src_ar = self._src_w / self._src_h
        wid_ar = vw / max(vh, 1)
        if src_ar > wid_ar:
            dw = vw
            dh = int(vw / src_ar)
        else:
            dh = vh
            dw = int(vh * src_ar)
        ox = (vw - dw) // 2
        oy = (vh - dh) // 2
        return QRect(ox, oy, dw, dh)

    def paintEvent(self, event):
        if not self._crop_rect:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        dr   = self._video_display_rect()
        x, y, w, h = self._crop_rect

        # Crop rect mapped to display coords
        cx = dr.x() + int(x / self._src_w * dr.width())
        cy = dr.y() + int(y / self._src_h * dr.height())
        cw = int(w / self._src_w * dr.width())
        ch = int(h / self._src_h * dr.height())

        # Teal border around the crop area — no dimming, no occlusion of the
        # surrounding video. Drawn just outside the crop rect so the keep-area
        # is fully visible underneath.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(Colors.ACCENT), 2))
        painter.drawRect(cx - 1, cy - 1, cw + 2, ch + 2)


# ---------------------------------------------------------------------------
# _DragZone — proper QWidget subclass that initiates a file drag
# ---------------------------------------------------------------------------

class _DragZone(QWidget):
    """Transparent overlay that initiates a QDrag on mouse-move (>10px threshold)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path  = None
        self._drag_start = None

    def set_file(self, path: str):
        self._file_path = path
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._file_path:
            self._drag_start = event.pos()

    def mouseMoveEvent(self, event):
        if not self._file_path or not self._drag_start:
            return
        if (event.pos() - self._drag_start).manhattanLength() < 10:
            return
        self._drag_start = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self._file_path)])
        drag.setMimeData(mime)
        # Ghost pixmap shown while dragging
        px = QPixmap(100, 56)
        px.fill(QColor('#1a1a1a'))
        p = QPainter(px)
        p.setPen(QPen(QColor('#888888')))
        p.setFont(QFont('Segoe UI', 7, QFont.Weight.Bold))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, 'FTHRClips')
        p.end()
        drag.setPixmap(px)
        drag.setHotSpot(QPoint(50, 28))
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event):
        self._drag_start = None


# ---------------------------------------------------------------------------
# CropDialog — frame display with CropOverlay
# ---------------------------------------------------------------------------

class CropDialog(QDialog):
    """Show first frame of clip; user creates/adjusts crop with 8-handle net."""

    def __init__(self, clip_path: str, initial_crop=None, parent=None):
        super().__init__(parent)
        self.clip_path     = clip_path
        self.crop_rect     = None
        self._initial_crop = initial_crop
        self._src_w        = 1
        self._src_h        = 1
        self._offset_x     = 0
        self._offset_y     = 0
        self._pixmap       = None

        self.setWindowTitle('FTHR — SET CROP')
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setMinimumSize(900, 580)
        self.setStyleSheet('QDialog { background-color: #000000; }')

        self._build_ui()
        self._load_frame()   # reads frame, stores self._pixmap (no display yet)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr_frame = QFrame()
        hdr_frame.setFixedHeight(40)
        hdr_frame.setStyleSheet('background-color: #000000; border-bottom: 1px solid #ffffff;')
        hdr = QHBoxLayout(hdr_frame)
        hdr.setContentsMargins(20, 0, 12, 0)
        title = QLabel('CROP')
        title.setStyleSheet('color: #ffffff; font-size: 9px; font-weight: bold; '
                            'font-family: "Segoe UI"; letter-spacing: 2px; background: transparent;')
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton('✕')
        close_btn.setFixedSize(40, 40)
        close_btn.setStyleSheet(
            'QPushButton { background: transparent; border: none; color: #ffffff; font-size: 13px; }'
            'QPushButton:hover { color: #cc0000; }')
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(close_btn)
        root.addWidget(hdr_frame)

        instr = QLabel(
            'DRAG HANDLES TO RESIZE  ·  DRAG INSIDE TO MOVE  ·  16:9 / 9:16 FOR REFERENCE FRAME')
        instr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instr.setFixedHeight(28)
        instr.setStyleSheet(f'color: {Colors.ACCENT}; font-size: 8px; font-family: "Segoe UI"; '
                            f'letter-spacing: 1px; background: #000000;')
        root.addWidget(instr)

        self.frame_label = QLabel()
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setStyleSheet('background-color: #000000;')
        self.frame_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.frame_label, stretch=1)

        # Overlay child of frame_label — starts 1×1, sized properly in _sync_overlay
        self._overlay = CropOverlay(self.frame_label)
        self._overlay.setGeometry(0, 0, 1, 1)
        self._overlay.crop_changed.connect(self._on_overlay_changed)

        bot = QFrame()
        bot.setFixedHeight(48)
        bot.setStyleSheet('background-color: #000000; border-top: 1px solid #ffffff;')
        bot_lay = QHBoxLayout(bot)
        bot_lay.setContentsMargins(16, 0, 16, 0)
        bot_lay.setSpacing(10)

        self.info_lbl = QLabel('')
        self.info_lbl.setStyleSheet(
            f'color: {Colors.ACCENT}; font-size: 9px; font-family: "Segoe UI"; background: transparent;')
        bot_lay.addWidget(self.info_lbl)
        bot_lay.addStretch()

        # Reference-aspect buttons — instantly create a centered crop with a
        # standard ratio. Useful when targeting widescreen (16:9) or vertical
        # short-form / mobile (9:16) destinations.
        ratio_btn_qss = (
            f'QPushButton {{ background: transparent; border: 1px solid #ffffff; '
            f'color: #ffffff; font-size: 9px; font-weight: bold; '
            f'font-family: "Segoe UI"; letter-spacing: 1px; }}'
            f'QPushButton:hover {{ border-color: {Colors.ACCENT}; color: {Colors.ACCENT}; }}'
        )

        ratio_16_9 = QPushButton('16:9')
        ratio_16_9.setFixedSize(60, 30)
        ratio_16_9.setToolTip('Set crop to a centered 16:9 reference frame')
        ratio_16_9.setStyleSheet(ratio_btn_qss)
        ratio_16_9.clicked.connect(lambda: self._set_aspect_ratio(16, 9))
        bot_lay.addWidget(ratio_16_9)

        ratio_9_16 = QPushButton('9:16')
        ratio_9_16.setFixedSize(60, 30)
        ratio_9_16.setToolTip('Set crop to a centered 9:16 reference frame')
        ratio_9_16.setStyleSheet(ratio_btn_qss)
        ratio_9_16.clicked.connect(lambda: self._set_aspect_ratio(9, 16))
        bot_lay.addWidget(ratio_9_16)

        clear_btn = QPushButton('CLEAR')
        clear_btn.setFixedSize(80, 30)
        clear_btn.setStyleSheet(
            f'QPushButton {{ background: transparent; border: 1px solid #ffffff; color: #ffffff;'
            f' font-size: 9px; font-weight: bold; font-family: "Segoe UI"; letter-spacing: 1px; }}'
            f'QPushButton:hover {{ border-color: {Colors.ACCENT}; color: {Colors.ACCENT}; }}')
        clear_btn.clicked.connect(self._clear_crop)
        bot_lay.addWidget(clear_btn)

        apply_btn = QPushButton('APPLY CROP')
        apply_btn.setFixedSize(110, 30)
        apply_btn.setStyleSheet(
            f'QPushButton {{ background: {Colors.ACCENT}; border: none; color: #000000;'
            f' font-size: 9px; font-weight: bold; font-family: "Segoe UI"; letter-spacing: 1px; }}'
            f'QPushButton:hover {{ background: #ffffff; }}')
        apply_btn.clicked.connect(self._apply)
        bot_lay.addWidget(apply_btn)
        root.addWidget(bot)

    def _load_frame(self):
        """Read first frame; store pixmap without displaying (size not known yet)."""
        cap = cv2.VideoCapture(self.clip_path)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            self.frame_label.setText('Could not load frame')
            return
        self._src_h, self._src_w = frame.shape[:2]
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w      = frame_rgb.shape[:2]
        # Copy data before array goes out of scope
        qimg = QImage(frame_rgb.copy().data, w, h, w * 3, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)

    def showEvent(self, event):
        """Called after Qt has computed final widget sizes — safe to sync overlay now."""
        super().showEvent(event)
        self._sync_overlay()

    def _sync_overlay(self):
        if self._pixmap is None:
            return
        lw, lh = self.frame_label.width(), self.frame_label.height()
        if lw <= 0 or lh <= 0:
            return

        scaled = self._pixmap.scaled(QSize(lw, lh),
                                     Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        self.frame_label.setPixmap(scaled)

        sw, sh         = scaled.width(), scaled.height()
        self._offset_x = (lw - sw) // 2
        self._offset_y = (lh - sh) // 2

        self._overlay.setGeometry(0, 0, lw, lh)
        self._overlay.raise_()
        self._overlay.set_bounds(QRect(self._offset_x, self._offset_y, sw, sh))

        if self._initial_crop:
            x, y, w, h = self._initial_crop
            sc = sw / max(self._src_w, 1)
            self._overlay.set_crop(QRect(
                self._offset_x + int(x * sc),
                self._offset_y + int(y * sc),
                int(w * sc), int(h * sc),
            ))
            self.crop_rect = self._initial_crop
            self.info_lbl.setText(f'{w} × {h}  at  ({x}, {y})')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay()

    def _on_overlay_changed(self, rect: QRect):
        if self._pixmap is None:
            return
        lw, lh = self.frame_label.width(), self.frame_label.height()
        scaled = self._pixmap.scaled(QSize(lw, lh),
                                     Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        sw, sh = scaled.width(), scaled.height()
        ox, oy = self._offset_x, self._offset_y
        x1 = max(0, int((rect.left()   - ox) * self._src_w / max(sw, 1)))
        y1 = max(0, int((rect.top()    - oy) * self._src_h / max(sh, 1)))
        x2 = min(self._src_w, int((rect.right()  - ox) * self._src_w / max(sw, 1)))
        y2 = min(self._src_h, int((rect.bottom() - oy) * self._src_h / max(sh, 1)))
        w, h = x2 - x1, y2 - y1
        if w > 4 and h > 4:
            self.crop_rect = (x1, y1, w, h)
            self.info_lbl.setText(f'{w} × {h}  at  ({x1}, {y1})')
        else:
            self.crop_rect = None

    def _clear_crop(self):
        self._overlay.clear()
        self.crop_rect = None
        self.info_lbl.setText('')
        self._initial_crop = None   # don't re-populate on next resize

    def _set_aspect_ratio(self, aspect_w: int, aspect_h: int):
        """Snap the crop to a centered rectangle with the given aspect ratio.

        Sized to fill the source frame as much as possible while preserving
        the ratio — i.e. inscribed inside the displayed frame, centered.
        """
        if self._pixmap is None:
            return
        bounds = self._overlay._bounds
        if bounds.isNull() or bounds.width() <= 4 or bounds.height() <= 4:
            return

        target_ratio = aspect_w / aspect_h
        bw, bh       = bounds.width(), bounds.height()
        bounds_ratio = bw / bh

        if target_ratio > bounds_ratio:
            # Wider target than bounds — limited by width
            cw = bw
            ch = max(4, int(round(cw / target_ratio)))
        else:
            # Taller target than bounds — limited by height
            ch = bh
            cw = max(4, int(round(ch * target_ratio)))

        cx = bounds.x() + (bw - cw) // 2
        cy = bounds.y() + (bh - ch) // 2

        new_rect = QRect(cx, cy, cw, ch)
        self._overlay.set_crop(new_rect)
        self._on_overlay_changed(new_rect)

    def _apply(self):
        self.accept()


# ---------------------------------------------------------------------------
# ShareModeDialog — share mode picker (frameless QDialog, same pattern as ShareWindow)
# ---------------------------------------------------------------------------

class ShareModeDialog(QDialog):
    """
    Small frameless dialog that appears centered over the ClipViewer.
    User picks FULL QUALITY or 10 MB · DISCORD; emits mode_selected then closes.
    """
    mode_selected = Signal(bool)   # True = discord 10 MB, False = full quality

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.Tool)
        self.setFixedSize(280, 158)
        self.setStyleSheet('QDialog { background-color: #0e0e0e; }')
        self._win_drag_pos = None
        self._build_ui()
        self._center_on_parent()

    def _center_on_parent(self):
        if self.parent():
            pg = self.parent().frameGeometry()
            self.move(pg.center() - self.rect().center())
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(0)
        title = QLabel('SHARE AS')
        title.setStyleSheet(
            'color: #444444; font-size: 8px; font-weight: bold; '
            'font-family: "Segoe UI"; letter-spacing: 3px; background: transparent;'
        )
        hdr.addWidget(title)
        hdr.addStretch()
        x_btn = QPushButton('✕')
        x_btn.setFixedSize(22, 22)
        x_btn.setStyleSheet(
            'QPushButton { background: transparent; border: none; '
            'color: #444444; font-size: 11px; }'
            'QPushButton:hover { color: #cc0000; }'
        )
        x_btn.clicked.connect(self.close)
        hdr.addWidget(x_btn)
        root.addLayout(hdr)

        # Full quality button
        fq_btn = QPushButton('FULL QUALITY')
        fq_btn.setFixedHeight(40)
        fq_btn.setStyleSheet(
            'QPushButton { background-color: #ffffff; border: none; color: #000000; '
            'font-size: 10px; font-weight: bold; font-family: "Segoe UI"; '
            'letter-spacing: 1px; }'
            'QPushButton:hover { background-color: #dddddd; }'
        )
        fq_btn.clicked.connect(lambda: self._select(False))
        root.addWidget(fq_btn)

        # Discord button
        dc_btn = QPushButton('10 MB  ·  DISCORD')
        dc_btn.setFixedHeight(40)
        dc_btn.setStyleSheet(
            'QPushButton { background-color: transparent; border: 1px solid #2a2a2a; '
            'color: #666666; font-size: 10px; font-weight: bold; '
            'font-family: "Segoe UI"; letter-spacing: 1px; }'
            'QPushButton:hover { border-color: #ffffff; color: #ffffff; }'
        )
        dc_btn.clicked.connect(lambda: self._select(True))
        root.addWidget(dc_btn)

    def _select(self, discord_mode: bool):
        self.mode_selected.emit(discord_mode)
        self.close()

    # Allow dragging the dialog by clicking anywhere on it
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._win_drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._win_drag_pos and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._win_drag_pos)

    def mouseReleaseEvent(self, event):
        self._win_drag_pos = None


# ---------------------------------------------------------------------------
# ShareWindow — centered modal-ish dialog: export + drag-to-share + watermark
# ---------------------------------------------------------------------------

class ShareWindow(QDialog):
    """
    Non-modal dialog centered on ClipViewer.
    Exports the clip immediately; once done shows a thumbnail the user can
    drag directly into Discord (or any app).
    Closing before export finishes cancels the subprocess.
    A FTHRClips watermark slides in and out twice when the clip is ready.
    """

    _export_sig  = Signal(bool, str)
    export_error = Signal(str, str, str)   # title, detail, level

    def __init__(self, clip_path: str, start_s: float, end_s: float,
                 crop_rect, settings_info: dict, discord_mode: bool = False,
                 source_volumes: dict | None = None,
                 source_mutes: dict | None = None,
                 master_volume: int = 100,
                 playback_sources: tuple[PlaybackSource, ...] = (),
                 multitrack_audio: bool = False,
                 audio_tracks: tuple[tuple[str, int], ...] = (), parent=None):
        super().__init__(parent,
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.Tool)
        self._clip_path          = clip_path
        self._start_s            = start_s
        self._end_s              = end_s
        self._crop_rect          = crop_rect
        self._settings           = settings_info
        self._export_path        = None
        self._pending_export_out = None  # set before Popen, cleared after success
        self._proc               = None
        self._cancelled          = False
        self._win_drag_pos       = None
        self._discord_mode = discord_mode
        self._popup_anim   = None
        self._source_volumes = source_volumes or {}
        self._source_mutes = source_mutes or {}
        self._master_volume = master_volume
        self._playback_sources = tuple(playback_sources)
        self._preserve_tracks = not discord_mode
        # Clip-local semantic keys and FFmpeg audio-stream positions. This is
        # intentionally supplied by the clip manifest, never by fixed legacy
        # categories or by currently-running processes.
        self._audio_tracks = tuple(audio_tracks)
        self._multitrack_audio = bool(multitrack_audio and self._audio_tracks)

        self.setFixedSize(340, 242)
        self._build_ui()
        self._export_sig.connect(self._on_export_done)

        # Extract and show thumbnail before starting export
        thumb = self._make_thumbnail()
        if thumb:
            self._thumb_lbl.setPixmap(
                thumb.scaled(self._thumb_lbl.size(),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation))
        self._center_on_parent()

    def _center_on_parent(self):
        if self.parent():
            pg = self.parent().frameGeometry()
            self.move(pg.center() - self.rect().center())
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())

    def _make_thumbnail(self) -> QPixmap | None:
        """Extract a frame at ~start_s+0.5s, applying crop if set."""
        try:
            cap = cv2.VideoCapture(self._clip_path)
            fps   = cap.get(cv2.CAP_PROP_FPS) or 30
            total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            target = min(int((self._start_s + 0.5) * fps), max(0, int(total) - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                return None
            if self._crop_rect:
                x, y, w, h = self._crop_rect
                fh, fw = frame.shape[:2]
                x2 = min(fw, x + w)
                y2 = min(fh, y + h)
                frame = frame[max(0,y):y2, max(0,x):x2]
            if frame.size == 0:
                return None
            fh, fw = frame.shape[:2]
            scale  = min(310 / max(fw, 1), 174 / max(fh, 1))
            nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
            frame  = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
            rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).copy()
            qimg   = QImage(rgb.data, nw, nh, nw * 3, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(qimg)
        except Exception:
            return None

    def _build_ui(self):
        self.setStyleSheet('QDialog { background-color: #0c0c0c; }')
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header (window drag handle) ──────────────────────────────────────
        hdr = QFrame()
        hdr.setFixedHeight(38)
        hdr.setObjectName('swHdr')
        hdr.setStyleSheet(
            '#swHdr { background-color: #111111; border-bottom: 1px solid #1c1c1c; }')
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(16, 0, 8, 0)

        title_text = 'SHARE  ·  DISCORD ≤10MB' if self._discord_mode else 'SHARE CLIP'
        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet(
            'color: #444444; font-size: 8px; font-weight: bold; '
            'font-family: "Segoe UI"; letter-spacing: 2px; background: transparent;')
        hdr_lay.addWidget(title_lbl)
        hdr_lay.addStretch()

        self._status_badge = QLabel('EXPORTING...')
        self._status_badge.setStyleSheet(
            'color: #2a2a2a; font-size: 7px; font-weight: bold; '
            'font-family: "Segoe UI"; letter-spacing: 2px; background: transparent;')
        hdr_lay.addWidget(self._status_badge)
        hdr_lay.addSpacing(8)

        close_btn = QPushButton('✕')
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet(
            'QPushButton { background: transparent; border: none; color: #444444; font-size: 11px; }'
            'QPushButton:hover { color: #cc0000; }')
        close_btn.clicked.connect(self.close)
        hdr_lay.addWidget(close_btn)
        root.addWidget(hdr)

        # ── Thumbnail area (also the drag zone once ready) ───────────────────
        self._drag_zone = _DragZone(self)
        self._drag_zone.setFixedHeight(174)
        self._drag_zone.setStyleSheet('background-color: #080808;')

        dz_inner = QVBoxLayout(self._drag_zone)
        dz_inner.setContentsMargins(0, 0, 0, 0)
        dz_inner.setSpacing(0)

        self._thumb_lbl = QLabel()
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_lbl.setFixedHeight(174)
        self._thumb_lbl.setStyleSheet('background-color: #080808;')
        dz_inner.addWidget(self._thumb_lbl)

        # "DRAG TO SHARE" hint overlay on thumbnail (bottom strip)
        self._drag_hint = QLabel('⊡  DRAG TO SHARE', self._drag_zone)
        self._drag_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drag_hint.setFixedHeight(28)
        self._drag_hint.setStyleSheet(
            'background-color: rgba(0,0,0,180); color: #2e2e2e; '
            'font-size: 8px; font-weight: bold; font-family: "Segoe UI"; letter-spacing: 2px;')
        self._drag_hint.hide()

        root.addWidget(self._drag_zone)

        # ── Filename bar ─────────────────────────────────────────────────────
        fname_bar = QFrame()
        fname_bar.setFixedHeight(30)
        fname_bar.setStyleSheet('background-color: #090909; border-top: 1px solid #141414;')
        fb_lay = QHBoxLayout(fname_bar)
        fb_lay.setContentsMargins(14, 0, 14, 0)

        self._fname_lbl = QLabel('Preparing...')
        self._fname_lbl.setStyleSheet(
            'color: #2a2a2a; font-size: 8px; font-family: "Segoe UI"; background: transparent;')
        fb_lay.addWidget(self._fname_lbl)
        fb_lay.addStretch()
        root.addWidget(fname_bar)

        # ── FTHRClips corner popup (slides in over thumbnail when ready) ─────
        self._popup_lbl = QLabel('FTHRClips', self._drag_zone)
        self._popup_lbl.setStyleSheet(
            'background-color: rgba(0,0,0,170); color: #ffffff; '
            'font-size: 9px; font-weight: bold; font-family: "Segoe UI"; '
            'letter-spacing: 1px; padding: 4px 10px;')
        self._popup_lbl.adjustSize()
        _ph = self._popup_lbl.height()
        self._popup_lbl.move(340, 174 - _ph - 10)   # start off-screen right

    # ── Window dragging ──────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._win_drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._win_drag_pos and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._win_drag_pos)

    def mouseReleaseEvent(self, event):
        self._win_drag_pos = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep drag-hint pinned to bottom of thumbnail
        if hasattr(self, '_drag_hint'):
            self._drag_hint.setGeometry(0,
                                        self._drag_zone.height() - self._drag_hint.height(),
                                        self._drag_zone.width(),
                                        self._drag_hint.height())

    # ── Export ───────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        # Pin drag-hint geometry once sizes are known
        self._drag_hint.setGeometry(0,
                                    self._drag_zone.height() - self._drag_hint.height(),
                                    self._drag_zone.width(),
                                    self._drag_hint.height())
        # Start export — exactly once. showEvent fires again on minimize/
        # restore on some platforms, which would launch a second concurrent
        # ffmpeg run writing the same output file.
        if not getattr(self, '_export_started', False):
            self._export_started = True
            threading.Thread(target=self._export_worker, daemon=True).start()

    def _build_audio_filter_chain(self) -> tuple[list[str], str | None]:
        """Return (filter snippets, output label) for the per-source audio mix.

        Returns ([], None) if multi-track audio isn't present so the caller
        knows to skip the filter and fall through to a stream-copy or
        single-encode path.
        """
        if not self._multitrack_audio or self._preserve_tracks:
            return [], None
        states = {
            source.source_id: SourceMixState(
                gain_percent=self._source_volumes.get(source.source_id, 100),
                muted=self._source_mutes.get(source.source_id, False))
            for source in self._playback_sources
        }
        return ffmpeg_mix_filter(self._playback_sources, states, self._master_volume)

    def _export_worker(self):
        try:
            ffmpeg = get_ffmpeg_exe()
        except FFmpegUnavailable as e:
            self._export_sig.emit(False, str(e))
            self.export_error.emit(
                'FFMPEG NOT FOUND',
                'ffmpeg is required for export. It normally ships with FTHR Clips; '
                'if you are running from source, install it: '
                'sudo pacman -S ffmpeg (Arch) or sudo apt install ffmpeg (Debian).',
                'error',
            )
            return

        stem      = Path(self._clip_path).stem
        share_dir = Path.home() / 'FTHR_Clips' / 'Shared'
        try:
            share_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # An uncaught error here kills the worker thread silently and the
            # window sits on 'EXPORTING…' forever.
            self._export_sig.emit(False, f'Cannot create {share_dir}: {e}')
            return
        from datetime import datetime as _dt
        ts        = _dt.now().strftime('%H-%M-%S')
        suffix    = f'_discord_{ts}.mp4' if self._discord_mode else f'_share_{ts}.mp4'
        out_p     = share_dir / f'{stem}{suffix}'
        n = 2
        while out_p.exists():   # same-second re-export must not overwrite (-y)
            out_p = share_dir / f'{stem}{suffix[:-4]}_{n}.mp4'
            n += 1
        out       = str(out_p)
        staged    = str(create_staged_output_path(out_p))
        cr        = self._crop_rect
        duration_s = max(self._end_s - self._start_s, 0.1)

        audio_filters, audio_out = self._build_audio_filter_chain()

        if self._discord_mode:
            # Target ≤10 MB: calculate video bitrate that fits within the limit
            target_bits  = 10 * 1024 * 1024 * 8
            audio_kbps   = 96
            video_kbps   = max(200, int((target_bits / duration_s - audio_kbps * 1000) / 1000))
            maxrate_kbps = int(video_kbps * 1.5)
            bufsize_kbps = video_kbps * 2

            # Always re-encode for size cap. Build a unified -filter_complex
            # combining (optional) crop, the even-dim scale, and (optional)
            # per-source audio mix. -vf can't be combined with -filter_complex
            # so everything lives in the latter.
            v_filters = []
            if cr:
                x, y, w, h = cr
                w &= ~1; h &= ~1
                v_filters.append(f'crop={w}:{h}:{x}:{y}')
            v_filters.append('scale=trunc(iw/2)*2:trunc(ih/2)*2')

            fc = [f'[0:v]{",".join(v_filters)}[vout]']
            fc += audio_filters

            cmd = [ffmpeg, '-y',
                   '-ss', str(self._start_s), '-i', self._clip_path,
                   '-t', str(duration_s),
                   '-filter_complex', ';'.join(fc),
                   '-map', '[vout]',
                   '-map', audio_out if audio_out else '0:a?',
                   *software_video_args(bitrate_kbps=video_kbps),
                   '-maxrate', f'{maxrate_kbps}k',
                   '-bufsize', f'{bufsize_kbps}k',
                   '-c:a', 'aac', '-b:a', f'{audio_kbps}k', staged]
        elif cr or audio_out:
            # Crop or mix needed — re-encode the affected stream(s).
            filters = []
            if cr:
                x, y, w, h = cr
                w &= ~1; h &= ~1
                filters.append(f'[0:v]crop={w}:{h}:{x}:{y}[vout]')
            filters += audio_filters

            cmd = [ffmpeg, '-y',
                   '-ss', str(self._start_s), '-i', self._clip_path,
                   '-t', str(duration_s),
                   '-filter_complex', ';'.join(filters),
                   '-map', '[vout]' if cr else '0:v:0',
                   '-map', audio_out if audio_out else '0:a?']
            if cr:
                cmd += software_video_args()
            else:
                cmd += ['-c:v', 'copy']
            if audio_out:
                cmd += ['-c:a', 'aac', '-b:a', '192k']
            else:
                cmd += ['-c:a', 'copy']
            cmd.append(staged)
        else:
            cmd = [ffmpeg, '-y',
                   '-ss', str(self._start_s), '-i', self._clip_path,
                   '-t', str(duration_s),
                   # FFmpeg's automatic stream choice keeps only one audio
                   # stream. Preserve every existing video/audio stream in a
                   # no-edit share rather than silently deleting future stems.
                   '-map', '0:v?', '-map', '0:a?', '-c', 'copy', staged]

        self._pending_export_out = staged
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **_NO_WINDOW,
            )
            _, stderr = self._proc.communicate()
            if self._cancelled:
                discard_staged_output(staged)
                return
            if self._proc.returncode == 0:
                commit_staged_output(staged, out)
                self._pending_export_out = None
                self._export_sig.emit(True, out)
            else:
                err  = stderr.decode(errors='replace').strip()
                last = next((l for l in reversed(err.splitlines()) if l.strip()), err[:120])
                discard_staged_output(staged)
                self._pending_export_out = None
                self._export_sig.emit(False, last)
        except Exception as e:
            discard_staged_output(staged)
            self._pending_export_out = None
            if not self._cancelled:
                self._export_sig.emit(False, str(e))

    def _on_export_done(self, success: bool, msg: str):
        if self._cancelled:
            return
        if success:
            self._export_path = msg
            fname = Path(msg).name
            if len(fname) > 42:
                fname = fname[:40] + '…'
            self._fname_lbl.setText(fname)
            self._fname_lbl.setStyleSheet(
                'color: #444444; font-size: 8px; font-family: "Segoe UI"; background: transparent;')
            self._status_badge.setText('READY')
            self._status_badge.setStyleSheet(
                f'color: {Colors.ACCENT}; font-size: 7px; font-weight: bold; '
                f'font-family: "Segoe UI"; letter-spacing: 2px; background: transparent;')
            self._drag_zone.set_file(self._export_path)
            self._drag_hint.show()
            self._drag_hint.raise_()
            QTimer.singleShot(300, self._animate_popup_in)
        else:
            short = msg if len(msg) <= 44 else msg[:42] + '…'
            self._fname_lbl.setText(short)
            self._fname_lbl.setStyleSheet(
                'color: #cc0000; font-size: 8px; font-family: "Segoe UI"; background: transparent;')
            self._status_badge.setText('FAILED')
            self._status_badge.setStyleSheet(
                'color: #cc0000; font-size: 7px; font-weight: bold; '
                'font-family: "Segoe UI"; letter-spacing: 2px; background: transparent;')
            self.export_error.emit(
                'EXPORT FAILED',
                'FFmpeg returned an error. The output file may be incomplete.',
                'warning',
            )

    # ── FTHRClips corner popup animation ────────────────────────────────────

    def _animate_popup_in(self):
        if self._cancelled:
            return
        lbl = self._popup_lbl
        pw, ph = lbl.width(), lbl.height()
        target_x = 340 - pw - 10
        target_y = 174 - ph - 10
        lbl.move(340, target_y)
        lbl.raise_()
        anim = QPropertyAnimation(lbl, b'pos', self)
        anim.setDuration(380)
        anim.setStartValue(QPoint(340, target_y))
        anim.setEndValue(QPoint(target_x, target_y))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: QTimer.singleShot(2500, self._animate_popup_out))
        self._popup_anim = anim
        anim.start()

    def _animate_popup_out(self):
        if self._cancelled:
            return
        lbl = self._popup_lbl
        anim = QPropertyAnimation(lbl, b'pos', self)
        anim.setDuration(280)
        anim.setStartValue(QPoint(lbl.x(), lbl.y()))
        anim.setEndValue(QPoint(340, lbl.y()))
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._popup_anim = anim
        anim.start()

    # ── Cleanup on close ────────────────────────────────────────────────────

    def closeEvent(self, event):
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()
        # Remove the partially-written output file if the user closed before
        # the export finished (ffmpeg was killed mid-write above).
        pending = self._pending_export_out
        if pending:
            self._pending_export_out = None
            try:
                os.remove(pending)
            except OSError:
                pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# VolumePopup — frameless dropdown with per-source mix sliders
# ---------------------------------------------------------------------------

class VolumePopup(QDialog):
    """
    Floating volume popup. Per-source labels are shown only when a verified
    clip-side manifest proves their stream identity.
    """

    master_changed = Signal(int)            # 0–100
    source_changed = Signal(str, int)       # (source_key, 0–100)
    source_muted = Signal(str, bool)        # (source_key, muted)

    _SOURCES = [('master', 'MASTER')]

    def __init__(self, master_vol: int,
                 source_volumes: dict | None = None,
                 source_mutes: dict | None = None,
                 source_tracks: tuple[PlaybackSource, ...] = (),
                 live_preview: bool = False,
                 parent=None):
        super().__init__(parent,
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.Popup)
        self.setStyleSheet(
            'QDialog { background-color: #0a0a0a; border: 1px solid #ffffff; }')
        self._sliders:  dict[str, QSlider] = {}
        self._values:   dict[str, QLabel]  = {}
        self._labels:   dict[str, QLabel]  = {}
        self._mute_buttons: dict[str, QPushButton] = {}
        self._row_order: list[str] = []
        self._source_tracks = tuple(source_tracks)
        self._source_mutes = source_mutes or {}
        self._build_ui(master_vol, source_volumes or {}, live_preview)
        self.setFixedSize(self.sizeHint())

    def _build_ui(self, master_vol: int, source_volumes: dict, live_preview: bool):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        title = QLabel('AUDIO MIX')
        title.setStyleSheet(
            'color: #ffffff; font-size: 8px; font-weight: bold; '
            'font-family: "Segoe UI"; letter-spacing: 2px; background: transparent;')
        root.addWidget(title)

        sources = [(key, label, None) for key, label in self._SOURCES]
        sources.extend((source.source_id, self._display_label(source), source)
                       for source in self._source_tracks)
        for key, label, source in sources:
            self._row_order.append(key)
            row = QHBoxLayout()
            row.setSpacing(10)

            if source is not None:
                icon_key = source_icon_key(source)
                # Manifest icon references are semantic/privacy-safe keys, not
                # file paths.  A compact glyph keeps the existing panel layout
                # while reflecting known keys and giving imports an honest
                # generic fallback.
                glyph = {
                    'system': '⌁', 'microphone': '●',
                    'application': '◆', 'track': '◌',
                }[icon_key]
                icon = QLabel(glyph)
                icon.setFixedWidth(14)
                icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
                icon.setToolTip(f'{icon_key.title()} audio source')
                icon.setStyleSheet(
                    f'color: {Colors.ACCENT}; font-size: 11px; '
                    'background: transparent;')
                row.addWidget(icon)

            label_width = 76 if source is None else 78
            lbl = QLabel()
            lbl.setFixedWidth(label_width)
            lbl.setStyleSheet(
                'color: #ffffff; font-size: 8px; font-weight: bold; '
                'font-family: "Segoe UI"; letter-spacing: 1px; background: transparent;')
            visible_label = QFontMetrics(lbl.font()).elidedText(
                label, Qt.TextElideMode.ElideRight, label_width)
            lbl.setText(visible_label)
            lbl.setToolTip(label)
            lbl.setAccessibleName(f'{label} source volume')
            if source is not None:
                self._labels[key] = lbl
                if not source.available:
                    lbl.setStyleSheet(
                        'color: #666666; font-size: 8px; font-weight: bold; '
                        'font-family: "Segoe UI"; letter-spacing: 1px; background: transparent;')
                    lbl.setToolTip(f'{label} is unavailable in this clip.')
            row.addWidget(lbl)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setFixedWidth(140)
            if key == 'master':
                slider.setValue(master_vol)
            else:
                slider.setValue(int(source_volumes.get(key, 100)))
                slider.setToolTip(
                    f'{label} gain for this open clip only.')
                slider.setAccessibleName(f'{label} volume')
                slider.setEnabled(bool(source and source.available))
            slider.valueChanged.connect(
                lambda v, k=key, lab=label: self._on_changed(k, v, lab))
            self._sliders[key] = slider
            row.addWidget(slider)

            value_lbl = QLabel(f'{slider.value()}%')
            value_lbl.setFixedWidth(36)
            value_lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value_lbl.setStyleSheet(
                f'color: {Colors.ACCENT if source is None or source.available else "#666666"}; font-size: 9px; '
                f'font-family: "Segoe UI"; background: transparent;')
            if source is not None and not source.available:
                value_lbl.setText('—')
                value_lbl.setToolTip('The stored audio stream is unavailable.')
            self._values[key] = value_lbl
            row.addWidget(value_lbl)

            if source is not None:
                mute = QPushButton('MUTE')
                mute.setCheckable(True)
                mute.setChecked(bool(self._source_mutes.get(key, False)))
                mute.setText('MUTED' if mute.isChecked() else 'MUTE')
                mute.setEnabled(source.available)
                mute.setFixedWidth(42)
                mute.setAccessibleName(f'Mute {label}')
                mute.setStyleSheet(
                    'QPushButton { color: #777777; background: transparent; border: none; '
                    'font-size: 7px; font-weight: bold; } '
                    'QPushButton:checked { color: #cc0000; }')
                self._mute_buttons[key] = mute
                mute.toggled.connect(
                    lambda checked, k=key, button=mute: self._on_mute_toggled(k, button, checked))
                row.addWidget(mute)

            root.addLayout(row)

        unavailable = sum(not source.available for source in self._source_tracks)
        note_text = ('Live editable mix — changes apply only while this clip is open'
                     if self._source_tracks and live_preview and not unavailable else
                     f'{unavailable} source{"s" if unavailable != 1 else ""} unavailable; '
                     'remaining tracks continue to play'
                     if self._source_tracks and live_preview else
                     'Track controls unavailable; preview falls back to container audio'
                     if self._source_tracks else 'No audio tracks found in this clip')
        note = QLabel(note_text)
        note.setStyleSheet(
            'color: #666666; font-size: 8px; font-style: italic; '
            'font-family: "Segoe UI"; background: transparent;')
        root.addWidget(note)

        self.setStyleSheet(self.styleSheet() + f'''
            QSlider::groove:horizontal {{ background: #333333; height: 2px; }}
            QSlider::handle:horizontal {{
                background: {Colors.ACCENT}; width: 10px; height: 10px;
                margin: -4px 0; border-radius: 0px;
            }}
            QSlider::handle:horizontal:hover {{ background: #ffffff; }}
            QSlider::sub-page:horizontal {{ background: {Colors.ACCENT}; }}
        ''')

    @staticmethod
    def _display_label(source: PlaybackSource) -> str:
        return source_display_name(source).upper()

    def _on_changed(self, key: str, value: int, _label: str):
        self._values[key].setText(f'{value}%')
        if key == 'master':
            self.master_changed.emit(value)
        else:
            self.source_changed.emit(key, value)

    def _on_mute_toggled(self, key: str, button: QPushButton, muted: bool):
        button.setText('MUTED' if muted else 'MUTE')
        self.source_muted.emit(key, muted)

    def show_above(self, anchor_widget: QWidget):
        """Pop up just above the anchor widget so it doesn't overlap the trim bar."""
        size = self.size()
        anchor_top = anchor_widget.mapToGlobal(QPoint(0, 0))
        x = anchor_top.x() + (anchor_widget.width() - size.width()) // 2
        y = anchor_top.y() - size.height() - 6
        self.move(max(8, x), max(8, y))
        self.show()


# ---------------------------------------------------------------------------
# ClipViewer
# ---------------------------------------------------------------------------

class ClipViewer(QDialog):
    """Full FTHR clip editor — video preview, trim bar, export/delete/crop sidebar."""

    _export_done     = Signal(bool, str)
    upload_requested = Signal(str)
    export_error     = Signal(str, str, str)   # title, detail, level

    def __init__(self, clip_path: str, bridge, parent=None,
                 thumb_pixmap: QPixmap = None, settings_manager=None,
                 upload_enabled: bool = False, metadata_manager=None,
                 linked_import: bool = False):
        super().__init__(parent)
        self.clip_path       = clip_path
        self.bridge          = bridge
        self.sm              = settings_manager
        self._mm             = metadata_manager
        self._upload_enabled = upload_enabled
        self._linked_import = linked_import
        self._crop_rect    = None
        self._thumb_pixmap = thumb_pixmap
        self._thumb_overlay: QLabel | None = None
        self._thumb_fade_anim: QPropertyAnimation | None = None
        # The source rows are filled asynchronously from either a hash-bound
        # FTHR manifest or actual container metadata. Imported media therefore
        # gets generic track labels instead of fabricated app identities.
        self._playback_sources: tuple[PlaybackSource, ...] = ()
        self._audio_tracks: tuple[tuple[str, int], ...] = ()
        self._audio_mixer: FFmpegPlaybackController | None = None
        self._audio_mixer_live = False

        self.setWindowTitle(f'FTHR — {Path(clip_path).name}')
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setMinimumSize(960, 600)
        self._drag_pos: QPoint | None = None
        self._native_style_applied = False

        # Try the metadata cache that clip_grid maintains as a side effect of
        # building each thumbnail. On a cache hit we skip cv2.VideoCapture
        # entirely — that single call is the biggest contributor to first-open
        # lag (cold cv2 + FFmpeg demux is typically 50–150 ms on the UI thread).
        meta = None
        try:
            from ui.clip_grid import get_cached_clip_metadata
            meta = get_cached_clip_metadata(clip_path)
        except Exception:
            meta = None

        if meta and meta[1] > 0 and meta[2] > 0 and meta[3] > 0:
            duration_sec, w, h, fps = meta
            self._src_w  = int(w)
            self._src_h  = int(h)
            self._fps    = float(fps)
            self.duration_ms = max(int(duration_sec * 1000), 1)
        else:
            cap          = cv2.VideoCapture(clip_path)
            fps          = cap.get(cv2.CAP_PROP_FPS) or 60
            frames       = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            self._src_w  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self._src_h  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._fps    = fps
            cap.release()
            self.duration_ms = max(int(frames / fps * 1000), 1)

        try:
            self._size_mb = os.path.getsize(clip_path) / (1024 * 1024)
        except OSError:
            self._size_mb = 0.0
        try:
            self._file_date = datetime.fromtimestamp(os.path.getmtime(clip_path))
        except OSError:
            self._file_date = datetime.now()

        self._export_done.connect(self._on_export_done)

        self._setup_ui()
        self._apply_styles()
        self._refresh_quick_crop_btn()

        # setWindowOpacity not supported on Wayland — skip fade there
        from PySide6.QtWidgets import QApplication as _App
        _app = _App.instance()
        _wayland = _app and _app.platformName() == 'wayland'
        self._fade_in_anim = None
        if not _wayland:
            self.setWindowOpacity(0.0)
            self._fade_in_anim = QPropertyAnimation(self, b'windowOpacity')
            self._fade_in_anim.setDuration(180)
            self._fade_in_anim.setStartValue(0.0)
            self._fade_in_anim.setEndValue(1.0)
            self._fade_in_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._ph_timer = QTimer(self)
        self._ph_timer.setInterval(80)
        self._ph_timer.timeout.connect(self._update_playhead)
        self._ph_timer.start()

        # ── Seek/play/pause coalescing ────────────────────────────────────
        # QMediaPlayer (Windows MediaFoundation backend) cannot keep up if
        # setPosition is called dozens of times per second while a slider is
        # dragged — the result is a frozen video output or, worst case, a
        # native crash inside MFMediaSession. We coalesce all seek requests
        # through a 50 ms single-shot timer that holds only the latest target
        # position; the user perceives this as immediate but the player only
        # ever sees one in-flight seek at a time.
        self._pending_seek_ms: int | None = None
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(50)
        self._seek_timer.timeout.connect(self._flush_pending_seek)

        # Guard against re-entrant play/pause calls. setChecked() on the
        # QPushButton emits clicked, so toggling the icon programmatically
        # used to recurse back into _toggle_play.
        self._play_pause_busy = False
        self._audio_sources_discovered.connect(self._on_audio_sources_discovered)
        QTimer.singleShot(0, self._discover_audio_sources_async)

    # =========================================================================
    # Show / fade-in (consolidated showEvent lives near nativeEvent below)
    # =========================================================================

    def _install_thumb_overlay(self):
        host = self._video_clip_frame
        overlay = QLabel(host)
        overlay.setGeometry(0, 0, host.width(), host.height())
        overlay.setScaledContents(False)
        overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay.setStyleSheet('background-color: #000000; border: none;')
        overlay.setPixmap(
            self._thumb_pixmap.scaled(
                host.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        overlay.show()
        overlay.raise_()
        self._thumb_overlay = overlay
        QTimer.singleShot(500, self._fade_out_thumb_overlay)

    def _fade_out_thumb_overlay(self):
        if self._thumb_overlay is None:
            return
        effect = QGraphicsOpacityEffect(self._thumb_overlay)
        effect.setOpacity(1.0)
        self._thumb_overlay.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b'opacity', self._thumb_overlay)
        anim.setDuration(350)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self._remove_thumb_overlay)
        self._thumb_fade_anim = anim
        anim.start()

    def _remove_thumb_overlay(self):
        if self._thumb_overlay:
            self._thumb_overlay.deleteLater()
            self._thumb_overlay = None

    # =========================================================================
    # UI layout
    # =========================================================================

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(40)
        header.setObjectName('editorHeader')
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(20, 0, 0, 0)
        hdr.setSpacing(0)

        title = QLabel(Path(self.clip_path).stem[:50].upper())
        title.setObjectName('editorTitle')
        hdr.addWidget(title)
        hdr.addStretch()

        min_btn = QPushButton('—')
        min_btn.setObjectName('editorMin')
        min_btn.setFixedSize(40, 40)
        min_btn.clicked.connect(self.showMinimized)
        hdr.addWidget(min_btn)

        close_btn = QPushButton('✕')
        close_btn.setObjectName('editorClose')
        close_btn.setFixedSize(40, 40)
        close_btn.clicked.connect(self._close)
        hdr.addWidget(close_btn)
        root.addWidget(header)

        # Drag/double-click on the header background (buttons absorb their own clicks)
        header.mousePressEvent       = self._bar_mouse_press
        header.mouseMoveEvent        = self._bar_mouse_move
        header.mouseReleaseEvent     = self._bar_mouse_release
        header.mouseDoubleClickEvent = self._bar_double_click
        self._editor_header = header
        self._editor_header_h = 40

        # ── Main ─────────────────────────────────────────────────────────────
        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # ── Video column ─────────────────────────────────────────────────────
        vid_col = QVBoxLayout()
        vid_col.setContentsMargins(0, 0, 0, 0)
        vid_col.setSpacing(0)

        # Clipping container — children rendered outside this rect are clipped.
        # The video_widget is positioned absolutely inside it so we can
        # scale/offset it to show only the cropped region while keeping the
        # outer area pure black.
        self._video_clip_frame = QFrame()
        self._video_clip_frame.setObjectName('videoClipFrame')
        self._video_clip_frame.setStyleSheet(
            'QFrame#videoClipFrame { background-color: #000000; border: none; }')
        self._video_clip_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.video_widget = QVideoWidget(self._video_clip_frame)
        self.video_widget.setStyleSheet('background-color: #000000;')
        self.video_widget.setGeometry(0, 0, 1, 1)

        vid_col.addWidget(self._video_clip_frame, stretch=1)

        # Crop preview overlay — always visible when a crop is set so the
        # user instantly sees what region will be exported. Qt's QVideoWidget
        # geometry changes are not reliably picked up by the video pipeline
        # while paused, so we keep the player at full size and dim the
        # discarded regions on top of it.
        self._crop_preview = CropPreviewOverlay(
            self._src_w, self._src_h, self._video_clip_frame)
        self._crop_preview.setGeometry(0, 0, 1, 1)
        self._crop_preview.hide()

        self._video_clip_frame.installEventFilter(self)


        ctrl = QFrame()
        ctrl.setObjectName('ctrlBar')
        ctrl.setFixedHeight(46)
        ctrl_lay = QHBoxLayout(ctrl)
        ctrl_lay.setContentsMargins(16, 0, 16, 0)
        ctrl_lay.setSpacing(12)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName('playBtn')
        self.play_btn.setCheckable(True)
        self.play_btn.setFixedWidth(96)
        self.play_btn.clicked.connect(self._toggle_play)
        _play_ico_path = Path(__file__).parent.parent / 'assets' / 'icons' / 'play.png'
        _pause_ico_path = Path(__file__).parent.parent / 'assets' / 'icons' / 'pause.png'
        self._play_icon  = QIcon(QPixmap(str(_play_ico_path)).scaled(
            16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )) if _play_ico_path.exists() else QIcon()
        self._pause_icon = QIcon(QPixmap(str(_pause_ico_path)).scaled(
            16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )) if _pause_ico_path.exists() else QIcon()
        if not self._play_icon.isNull():
            self.play_btn.setIcon(self._play_icon)
            self.play_btn.setIconSize(QSize(16, 16))
        else:
            self.play_btn.setText('▶  PLAY')
        ctrl_lay.addWidget(self.play_btn)

        self.time_label = QLabel(f'0:00 / {self._fmt(self.duration_ms)}')
        self.time_label.setObjectName('timeLabel')
        ctrl_lay.addWidget(self.time_label)
        ctrl_lay.addStretch()

        # Volume dropdown — master playback level only for the alpha.
        if self.sm:
            self._master_volume = int(self.sm.get('master_volume', 80))
        else:
            self._master_volume = 80
        self._source_volumes: dict[str, int] = {}
        self._source_mutes: dict[str, bool] = {}
        self._multitrack_audio = False
        self.vol_btn = QPushButton(f'VOL  {self._master_volume}%  ▾')
        self.vol_btn.setObjectName('volBtn')
        self.vol_btn.setFixedHeight(28)
        self.vol_btn.clicked.connect(self._toggle_volume_popup)
        ctrl_lay.addWidget(self.vol_btn)
        self._volume_popup: VolumePopup | None = None
        vid_col.addWidget(ctrl)

        trim_frame = QFrame()
        trim_frame.setObjectName('trimBar')
        trim_lay = QVBoxLayout(trim_frame)
        trim_lay.setContentsMargins(16, 8, 16, 12)
        trim_lay.setSpacing(4)

        trim_hdr = QHBoxLayout()
        trim_hdr.setSpacing(0)
        trim_lbl = QLabel('TRIM  ·  DRAG TEAL MARKER TO SEEK')
        trim_lbl.setObjectName('trimLabel')
        trim_hdr.addWidget(trim_lbl)
        trim_hdr.addStretch()
        self.trim_range_lbl = QLabel(f'0:00 → {self._fmt(self.duration_ms)}')
        self.trim_range_lbl.setObjectName('trimRangeLbl')
        trim_hdr.addWidget(self.trim_range_lbl)
        trim_lay.addLayout(trim_hdr)

        self.trim_slider = TrimSlider(self.duration_ms)
        self.trim_slider.range_changed.connect(self._on_trim_changed)
        self.trim_slider.seek_requested.connect(self._on_seek_requested)
        trim_lay.addWidget(self.trim_slider)
        vid_col.addWidget(trim_frame)
        main.addLayout(vid_col, stretch=1)

        # ── Sidebar (Medal-style) ───────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setObjectName('editorSidebar')
        sidebar.setFixedWidth(304)

        # Wrap sidebar contents in a scroll-friendly column. Sticky bottom
        # holds the Delete button; everything else lives in the scroll body.
        sb_outer = QVBoxLayout(sidebar)
        sb_outer.setContentsMargins(20, 20, 20, 20)
        sb_outer.setSpacing(10)

        # ── PRIMARY ACTIONS ──
        self.share_btn = QPushButton('Share clip')
        self.share_btn.setObjectName('shareBtn')
        self.share_btn.setFixedHeight(42)
        self.share_btn.clicked.connect(self._show_share_overlay)
        sb_outer.addWidget(self.share_btn)

        self.export_btn = QPushButton('Export')
        self.export_btn.setObjectName('exportBtn')
        self.export_btn.setFixedHeight(38)
        self.export_btn.clicked.connect(self._export_clip)
        sb_outer.addWidget(self.export_btn)

        self.upload_btn = QPushButton('Upload')
        self.upload_btn.setObjectName('uploadBtn')
        self.upload_btn.setFixedHeight(38)
        self.upload_btn.setVisible(self._upload_enabled)
        self.upload_btn.clicked.connect(self._on_upload_click)
        sb_outer.addWidget(self.upload_btn)

        sb_outer.addSpacing(6)

        # ── CLIP DETAILS panel (collapsible, expanded by default) ──
        details_panel, details_body = self._build_collapsible_panel('CLIP DETAILS', expanded=True)
        dpb = QVBoxLayout()
        dpb.setContentsMargins(14, 4, 14, 14)
        dpb.setSpacing(10)

        dur_str  = self._fmt(self.duration_ms)
        date_str = self._file_date.strftime('%b %d, %Y  %H:%M')
        fname    = Path(self.clip_path).name
        title_text = fname.rsplit('.', 1)[0]
        if len(title_text) > 38:
            title_text = title_text[:36] + '…'

        # Title
        dpb.addWidget(self._kv_label('Title'))
        title_val = QLabel(title_text)
        title_val.setObjectName('detailValBig')
        title_val.setWordWrap(True)
        dpb.addWidget(title_val)

        # Tag
        dpb.addWidget(self._kv_label('Tag'))
        meta = self._mm.get(self.clip_path) if self._mm else {'tag': '', 'description': ''}
        _edit_style = (
            f'QLineEdit#detailEdit {{ background: transparent; border: none;'
            f' border-bottom: 1px solid {Colors.TEXT_DIM}; color: {Colors.TEXT};'
            f' font-size: 12px; padding: 2px 0; }}'
            f'QLineEdit#detailEdit:focus {{ border-bottom-color: {Colors.ACCENT}; }}'
        )
        tag_edit = QLineEdit(meta['tag'])
        tag_edit.setPlaceholderText('e.g. Gaming, Highlight …')
        tag_edit.setObjectName('detailEdit')
        tag_edit.setStyleSheet(_edit_style)
        if self._mm:
            tag_edit.editingFinished.connect(
                lambda: self._mm.set(self.clip_path, tag=tag_edit.text().strip())
            )
        dpb.addWidget(tag_edit)

        # Description
        dpb.addWidget(self._kv_label('Description'))
        desc_edit = QLineEdit(meta['description'])
        desc_edit.setPlaceholderText('Short clip description …')
        desc_edit.setObjectName('detailEdit')
        desc_edit.setStyleSheet(_edit_style)
        if self._mm:
            desc_edit.editingFinished.connect(
                lambda: self._mm.set(self.clip_path, description=desc_edit.text().strip())
            )
        dpb.addWidget(desc_edit)

        details_body.addLayout(dpb)
        sb_outer.addWidget(details_panel)

        # ── SHARING ACTIVITY panel ──
        share_panel, share_body = self._build_collapsible_panel('SHARING ACTIVITY', expanded=True)
        spb = QVBoxLayout()
        spb.setContentsMargins(14, 4, 14, 14)
        spb.setSpacing(8)
        no_share = QLabel('Not shared yet')
        no_share.setObjectName('detailVal')
        spb.addWidget(no_share)
        share_body.addLayout(spb)
        sb_outer.addWidget(share_panel)

        # ── FILE DETAILS panel ──
        file_panel, file_body = self._build_collapsible_panel('FILE DETAILS', expanded=True)
        fpb = QVBoxLayout()
        fpb.setContentsMargins(14, 4, 14, 14)
        fpb.setSpacing(10)

        # Created
        fpb.addWidget(self._kv_label('Created'))
        fpb.addWidget(self._kv_value(date_str))
        # Quality
        fpb.addWidget(self._kv_label('Video Quality'))
        fpb.addWidget(self._kv_value(f'{self._src_w}x{self._src_h}, {int(self._fps)}fps'))
        # Duration
        fpb.addWidget(self._kv_label('Duration'))
        fpb.addWidget(self._kv_value(dur_str))
        # Size
        fpb.addWidget(self._kv_label('Size'))
        fpb.addWidget(self._kv_value(f'{self._size_mb:.1f} MB'))
        # Location (truncated)
        fpb.addWidget(self._kv_label('Location'))
        location_path = str(Path(self.clip_path).parent)
        if len(location_path) > 36:
            location_path = '…' + location_path[-34:]
        fpb.addWidget(self._kv_value(location_path))

        file_body.addLayout(fpb)
        sb_outer.addWidget(file_panel)

        # ── CROP / QUICK CROP / SAVE QC (kept under details panels) ──
        crop_row = QHBoxLayout()
        crop_row.setSpacing(6)
        self.crop_btn = QPushButton('SET CROP')
        self.crop_btn.setObjectName('cropBtn')
        self.crop_btn.setFixedHeight(32)
        self.crop_btn.clicked.connect(self._open_crop_dialog)
        crop_row.addWidget(self.crop_btn, stretch=1)
        self.quick_crop_btn = QPushButton('QUICK')
        self.quick_crop_btn.setObjectName('quickCropBtn')
        self.quick_crop_btn.setFixedSize(62, 32)
        self.quick_crop_btn.setToolTip('Apply saved Quick Crop')
        self.quick_crop_btn.clicked.connect(self._apply_quick_crop)
        crop_row.addWidget(self.quick_crop_btn)
        sb_outer.addLayout(crop_row)

        self.save_qc_btn = QPushButton('SAVE AS QUICK CROP')
        self.save_qc_btn.setObjectName('saveQcBtn')
        self.save_qc_btn.setFixedHeight(26)
        self.save_qc_btn.setEnabled(False)
        self.save_qc_btn.clicked.connect(self._save_quick_crop)
        sb_outer.addWidget(self.save_qc_btn)

        self.crop_info_lbl = QLabel('')
        self.crop_info_lbl.setObjectName('cropInfoLbl')
        self.crop_info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb_outer.addWidget(self.crop_info_lbl)

        sb_outer.addStretch(1)

        self.delete_btn = QPushButton('Delete')
        self.delete_btn.setObjectName('deleteBtn')
        self.delete_btn.setFixedHeight(36)
        self.delete_btn.clicked.connect(self._delete_clip)
        if self._linked_import:
            self.delete_btn.setText('Linked original — protected')
            self.delete_btn.setEnabled(False)
            self.delete_btn.setToolTip(
                'Remove its import folder from FTHR; the original stays on disk.')
        sb_outer.addWidget(self.delete_btn)

        main.addWidget(sidebar)
        root.addLayout(main, stretch=1)

        # ── Media player ──────────────────────────────────────────────────────
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_output.setVolume(self._master_volume / 100.0)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        try:
            self.player.setSource(QUrl.fromLocalFile(self.clip_path))
        except Exception as e:
            print(f'[ClipViewer] setSource failed: {e}')
        self.player.playbackStateChanged.connect(self._on_state_changed)
        # Surface backend errors instead of letting them propagate up as a
        # hard crash. This connects QMediaPlayer.errorOccurred where available
        # (Qt 6.5+); older bindings simply ignore the AttributeError.
        try:
            self.player.errorOccurred.connect(self._on_player_error)
        except AttributeError:
            pass

    # =========================================================================
    # Event filter — keep video_widget + crop overlay sized to the clip frame
    # =========================================================================

    def eventFilter(self, obj, event):
        if obj is self._video_clip_frame and event.type() == QEvent.Type.Resize:
            self._relayout_video()
        return super().eventFilter(obj, event)

    def _relayout_video(self):
        """
        Position the QVideoWidget inside its clipping frame and update the
        crop-preview overlay.

        Strategy: keep the video at full container size at all times and draw
        a teal border around the crop region via CropPreviewOverlay. The user
        sees the full frame plus a clear marker for what will be exported,
        instead of having the rest of the video hidden behind a black mask.
        """
        cw = self._video_clip_frame.width()
        ch = self._video_clip_frame.height()
        if cw <= 0 or ch <= 0:
            return

        self.video_widget.setGeometry(0, 0, cw, ch)

        self._crop_preview.setGeometry(0, 0, cw, ch)
        if self._crop_rect:
            self._crop_preview.set_crop(self._crop_rect)
            self._crop_preview.show()
            self._crop_preview.raise_()
        else:
            self._crop_preview.set_crop(None)
            self._crop_preview.hide()

    # =========================================================================
    # Sidebar helpers (collapsible panels + key/value labels)
    # =========================================================================

    def _build_collapsible_panel(self, title: str, expanded: bool = True):
        """
        Returns (panel_frame, body_layout). Body_layout is where callers add
        their content. Header click toggles visibility of an inner widget.
        """
        panel = QFrame()
        panel.setObjectName('detailsPanel')
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header_btn = QPushButton(f'{title}     —' if expanded else f'{title}     +')
        header_btn.setObjectName('panelHeader')
        header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_btn.setCheckable(True)
        header_btn.setChecked(expanded)
        outer.addWidget(header_btn)

        body_widget = QFrame()
        body_widget.setObjectName('panelBody')
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_widget.setVisible(expanded)
        outer.addWidget(body_widget)

        def _toggle():
            shown = body_widget.isVisible()
            body_widget.setVisible(not shown)
            header_btn.setText(f'{title}     {"+" if shown else "—"}')
        header_btn.clicked.connect(_toggle)

        return panel, body_layout

    def _kv_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName('detailKey')
        return lbl

    def _kv_value(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName('detailVal')
        lbl.setWordWrap(True)
        return lbl

    # =========================================================================
    # Styles
    # =========================================================================

    def _apply_styles(self):
        # Medal-style clip viewer — dark canvas, accented primary action,
        # rounded panels, monochrome secondary actions, error tone for delete.
        self.setStyleSheet(f'''
            QDialog {{ background-color: {Colors.BG}; }}

            /* Header */
            QFrame#editorHeader {{
                background-color: {Colors.SHELL_BG};
                border-bottom: 1px solid {Colors.SHELL_DIVIDER};
            }}
            QLabel#editorTitle {{
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_LABEL}px; font-weight: bold;
                font-family: {Fonts.DISPLAY}; letter-spacing: 2px;
                background: transparent;
            }}
            QPushButton#editorMin {{
                background: transparent; border: none; color: {Colors.TEXT_DIM};
                font-size: 14px; font-weight: bold;
            }}
            QPushButton#editorMin:hover {{
                background-color: {Colors.SURFACE_3}; color: {Colors.TEXT};
            }}
            QPushButton#editorClose {{
                background: transparent; border: none; color: {Colors.TEXT_DIM}; font-size: 13px;
            }}
            QPushButton#editorClose:hover {{ background-color: {Colors.ERROR}; color: {Colors.TEXT}; }}

            QVideoWidget {{ background-color: #000000; }}

            /* Control + trim bars */
            QFrame#ctrlBar {{
                background-color: {Colors.SURFACE_1};
                border-top: 1px solid {Colors.SHELL_DIVIDER};
            }}
            QPushButton#playBtn {{
                background-color: {Colors.SURFACE_2}; border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_LABEL}px;
                font-family: {Fonts.DISPLAY}; font-weight: bold;
                letter-spacing: 1px; padding: 5px 14px;
            }}
            QPushButton#playBtn:hover {{ border-color: {Colors.ACCENT}; color: {Colors.ACCENT}; }}
            QPushButton#playBtn:checked {{
                background-color: {Colors.ACCENT}; border-color: {Colors.ACCENT}; color: {Colors.BG};
            }}
            QLabel#timeLabel {{
                color: {Colors.TEXT_DIM}; font-size: {Fonts.SIZE_BODY}px;
                font-family: {Fonts.BODY}; background: transparent;
            }}
            QLabel#volLabel {{
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_MICRO}px;
                font-family: {Fonts.BODY}; letter-spacing: 1px; background: transparent;
            }}
            QPushButton#volBtn {{
                background-color: {Colors.SURFACE_2}; border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_LABEL}px;
                font-family: {Fonts.DISPLAY}; font-weight: bold;
                letter-spacing: 1px; padding: 4px 12px;
            }}
            QPushButton#volBtn:hover {{ border-color: {Colors.ACCENT}; color: {Colors.ACCENT}; }}

            QFrame#trimBar {{
                background-color: {Colors.SURFACE_1};
                border-top: 1px solid {Colors.SHELL_DIVIDER};
            }}
            QLabel#trimLabel {{
                color: {Colors.TEXT_DIM}; font-size: {Fonts.SIZE_MICRO}px; font-weight: bold;
                font-family: {Fonts.DISPLAY}; letter-spacing: 2px; background: transparent;
            }}
            QLabel#trimRangeLbl {{
                color: {Colors.ACCENT}; font-size: {Fonts.SIZE_LABEL}px;
                font-family: {Fonts.BODY}; background: transparent;
            }}

            /* Sidebar */
            QFrame#editorSidebar {{
                background-color: {Colors.SHELL_BG};
                border-left: 1px solid {Colors.SHELL_DIVIDER};
            }}

            /* Primary "Share clip" — teal solid */
            QPushButton#shareBtn {{
                background-color: {Colors.ACCENT}; border: none;
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.BG}; font-size: {Fonts.SIZE_BODY_L}px;
                font-family: {Fonts.BODY}; font-weight: bold;
            }}
            QPushButton#shareBtn:hover {{ background-color: {Colors.TEXT}; }}
            QPushButton#shareBtn:pressed {{ background-color: {Colors.ACCENT_DIM}; color: {Colors.TEXT}; }}

            /* Secondary "Export" / "Upload" — dark surface */
            QPushButton#exportBtn, QPushButton#uploadBtn {{
                background-color: {Colors.SURFACE_3}; border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_BODY_L}px;
                font-family: {Fonts.BODY}; font-weight: bold;
            }}
            QPushButton#exportBtn:hover, QPushButton#uploadBtn:hover {{ background-color: {Colors.BORDER}; border-color: {Colors.BORDER_HI}; }}
            QPushButton#exportBtn:disabled, QPushButton#uploadBtn:disabled {{ background-color: {Colors.SURFACE_2}; color: {Colors.TEXT_MUTED}; }}

            /* Crop / quick-crop */
            QPushButton#cropBtn, QPushButton#quickCropBtn, QPushButton#saveQcBtn {{
                background-color: transparent; border: 1px solid {Colors.BORDER_HI};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_LABEL}px; font-weight: bold;
                font-family: {Fonts.DISPLAY}; letter-spacing: 1px;
            }}
            QPushButton#cropBtn:hover, QPushButton#quickCropBtn:hover, QPushButton#saveQcBtn:enabled:hover {{
                border-color: {Colors.ACCENT}; color: {Colors.ACCENT};
            }}
            QPushButton#cropBtn[active="true"] {{ border-color: {Colors.ACCENT}; color: {Colors.ACCENT}; }}
            QPushButton#quickCropBtn:disabled, QPushButton#saveQcBtn:disabled {{
                color: {Colors.TEXT_MUTED}; border-color: {Colors.BORDER};
            }}
            QPushButton#quickCropBtn[saved="true"] {{ border-color: {Colors.ACCENT}; color: {Colors.ACCENT}; }}

            QLabel#cropInfoLbl {{
                color: {Colors.ACCENT}; font-size: {Fonts.SIZE_MICRO}px;
                font-family: {Fonts.BODY}; background: transparent;
            }}

            /* Collapsible panels */
            QFrame#detailsPanel {{
                background-color: {Colors.SURFACE_1};
                border: 1px solid {Colors.BORDER};
                border-radius: {Sizes.RADIUS_MD}px;
            }}
            QPushButton#panelHeader {{
                background-color: transparent; border: none;
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_LABEL}px;
                font-family: {Fonts.DISPLAY}; font-weight: bold;
                letter-spacing: {Fonts.TRACK_LABEL}px;
                padding: 12px 14px; text-align: left;
            }}
            QPushButton#panelHeader:hover {{ color: {Colors.ACCENT}; }}
            QFrame#panelBody {{ background: transparent; }}

            /* Key/value rows inside panels */
            QLabel#detailKey {{
                color: {Colors.TEXT_DIM}; font-size: {Fonts.SIZE_LABEL}px; font-weight: bold;
                font-family: {Fonts.BODY}; letter-spacing: 0px; background: transparent;
            }}
            QLabel#detailVal {{
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_BODY}px;
                font-family: {Fonts.BODY}; background: transparent;
            }}
            QLabel#detailValBig {{
                color: {Colors.TEXT}; font-size: {Fonts.SIZE_BODY_L}px; font-weight: bold;
                font-family: {Fonts.BODY}; background: transparent;
            }}

            /* Delete — outlined, independently themed */
            QPushButton#deleteBtn {{
                background: transparent; border: 1px solid {Colors.DELETE};
                border-radius: {Sizes.RADIUS_MD}px;
                color: {Colors.DELETE}; font-size: {Fonts.SIZE_BODY_L}px; font-weight: bold;
                font-family: {Fonts.BODY};
            }}
            QPushButton#deleteBtn:hover {{ background-color: {Colors.DELETE}; color: {Colors.TEXT}; }}

            QSlider::groove:horizontal {{ background: {Colors.BORDER}; height: 2px; }}
            QSlider::handle:horizontal {{
                background: {Colors.ACCENT}; width: 10px; height: 10px;
                margin: -4px 0; border-radius: 0px;
            }}
            QSlider::handle:horizontal:hover {{ background: {Colors.TEXT}; }}
            QSlider::sub-page:horizontal {{ background: {Colors.ACCENT}; }}
        ''')

    # =========================================================================
    # Playback
    # =========================================================================

    def _toggle_play(self):
        # Re-entrancy guard: setIcon() does not recurse, but the button is
        # checkable and any future change to programmatic-toggle behavior
        # could. The flag is also useful as a coarse "command in flight"
        # marker to keep us honest about state transitions.
        if self._play_pause_busy:
            return
        self._play_pause_busy = True
        try:
            # Always derive the desired action from the player's reported state
            # rather than from the QPushButton's checked state. The two can drift
            # if Qt buffers a click while the previous play()/pause() is still
            # being processed by MediaFoundation, which is exactly the scenario
            # the user hit when spamming the button.
            try:
                state = self.player.playbackState()
            except Exception:
                state = QMediaPlayer.PlaybackState.StoppedState

            want_play = state != QMediaPlayer.PlaybackState.PlayingState
            try:
                if want_play:
                    if self._audio_mixer is not None:
                        self._audio_mixer.play(self.player.position())
                    self.player.play()
                else:
                    if self._audio_mixer is not None:
                        self._audio_mixer.pause()
                    self.player.pause()
            except Exception as e:
                print(f'[ClipViewer] play/pause command rejected: {e}')

            # Reflect the *requested* state on the button so the icon doesn't
            # lag the click. _on_state_changed will reconcile if the player
            # ends up somewhere else (e.g. StoppedState at end-of-clip).
            self.play_btn.blockSignals(True)
            self.play_btn.setChecked(want_play)
            self.play_btn.blockSignals(False)
            if want_play:
                if not self._pause_icon.isNull():
                    self.play_btn.setIcon(self._pause_icon)
                else:
                    self.play_btn.setText('⏸  PAUSE')
            else:
                if not self._play_icon.isNull():
                    self.play_btn.setIcon(self._play_icon)
                else:
                    self.play_btn.setText('▶  PLAY')
        finally:
            self._play_pause_busy = False

    def _on_state_changed(self, state):
        # Keep the button visually consistent with whatever the player ends up
        # doing — including auto-stop at end of clip.
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if self._audio_mixer is not None:
                self._audio_mixer.play(self.player.position())
            self.play_btn.blockSignals(True)
            self.play_btn.setChecked(True)
            self.play_btn.blockSignals(False)
            if not self._pause_icon.isNull():
                self.play_btn.setIcon(self._pause_icon)
            else:
                self.play_btn.setText('⏸  PAUSE')
        else:
            if self._audio_mixer is not None:
                self._audio_mixer.pause()
            # PausedState OR StoppedState — both show the play glyph.
            self.play_btn.blockSignals(True)
            self.play_btn.setChecked(False)
            self.play_btn.blockSignals(False)
            if not self._play_icon.isNull():
                self.play_btn.setIcon(self._play_icon)
            else:
                self.play_btn.setText('▶  PLAY')

    def _update_playhead(self):
        try:
            pos = self.player.position()
        except Exception:
            return
        if self.duration_ms > 0 and self.trim_slider.dragging != 'playhead':
            self.trim_slider.set_playhead(pos / self.duration_ms)
        self.time_label.setText(f'{self._fmt(pos)} / {self._fmt(self.duration_ms)}')
        if (self._audio_mixer is not None
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState):
            self._audio_mixer.sync_to_video_position(pos)

    def _on_seek_requested(self, pct: float):
        # Coalesce: the slider can fire this 100+ times per second during a
        # drag. We remember only the most-recent target and let the timer
        # apply it once playback can keep up.
        self._pending_seek_ms = max(0, min(self.duration_ms,
                                            int(pct * self.duration_ms)))
        if not self._seek_timer.isActive():
            self._seek_timer.start()

    def _flush_pending_seek(self):
        if self._pending_seek_ms is None:
            return
        target = self._pending_seek_ms
        self._pending_seek_ms = None
        if self._audio_mixer is not None:
            self._audio_mixer.seek(target)
        try:
            self.player.setPosition(target)
        except Exception as e:
            # Swallow MediaFoundation hiccups so the editor stays alive even
            # when the underlying session is in a transitional state.
            print(f'[ClipViewer] setPosition rejected ({target} ms): {e}')

    def _on_player_error(self, *args):
        # QMediaPlayer.errorOccurred fires for unsupported codecs, locked
        # files, and a few transient seek failures. Logging keeps the editor
        # alive — the alternative was an uncaught exception ricocheting up
        # through the Qt event loop.
        try:
            err_str = self.player.errorString()
        except Exception:
            err_str = '(unknown)'
        print(f'[ClipViewer] media player error: {err_str}')

    # =========================================================================
    # Volume / audio mix popup
    # =========================================================================

    def _toggle_volume_popup(self):
        if self._volume_popup and self._volume_popup.isVisible():
            self._volume_popup.close()
            self._volume_popup = None
            return
        popup = VolumePopup(
            master_vol=self._master_volume,
            source_volumes=self._source_volumes,
            source_mutes=self._source_mutes,
            source_tracks=self._playback_sources,
            live_preview=self._audio_mixer_live,
            parent=self,
        )
        popup.master_changed.connect(self._on_master_volume_changed)
        popup.source_changed.connect(self._on_source_volume_changed)
        popup.source_muted.connect(self._on_source_muted)
        popup.show_above(self.vol_btn)
        self._volume_popup = popup

    def _on_master_volume_changed(self, value: int):
        self._master_volume = value
        if self._audio_mixer is not None:
            self._audio_mixer.set_master_percent(value)
        else:
            self.audio_output.setVolume(value / 100.0)
        self.vol_btn.setText(f'VOL  {value}%  ▾')
        if self.sm:
            self.sm.set('master_volume', value)
            self.sm.save_settings()

    def _on_source_volume_changed(self, key: str, value: int):
        self._source_volumes[key] = value
        if self._audio_mixer is not None:
            self._audio_mixer.set_source_state(key, gain_percent=value)

    def _on_source_muted(self, key: str, muted: bool):
        self._source_mutes[key] = muted
        if self._audio_mixer is not None:
            self._audio_mixer.set_source_state(key, muted=muted)

    # Discovery and native decoder creation happen away from the UI thread.
    _audio_sources_discovered = Signal(object, str)

    def _discover_audio_sources_async(self):
        clip_path = self.clip_path
        signal = self._audio_sources_discovered

        def _worker():
            try:
                signal.emit(discover_playback_sources(clip_path), '')
            except PlaybackError as error:
                signal.emit((), str(error))

        threading.Thread(target=_worker, name='FTHR-audio-probe', daemon=True).start()

    def _on_audio_sources_discovered(self, sources, error: str):
        if error:
            print(f'[ClipViewer] audio source probe failed: {error}')
            return
        self._playback_sources = tuple(sources)
        self._audio_tracks = tuple(
            (source.source_id, source.audio_index)
            for source in self._playback_sources
            if source.available and source.audio_index is not None)
        self._multitrack_audio = bool(self._audio_tracks)
        for source in self._playback_sources:
            self._source_volumes.setdefault(source.source_id, 100)
            self._source_mutes.setdefault(source.source_id, False)
        if self._audio_tracks:
            try:
                self._audio_mixer = FFmpegPlaybackController(
                    self.clip_path, self._playback_sources, self)
                self._audio_mixer.ready_changed.connect(self._on_audio_mixer_ready)
                self._audio_mixer.audio_failed.connect(self._on_audio_mixer_failed)
                self._audio_mixer.source_failed.connect(self._on_audio_source_failed)
                self._audio_mixer.reached_eof.connect(self._on_audio_mixer_eof)
                # Avoid QMediaPlayer's one-track audio plus the custom mix.
                self.audio_output.setVolume(0.0)
                if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                    self._audio_mixer.play(self.player.position())
            except PlaybackError as mixer_error:
                print(f'[ClipViewer] custom audio mixer unavailable: {mixer_error}')
                self._audio_mixer = None
                self.audio_output.setVolume(self._master_volume / 100.0)
        if self._volume_popup is not None and self._volume_popup.isVisible():
            self._volume_popup.close()
            self._volume_popup = None
            self._toggle_volume_popup()

    def _on_audio_mixer_ready(self, ready: bool, detail: str):
        self._audio_mixer_live = ready
        if not ready:
            print(f'[ClipViewer] custom audio mixer failed to start: {detail}')
            self.audio_output.setVolume(self._master_volume / 100.0)

    def _on_audio_mixer_failed(self, detail: str):
        print(f'[ClipViewer] custom audio mixer error: {detail}')
        self._audio_mixer_live = False
        # Keep the video and audio timeline coherent on output-device failure.
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()

    def _on_audio_source_failed(self, source_id: str):
        # The bridge continues the healthy stems. Keep this clip-local row
        # visible but honestly disable its controls rather than inventing audio.
        self._playback_sources = tuple(
            replace(source, available=False) if source.source_id == source_id else source
            for source in self._playback_sources)
        if self._volume_popup is not None and self._volume_popup.isVisible():
            self._volume_popup.close()
            self._volume_popup = None
            self._toggle_volume_popup()

    def _on_audio_mixer_eof(self):
        # QMediaPlayer remains the media clock and will normally stop at the
        # same point. Do not issue a competing seek or restart here.
        return

    # =========================================================================
    # Trim
    # =========================================================================

    def _on_trim_changed(self, start_pct: float, end_pct: float):
        s = int(start_pct * self.duration_ms)
        e = int(end_pct   * self.duration_ms)
        self.trim_range_lbl.setText(f'{self._fmt(s)} → {self._fmt(e)}')

    # =========================================================================
    # Crop
    # =========================================================================

    def _open_crop_dialog(self):
        was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if was_playing:
            self.player.pause()
        dlg = CropDialog(self.clip_path, initial_crop=self._crop_rect, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._crop_rect = dlg.crop_rect
            self._update_crop_ui()
        if was_playing:
            self.player.play()

    def _update_crop_ui(self):
        if self._crop_rect:
            x, y, w, h = self._crop_rect
            self.crop_info_lbl.setText(f'{w} × {h}')
            self.crop_btn.setProperty('active', 'true')
            self.crop_btn.setText('EDIT CROP')
            self.save_qc_btn.setEnabled(True)
        else:
            self.crop_info_lbl.setText('')
            self.crop_btn.setProperty('active', 'false')
            self.crop_btn.setText('SET CROP')
            self.save_qc_btn.setEnabled(False)
        self.crop_btn.style().unpolish(self.crop_btn)
        self.crop_btn.style().polish(self.crop_btn)
        # Refresh the crop-preview overlay so the dimmed regions update immediately
        self._relayout_video()

    # ── Quick crop ────────────────────────────────────────────────────────────

    def _refresh_quick_crop_btn(self):
        qc = self.sm.get('quick_crop') if self.sm else None
        if qc and all(k in qc for k in ('x', 'y', 'w', 'h')):
            sw, sh = qc.get('src_w', 0), qc.get('src_h', 0)
            self.quick_crop_btn.setToolTip(f"Saved: {qc['w']}×{qc['h']} ({sw}×{sh} source)")
            self.quick_crop_btn.setEnabled(True)
            self.quick_crop_btn.setProperty('saved', 'true')
        else:
            self.quick_crop_btn.setToolTip('No quick crop saved yet')
            self.quick_crop_btn.setEnabled(False)
            self.quick_crop_btn.setProperty('saved', 'false')
        self.quick_crop_btn.style().unpolish(self.quick_crop_btn)
        self.quick_crop_btn.style().polish(self.quick_crop_btn)

    def _apply_quick_crop(self):
        if not self.sm:
            return
        qc = self.sm.get('quick_crop')
        if not qc:
            return
        sx = self._src_w / max(qc.get('src_w', 1), 1)
        sy = self._src_h / max(qc.get('src_h', 1), 1)
        x  = int(qc['x'] * sx)
        y  = int(qc['y'] * sy)
        w  = min(int(qc['w'] * sx), self._src_w - x)
        h  = min(int(qc['h'] * sy), self._src_h - y)
        if w > 4 and h > 4:
            self._crop_rect = (x, y, w, h)
            self._update_crop_ui()

    def _save_quick_crop(self):
        if not self.sm or not self._crop_rect:
            return
        x, y, w, h = self._crop_rect
        self.sm.set('quick_crop', {'x': x, 'y': y, 'w': w, 'h': h,
                                   'src_w': self._src_w, 'src_h': self._src_h})
        self.sm.save_settings()
        self._refresh_quick_crop_btn()
        orig = self.save_qc_btn.text()
        self.save_qc_btn.setText('✓  SAVED')
        QTimer.singleShot(1800, lambda: self.save_qc_btn.setText(orig))

    # =========================================================================
    # Export
    # =========================================================================

    def _on_upload_click(self):
        # Don't show 'Queued ✓' when the manager will silently drop the
        # request — the user would believe the clip was uploaded.
        if self.sm and (not self.sm.get('upload_enabled', False)
                        or not str(self.sm.get('upload_server_url', '')).strip()):
            self.upload_btn.setText('Upload off')
            self.export_error.emit(
                'UPLOAD NOT CONFIGURED',
                'Enable uploads and set a server URL in Upload Settings first.',
                'warning',
            )
            QTimer.singleShot(2500, lambda: self.upload_btn.setText('Upload'))
            return
        self.upload_requested.emit(self.clip_path)
        self.upload_btn.setText('Queued ✓')
        self.upload_btn.setEnabled(False)
        QTimer.singleShot(2500, lambda: (
            self.upload_btn.setText('Upload'),
            self.upload_btn.setEnabled(True),
        ))

    def _export_clip(self):
        start_s   = self.trim_slider.start_pct * self.duration_ms / 1000
        end_s     = self.trim_slider.end_pct   * self.duration_ms / 1000
        # Zero/negative trim windows make ffmpeg fail with a cryptic error —
        # validate before touching the button state.
        if end_s - start_s < 0.1:
            self.export_error.emit(
                'EXPORT FAILED',
                'Trim window is empty — drag the trim handles apart first.',
                'warning',
            )
            return

        self.export_btn.setText('EXPORTING...')
        self.export_btn.setEnabled(False)

        export_dir = Path.home() / 'FTHR_Clips' / 'Exported'
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # Without this the exception escapes the slot and the button
            # stays disabled on 'EXPORTING...' forever.
            self.export_btn.setText('EXPORT CLIP')
            self.export_btn.setEnabled(True)
            self.export_error.emit(
                'EXPORT FAILED',
                f'Cannot create export folder: {e}',
                'error',
            )
            return
        from datetime import datetime as _dt
        ts = _dt.now().strftime('%H-%M-%S')
        out_path = export_dir / f'{Path(self.clip_path).stem}_export_{ts}.mp4'
        # Two exports in the same second (Export + Share side by side) must
        # not overwrite each other — ffmpeg runs with -y.
        n = 2
        while out_path.exists():
            out_path = export_dir / f'{Path(self.clip_path).stem}_export_{ts}_{n}.mp4'
            n += 1
        out = str(out_path)
        crop_rect = self._crop_rect

        threading.Thread(
            target=self._export_worker,
            args=(start_s, end_s, out, crop_rect),
            daemon=True,
        ).start()

    def _build_export_cmd(self, ffmpeg: str, start_s: float, duration_s: float,
                          out_path: str, crop_rect, video_args: list) -> list:
        """Compose an ffmpeg command for the editor's export pipeline.

        - ``video_args`` is the list of codec/preset args used when video is
          re-encoded (libx264 path, or x264 + bitrate cap for Discord share).
        - When neither crop nor multi-track audio mixing is needed, falls back
          to a pure ``-c copy`` stream copy.
        """
        base = [ffmpeg, '-y', '-ss', str(start_s), '-i', self.clip_path,
                '-t', str(duration_s)]

        use_video_filter = crop_rect is not None
        use_audio_filter = bool(self._audio_tracks)

        if not use_video_filter and not use_audio_filter:
            return base + ['-map', '0:v?', '-map', '0:a?', '-c', 'copy', out_path]

        filters: list[str] = []
        if use_video_filter:
            x, y, w, h = crop_rect
            w &= ~1
            h &= ~1
            filters.append(f'[0:v]crop={w}:{h}:{x}:{y}[vout]')
        if use_audio_filter:
            states = {
                source.source_id: SourceMixState(
                    gain_percent=self._source_volumes.get(source.source_id, 100),
                    muted=self._source_mutes.get(source.source_id, False))
                for source in self._playback_sources
            }
            audio_filters, audio_output = ffmpeg_mix_filter(
                self._playback_sources, states, self._master_volume)
            filters.extend(audio_filters)

        cmd = base + ['-filter_complex', ';'.join(filters)]
        cmd += ['-map', '[vout]' if use_video_filter else '0:v:0']
        cmd += ['-map', audio_output if use_audio_filter else '0:a?']

        if use_video_filter:
            cmd += video_args
        else:
            cmd += ['-c:v', 'copy']

        if use_audio_filter:
            cmd += ['-c:a', 'aac', '-b:a', '192k']
        else:
            cmd += ['-c:a', 'copy']

        cmd.append(out_path)
        return cmd

    def _export_worker(self, start_s: float, end_s: float, out: str, crop_rect):
        try:
            ffmpeg = get_ffmpeg_exe()
        except FFmpegUnavailable as e:
            self._export_done.emit(False, str(e))
            self.export_error.emit(
                'FFMPEG NOT FOUND',
                'ffmpeg is required for export. It normally ships with FTHR Clips; '
                'if you are running from source, install it: '
                'sudo pacman -S ffmpeg (Arch) or sudo apt install ffmpeg (Debian).',
                'error',
            )
            return

        staged = create_staged_output_path(out)
        cmd = self._build_export_cmd(
            ffmpeg=ffmpeg,
            start_s=start_s,
            duration_s=end_s - start_s,
            out_path=str(staged),
            crop_rect=crop_rect,
            video_args=software_video_args(),
        )

        try:
            subprocess.run(cmd, check=True, capture_output=True,
                           timeout=600, **_NO_WINDOW)
            commit_staged_output(staged, out)
            self._export_done.emit(True, out)
        except subprocess.TimeoutExpired:
            discard_staged_output(staged)
            self._export_done.emit(False, 'Export timed out after 10 minutes')
        except subprocess.CalledProcessError as e:
            discard_staged_output(staged)
            err  = e.stderr.decode(errors='replace').strip()
            last = next((l for l in reversed(err.splitlines()) if l.strip()), err[:120])
            self._export_done.emit(False, last)
        except Exception as e:
            discard_staged_output(staged)
            self._export_done.emit(False, str(e))

    def _on_export_done(self, success: bool, msg: str):
        if success:
            self.export_btn.setText('✓  EXPORTED')
        else:
            self.export_btn.setText('EXPORT FAILED')
            self.export_error.emit(
                'EXPORT FAILED',
                'FFmpeg returned an error. The output file may be incomplete.',
                'warning',
            )
            self.crop_info_lbl.setStyleSheet(
                'color: #cc0000; font-size: 8px; font-family: "Segoe UI"; background: transparent;')
            short = msg if len(msg) <= 40 else msg[:38] + '…'
            self.crop_info_lbl.setText(short)
            QTimer.singleShot(6000, self._clear_export_error)

        def _reset():
            self.export_btn.setText('EXPORT CLIP')
            self.export_btn.setEnabled(True)
        QTimer.singleShot(3000, _reset)

    def _clear_export_error(self):
        self.crop_info_lbl.setStyleSheet(
            f'color: {Colors.ACCENT}; font-size: 8px; font-family: "Segoe UI"; background: transparent;')
        self._update_crop_ui()

    # =========================================================================
    # Share
    # =========================================================================

    def _show_share_overlay(self):
        dlg = ShareModeDialog(parent=self)
        dlg.mode_selected.connect(self._open_share_window)
        dlg.show()

    def _open_share_window(self, discord_mode: bool):
        start_s = self.trim_slider.start_pct * self.duration_ms / 1000
        end_s   = self.trim_slider.end_pct   * self.duration_ms / 1000
        settings_info = {
            'resolution': self.sm.get('resolution', 'source') if self.sm else 'source',
            'fps':        int(self._fps),
            'bitrate':    self.sm.get('bitrate_level', 'medium') if self.sm else 'medium',
        }
        sw = ShareWindow(
            clip_path=self.clip_path,
            start_s=start_s,
            end_s=end_s,
            crop_rect=self._crop_rect,
            settings_info=settings_info,
            discord_mode=discord_mode,
            source_volumes=self._source_volumes,
            source_mutes=self._source_mutes,
            master_volume=self._master_volume,
            playback_sources=self._playback_sources,
            multitrack_audio=self._multitrack_audio,
            audio_tracks=self._audio_tracks,
            parent=self,
        )
        sw.export_error.connect(self.export_error)
        sw.show()

    # =========================================================================
    # Delete
    # =========================================================================

    def _delete_clip(self):
        from PySide6.QtWidgets import QMessageBox
        imported_roots = (
            self.sm.get('imported_clip_folders', []) if self.sm else [])
        ownership = classify_media_path(
            self.clip_path, Path.home() / 'FTHR_Clips', imported_roots)
        if self._linked_import or ownership is not MediaOwnership.FTHR_OWNED:
            QMessageBox.information(
                self,
                'Linked Original Protected',
                'This clip is linked from another folder. FTHR will not '
                'delete the original with the generic Delete action.',
            )
            return
        reply = QMessageBox.question(
            self, 'Delete Clip',
            f'Delete {os.path.basename(self.clip_path)}?\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Release the MediaFoundation handle before deleting.
        # On Windows, player.stop() alone doesn't free the file handle —
        # setSource(QUrl()) is required to detach the media pipeline entirely.
        # Without this, os.remove() fails with WinError 32 immediately.
        self._teardown_player()
        try:
            from PySide6.QtCore import QUrl as _QUrl
            self.player.setSource(_QUrl())
        except Exception:
            pass

        # Compute cache paths before deleting (needs mtime while file still exists)
        cache_path = None
        dur_path = None
        try:
            from ui.clip_grid import _get_cached_thumb_path, _get_cached_duration_path
            cache_path = _get_cached_thumb_path(self.clip_path)
            dur_path = _get_cached_duration_path(cache_path)
        except Exception:
            pass

        import time as _time
        for attempt in range(3):
            try:
                os.remove(self.clip_path)
                for p in (cache_path, dur_path):
                    if p:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                self.accept()
                return
            except OSError as e:
                if sys.platform == 'win32' and getattr(e, 'winerror', 0) == 32:
                    if attempt < 2:
                        _time.sleep(0.2)
                        continue
                    QMessageBox.warning(
                        self, 'Delete Failed',
                        f'"{os.path.basename(self.clip_path)}" is still in use.\n\n'
                        'Wait for any active clip save or upload to finish, then try again.',
                    )
                    return
                QMessageBox.warning(self, 'Delete Failed', str(e))
                return

    # =========================================================================
    # Close / cleanup
    # =========================================================================

    def _close(self):
        self._teardown_player()
        self.reject()

    def closeEvent(self, event):
        self._teardown_player()
        super().closeEvent(event)

    def _teardown_player(self):
        # Stop timers first so no late tick can re-issue a seek into a player
        # we are about to release. Each call is wrapped because Qt can raise
        # if a timer was already deleted by a parent during a fast close.
        try:
            self._seek_timer.stop()
        except Exception:
            pass
        self._pending_seek_ms = None
        try:
            self._ph_timer.stop()
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        if self._audio_mixer is not None:
            self._audio_mixer.stop()
            self._audio_mixer = None

    # =========================================================================
    # Window drag / native resize (matches MainWindow)
    # =========================================================================

    def _bar_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (event.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())

    def _bar_mouse_move(self, event):
        if self._drag_pos and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _bar_mouse_release(self, event):
        self._drag_pos = None

    def _bar_double_click(self, event):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def showEvent(self, event):
        super().showEvent(event)
        if self._fade_in_anim is not None:
            self._fade_in_anim.start()
        if self._thumb_pixmap and not self._thumb_pixmap.isNull():
            self._install_thumb_overlay()
        if not self._native_style_applied:
            self._native_style_applied = True
            QTimer.singleShot(0, self._apply_native_style)

    def _apply_native_style(self):
        """Apply WS_THICKFRAME so native resize / Aero-snap work on a frameless dialog."""
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
        if eventType == b'windows_generic_MSG':
            try:
                import ctypes, ctypes.wintypes
                ptr = int(message)
                if ptr:
                    msg_type = ctypes.cast(
                        ptr, ctypes.POINTER(ctypes.c_uint32))[2]
                    if msg_type == 0x0084:  # WM_NCHITTEST
                        msg = ctypes.wintypes.MSG.from_address(ptr)
                        lp  = msg.lParam
                        cx  = ctypes.c_short(lp & 0xFFFF).value
                        cy  = ctypes.c_short((lp >> 16) & 0xFFFF).value
                        g   = self.frameGeometry()
                        bw  = 6  # resize border width

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

                        if cy < g.top() + self._editor_header_h:
                            from PySide6.QtWidgets import QPushButton
                            local = self.mapFromGlobal(QPoint(cx, cy))
                            w = self.childAt(local)
                            if w is None or not isinstance(w, QPushButton):
                                return True, 2  # HTCAPTION
            except Exception:
                pass
        return False, 0

    # =========================================================================
    # Helpers
    # =========================================================================

    def _fmt(self, ms) -> str:
        s = int(ms / 1000)
        return f'{s // 60}:{s % 60:02d}'

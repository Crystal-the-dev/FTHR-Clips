# screenshot_editor.py - Screenshot crop tool
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox,
)
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPixmap, QPainter, QPen
from ui.style import Colors
from pathlib import Path
import os
from core.screenshot_save import (
    ScreenshotPngSaveWorker,
    ScreenshotSaveError,
    publish_staged_png,
    reserve_cropped_screenshot_paths,
)

class CropCanvas(QLabel):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.original_pixmap = pixmap
        self.setPixmap(pixmap)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.crop_rect = QRect()
        self.drag_start = None
        self.is_dragging = False
        self.setMouseTracking(True)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.crop_rect.isEmpty():
            return

        painter = QPainter(self)
        painter.setBrush(Qt.GlobalColor.black)
        painter.setOpacity(0.5)

        full, crop = self.rect(), self.crop_rect
        painter.drawRect(full.x(), full.y(), full.width(), crop.y() - full.y())
        painter.drawRect(full.x(), crop.bottom(), full.width(), full.bottom() - crop.bottom())
        painter.drawRect(full.x(), crop.y(), crop.x() - full.x(), crop.height())
        painter.drawRect(crop.right(), crop.y(), full.right() - crop.right(), crop.height())

        painter.setOpacity(1.0)
        painter.setPen(QPen(Qt.GlobalColor.white, 2, Qt.PenStyle.DashLine))
        painter.drawRect(self.crop_rect)

        painter.setBrush(Qt.GlobalColor.white)
        for corner in [crop.topLeft(), crop.topRight(), crop.bottomLeft(), crop.bottomRight()]:
            painter.drawRect(corner.x() - 4, corner.y() - 4, 8, 8)

    def mousePressEvent(self, event):
        self.drag_start = event.pos()
        self.is_dragging = True
        self.crop_rect = QRect(self.drag_start, self.drag_start)
        self.update()

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            self.crop_rect = QRect(self.drag_start, event.pos()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        self.is_dragging = False

    def get_crop_rect(self) -> QRect:
        displayed = self.pixmap().rect()
        actual = self.original_pixmap.rect()
        scale_x = actual.width() / displayed.width()
        scale_y = actual.height() / displayed.height()
        return QRect(int(self.crop_rect.x() * scale_x), int(self.crop_rect.y() * scale_y),
            int(self.crop_rect.width() * scale_x), int(self.crop_rect.height() * scale_y))

class ScreenshotEditor(QDialog):
    def __init__(self, staged_image_path: str, final_image_path: str, parent=None):
        super().__init__(parent)
        self.staged_image_path = Path(staged_image_path)
        self.final_image_path = Path(final_image_path)
        self._full_published = False
        self._crop_worker = None
        self.setWindowTitle(f'FTHR - {os.path.basename(final_image_path)}')
        self.setMinimumSize(900, 700)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        hint = QLabel('DRAG TO SELECT CROP AREA')
        hint.setStyleSheet(f'color: {Colors.ACCENT}; font-size: 12px;')
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        pixmap = QPixmap(str(self.staged_image_path))
        self.canvas = CropCanvas(pixmap)
        layout.addWidget(self.canvas)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.save_btn = QPushButton('SAVE CROP')
        self.save_btn.clicked.connect(self._save_crop)
        buttons.addWidget(self.save_btn)
        self.save_full_btn = QPushButton('SAVE FULL')
        self.save_full_btn.clicked.connect(self._save_full)
        buttons.addWidget(self.save_full_btn)
        self.cancel_btn = QPushButton('CANCEL')
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

    def _apply_styles(self):
        self.setStyleSheet(f'''QDialog {{ background-color: #0d0d0d; }}
            QPushButton {{ background-color: #1a1a1a;
            border: 1px solid #333333; border-radius: 0px;
            padding: 10px 24px; color: #ffffff; font-weight: bold; }}
            QPushButton:hover {{ border-color: {Colors.ACCENT}; }}''')

    def _save_crop(self):
        crop_rect = self.canvas.get_crop_rect()
        if crop_rect.isEmpty():
            self._save_full()
            return
        pixmap = QPixmap(str(self.staged_image_path))
        cropped = pixmap.copy(crop_rect)
        if cropped.isNull():
            self._show_save_error(
                'IMAGE_ENCODE_FAILED', 'The selected crop could not be created.')
            return
        try:
            crop_paths = reserve_cropped_screenshot_paths(self.final_image_path)
        except ScreenshotSaveError as error:
            self._show_save_error(error.code, error.detail)
            return

        self._set_saving(True)
        worker = ScreenshotPngSaveWorker(cropped.toImage(), crop_paths.staged, self)
        self._crop_worker = worker
        worker.succeeded.connect(
            lambda _staged, paths=crop_paths: self._publish_crop(paths))
        worker.failed.connect(self._on_crop_save_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _save_full(self):
        try:
            self._publish_full_if_needed()
        except ScreenshotSaveError as error:
            self._show_save_error(error.code, error.detail)
            return
        self.accept()

    def _publish_crop(self, crop_paths) -> None:
        self._crop_worker = None
        try:
            self._publish_full_if_needed()
            publish_staged_png(crop_paths.staged, crop_paths.final)
        except ScreenshotSaveError as error:
            crop_paths.staged.unlink(missing_ok=True)
            self._set_saving(False)
            self._show_save_error(error.code, error.detail)
            return
        self.accept()

    def _on_crop_save_failed(self, code: str, detail: str) -> None:
        self._crop_worker = None
        self._set_saving(False)
        self._show_save_error(code, detail)

    def _publish_full_if_needed(self) -> None:
        if not self._full_published:
            publish_staged_png(self.staged_image_path, self.final_image_path)
            self._full_published = True

    def _set_saving(self, saving: bool) -> None:
        self.save_btn.setEnabled(not saving)
        self.save_full_btn.setEnabled(not saving)
        self.cancel_btn.setEnabled(not saving)

    def _show_save_error(self, code: str, detail: str) -> None:
        QMessageBox.warning(self, 'Screenshot Failed', f'{code}\n\n{detail}')

    def reject(self):
        if self._crop_worker is not None and self._crop_worker.isRunning():
            return
        super().reject()

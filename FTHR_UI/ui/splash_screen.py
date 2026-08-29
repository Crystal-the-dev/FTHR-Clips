"""
splash_screen.py — Startup splash. White card with FTHR mark and a thin
teal progress bar. Echoes the inverted top-bar design of the main window.
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QSplashScreen, QVBoxLayout, QLabel, QProgressBar, QWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QColor, QPainter

from core.theme_manager import ThemeManager
from ui.style import Colors, Fonts, Sizes


_W, _H = 500, 300


class SplashScreen(QSplashScreen):
    """White-card splash with logo, progress, and a quiet build slug."""

    finished = Signal()

    def __init__(self):
        # The pixmap fills any area the container doesn't cover; pure black so
        # the splash transitions cleanly into the main window's dark canvas.
        self.splash_pixmap = QPixmap(_W, _H)
        self.splash_pixmap.fill(QColor(Colors.BG))

        super().__init__(self.splash_pixmap)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

        self.progress_value = 0
        self._setup_ui()
        self._center_on_screen()

    def _setup_ui(self):
        container = QWidget(self)
        container.setGeometry(0, 0, _W, _H)
        container.setStyleSheet(
            f'background-color: {Colors.SHELL_BG};'
            f' border: {Sizes.BORDER_W}px solid {Colors.TEXT};'
        )

        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            Sizes.SPACE_8, Sizes.SPACE_8, Sizes.SPACE_8, Sizes.SPACE_7,
        )
        layout.setSpacing(Sizes.SPACE_6)

        # ── Logo ──
        logo_label = QLabel()
        theme = ThemeManager()
        custom_logo = theme.get_custom_icon_path('favicon.ico')
        logo_path = custom_logo or Path(__file__).parent.parent / 'assets' / 'favicon.ico'
        if logo_path.exists():
            logo_pixmap = QPixmap(str(logo_path))
            if custom_logo is None and not logo_pixmap.isNull():
                tinted = QPixmap(logo_pixmap.size())
                tinted.fill(Qt.GlobalColor.transparent)
                painter = QPainter(tinted)
                painter.drawPixmap(0, 0, logo_pixmap)
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(tinted.rect(), QColor(
                    theme.get_icon_tint('favicon.ico')))
                painter.end()
                logo_pixmap = tinted
            scaled_logo = logo_pixmap.scaledToHeight(
                140, Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(scaled_logo)
        else:
            logo_label.setText('FTHR')
            logo_label.setStyleSheet(
                f'color: {Colors.TEXT};'
                f' font-size: {Fonts.SIZE_H1}px;'
                f' font-weight: bold;'
                f' font-family: {Fonts.DISPLAY};'
                f' letter-spacing: {Fonts.TRACK_HEADING}px;'
            )
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo_label)

        layout.addStretch()

        # ── Loading text ──
        self.loading_label = QLabel('Initializing…')
        self.loading_label.setStyleSheet(
            f'color: {Colors.TEXT_DIM};'
            f' font-size: {Fonts.SIZE_BODY}px;'
            f' font-family: {Fonts.BODY};'
            f' letter-spacing: 2px;'
        )
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.loading_label)

        # ── Progress bar — sharp corners, thin, teal fill ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setStyleSheet(f'''
            QProgressBar {{
                background-color: {Colors.HAIRLINE};
                border: none;
                border-radius: 0px;
            }}
            QProgressBar::chunk {{
                background-color: {Colors.ACCENT};
                border-radius: 0px;
            }}
        ''')
        layout.addWidget(self.progress_bar)

        # ── Slug ──
        version_label = QLabel('VOID/BREAKER is peak.')
        version_label.setStyleSheet(
            f'color: {Colors.TEXT_MUTED};'
            f' font-size: {Fonts.SIZE_MICRO}px;'
            f' font-family: {Fonts.BODY};'
            f' letter-spacing: 1px;'
        )
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

    def _center_on_screen(self):
        screen = self.screen()
        if screen:
            center = screen.geometry().center()
            self.move(center.x() - _W // 2, center.y() - _H // 2)

    def show_progress(self, value: int, message: str = ""):
        self.progress_value = min(100, max(0, value))
        self.progress_bar.setValue(self.progress_value)
        if message:
            self.loading_label.setText(message)
        self.repaint()

    def set_message(self, message: str):
        self.loading_label.setText(message)
        self.repaint()

    def finish_splash(self, main_window):
        if main_window is not None and main_window.isVisible():
            self.finish(main_window)
        else:
            self.close()
        self.finished.emit()

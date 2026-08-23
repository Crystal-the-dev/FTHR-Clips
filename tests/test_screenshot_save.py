from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from core.screenshot_save import (
    ScreenshotPngSaveWorker,
    ScreenshotSaveError,
    publish_staged_png,
    reserve_screenshot_paths,
    write_png_to_staged,
)
from ui.screenshot_editor import ScreenshotEditor


def _test_image() -> QImage:
    image = QImage(2, 2, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    image.setPixelColor(0, 0, QColor(255, 0, 0, 255))
    image.setPixelColor(1, 0, QColor(0, 0, 255, 255))
    image.setPixelColor(0, 1, QColor(0, 255, 0, 255))
    image.setPixelColor(1, 1, QColor(255, 255, 255, 255))
    return image


def test_screenshot_names_are_timestamped_and_collision_safe(tmp_path):
    now = datetime(2026, 8, 23, 12, 34, 56, 123456)
    first = reserve_screenshot_paths(tmp_path, now=now)
    first.staged.unlink()
    first.final.write_bytes(b'existing')

    second = reserve_screenshot_paths(tmp_path, now=now)

    assert first.final.name == 'screenshot_from_20260823_123456_123456.png'
    assert second.final.name == 'screenshot_from_20260823_123456_123456_01.png'
    assert second.staged.parent == tmp_path
    assert second.staged.name.endswith('.png.partial')


def test_png_is_staged_then_published_without_channel_or_alpha_corruption(tmp_path):
    paths = reserve_screenshot_paths(tmp_path, now=datetime(2026, 8, 23))

    write_png_to_staged(_test_image(), paths.staged)
    assert paths.staged.exists()
    assert not paths.final.exists()

    publish_staged_png(paths.staged, paths.final)

    decoded = QImage(str(paths.final))
    assert decoded.size().width() == 2
    assert decoded.size().height() == 2
    assert decoded.pixelColor(0, 0) == QColor(255, 0, 0, 255)
    assert decoded.pixelColor(1, 0) == QColor(0, 0, 255, 255)
    assert decoded.pixelColor(0, 1) == QColor(0, 255, 0, 255)
    assert decoded.pixelColor(1, 1) == QColor(255, 255, 255, 255)
    assert not paths.staged.exists()


def test_encode_failure_never_publishes_a_final_looking_png(tmp_path):
    class _FailingImage:
        def save(self, *_args) -> bool:
            return False

    paths = reserve_screenshot_paths(tmp_path, now=datetime(2026, 8, 23))

    try:
        write_png_to_staged(_FailingImage(), paths.staged)
    except ScreenshotSaveError as error:
        assert error.code == 'IMAGE_ENCODE_FAILED'
    else:
        raise AssertionError('the failed image encoder was accepted')

    assert not paths.final.exists()
    assert not paths.staged.exists()


def test_png_worker_writes_off_the_qt_ui_thread(qtbot, tmp_path):
    paths = reserve_screenshot_paths(tmp_path, now=datetime(2026, 8, 23))
    worker = ScreenshotPngSaveWorker(_test_image(), paths.staged)
    completed: list[str] = []
    failures: list[tuple[str, str]] = []
    worker.succeeded.connect(completed.append)
    worker.failed.connect(lambda code, detail: failures.append((code, detail)))

    worker.start()
    qtbot.waitUntil(lambda: bool(completed or failures), timeout=2000)

    assert worker.wait(2000)
    assert completed == [str(paths.staged)]
    assert not failures
    assert paths.staged.exists()


def test_editor_publishes_full_image_only_after_save_is_accepted(qtbot, tmp_path):
    paths = reserve_screenshot_paths(tmp_path, now=datetime(2026, 8, 23))
    write_png_to_staged(_test_image(), paths.staged)
    editor = ScreenshotEditor(str(paths.staged), str(paths.final))
    qtbot.addWidget(editor)

    editor._save_full()

    assert editor.result() == editor.DialogCode.Accepted
    assert paths.final.is_file()
    assert not paths.staged.exists()


def test_editor_crop_encodes_in_worker_then_publishes_both_pngs(qtbot, tmp_path):
    paths = reserve_screenshot_paths(tmp_path, now=datetime(2026, 8, 23))
    write_png_to_staged(_test_image(), paths.staged)
    editor = ScreenshotEditor(str(paths.staged), str(paths.final))
    qtbot.addWidget(editor)
    editor.canvas.crop_rect.setRect(0, 0, 1, 1)

    editor._save_crop()
    qtbot.waitUntil(
        lambda: editor.result() == editor.DialogCode.Accepted,
        timeout=2000,
    )

    crop_path = paths.final.with_stem(f'{paths.final.stem}_cropped')
    assert paths.final.is_file()
    assert crop_path.is_file()
    assert QImage(str(crop_path)).size().width() == 1
    assert QImage(str(crop_path)).size().height() == 1

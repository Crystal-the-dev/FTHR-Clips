from __future__ import annotations

from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrameFormat

from ui import clip_viewer


def _image(colors: list[tuple[int, int, int]]) -> QImage:
    image = QImage(len(colors), 1, QImage.Format.Format_RGB888)
    for x, color in enumerate(colors):
        image.setPixelColor(x, 0, QColor(*color))
    return image


def test_windows_editor_expands_limited_rgb_codes_exactly_once(monkeypatch):
    monkeypatch.setattr(clip_viewer.sys, 'platform', 'win32')
    source = _image([
        (16, 16, 16),
        (30, 30, 30),
        (43, 43, 43),
        (126, 126, 126),
        (218, 218, 218),
        (235, 235, 235),
        (235, 16, 16),
        (16, 235, 16),
        (16, 16, 235),
    ])

    expanded = clip_viewer.LiveVideoPreview._normalize_decoded_video_range(
        source, QVideoFrameFormat.ColorRange.ColorRange_Video)

    expected = (
        (0, 0, 0), (16, 16, 16), (31, 31, 31),
        (128, 128, 128), (235, 235, 235), (255, 255, 255),
        (255, 0, 0), (0, 255, 0), (0, 0, 255),
    )
    actual = tuple(
        (expanded.pixelColor(x, 0).red(),
         expanded.pixelColor(x, 0).green(),
         expanded.pixelColor(x, 0).blue())
        for x in range(expanded.width()))
    assert actual == expected


def test_full_range_editor_frame_is_not_transformed(monkeypatch):
    monkeypatch.setattr(clip_viewer.sys, 'platform', 'win32')
    source = _image([(0, 128, 255)])

    unchanged = clip_viewer.LiveVideoPreview._normalize_decoded_video_range(
        source, QVideoFrameFormat.ColorRange.ColorRange_Full)

    color = unchanged.pixelColor(0, 0)
    assert (color.red(), color.green(), color.blue()) == (0, 128, 255)

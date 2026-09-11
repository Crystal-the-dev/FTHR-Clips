from __future__ import annotations

from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrameFormat

from ui import clip_viewer


def _image(colors: list[tuple[int, int, int]]) -> QImage:
    image = QImage(len(colors), 1, QImage.Format.Format_RGB888)
    for x, color in enumerate(colors):
        image.setPixelColor(x, 0, QColor(*color))
    return image


def test_windows_editor_does_not_expand_qt_display_rgb_twice(monkeypatch):
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

    display_image = clip_viewer.LiveVideoPreview._normalize_decoded_video_range(
        source, QVideoFrameFormat.ColorRange.ColorRange_Video)

    # QVideoFrame.toImage() has already performed YUV -> display RGB. These
    # values must remain unchanged when the source surface reports video
    # range, otherwise legal RGB values are expanded a second time.
    expected = ((16, 16, 16), (30, 30, 30), (43, 43, 43),
                (126, 126, 126), (218, 218, 218), (235, 235, 235),
                (235, 16, 16), (16, 235, 16), (16, 16, 235))
    actual = tuple(
        (display_image.pixelColor(x, 0).red(),
         display_image.pixelColor(x, 0).green(),
         display_image.pixelColor(x, 0).blue())
        for x in range(display_image.width()))
    assert actual == expected


def test_full_range_editor_frame_is_not_transformed(monkeypatch):
    monkeypatch.setattr(clip_viewer.sys, 'platform', 'win32')
    source = _image([(0, 128, 255)])

    unchanged = clip_viewer.LiveVideoPreview._normalize_decoded_video_range(
        source, QVideoFrameFormat.ColorRange.ColorRange_Full)

    color = unchanged.pixelColor(0, 0)
    assert (color.red(), color.green(), color.blue()) == (0, 128, 255)


def test_editor_detaches_display_image_from_decoder_storage():
    source = _image([(12, 34, 56)])

    display_image = clip_viewer.LiveVideoPreview._normalize_decoded_video_range(
        source, QVideoFrameFormat.ColorRange.ColorRange_Video)
    source.setPixelColor(0, 0, QColor(200, 201, 202))

    color = display_image.pixelColor(0, 0)
    assert (color.red(), color.green(), color.blue()) == (12, 34, 56)


def test_neutral_preview_keeps_backend_image_format_without_eager_copy():
    source = QImage(2, 2, QImage.Format.Format_ARGB32)

    display_image = clip_viewer.LiveVideoPreview._normalize_decoded_video_range(
        source, QVideoFrameFormat.ColorRange.ColorRange_Full)

    assert display_image.format() == QImage.Format.Format_ARGB32

"""AUDIT-028 library and thumbnail integration guards."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'FTHR_UI'))
pytest.importorskip('PySide6.QtCore')

from ui import clip_grid


def test_library_worker_discovers_mp4_but_not_partial(tmp_path: Path) -> None:
    completed = tmp_path / 'foo.mp4'
    partial = tmp_path / 'foo.mp4.partial'
    completed.write_bytes(b'complete')
    partial.write_bytes(b'partial')
    received: list[tuple[set[str], object, object, object]] = []
    worker = clip_grid._FileCollectWorker(str(tmp_path), [], 'all', 'newest')
    worker.signals.finished.connect(
        lambda found, pairs, imported, subdirs: received.append(
            (found, pairs, imported, subdirs)))

    worker.run()

    assert received
    found = received[0][0]
    assert str(completed) in found
    assert str(partial) not in found


def test_thumbnail_worker_rejects_partial_before_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    partial = tmp_path / 'foo.mp4.partial'
    partial.write_bytes(b'partial')
    decoder_called = False

    def unexpected_decoder(_path: str) -> object:
        nonlocal decoder_called
        decoder_called = True
        raise AssertionError('partial file reached cv2.VideoCapture')

    monkeypatch.setattr(clip_grid.cv2, 'VideoCapture', unexpected_decoder)
    received: list[tuple[str, str, int]] = []
    worker = clip_grid._ThumbnailWorker(str(partial))
    worker.signals.finished.connect(
        lambda path, cache, duration: received.append((path, cache, duration)))

    worker.run()

    assert not decoder_called
    assert received == [(str(partial), '', 0)]

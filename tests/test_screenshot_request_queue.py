from __future__ import annotations

from types import SimpleNamespace

import main
from main import MainWindow


def test_rapid_screenshot_requests_are_bounded_and_queued():
    window = SimpleNamespace(
        _screenshot_inflight=True,
        _screenshot_pending_requests=0,
    )

    # The active request plus these ten attempts can retain at most ten total.
    for _ in range(10):
        MainWindow._on_hotkey_save_screenshot(window)

    assert window._screenshot_pending_requests == 9


def test_finishing_one_screenshot_schedules_exactly_one_pending_request(
        monkeypatch):
    callbacks = []
    window = SimpleNamespace(
        _screenshot_inflight=True,
        _screenshot_pending_requests=3,
        _shutdown_requested=False,
        _on_hotkey_save_screenshot=lambda: None,
    )
    monkeypatch.setattr(main, 'QTimer', SimpleNamespace(
        singleShot=lambda delay, callback: callbacks.append((delay, callback))))

    MainWindow._finish_screenshot_request(window)

    assert not window._screenshot_inflight
    assert window._screenshot_pending_requests == 2
    assert callbacks == [(0, window._on_hotkey_save_screenshot)]

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _PauseTarget:
    def __init__(self):
        self.states: list[bool] = []

    def set_background_paused(self, paused: bool) -> None:
        self.states.append(bool(paused))


class _SettingsPauseTarget:
    def __init__(self):
        self.states: list[bool] = []

    def set_background_ui_paused(self, paused: bool) -> None:
        self.states.append(bool(paused))


def test_background_ui_pause_is_enabled_by_default(tmp_path, monkeypatch):
    from core.settings_manager import SettingsManager

    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)

    assert SettingsManager().get('pause_ui_in_background') is True


def test_performance_toggle_does_not_change_card_or_sound_options(
        qtbot, tmp_path, monkeypatch):
    qt_widgets = pytest.importorskip('PySide6.QtWidgets')
    from core.settings_manager import SettingsManager
    from main import _SettingsPage

    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    monkeypatch.setattr(_SettingsPage, '_start_encoder_probe', lambda _self: None)
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    settings = SettingsManager()
    settings.set('capture_card_enabled', False)
    settings.set('notification_sounds_enabled', True)
    settings.set('sound_volume_clip', 37)
    settings.set('sound_volume_screenshot', 18)
    settings.save_settings()

    page = _SettingsPage(settings)
    qtbot.addWidget(page)
    changed: list[bool] = []
    page.background_ui_pause_changed.connect(changed.append)

    assert page.performance_background_pause_check.isChecked()
    page.performance_background_pause_check.setChecked(False)

    assert changed == [False]
    assert settings.get('pause_ui_in_background') is False
    assert settings.get('capture_card_enabled') is False
    assert settings.get('notification_sounds_enabled') is True
    assert settings.get('sound_volume_clip') == 37
    assert settings.get('sound_volume_screenshot') == 18


def test_clip_grid_coalesces_refreshes_until_foreground(
        qtbot, tmp_path, monkeypatch):
    qt_widgets = pytest.importorskip('PySide6.QtWidgets')
    from core.settings_manager import SettingsManager
    from ui.clip_grid import ClipGrid

    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    settings = SettingsManager()
    grid = ClipGrid(settings)
    qtbot.addWidget(grid)
    loads: list[str] = []
    monkeypatch.setattr(grid, '_load_clips', lambda: loads.append('load'))

    grid.set_background_paused(True)
    grid.force_refresh()

    assert not grid.refresh_timer.isActive()
    assert grid._background_refresh_pending
    assert loads == []

    grid.set_background_paused(False)

    assert grid.refresh_timer.isActive()
    assert not grid._background_refresh_pending
    assert loads == ['load']


def test_application_inactive_state_drives_the_saved_pause_preference(qtbot):
    from PySide6.QtCore import Qt
    from main import MainWindow

    applied: list[bool] = []
    settings = {'pause_ui_in_background': True}
    host = SimpleNamespace(
        _background_start=False,
        settings_manager=SimpleNamespace(
            get=lambda key, default=None: settings.get(key, default)),
        isVisible=lambda: True,
        isMinimized=lambda: False,
        _apply_background_ui_paused=lambda paused: applied.append(bool(paused)),
    )

    MainWindow._refresh_background_ui_pause_state(
        host, Qt.ApplicationState.ApplicationInactive)
    settings['pause_ui_in_background'] = False
    MainWindow._refresh_background_ui_pause_state(
        host, Qt.ApplicationState.ApplicationInactive)

    assert applied == [True, False]


def test_background_pause_leaves_core_timer_card_and_sound_state_alone(qtbot):
    from PySide6.QtCore import QTimer
    from main import MainWindow

    core_timer = QTimer()
    core_timer.start(500)
    grid = _PauseTarget()
    settings_page = _SettingsPauseTarget()
    options = {
        'capture_card_enabled': False,
        'notification_sounds_enabled': True,
        'sound_volume_clip': 42,
    }
    ui_updates: list[bool] = []
    hotkey_state = {'registered': True}
    host = SimpleNamespace(
        _background_ui_paused=False,
        _ui_ready=True,
        _pending_status_display=None,
        clip_grid=grid,
        _settings_page_widget=settings_page,
        status_timer=core_timer,
        capture_card=object(),
        hotkey_manager=hotkey_state,
        settings_manager=options,
        setUpdatesEnabled=lambda enabled: ui_updates.append(bool(enabled)),
        update=lambda: None,
    )

    MainWindow._apply_background_ui_paused(host, True)

    assert ui_updates == [False]
    assert grid.states == [True]
    assert settings_page.states == [True]
    assert core_timer.isActive()
    assert hotkey_state == {'registered': True}
    assert options == {
        'capture_card_enabled': False,
        'notification_sounds_enabled': True,
        'sound_volume_clip': 42,
    }
    core_timer.stop()

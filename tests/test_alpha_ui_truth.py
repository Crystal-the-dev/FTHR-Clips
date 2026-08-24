from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parent.parent
MAIN_SOURCE = (ROOT / 'FTHR_UI' / 'main.py').read_text(encoding='utf-8')


def _method_source(name: str, next_name: str) -> str:
    start = MAIN_SOURCE.index(f'    def {name}')
    end = MAIN_SOURCE.index(f'    def {next_name}', start)
    return MAIN_SOURCE[start:end]


def test_retired_hotkey_path_and_fake_update_claim_are_absent():
    assert '/tmp/fthr_hotkey.sock' not in MAIN_SOURCE
    assert 'You are on the latest version.' not in MAIN_SOURCE
    assert 'Automatic update checks are not available yet.' in MAIN_SOURCE


def test_dead_splash_control_is_not_exposed():
    assert 'Enable startup splash screen' not in MAIN_SOURCE


@pytest.mark.parametrize(
    ('handler', 'next_handler'),
    [
        ('_on_clip_changed', '_on_ext_clip_changed'),
        ('_on_ext_clip_changed', 'reload_from_settings'),
        ('_on_fps_changed', '_on_res_changed'),
        ('_on_res_changed', '_on_qual_changed'),
        ('_on_qual_changed', '_mark_restart'),
    ],
)
def test_capture_popup_changes_are_explicitly_restart_required(
        handler, next_handler):
    assert 'self._mark_restart()' in _method_source(handler, next_handler)


def test_encoder_apply_uses_process_restart_not_stub_reconfigure():
    pytest.importorskip('PySide6.QtCore')
    from main import MainWindow

    calls: list[str] = []
    fake = SimpleNamespace(
        _set_status=lambda *_args: calls.append('status'),
        _restart_capture_engine=lambda: calls.append('restart'),
    )

    MainWindow._on_encoder_config_changed(fake)

    assert calls == ['status', 'restart']
    body = _method_source('_on_encoder_config_changed', '_write_audio_categories_json')
    assert 'set_encoder_config' not in body


def test_audio_toggle_requests_capture_restart():
    body = _method_source('_on_audio_capture_changed', '_requested_capture_config')
    assert 'self._restart_capture_engine()' in body


def test_engine_launch_defensively_disables_multiband():
    body = _method_source('start_engine', '_read_engine_startup_output')
    assert "multiband_arg = '0'" in body


def test_startup_limitations_never_open_modal_dialogs():
    hotkey_warning = _method_source('_warn_input_group', '_warn_no_engine')
    compositor_warning = _method_source(
        '_show_compositor_warning', '_on_game_appeared')

    assert 'QMessageBox' not in hotkey_warning
    assert '.exec()' not in hotkey_warning
    assert 'self.push_error(' in hotkey_warning
    assert 'QMessageBox' not in compositor_warning
    assert '.exec()' not in compositor_warning
    assert 'self.push_error(' in compositor_warning


def test_windows_never_schedules_linux_compositor_warning():
    body = _method_source('_setup_hotkeys', '_on_hotkey_save_clip')
    assert "sys.platform != 'win32'" in body
    assert 'self._show_compositor_warning' in body


def test_normal_start_has_no_startup_sound_path():
    capture_card = (
        ROOT / 'FTHR_UI' / 'ui' / 'capture_card.py'
    ).read_text(encoding='utf-8')
    settings = (
        ROOT / 'FTHR_UI' / 'core' / 'settings_manager.py'
    ).read_text(encoding='utf-8')

    assert '_SND_STARTUP' not in MAIN_SOURCE
    assert '_SND_STARTUP' not in capture_card
    assert 'sound_volume_startup' not in MAIN_SOURCE
    assert 'sound_volume_startup' not in capture_card
    assert 'sound_volume_startup' not in settings
    assert 'Startup Sound' not in MAIN_SOURCE


def test_final_ready_clip_emits_exactly_one_capture_card(tmp_path, monkeypatch):
    pytest.importorskip('PySide6.QtCore')
    from main import MainWindow, QTimer

    first_clip = tmp_path / 'ready-1.mp4'
    second_clip = tmp_path / 'ready-2.mp4'
    first_clip.write_bytes(b'complete')
    second_clip.write_bytes(b'complete')
    notifications = []
    emitted = []
    monkeypatch.setattr(QTimer, 'singleShot', lambda *_args: None)
    fake = SimpleNamespace(
        _published_final_clips=set(),
        _clip_readiness=SimpleNamespace(
            can_access=lambda _path: True,
            warnings=lambda _path: (),
        ),
        _set_status=lambda *_args: None,
        _update_status=lambda: None,
        clip_saved=SimpleNamespace(emit=emitted.append),
        capture_card=SimpleNamespace(
            show_clip=lambda *args: notifications.append(args)),
        _capture_config=SimpleNamespace(
            active=SimpleNamespace(fps=60, width=0, height=0)),
        capture_fps=60,
        capture_width=0,
        capture_height=0,
    )

    MainWindow._publish_final_clip(fake, str(first_clip), 5)
    MainWindow._publish_final_clip(fake, str(first_clip), 5)
    MainWindow._publish_final_clip(fake, str(second_clip), 10)

    assert emitted == [str(first_clip), str(second_clip)]
    assert notifications == [(5, 60, 'SOURCE'), (10, 60, 'SOURCE')]


def test_unusable_final_clip_never_emits_success_feedback(tmp_path, monkeypatch):
    pytest.importorskip('PySide6.QtCore')
    from main import MainWindow, QTimer

    notifications = []
    errors = []
    monkeypatch.setattr(QTimer, 'singleShot', lambda *_args: None)
    fake = SimpleNamespace(
        _published_final_clips=set(),
        _clip_readiness=SimpleNamespace(can_access=lambda _path: False),
        _set_status=lambda *_args: None,
        push_error=lambda *args, **kwargs: errors.append((args, kwargs)),
        capture_card=SimpleNamespace(
            show_clip=lambda *args: notifications.append(args)),
    )

    MainWindow._publish_final_clip(fake, str(tmp_path / 'missing.mp4'), 5)

    assert notifications == []
    assert len(errors) == 1

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

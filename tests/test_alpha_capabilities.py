from __future__ import annotations

from core.alpha_capabilities import (
    effective_multiband_audio_enabled,
    encoder_preset_supported,
    filter_alpha_preset,
    focus_pause_supported,
    multiband_audio_supported,
)
from core.presets_manager import PRESET_KEYS


def test_multiband_setting_is_retired():
    assert not multiband_audio_supported()
    assert not effective_multiband_audio_enabled(True)
    assert 'multiband_audio_enabled' not in PRESET_KEYS


def test_windows_focus_pause_and_nvenc_preset_policy():
    assert not focus_pause_supported('win32')
    assert encoder_preset_supported('win32', 'nvenc')
    assert not encoder_preset_supported('win32', 'qsv')
    assert focus_pause_supported('linux')
    assert encoder_preset_supported('linux', 'nvenc')


def test_old_preset_cannot_reactivate_multiband_choice():
    filtered = filter_alpha_preset({
        'framerate': 60,
        'multiband_audio_enabled': True,
    })
    assert filtered == {'framerate': 60}


def test_old_preset_cannot_reactivate_removed_auto_crop():
    filtered = filter_alpha_preset({
        'framerate': 60,
        'auto_crop_enabled': True,
    })
    assert filtered == {'framerate': 60}

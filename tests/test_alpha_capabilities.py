from __future__ import annotations

from core.alpha_capabilities import (
    effective_multiband_audio_enabled,
    encoder_preset_supported,
    filter_alpha_preset,
    focus_pause_supported,
    multiband_audio_supported,
)


def test_multiband_cannot_be_reactivated_by_legacy_true_setting():
    assert not multiband_audio_supported()
    assert not effective_multiband_audio_enabled(True)


def test_windows_focus_pause_and_encoder_preset_are_not_advertised():
    assert not focus_pause_supported('win32')
    assert not encoder_preset_supported('win32')
    assert focus_pause_supported('linux')
    assert encoder_preset_supported('linux')


def test_old_preset_cannot_reactivate_multiband():
    filtered = filter_alpha_preset({
        'framerate': 60,
        'multiband_audio_enabled': True,
    })
    assert filtered == {'framerate': 60}

"""Single source for features deliberately unavailable in the public alpha."""

from __future__ import annotations


def multiband_audio_supported() -> bool:
    return False


def effective_multiband_audio_enabled(_requested: object = False) -> bool:
    return False


def focus_pause_supported(platform: str) -> bool:
    return not platform.startswith('win')


def encoder_preset_supported(platform: str) -> bool:
    return not platform.startswith('win')


def filter_alpha_preset(data: dict[str, object]) -> dict[str, object]:
    """Discard settings that an old preset must not reactivate in alpha."""
    return {
        key: value
        for key, value in data.items()
        if key != 'multiband_audio_enabled'
    }

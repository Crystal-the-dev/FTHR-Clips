from __future__ import annotations

from types import SimpleNamespace


class _SettingsStub:
    def __init__(self) -> None:
        self.settings = {
            'clip_length': 30,
            'extended_clip_length': 60,
            'framerate': 60,
            'resolution': 'source',
            'bitrate_level': 'high',
            'custom_bitrate_kbps': 25_000,
            'recording_framerate': 30,
            'recording_resolution': '1080p',
            'recording_bitrate_level': 'medium',
            'recording_custom_bitrate_kbps': 18_000,
        }
        self.save_calls = 0

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        self.settings[key] = value

    def save_settings(self):
        self.save_calls += 1
        return True


def test_custom_bitrate_control_persists_and_steps_by_500(qtbot):
    from main import CaptureSettingsPopup

    settings = _SettingsStub()
    popup = CaptureSettingsPopup(settings)
    qtbot.addWidget(popup)

    popup.qual_combo.setCurrentText('Custom')
    assert popup.custom_bitrate_edit.isEnabled()
    assert popup.get_bitrate() == 25_000

    popup._adjust_custom_bitrate(500)
    assert popup.get_bitrate() == 25_500
    assert settings.get('custom_bitrate_kbps') == 25_500

    popup.custom_bitrate_edit.setText('42000')
    popup._commit_custom_bitrate_text()
    assert popup.get_bitrate() == 42_000
    assert settings.get('bitrate_level') == 'custom'


def test_custom_bitrate_is_clamped_to_supported_range(qtbot):
    from main import CaptureSettingsPopup

    popup = CaptureSettingsPopup(_SettingsStub())
    qtbot.addWidget(popup)
    popup.qual_combo.setCurrentText('Custom')

    popup._set_custom_bitrate(-1)
    assert popup.get_bitrate() == 500
    assert not popup.bitrate_minus_btn.isEnabled()


def test_recording_profile_is_independent_and_needs_no_manual_apply(qtbot):
    from main import CaptureSettingsPopup

    settings = _SettingsStub()
    popup = CaptureSettingsPopup(settings)
    qtbot.addWidget(popup)

    popup.recording_profile_btn.click()
    assert popup.recording_fields.isVisibleTo(popup)
    assert not popup.clip_fields.isVisibleTo(popup)
    assert popup.recording_fps_combo.currentText() == '30'
    assert popup.recording_res_combo.currentText() == '1080p'
    assert not popup.restart_btn.isVisible()

    popup.recording_qual_combo.setCurrentText('Custom')
    popup._set_recording_custom_bitrate(42_000)

    assert popup.get_recording_bitrate() == 42_000
    assert settings.get('recording_bitrate_level') == 'custom'
    assert settings.get('recording_custom_bitrate_kbps') == 42_000
    assert settings.get('bitrate_level') == 'high'
    assert popup._restart_pending is False


def test_capture_profiles_share_the_popup_surface_without_helper_copy(qtbot):
    from main import CaptureSettingsPopup

    popup = CaptureSettingsPopup(_SettingsStub())
    qtbot.addWidget(popup)

    assert not hasattr(popup, 'profile_hint')
    assert popup.clip_fields.styleSheet() == 'background: transparent;'
    assert popup.recording_fields.styleSheet() == 'background: transparent;'


def test_recording_profile_builds_the_encoder_config(monkeypatch):
    import main

    settings = _SettingsStub()
    settings.settings.update({
        'recording_framerate': 30,
        'recording_resolution': '720p',
        'recording_bitrate_level': 'custom',
        'recording_custom_bitrate_kbps': 12_500,
        'capture_monitor': '',
        'codec_pref': 'auto',
        'encoder_preset': 4,
        'scaling_mode': 'stretch',
        'audio_capture_enabled': True,
        'encoder_pref': 'auto',
        'multiband_audio_enabled': False,
        'mic_device_id': '',
        'target_hwnd': 0,
    })
    host = SimpleNamespace(
        settings_manager=settings,
        capture_fps=60,
        capture_width=0,
        capture_height=0,
        capture_bitrate=50_000,
        clip_duration=30,
        extended_clip_duration=60,
        _active_game_window=None,
    )
    monkeypatch.setattr(main, 'default_windows_monitor_path', lambda: '')

    config = main.MainWindow._requested_capture_config(host, 'recording')

    assert (config.fps, config.width, config.height, config.bitrate_kbps) == (
        30, 1280, 720, 12_500)

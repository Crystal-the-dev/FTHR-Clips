import sys
import json
import pytest
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))

from core.settings_manager import SettingsManager


def test_new_defaults_present(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    sm = SettingsManager()
    assert sm.get('codec_pref')     == 'auto'
    assert sm.get('encoder_preset') == 4


def test_old_config_gets_new_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    cfg_file = tmp_path / '.fthr' / 'settings.json'
    cfg_file.parent.mkdir(parents=True)
    with open(cfg_file, 'w') as f:
        json.dump({'clip_length': 60}, f)

    sm = SettingsManager()
    assert sm.get('codec_pref')     == 'auto'
    assert sm.get('encoder_preset') == 4
    assert sm.get('clip_length')    == 60   # existing value preserved


@pytest.mark.parametrize('codec', ['h264', 'hevc', 'av1'])
def test_codec_preference_persists_across_restart(tmp_path, monkeypatch, codec):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)

    first_run = SettingsManager()
    first_run.set('codec_pref', codec)
    assert first_run.save_settings() is True

    restarted = SettingsManager()
    assert restarted.get('codec_pref') == codec


def test_extended_clip_length_default(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    sm = SettingsManager()
    assert sm.get('extended_clip_length') == 60


def test_old_config_gets_extended_clip_length_default(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    cfg_file = tmp_path / '.fthr' / 'settings.json'
    cfg_file.parent.mkdir(parents=True)
    with open(cfg_file, 'w') as f:
        json.dump({'clip_length': 30}, f)
    sm = SettingsManager()
    assert sm.get('extended_clip_length') == 60
    assert sm.get('clip_length') == 30  # existing value unaffected

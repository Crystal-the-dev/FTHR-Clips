import sys, json
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
from core.settings_manager import SettingsManager


def test_audio_categories_json_compact_format():
    """audio_categories.json must use compact separators so C++ strstr finds keys."""
    cats = [
        {'name': 'Game', 'volume': 100, 'patterns': []},
        {'name': 'Discord', 'volume': 80, 'patterns': ['discord', 'Discord']},
    ]
    lines = []
    for cat in cats:
        sink_name = 'fthr_' + ''.join(
            c if c.isalnum() else '_' for c in cat['name'].lower())
        obj = {'name': cat['name'], 'sink': sink_name, 'patterns': cat.get('patterns', [])}
        lines.append(json.dumps(obj, ensure_ascii=True, separators=(',', ':')))

    for line in lines:
        # C++ parser uses strstr(line, '"name":"') — no space after colon
        assert '"name":"' in line, f"Compact format required, got: {line}"
        assert '"sink":"' in line, f"Compact format required, got: {line}"
        # Standard json.dumps WITH spaces would break the C++ parser
        assert '"name": "' not in line, f"Spaces not allowed, got: {line}"


def test_multiband_defaults_are_absent(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    sm = SettingsManager()
    assert sm.get('multiband_audio_enabled') is None
    assert sm.get('audio_categories') is None


def test_retired_multiband_settings_are_removed_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    cfg = tmp_path / '.fthr' / 'settings.json'
    cfg.parent.mkdir(parents=True)
    with open(cfg, 'w') as f:
        json.dump({
            'multiband_audio_enabled': True,
            'audio_categories': [{'name': 'Game', 'volume': 90, 'patterns': []}],
        }, f)

    sm = SettingsManager()
    assert sm.get('multiband_audio_enabled') is None
    assert sm.get('audio_categories') is None
    saved = json.loads(cfg.read_text())
    assert 'multiband_audio_enabled' not in saved
    assert 'audio_categories' not in saved


def test_retired_auto_crop_setting_is_removed_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    cfg = tmp_path / '.fthr' / 'settings.json'
    cfg.parent.mkdir(parents=True)
    with open(cfg, 'w') as f:
        json.dump({'auto_crop_enabled': True}, f)

    sm = SettingsManager()
    assert sm.get('auto_crop_enabled') is None
    saved = json.loads(cfg.read_text())
    assert 'auto_crop_enabled' not in saved


def test_multiband_lookup_has_no_default(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    sm = SettingsManager()
    assert sm.get('multiband_audio_enabled') is None

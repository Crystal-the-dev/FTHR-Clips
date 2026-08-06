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


def test_multiband_defaults_present(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    sm = SettingsManager()
    assert sm.get('multiband_audio_enabled') is False
    cats = sm.get('audio_categories')
    assert isinstance(cats, list)
    names = [c['name'] for c in cats]
    assert 'Game' in names
    assert 'Sonstige' in names
    for c in cats:
        assert 'name' in c and 'volume' in c and 'patterns' in c


def test_old_source_volumes_migrated(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    cfg = tmp_path / '.fthr' / 'settings.json'
    cfg.parent.mkdir(parents=True)
    with open(cfg, 'w') as f:
        json.dump({'source_volumes': {'game': 90, 'discord': 70}}, f)

    sm = SettingsManager()
    cats = {c['name']: c for c in sm.get('audio_categories')}
    assert cats['Game']['volume'] == 90
    assert cats['Discord']['volume'] == 70


def test_multiband_toggle_default_false(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    sm = SettingsManager()
    assert sm.get('multiband_audio_enabled') is False

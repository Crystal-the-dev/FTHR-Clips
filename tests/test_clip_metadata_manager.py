import sys, os
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
from pathlib import Path


def test_get_returns_defaults_for_unknown_clip(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))  # Path.home() on Windows
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    mgr = m.ClipMetadataManager()
    result = mgr.get('/some/clip.mp4')
    assert result == {'tag': '', 'description': ''}


def test_set_and_get_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))  # Path.home() on Windows
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    mgr = m.ClipMetadataManager()
    mgr.set('/clips/a.mp4', tag='Gaming', description='Epic moment')
    assert mgr.get('/clips/a.mp4') == {'tag': 'Gaming', 'description': 'Epic moment'}


def test_set_partial_update_preserves_other_fields(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))  # Path.home() on Windows
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    mgr = m.ClipMetadataManager()
    mgr.set('/clips/b.mp4', tag='FPS', description='First take')
    mgr.set('/clips/b.mp4', tag='RPG')
    assert mgr.get('/clips/b.mp4') == {'tag': 'RPG', 'description': 'First take'}


def test_persists_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))  # Path.home() on Windows
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    mgr = m.ClipMetadataManager()
    mgr.set('/clips/c.mp4', tag='Highlight', description='Nice play')

    # Reload to simulate new session
    reload(m)
    mgr2 = m.ClipMetadataManager()
    assert mgr2.get('/clips/c.mp4') == {'tag': 'Highlight', 'description': 'Nice play'}


def test_atomic_write_uses_temp_file(tmp_path, monkeypatch):
    """set() must write via temp file + os.replace — no partial writes."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))  # Path.home() on Windows
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    mgr = m.ClipMetadataManager()
    mgr.set('/clips/d.mp4', tag='Test')

    meta_file = Path(tmp_path) / '.fthr' / 'clip_metadata.json'
    assert meta_file.exists()
    data = json.loads(meta_file.read_text())
    assert '/clips/d.mp4' in data

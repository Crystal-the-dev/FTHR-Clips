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


def test_editor_draft_roundtrip_is_bound_to_the_source_file(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    clip = tmp_path / 'draft.mp4'
    clip.write_bytes(b'original clip')
    state = {
        'crop_rect': [10, 20, 640, 360],
        'stretch_ratio': 1.25,
        'effects': {'contrast': 20},
        'segments': [[0.0, 1.0, False]],
    }

    mgr = m.ClipMetadataManager()
    assert mgr.set_editor_draft(str(clip), state) is True

    reload(m)
    assert m.ClipMetadataManager().get_editor_draft(str(clip)) == state

    # Replacing a file at the same path must not apply an old clip's edit.
    clip.write_bytes(b'a different replacement clip')
    assert m.ClipMetadataManager().get_editor_draft(str(clip)) is None


def test_clearing_editor_draft_preserves_clip_details(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    clip = tmp_path / 'details.mp4'
    clip.write_bytes(b'clip')
    mgr = m.ClipMetadataManager()
    mgr.set(str(clip), tag='Highlight', description='Keep this')
    mgr.set_editor_draft(str(clip), {'segments': []})

    mgr.clear_editor_draft(str(clip))

    assert mgr.get_editor_draft(str(clip)) is None
    assert mgr.get(str(clip)) == {
        'tag': 'Highlight', 'description': 'Keep this'}


def test_editor_draft_storage_is_bounded_without_pruning_clip_details(
        tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    from importlib import reload
    import core.clip_metadata_manager as m
    reload(m)
    clip = tmp_path / 'new.mp4'
    clip.write_bytes(b'clip')
    mgr = m.ClipMetadataManager()
    for index in range(m._EDITOR_DRAFT_LIMIT):
        mgr._data[f'old-{index}.mp4'] = {
            'tag': f'Tag {index}',
            'editor_draft': {
                'version': 1,
                'updated_at': index,
                'file': {'mtime_ns': 1, 'size': 1},
                'state': {},
            },
        }

    mgr.set_editor_draft(str(clip), {'segments': []})

    draft_count = sum(
        'editor_draft' in entry for entry in mgr._data.values()
        if isinstance(entry, dict))
    assert draft_count == m._EDITOR_DRAFT_LIMIT
    assert mgr._data['old-0.mp4']['tag'] == 'Tag 0'
    assert 'editor_draft' not in mgr._data['old-0.mp4']

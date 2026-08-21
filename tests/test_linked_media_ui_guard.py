from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QMessageBox

from ui.clip_grid import ClipThumbnail


def test_imported_card_delete_never_calls_remove(
        qapp, tmp_path, monkeypatch):
    source = tmp_path / 'external.mp4'
    source.write_bytes(b'user original')
    card = ClipThumbnail(str(source), imported=True)
    removed: list[str] = []
    monkeypatch.setattr('ui.clip_grid.os.remove', removed.append)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args: None)

    card._confirm_delete()

    assert removed == []
    assert source.read_bytes() == b'user original'


def test_grid_uses_normalized_link_provenance(qapp, tmp_path):
    from ui.clip_grid import ClipGrid

    source = tmp_path / 'external.mp4'
    source.write_bytes(b'linked')
    grid = SimpleNamespace(_imported_files={str(source)})

    assert ClipGrid.is_linked_import(
        grid, str(source.parent / '.' / source.name))


def test_finalizing_card_cannot_open_or_delete(qapp, tmp_path, monkeypatch):
    source = tmp_path / 'clip.mp4'
    source.write_bytes(b'base')
    card = ClipThumbnail(str(source), ready=False)
    opened: list[str] = []
    removed: list[str] = []
    card.opened.connect(lambda path, *_args: opened.append(path))
    monkeypatch.setattr('ui.clip_grid.os.remove', removed.append)

    card._on_share_click()
    card._confirm_delete()

    assert opened == []
    assert removed == []
    assert source.exists()

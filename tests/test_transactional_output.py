from __future__ import annotations

from core.transactional_output import (
    commit_staged_output,
    create_staged_output_path,
    discard_staged_output,
)


def test_staged_export_is_same_directory_and_not_final_looking(tmp_path):
    final = tmp_path / 'export.mp4'

    staged = create_staged_output_path(final)

    assert staged.parent == final.parent
    assert staged != final
    assert staged.suffix == '.mp4'
    assert '.partial.' in staged.name


def test_failed_export_cleanup_leaves_no_final_file(tmp_path):
    final = tmp_path / 'export.mp4'
    staged = create_staged_output_path(final)
    staged.write_bytes(b'corrupt partial output')

    discard_staged_output(staged)

    assert not staged.exists()
    assert not final.exists()


def test_success_atomically_promotes_staged_output(tmp_path):
    final = tmp_path / 'export.mp4'
    staged = create_staged_output_path(final)
    staged.write_bytes(b'complete output')

    commit_staged_output(staged, final)

    assert final.read_bytes() == b'complete output'
    assert not staged.exists()

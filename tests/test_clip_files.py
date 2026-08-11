"""AUDIT-028 completed-clip filtering and crash-recovery tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.clip_files import (
    cleanup_stale_partial_clips,
    is_completed_video_path,
    is_fthr_owned_partial_path,
    is_library_media_path,
    is_partial_clip_path,
)


@pytest.mark.parametrize('path', ['foo.mp4', 'foo.MP4', 'foo.mkv', 'foo.avi'])
def test_completed_video_names_are_accepted(path: str) -> None:
    assert is_completed_video_path(path)


@pytest.mark.parametrize(
    'path',
    ['foo.mp4.partial', 'foo.MP4.PARTIAL', 'foo.partial', 'foo.mp4.tmp'],
)
def test_partial_or_incomplete_names_are_rejected(path: str) -> None:
    assert not is_completed_video_path(path)
    assert not is_library_media_path(path)


def test_partial_check_is_explicit_not_an_extension_accident() -> None:
    partial = Path('desktop_clip_from_11Aug2026_12-00-00.mp4.partial')
    assert is_partial_clip_path(partial)
    assert is_fthr_owned_partial_path(partial)
    assert not is_completed_video_path(partial)


def test_library_accepts_completed_video_and_image_names() -> None:
    assert is_library_media_path('clip.mp4')
    assert is_library_media_path('screenshot.png')


def test_startup_cleanup_removes_only_old_fthr_partials(tmp_path: Path) -> None:
    old = tmp_path / 'Desktop' / 'desktop_clip_from_11Aug2026_12-00-00.mp4.partial'
    fresh = tmp_path / 'Desktop' / 'desktop_clip_from_11Aug2026_12-01-00.mp4.partial'
    unrelated = tmp_path / 'Desktop' / 'manual-recording.mp4.partial'
    old.parent.mkdir()
    for path in (old, fresh, unrelated):
        path.write_bytes(b'partial')

    now = 2_000_000.0
    os.utime(old, (now - 90_000, now - 90_000))
    os.utime(fresh, (now - 60, now - 60))
    os.utime(unrelated, (now - 90_000, now - 90_000))

    result = cleanup_stale_partial_clips(tmp_path, now=now)

    assert result.removed == (old,)
    assert result.failures == ()
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_cleanup_failure_is_secondary_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    partial = tmp_path / 'game_clip_from_11Aug2026_12-00-00.mp4.partial'
    partial.write_bytes(b'partial')
    now = 2_000_000.0
    os.utime(partial, (now - 90_000, now - 90_000))

    original_unlink = Path.unlink

    def fail_target(path: Path, *args: object, **kwargs: object) -> None:
        if path == partial:
            raise PermissionError('still locked')
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_target)
    result = cleanup_stale_partial_clips(tmp_path, now=now)

    assert result.removed == ()
    assert result.failures[0][0] == partial
    assert 'still locked' in result.failures[0][1]
    assert partial.exists()


def test_negative_cleanup_age_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='non-negative'):
        cleanup_stale_partial_clips(tmp_path, minimum_age_seconds=-1)

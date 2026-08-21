from __future__ import annotations

from core.clip_readiness import ClipReadinessRegistry, ClipReadinessState


def test_engine_commit_is_not_final_ready_while_processing_is_pending(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'base')
    registry = ClipReadinessRegistry()

    handle = registry.engine_committed(str(clip), needs_finalization=True)

    assert handle.state is ClipReadinessState.FINALIZING
    assert not handle.event.is_set()
    assert not registry.can_access(str(clip))


def test_success_sets_real_ready_state_and_unblocks_waiters(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'final')
    registry = ClipReadinessRegistry()
    handle = registry.engine_committed(str(clip), needs_finalization=True)

    registry.ready(str(clip))

    assert handle.event.is_set()
    assert registry.state(str(clip)) is ClipReadinessState.READY
    assert registry.can_access(str(clip))


def test_optional_failure_retains_valid_base_as_ready_with_warning(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'valid base')
    registry = ClipReadinessRegistry()
    handle = registry.engine_committed(str(clip), needs_finalization=True)

    registry.finalization_failed(
        str(clip), 'microphone mix failed', base_clip_usable=True)

    assert handle.event.is_set()
    assert registry.state(str(clip)) is ClipReadinessState.READY_WITH_WARNING
    assert registry.can_access(str(clip))
    assert registry.warnings(str(clip)) == ('microphone mix failed',)


def test_unusable_finalization_failure_never_becomes_accessible(tmp_path):
    clip = tmp_path / 'clip.mp4'
    registry = ClipReadinessRegistry()
    handle = registry.engine_committed(str(clip), needs_finalization=True)

    registry.finalization_failed(str(clip), 'file missing', base_clip_usable=False)

    assert handle.event.is_set()
    assert registry.state(str(clip)) is ClipReadinessState.FINALIZATION_FAILED
    assert not registry.can_access(str(clip))


def test_existing_untracked_clip_is_ready_by_default(tmp_path):
    clip = tmp_path / 'old.mp4'
    clip.write_bytes(b'old complete clip')
    registry = ClipReadinessRegistry()

    assert registry.can_access(str(clip))
    assert registry.state(str(clip)) is ClipReadinessState.READY

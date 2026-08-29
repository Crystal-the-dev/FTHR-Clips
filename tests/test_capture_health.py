"""Deterministic capture-health, content and save-admission coverage."""

from __future__ import annotations

from core.capture_health import (
    CaptureHealthFlag,
    CaptureHealthMonitor,
    CaptureHealthState,
    ContentSuspicionTracker,
    evaluate_save_admission,
    sample_bgra_grid,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _observe(monitor: CaptureHealthMonitor, count: int, flags: int | None = None):
    return monitor.observe(
        connected=True,
        frame_count=count,
        engine_flags=int(CaptureHealthFlag.ACTIVE if flags is None else flags),
        generation=1,
    )


def test_normal_progress_remains_healthy() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    for count in (1, 2, 3, 4):
        result = _observe(monitor, count)
        assert result.state is CaptureHealthState.HEALTHY
        assert result.save_allowed
        clock.advance(0.5)


def test_temporary_pause_below_warning_threshold_is_not_stalled() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 1)
    clock.advance(2.9)
    result = _observe(monitor, 1)
    assert result.state is CaptureHealthState.HEALTHY


def test_sustained_stall_requests_only_one_recovery() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 10)
    clock.advance(3.1)
    assert _observe(monitor, 10).state is CaptureHealthState.DEGRADED
    clock.advance(5.0)
    first = _observe(monitor, 10)
    second = _observe(monitor, 10)
    assert first.state is CaptureHealthState.STALLED
    assert first.request_recovery
    assert not first.save_allowed
    assert not second.request_recovery


def test_progress_after_stall_recovers_and_save_is_allowed() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 1)
    clock.advance(9.0)
    assert not _observe(monitor, 1).save_allowed
    clock.advance(0.1)
    recovered = _observe(monitor, 2)
    assert recovered.state is CaptureHealthState.HEALTHY
    assert recovered.save_allowed


def test_one_post_recovery_frame_does_not_warm_buffer_by_wall_time() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 1)
    clock.advance(9.0)
    _observe(monitor, 1)
    _observe(monitor, 2)
    clock.advance(6.0)
    no_progress = _observe(monitor, 2)
    assert no_progress.fresh_buffer_seconds == 0
    assert not evaluate_save_admission(no_progress, 30).allowed


def test_process_alive_backend_failed_is_not_healthy() -> None:
    monitor = CaptureHealthMonitor(clock=FakeClock())
    result = _observe(monitor, 50, int(CaptureHealthFlag.BACKEND_FAILED))
    assert result.state is CaptureHealthState.FAILED
    assert not result.save_allowed


def test_recovering_and_paused_capture_reject_saves() -> None:
    monitor = CaptureHealthMonitor(clock=FakeClock())
    recovering = _observe(monitor, 10, int(CaptureHealthFlag.RECOVERING))
    paused = _observe(monitor, 10, int(CaptureHealthFlag.ACTIVE | CaptureHealthFlag.PAUSED))
    assert recovering.state is CaptureHealthState.RECOVERING
    assert not recovering.save_allowed
    assert paused.state is CaptureHealthState.DEGRADED
    assert not paused.save_allowed  # no fresh buffer exists after an intentional pause


def test_intentional_pause_preserves_an_already_warm_replay_ring() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 1)
    clock.advance(6)
    warm = _observe(monitor, 2)
    assert warm.fresh_buffer_seconds == 6

    clock.advance(2)
    paused = _observe(
        monitor, 2, int(CaptureHealthFlag.ACTIVE | CaptureHealthFlag.PAUSED))
    clock.advance(1)
    resumed = _observe(monitor, 3)

    assert paused.save_allowed
    assert resumed.fresh_buffer_seconds == 7
    admitted = evaluate_save_admission(resumed, 30)
    assert admitted.allowed
    assert admitted.duration_seconds == 7


def test_static_wgc_source_keeps_verified_replay_saveable() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 1)
    clock.advance(10)
    _observe(monitor, 2)
    clock.advance(9)

    stalled = _observe(monitor, 2)

    assert stalled.state is CaptureHealthState.STALLED
    assert stalled.fresh_buffer_seconds == 10
    assert evaluate_save_admission(stalled, 30).allowed


def _solid_frame(width: int, height: int, bgr: tuple[int, int, int]) -> bytes:
    blue, green, red = bgr
    return bytes((blue, green, red, 255)) * (width * height)


def test_one_black_frame_does_not_become_suspect() -> None:
    sample = sample_bgra_grid(_solid_frame(64, 36, (0, 0, 0)), width=64, height=36, stride=256)
    tracker = ContentSuspicionTracker()
    assert sample.black_like
    assert not tracker.observe(sample)


def test_persistent_black_becomes_suspect_and_normal_recovers() -> None:
    black = sample_bgra_grid(_solid_frame(64, 36, (0, 0, 0)), width=64, height=36, stride=256)
    normal_data = bytearray(64 * 36 * 4)
    for y in range(36):
        for x in range(64):
            offset = (y * 64 + x) * 4
            normal_data[offset:offset + 4] = bytes(
                ((x * 11) % 256, (y * 17) % 256, ((x + y) * 7) % 256, 255)
            )
    normal = sample_bgra_grid(normal_data, width=64, height=36, stride=256)
    tracker = ContentSuspicionTracker(suspect_after_samples=3)
    assert [tracker.observe(black) for _ in range(3)] == [False, False, True]
    assert not tracker.observe(normal)
    assert tracker.suspicious_streak == 0


def test_dark_game_like_variance_is_not_black_or_uniform() -> None:
    width, height = 64, 36
    data = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            value = 2 + ((x * 5 + y * 7) % 27)
            offset = (y * width + x) * 4
            data[offset:offset + 4] = bytes((value, value, value, 255))
    sample = sample_bgra_grid(data, width=width, height=height, stride=width * 4)
    assert sample.luma_mean < 20
    assert not sample.black_like
    assert not sample.uniform


def test_static_normal_desktop_fresh_frames_are_not_stalled_or_suspect() -> None:
    width, height = 64, 36
    data = bytearray(_solid_frame(width, height, (20, 80, 160)))
    for pixel in range(0, width * height, 7):
        data[pixel * 4:pixel * 4 + 4] = bytes((200, 180, 30, 255))
    sample = sample_bgra_grid(data, width=width, height=height, stride=width * 4)
    tracker = ContentSuspicionTracker(suspect_after_samples=3)
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    for count in range(1, 20):
        assert not tracker.observe(sample)  # identical hash is diagnostic only
        assert _observe(monitor, count).state is CaptureHealthState.HEALTHY
        clock.advance(0.5)


def test_uniform_white_and_solid_color_need_temporal_evidence() -> None:
    for color in ((255, 255, 255), (20, 100, 180)):
        sample = sample_bgra_grid(_solid_frame(32, 18, color), width=32, height=18, stride=128)
        tracker = ContentSuspicionTracker(suspect_after_samples=3)
        assert sample.uniform
        assert not tracker.observe(sample)
        assert not tracker.observe(sample)
        assert tracker.observe(sample)


def test_generation_change_resets_fresh_buffer_age() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 1)
    clock.advance(20)
    old = _observe(monitor, 2)
    assert old.fresh_buffer_seconds == 20
    reset = monitor.observe(
        connected=True,
        frame_count=3,
        engine_flags=int(CaptureHealthFlag.ACTIVE),
        generation=2,
    )
    assert reset.state is CaptureHealthState.INITIALIZING
    assert reset.fresh_buffer_seconds == 0


def test_save_is_rejected_while_stalled_and_accepted_after_recovery() -> None:
    clock = FakeClock()
    monitor = CaptureHealthMonitor(clock=clock)
    _observe(monitor, 1)
    clock.advance(9)
    stalled = _observe(monitor, 1)
    assert not evaluate_save_admission(stalled, 30).allowed

    _observe(monitor, 2)
    clock.advance(6)
    healthy = _observe(monitor, 3)
    admitted = evaluate_save_admission(healthy, 30)
    assert admitted.allowed
    assert admitted.duration_seconds == 6
    assert "recently recovered" in admitted.reason

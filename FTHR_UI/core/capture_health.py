"""Capture-progress and privacy-safe content-health interpretation.

The native engines own low-level facts (backend state, produced-frame count and
small derived content metrics).  This module owns the user-facing state machine.
It never receives pixels in production and performs no I/O or sleeping, so it is
safe to call from the existing 500 ms Qt status timer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntFlag
import time
from collections.abc import Callable


class CaptureHealthFlag(IntFlag):
    """Typed v4 engine health bits published through shared memory."""

    NONE = 0
    ACTIVE = 1 << 0
    RECOVERING = 1 << 1
    BACKEND_FAILED = 1 << 2
    CONTENT_SUSPECT = 1 << 3
    PAUSED = 1 << 4


class CaptureHealthState(Enum):
    """User-facing capture health states."""

    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CONTENT_SUSPECT = "content_suspect"
    STALLED = "stalled"
    FAILED = "failed"
    RECOVERING = "recovering"
    STOPPED = "stopped"


@dataclass(frozen=True)
class CaptureHealthSnapshot:
    """One deterministic result from :class:`CaptureHealthMonitor`."""

    state: CaptureHealthState
    previous_state: CaptureHealthState
    reason: str
    changed: bool
    request_recovery: bool
    seconds_since_progress: float
    fresh_buffer_seconds: float

    @property
    def save_allowed(self) -> bool:
        """Whether replay data is recent enough to permit a save."""

        if self.state is CaptureHealthState.DEGRADED:
            return self.fresh_buffer_seconds > 0.0
        return self.state in {
            CaptureHealthState.HEALTHY,
            CaptureHealthState.CONTENT_SUSPECT,
        }


class CaptureHealthMonitor:
    """Interpret frame progress without adding another polling loop.

    Three seconds without a produced frame is a warning.  Eight seconds is a
    stall.  The gap intentionally tolerates compositor hiccups, low-FPS sources
    and loading screens while bounding how long the UI can claim ``CAPTURING``
    over stale replay data.  A single automatic recovery is requested per
    incident; 30 healthy seconds replenish that recommendation budget.
    """

    WARNING_AFTER_S = 3.0
    STALLED_AFTER_S = 8.0
    RECOVERY_BUDGET_RESET_S = 30.0

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        warning_after_s: float = WARNING_AFTER_S,
        stalled_after_s: float = STALLED_AFTER_S,
    ) -> None:
        if not 0 < warning_after_s < stalled_after_s:
            raise ValueError("health thresholds must satisfy 0 < warning < stalled")
        self._clock = clock
        self.warning_after_s = float(warning_after_s)
        self.stalled_after_s = float(stalled_after_s)
        self.state = CaptureHealthState.STOPPED
        self._last_count: int | None = None
        self._last_progress_at: float | None = None
        self._fresh_since: float | None = None
        self._fresh_duration_s = 0.0
        self._last_observed_at: float | None = None
        self._generation: int | None = None
        self._auto_recoveries = 0
        self._healthy_since: float | None = None

    def reset(self, *, preserve_recovery_budget: bool = True) -> None:
        """Reset progress baselines for a new engine/backend generation."""

        self.state = CaptureHealthState.INITIALIZING
        self._last_count = None
        self._last_progress_at = None
        self._fresh_since = None
        self._fresh_duration_s = 0.0
        self._last_observed_at = None
        self._generation = None
        self._healthy_since = None
        if not preserve_recovery_budget:
            self._auto_recoveries = 0

    def observe(
        self,
        *,
        connected: bool,
        frame_count: int,
        engine_flags: int,
        generation: int = 0,
        now: float | None = None,
    ) -> CaptureHealthSnapshot:
        """Advance the state machine from one status poll."""

        timestamp = self._clock() if now is None else float(now)
        poll_delta = (
            0.0
            if self._last_observed_at is None
            else max(0.0, timestamp - self._last_observed_at)
        )
        self._last_observed_at = timestamp
        flags = CaptureHealthFlag(engine_flags)
        old_state = self.state
        request_recovery = False
        count = max(0, int(frame_count))

        if not connected:
            self.state = CaptureHealthState.STOPPED
            self._last_count = None
            self._last_progress_at = None
            self._fresh_since = None
            self._fresh_duration_s = 0.0
            self._healthy_since = None
            return self._snapshot("engine disconnected", old_state, False, timestamp)

        if self._generation is None:
            self._generation = generation
            self._last_count = None
            self._last_progress_at = timestamp
            self._fresh_since = None
            self._fresh_duration_s = 0.0
            self._healthy_since = None
            self.state = CaptureHealthState.INITIALIZING
        elif generation != self._generation:
            self._generation = generation
            # The counter is process-monotonic, so its current value is only a
            # baseline for the new backend.  Require a later increment before
            # counting any post-recovery replay as fresh.
            self._last_count = count
            self._last_progress_at = timestamp
            self._fresh_since = None
            self._fresh_duration_s = 0.0
            self._healthy_since = None
            self.state = CaptureHealthState.INITIALIZING

        if flags & CaptureHealthFlag.BACKEND_FAILED:
            self.state = CaptureHealthState.FAILED
            self._fresh_since = None
            self._fresh_duration_s = 0.0
            self._healthy_since = None
            return self._snapshot("capture backend failed", old_state, False, timestamp)

        if flags & CaptureHealthFlag.RECOVERING:
            self.state = CaptureHealthState.RECOVERING
            self._last_progress_at = timestamp
            self._fresh_since = None
            self._fresh_duration_s = 0.0
            self._healthy_since = None
            return self._snapshot("capture backend is recovering", old_state, False, timestamp)

        if flags & CaptureHealthFlag.PAUSED:
            self.state = CaptureHealthState.DEGRADED
            self._last_progress_at = timestamp
            self._fresh_since = None
            self._fresh_duration_s = 0.0
            self._healthy_since = None
            return self._snapshot("capture is intentionally paused", old_state, False, timestamp)

        progressed = self._last_count is None and count > 0
        if self._last_count is not None:
            progressed = count != self._last_count
            if count < self._last_count:
                # Process/backend generation changed without the generation field
                # reaching us first.  Never carry the old buffer age forward.
                self._fresh_since = None
                self._fresh_duration_s = 0.0
        self._last_count = count

        if progressed:
            self._last_progress_at = timestamp
            if self._fresh_since is None:
                self._fresh_since = timestamp
                self._fresh_duration_s = 0.0
            else:
                # Accumulate only intervals in which the counter advanced.
                # Wall time by itself would let a single post-recovery frame
                # masquerade as a fully warmed replay buffer.
                self._fresh_duration_s += poll_delta
            if flags & CaptureHealthFlag.CONTENT_SUSPECT:
                self.state = CaptureHealthState.CONTENT_SUSPECT
                self._healthy_since = None
                reason = "fresh frames are persistently black-like or uniform"
            else:
                self.state = CaptureHealthState.HEALTHY
                reason = "frames progressing"
                if self._healthy_since is None:
                    self._healthy_since = timestamp
                elif timestamp - self._healthy_since >= self.RECOVERY_BUDGET_RESET_S:
                    self._auto_recoveries = 0
            return self._snapshot(reason, old_state, False, timestamp)

        if self._last_progress_at is None:
            self._last_progress_at = timestamp
        elapsed = timestamp - self._last_progress_at

        if elapsed >= self.stalled_after_s:
            self.state = CaptureHealthState.STALLED
            if old_state is not CaptureHealthState.STALLED:
                self._fresh_since = None
                self._fresh_duration_s = 0.0
            self._healthy_since = None
            if old_state is not CaptureHealthState.STALLED and self._auto_recoveries < 1:
                self._auto_recoveries += 1
                request_recovery = True
            return self._snapshot(
                f"no new frame for {elapsed:.1f} seconds",
                old_state,
                request_recovery,
                timestamp,
            )

        if elapsed >= self.warning_after_s:
            self.state = CaptureHealthState.DEGRADED
            self._healthy_since = None
            return self._snapshot(
                f"frame progress delayed for {elapsed:.1f} seconds",
                old_state,
                False,
                timestamp,
            )

        if self._fresh_since is not None and flags & CaptureHealthFlag.CONTENT_SUSPECT:
            self.state = CaptureHealthState.CONTENT_SUSPECT
            reason = "fresh content remains persistently black-like or uniform"
        else:
            self.state = (
                CaptureHealthState.INITIALIZING
                if self._fresh_since is None
                else CaptureHealthState.HEALTHY
            )
            reason = (
                "waiting for first frame"
                if self._fresh_since is None
                else "between frames"
            )
        return self._snapshot(
            reason,
            old_state,
            False,
            timestamp,
        )

    def _snapshot(
        self,
        reason: str,
        old_state: CaptureHealthState,
        request_recovery: bool,
        now: float,
    ) -> CaptureHealthSnapshot:
        since_progress = (
            0.0 if self._last_progress_at is None else max(0.0, now - self._last_progress_at)
        )
        return CaptureHealthSnapshot(
            state=self.state,
            previous_state=old_state,
            reason=reason,
            changed=self.state is not old_state,
            request_recovery=request_recovery,
            seconds_since_progress=since_progress,
            fresh_buffer_seconds=self._fresh_duration_s,
        )


@dataclass(frozen=True)
class ContentSample:
    """Privacy-safe metrics for tests/benchmarks and engine parity checks."""

    luma_mean: float
    luma_variance: float
    sample_hash: int
    black_like: bool
    uniform: bool


def sample_bgra_grid(
    frame: bytes | bytearray | memoryview,
    *,
    width: int,
    height: int,
    stride: int,
    columns: int = 16,
    rows: int = 9,
) -> ContentSample:
    """Sample a sparse BGRA grid without retaining or exposing frame pixels."""

    if width <= 0 or height <= 0 or stride < width * 4:
        raise ValueError("invalid BGRA frame geometry")
    view = memoryview(frame).cast("B")
    if len(view) < stride * height:
        raise ValueError("frame buffer is shorter than its declared geometry")

    count = max(1, columns) * max(1, rows)
    total = 0
    total_sq = 0
    digest = 2166136261
    for row in range(max(1, rows)):
        y = min(height - 1, ((2 * row + 1) * height) // (2 * max(1, rows)))
        for column in range(max(1, columns)):
            x = min(width - 1, ((2 * column + 1) * width) // (2 * max(1, columns)))
            offset = y * stride + x * 4
            blue, green, red = view[offset], view[offset + 1], view[offset + 2]
            luma = (19 * blue + 183 * green + 54 * red) >> 8
            total += luma
            total_sq += luma * luma
            digest = ((digest ^ luma) * 16777619) & 0xFFFFFFFF

    mean = total / count
    variance = max(0.0, total_sq / count - mean * mean)
    return ContentSample(
        luma_mean=mean,
        luma_variance=variance,
        sample_hash=digest,
        black_like=mean <= 8.0 and variance <= 6.0,
        uniform=variance <= 2.0,
    )


class ContentSuspicionTracker:
    """Require sustained suspicious samples; repeated normal content is valid."""

    SUSPECT_AFTER_SAMPLES = 12

    def __init__(self, suspect_after_samples: int = SUSPECT_AFTER_SAMPLES) -> None:
        if suspect_after_samples < 2:
            raise ValueError("content suspicion requires temporal evidence")
        self.suspect_after_samples = suspect_after_samples
        self.suspicious_streak = 0
        self.repeat_count = 0
        self._last_hash: int | None = None

    def observe(self, sample: ContentSample) -> bool:
        """Return true only after sustained black-like or uniform samples."""

        suspicious = sample.black_like or sample.uniform
        self.suspicious_streak = self.suspicious_streak + 1 if suspicious else 0
        self.repeat_count = self.repeat_count + 1 if sample.sample_hash == self._last_hash else 0
        self._last_hash = sample.sample_hash
        return self.suspicious_streak >= self.suspect_after_samples


@dataclass(frozen=True)
class SaveAdmission:
    """Capture-health decision made before publishing a save command."""

    allowed: bool
    duration_seconds: int
    reason: str


def evaluate_save_admission(
    snapshot: CaptureHealthSnapshot | None,
    requested_seconds: int,
    *,
    minimum_seconds: int = 5,
) -> SaveAdmission:
    """Reject stale capture and shorten only a genuinely warming replay ring."""

    requested = max(1, int(requested_seconds))
    if snapshot is None:
        return SaveAdmission(False, 0, "Capture health is not available yet.")
    if not snapshot.save_allowed:
        if snapshot.state is CaptureHealthState.RECOVERING:
            reason = "Capture is recovering. Try again in a moment."
        elif snapshot.state is CaptureHealthState.INITIALIZING:
            reason = "Capture is still starting. Wait for fresh frames before saving."
        else:
            reason = "Capture is not receiving new frames. Restart capture before saving."
        return SaveAdmission(False, 0, reason)

    available = int(snapshot.fresh_buffer_seconds)
    if available < minimum_seconds:
        return SaveAdmission(
            False,
            0,
            f"Only {available} seconds of fresh replay are available; wait a moment.",
        )
    duration = min(requested, available)
    reason = "" if duration == requested else (
        f"Capture recently recovered; saving the {duration} fresh seconds currently available."
    )
    return SaveAdmission(True, duration, reason)

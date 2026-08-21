"""Authoritative capture-setting policy and requested/active state tracking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


NORMAL_CLIP_VALUES = (5, 10, 15, 30, 45, 60, 90, 120, 180, 240, 300)
EXTENDED_CLIP_VALUES = (30, 45, 60, 90, 120, 180, 240, 300)
FPS_VALUES = (30, 60, 90, 120, 144, 165, 180, 240)


def _validate_int(value: int, *, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer')
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


def validate_normal_clip_length(value: int) -> int:
    return _validate_int(value, name='normal clip length', minimum=5, maximum=300)


def validate_extended_clip_length(value: int) -> int:
    return _validate_int(value, name='extended clip length', minimum=5, maximum=300)


def validate_fps(value: int) -> int:
    return _validate_int(value, name='frame rate', minimum=15, maximum=240)


@dataclass(frozen=True)
class CaptureConfig:
    fps: int
    buffer_seconds: int
    width: int
    height: int
    bitrate_kbps: int
    codec: str
    preset: int
    monitor: str
    scaling: str
    audio_enabled: bool


class ApplyStatus(Enum):
    ACTIVE = auto()
    REQUESTED = auto()
    APPLYING = auto()
    FAILED = auto()


@dataclass
class CaptureConfigTracker:
    active: CaptureConfig | None = None
    requested: CaptureConfig | None = None
    status: ApplyStatus = ApplyStatus.ACTIVE
    error: str = ''

    def __post_init__(self) -> None:
        if self.requested is None:
            self.requested = self.active

    def request(self, config: CaptureConfig) -> None:
        self.requested = config
        self.status = ApplyStatus.REQUESTED
        self.error = ''

    def begin_apply(self) -> None:
        if self.requested is None:
            raise RuntimeError('no capture configuration has been requested')
        self.status = ApplyStatus.APPLYING
        self.error = ''

    def succeed(self) -> None:
        if self.requested is None:
            raise RuntimeError('no capture configuration has been requested')
        self.active = self.requested
        self.status = ApplyStatus.ACTIVE
        self.error = ''

    def fail(self, error: str) -> None:
        self.status = ApplyStatus.FAILED
        self.error = str(error)

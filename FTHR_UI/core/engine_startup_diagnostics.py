"""Bounded parsing for structured native-engine startup failures."""

from dataclasses import dataclass
import re


_STARTUP_FAILURE = re.compile(
    r'^FTHR_STARTUP_ERROR:\s*([A-Z0-9_]+):\s*(.+)$',
    re.MULTILINE,
)
_STARTUP_WARNING = re.compile(
    r'^FTHR_STARTUP_WARNING:\s*([A-Z0-9_]+):\s*(.+)$',
    re.MULTILINE,
)
_MAX_DETAIL_LENGTH = 1000


@dataclass(frozen=True)
class EngineStartupFailure:
    code: str
    title: str
    detail: str


@dataclass(frozen=True)
class EngineStartupWarning:
    code: str
    title: str
    detail: str


def extract_startup_warnings(output: str) -> tuple[EngineStartupWarning, ...]:
    return tuple(
        EngineStartupWarning(
            code=match.group(1),
            title=match.group(1).replace('_', ' '),
            detail=match.group(2).strip()[:_MAX_DETAIL_LENGTH],
        )
        for match in _STARTUP_WARNING.finditer(output or '')
    )


def extract_startup_failure(output: str) -> EngineStartupFailure:
    """Return the last structured failure emitted by the native engine."""
    matches = list(_STARTUP_FAILURE.finditer(output or ''))
    if not matches:
        return EngineStartupFailure(
            code='ENGINE_START_FAILED',
            title='ENGINE COULD NOT START',
            detail='The capture engine stopped before connecting. Check the selected '
                   'monitor and hardware encoder, then restart capture.',
        )
    match = matches[-1]
    code = match.group(1)
    detail = match.group(2).strip()[:_MAX_DETAIL_LENGTH]
    return EngineStartupFailure(
        code=code,
        title=code.replace('_', ' '),
        detail=detail,
    )

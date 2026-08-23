"""Native Windows microphone endpoint discovery for the settings page.

The capture engine owns WASAPI.  This small adapter invokes its explicit
``--list-microphones`` mode so settings persist the same endpoint ID that the
engine will later open.  Friendly names remain display-only and are retained
solely for safe one-time migration of old settings files.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Callable, Iterable


DEVICE_STATE_ACTIVE = 0x00000001


@dataclass(frozen=True)
class WindowsMicrophoneEndpoint:
    endpoint_id: str
    display_name: str
    device_state: int
    is_default: bool

    @property
    def is_active(self) -> bool:
        return bool(self.device_state & DEVICE_STATE_ACTIVE)


def parse_microphone_inventory(payload: str) -> list[WindowsMicrophoneEndpoint]:
    """Parse the engine's versioned JSON inventory without trusting fields."""
    try:
        data = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError('native microphone inventory was not valid JSON') from exc
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('native microphone inventory has an unsupported schema')
    raw_endpoints = data.get('microphones')
    if not isinstance(raw_endpoints, list):
        raise ValueError('native microphone inventory has no endpoint list')

    result: list[WindowsMicrophoneEndpoint] = []
    seen_ids: set[str] = set()
    for item in raw_endpoints:
        if not isinstance(item, dict):
            continue
        endpoint_id = item.get('endpoint_id')
        display_name = item.get('display_name')
        state = item.get('device_state')
        if (not isinstance(endpoint_id, str) or not endpoint_id
                or not isinstance(display_name, str) or not display_name
                or not isinstance(state, int) or endpoint_id in seen_ids):
            continue
        seen_ids.add(endpoint_id)
        result.append(WindowsMicrophoneEndpoint(
            endpoint_id=endpoint_id,
            display_name=display_name,
            device_state=state,
            is_default=bool(item.get('is_default', False)),
        ))
    return result


def list_native_microphones(
    engine_path: str | Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[WindowsMicrophoneEndpoint]:
    """Return native endpoints or raise a bounded, user-presentable error."""
    try:
        result = runner(
            [str(engine_path), '--list-microphones'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'native microphone discovery failed: {exc}') from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or 'unknown native error').strip()
        raise RuntimeError(f'native microphone discovery failed: {detail}')
    return parse_microphone_inventory(result.stdout)


def migrate_legacy_microphone_name(
    legacy_name: str | None,
    endpoints: Iterable[WindowsMicrophoneEndpoint],
) -> str | None:
    """Resolve an old friendly-name setting only when it is unambiguous.

    Returning ``None`` leaves the old selection unresolved; callers must not
    turn that case into a random default-device binding.
    """
    if not legacy_name:
        return None
    matches = [endpoint.endpoint_id for endpoint in endpoints
               if endpoint.is_active and endpoint.display_name == legacy_name]
    return matches[0] if len(matches) == 1 else None

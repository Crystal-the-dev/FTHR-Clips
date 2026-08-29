"""Native-X11 monitor discovery and capture-rectangle selection.

Qt screen geometry is expressed in device-independent coordinates.  FFmpeg's
``x11grab`` input, however, reads physical root-window pixels.  On scaled or
mixed-DPI desktops those coordinate systems are not interchangeable.  RandR's
current output geometry is therefore the authority for the native X11 capture
rectangle, while the persisted setting remains the stable connector name.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence


_GEOMETRY_RE = re.compile(
    r'(?P<width>\d+)x(?P<height>\d+)(?P<x>[+-]\d+)(?P<y>[+-]\d+)'
)
_ROTATIONS = frozenset({'normal', 'left', 'right', 'inverted'})


class X11MonitorError(RuntimeError):
    """A monitor topology cannot safely produce an X11 capture target."""


@dataclass(frozen=True)
class X11Output:
    name: str
    x: int
    y: int
    width: int
    height: int
    rotation: str = 'normal'
    primary: bool = False


@dataclass(frozen=True)
class X11CaptureTarget:
    output: X11Output
    root_x: int
    root_y: int

    @property
    def engine_argument(self) -> str:
        """Opaque argv value understood only by the native X11 backend."""

        return (
            f'@x11:{self.root_x},{self.root_y},'
            f'{self.output.width},{self.output.height}'
        )


def is_native_x11_session(environment: Mapping[str, str] | None = None) -> bool:
    """Return true for native X11, never for a Wayland/XWayland session."""

    env = os.environ if environment is None else environment
    if env.get('WAYLAND_DISPLAY'):
        return False
    session_type = env.get('XDG_SESSION_TYPE', '').strip().casefold()
    if session_type == 'wayland':
        return False
    return bool(env.get('DISPLAY'))


def parse_xrandr_query(output: str) -> list[X11Output]:
    """Parse active connector geometry from ``xrandr --current --query``."""

    monitors: list[X11Output] = []
    seen: set[str] = set()
    for raw_line in output.splitlines():
        fields = raw_line.split()
        if len(fields) < 3 or fields[1] != 'connected':
            continue

        geometry_index = -1
        geometry_match = None
        for index, field in enumerate(fields[2:], start=2):
            match = _GEOMETRY_RE.fullmatch(field)
            if match is not None:
                geometry_index = index
                geometry_match = match
                break
        # A connected-but-disabled output has no active geometry.
        if geometry_match is None:
            continue

        name = fields[0]
        if name in seen:
            raise X11MonitorError(f'RandR reported duplicate output {name!r}.')
        seen.add(name)

        width = int(geometry_match.group('width'))
        height = int(geometry_match.group('height'))
        if width <= 0 or height <= 0:
            continue
        rotation = 'normal'
        if geometry_index + 1 < len(fields):
            candidate = fields[geometry_index + 1].casefold()
            if candidate in _ROTATIONS:
                rotation = candidate
        monitors.append(X11Output(
            name=name,
            x=int(geometry_match.group('x')),
            y=int(geometry_match.group('y')),
            width=width,
            height=height,
            rotation=rotation,
            primary='primary' in fields[2:geometry_index],
        ))
    return monitors


def choose_x11_output(
    outputs: Sequence[X11Output], selected_output: str
) -> X11Output:
    """Resolve an exact connector, or the actual RandR primary by default."""

    if not outputs:
        raise X11MonitorError('RandR reported no active X11 outputs.')
    if selected_output:
        for output in outputs:
            if output.name == selected_output:
                return output
        raise X11MonitorError(
            f'The configured X11 output {selected_output!r} is not active.'
        )
    return next((output for output in outputs if output.primary), outputs[0])


def make_x11_capture_target(
    outputs: Sequence[X11Output], selected_output: str = ''
) -> X11CaptureTarget:
    """Map RandR layout coordinates into non-negative root-window pixels."""

    selected = choose_x11_output(outputs, selected_output)
    # X11 root coordinates normally already start at zero.  Some RandR tools
    # nevertheless report a layout requested with negative offsets.  The root
    # window rebases that layout; mirror that rebase instead of passing a
    # negative coordinate that x11grab rejects.
    minimum_x = min(output.x for output in outputs)
    minimum_y = min(output.y for output in outputs)
    root_x = selected.x - min(0, minimum_x)
    root_y = selected.y - min(0, minimum_y)
    return X11CaptureTarget(selected, root_x, root_y)


def query_x11_outputs(xrandr_path: str, *, timeout: float = 2.0) -> list[X11Output]:
    """Read current RandR topology with a bounded, shell-free subprocess."""

    try:
        completed = subprocess.run(
            [xrandr_path, '--current', '--query'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise X11MonitorError(f'Could not query RandR topology: {error}') from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f'exit code {completed.returncode}'
        raise X11MonitorError(f'RandR topology query failed: {detail}')
    outputs = parse_xrandr_query(completed.stdout)
    if not outputs:
        raise X11MonitorError('RandR reported no active X11 outputs.')
    return outputs


def resolve_x11_capture_target(
    selected_output: str, xrandr_path: str
) -> X11CaptureTarget:
    """Resolve the persisted connector into a current physical capture rect."""

    return make_x11_capture_target(
        query_x11_outputs(xrandr_path), selected_output
    )

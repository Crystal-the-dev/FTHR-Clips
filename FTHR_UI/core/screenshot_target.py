"""Pure selected-monitor mapping for screenshots."""

from __future__ import annotations

import os
from typing import Iterable, Sequence


def _screen_name(screen: object) -> str:
    name = getattr(screen, 'name', None)
    return str(name() if callable(name) else name or '')


def select_qt_screen(
    selected_monitor: str,
    screens: Sequence[object],
    *,
    platform: str,
    windows_monitors: Iterable[object] = (),
    primary: object | None = None,
) -> object | None:
    if not selected_monitor:
        return primary if primary is not None else (screens[0] if screens else None)

    target = selected_monitor
    if platform.startswith('win'):
        selected_key = os.path.normcase(selected_monitor)
        for monitor in windows_monitors:
            device_path = os.path.normcase(str(getattr(monitor, 'device_path', '')))
            if device_path == selected_key:
                target = str(getattr(monitor, 'gdi_name', ''))
                break
        else:
            return None

    for screen in screens:
        actual = _screen_name(screen)
        if platform.startswith('win'):
            if actual.casefold() == target.casefold():
                return screen
        elif actual == target:
            return screen
    return None


def build_grim_command(
    grim_path: str, output_path: str, selected_output: str = ''
) -> list[str]:
    command = [grim_path]
    if selected_output:
        command.extend(['-o', selected_output])
    command.append(output_path)
    return command

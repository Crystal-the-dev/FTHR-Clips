"""Central resolution of the external Linux helper programs FTHR shells out to.

Why this exists
---------------
The code used to invoke `hyprctl`, `xdotool`, `xprop`, `xrandr`, `grim`, `nc` and
`xdg-open` by bare name, scattered across six modules. Three problems with
that:

1. **PATH is attacker-influenced.** `subprocess.run(['grim', ...])` executes
   whatever `grim` the current PATH resolves to. A writable directory earlier
   in PATH — a stale `~/.local/bin`, a game launcher that prepends its own
   `bin/`, a Flatpak wrapper — silently substitutes the binary. Resolving once
   through `shutil.which()` and then invoking the **absolute** path makes the
   choice explicit and inspectable, and it is logged.

2. **Missing tools produced confusing failures.** A missing `grim` surfaced as
   `FileNotFoundError` inside a screenshot handler and got swallowed, so the
   feature just did nothing. Callers can now ask whether a tool exists and say
   so in the UI.

3. **No diagnostics.** Bug reports said "screenshots don't work" with nothing
   in the log. `report()` dumps the full resolution table into the log at
   startup.

Design notes
------------
* Resolution is cached — PATH does not change during a run, and `which()` on
  every focus poll would be wasteful.
* Nothing here ever uses `shell=True`. All callers pass argument lists.
* Tools are classified REQUIRED (the feature owning them cannot work without
  them) or OPTIONAL (there is a fallback). Neither classification is fatal to
  startup: FTHR degrades, it does not refuse to run.
* This module is import-safe on Windows. Everything simply reports "missing",
  which is correct — none of these exist there and no caller uses them.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Optional

# Tools FTHR may invoke, with what breaks when they are absent.
# 'required' is relative to the feature, not to the application.
_TOOLS: dict[str, tuple[bool, str]] = {
    # name:            (required_for_its_feature, what it is used for)
    'hyprctl':  (True,  'Hyprland hotkey binds and window/game detection'),
    'xdotool':  (True,  'window focus and game detection on X11/XWayland'),
    'xprop':    (False, 'fullscreen detection on X11 (falls back to geometry)'),
    'xrandr':   (True,  'selected-monitor geometry for native X11 capture'),
    'grim':     (True,  'Wayland screenshots'),
    'nc':       (True,  'the hotkey socket client used by compositor binds'),
    'xdg-open': (False, 'opening the clips folder in a file manager'),
    'wmctrl':   (False, 'alternative window listing when xdotool is absent'),
}


@dataclass(frozen=True)
class Tool:
    name: str
    path: Optional[str]
    required: bool
    purpose: str

    @property
    def available(self) -> bool:
        return self.path is not None

    def __bool__(self) -> bool:
        return self.available


_cache: dict[str, Tool] = {}


def _resolve(name: str) -> Tool:
    required, purpose = _TOOLS.get(name, (False, 'unspecified'))
    # On Windows none of these exist and no caller asks for them; short-circuit
    # so a stray call cannot pick up some unrelated same-named .exe.
    path = None if sys.platform == 'win32' else shutil.which(name)
    return Tool(name=name, path=path, required=required, purpose=purpose)


def tool(name: str) -> Tool:
    """Resolve *name* once and cache it. Never raises."""
    cached = _cache.get(name)
    if cached is None:
        cached = _resolve(name)
        _cache[name] = cached
    return cached


def path(name: str) -> Optional[str]:
    """Absolute path of *name*, or None. Use this to build argv[0]."""
    return tool(name).path


def available(name: str) -> bool:
    return tool(name).available


def require(name: str) -> str:
    """Absolute path, or raise ToolMissing with a message worth showing a user."""
    t = tool(name)
    if t.path is None:
        raise ToolMissing(name, t.purpose)
    return t.path


def missing_message(name: str) -> str:
    """A sentence the UI can show verbatim."""
    t = tool(name)
    return (f"'{name}' is not installed or not on PATH. "
            f"It is needed for {t.purpose}. "
            f"Install it with your distribution's package manager.")


class ToolMissing(RuntimeError):
    def __init__(self, name: str, purpose: str) -> None:
        super().__init__(f"required tool '{name}' not found on PATH "
                         f"(needed for {purpose})")
        self.name = name
        self.purpose = purpose


def reset_cache() -> None:
    """Drop the cache. Tests use this after manipulating PATH."""
    _cache.clear()


def report() -> str:
    """Human-readable resolution table for the log and for bug reports."""
    if sys.platform == 'win32':
        return '[Tools] Linux helper tools not applicable on Windows.'
    lines = ['[Tools] External Linux helper resolution:']
    for name in sorted(_TOOLS):
        t = tool(name)
        kind = 'required' if t.required else 'optional'
        where = t.path if t.path else 'NOT FOUND'
        lines.append(f'[Tools]   {name:<9} {kind:<8} {where}')
    lines.append(f'[Tools]   PATH={os.environ.get("PATH", "")}')
    return '\n'.join(lines)

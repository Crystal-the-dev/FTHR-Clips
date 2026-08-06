import sys
import json
import subprocess
import threading
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from core.compositor import detect_compositor
from core import linux_tools


def _enumerate_via_hyprctl() -> list:
    """List windows on Hyprland via hyprctl clients -j."""
    try:
        hyprctl = linux_tools.require('hyprctl')
        r = subprocess.run([hyprctl, 'clients', '-j'],
                           capture_output=True, timeout=2)
        clients = json.loads(r.stdout.decode(errors='replace'))
        windows = []
        for c in clients:
            title = c.get('title') or c.get('class') or ''
            if not title:
                continue
            hwnd    = int(c.get('address', '0x0'), 16) & 0xFFFFFFFF
            is_game = bool(c.get('fullscreen')) or c.get('fullscreenMode', 0) > 0
            windows.append({'hwnd': hwnd, 'display_name': title,
                            'title': title, 'is_game': is_game})
        return windows
    except Exception:
        return []


def _enumerate_via_xdotool() -> list:
    """List visible windows via xdotool + xprop. Works for XWayland and X11.
    Covers Steam/Proton games and most Linux native games."""
    if not linux_tools.available('xdotool'):
        return []
    try:
        r = subprocess.run(
            [linux_tools.require('xdotool'), 'search', '--all',
             '--onlyvisible', '--maxdepth', '2', ''],
            capture_output=True, timeout=3)
        if r.returncode != 0:
            return []
        wids = [w.strip() for w in r.stdout.decode().splitlines() if w.strip()]
    except Exception:
        return []

    windows = []
    for wid in wids[:50]:  # cap to avoid slow scans
        try:
            r_name = subprocess.run([linux_tools.require('xdotool'),
                                     'getwindowname', wid],
                                    capture_output=True, timeout=1)
            title = r_name.stdout.decode().strip()
            if not title:
                continue

            is_game = False
            xprop = linux_tools.path('xprop')
            r_prop = subprocess.run([xprop, '-id', wid, '_NET_WM_STATE'],
                                    capture_output=True, timeout=1)
            if r_prop.returncode == 0:
                is_game = '_NET_WM_STATE_FULLSCREEN' in r_prop.stdout.decode()

            windows.append({
                'hwnd':         int(wid) & 0xFFFFFFFF,
                'display_name': title,
                'title':        title,
                'is_game':      is_game,
            })
        except Exception:
            continue
    return windows


def _enumerate_linux_windows() -> list:
    """Pick the right backend for the current compositor."""
    comp = detect_compositor()
    if comp == 'hyprland':
        return _enumerate_via_hyprctl()
    return _enumerate_via_xdotool()


class GameDetector(QObject):
    game_appeared = pyqtSignal(dict)   # new is_game=True window
    game_closed   = pyqtSignal(int)    # hwnd of a game that disappeared
    _windows_enumerated = pyqtSignal(list)  # worker thread → main thread

    def __init__(self, enumerate_fn=None, parent=None):
        super().__init__(parent)
        if enumerate_fn is None:
            if sys.platform == 'win32':
                from ui.capture_settings_widget import _enumerate_capturable_windows
                enumerate_fn = _enumerate_capturable_windows
            else:
                enumerate_fn = _enumerate_linux_windows
        self._enumerate = enumerate_fn
        self._known: dict[int, dict] = {}   # hwnd → window dict
        self._poll_running = False          # skip ticks while worker is busy
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        # Enumeration shells out to xdotool/xprop on X11 (dozens of blocking
        # subprocess calls) — never run that on the Qt main thread. The worker
        # thread enumerates; results come back via queued signal.
        self._timer.timeout.connect(self._start_poll)
        self._windows_enumerated.connect(self._apply_windows)

    def start(self):
        self._known.clear()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _start_poll(self):
        if self._poll_running:
            return  # previous enumeration still in flight — don't pile up
        self._poll_running = True

        def _work():
            try:
                windows = self._enumerate()
            except Exception as e:
                print(f'[GameDetector] enumeration failed: {e}')
                windows = []
            # Cross-thread emit — PyQt queues this to the main thread.
            self._windows_enumerated.emit(windows)

        threading.Thread(target=_work, daemon=True,
                         name='fthr-game-detect').start()

    def _apply_windows(self, windows: list):
        self._poll_running = False
        self._diff_and_emit({w['hwnd']: w for w in windows if w.get('is_game')})

    def _poll(self):
        """Synchronous poll — used by tests and as a manual refresh."""
        current = {w['hwnd']: w for w in self._enumerate() if w.get('is_game')}
        self._diff_and_emit(current)

    def _diff_and_emit(self, current: dict):
        for hwnd, window in current.items():
            if hwnd not in self._known:
                self._known[hwnd] = window
                self.game_appeared.emit(window)
        for hwnd in list(self._known):
            if hwnd not in current:
                del self._known[hwnd]
                self.game_closed.emit(hwnd)

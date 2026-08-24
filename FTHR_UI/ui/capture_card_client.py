"""Client proxy for the capture-card notification process.

The animated notification has its own process and Qt event loop so clip
finalization and library refresh work in the main process cannot stall it. This
class sends small, line-delimited commands over stdin and exposes the same public
methods as the in-process CaptureCard widget.

Public API is identical to the real CaptureCard so callers don't know the diff:
    client.show_clip(duration_s, fps, resolution)
    client.show_screenshot()
    client.show_error(detail='')
    client.show_upload(filename='')
    client.close()
"""

import os
import sys
import subprocess
from pathlib import Path

# Development launches the helper script with the current Python interpreter.
# Frozen builds relaunch the packaged executable with ``--card-process`` because
# no standalone interpreter or source script is available in the package.
_FROZEN = getattr(sys, 'frozen', False)
if _FROZEN:
    _LAUNCH_CMD = [sys.executable, '--card-process']
else:
    _PROCESS_SCRIPT = Path(__file__).parent / 'capture_card_process.py'
    _LAUNCH_CMD = [sys.executable, str(_PROCESS_SCRIPT)]

# Prevent a console window from flashing when the helper starts on Windows.
# This flag is not used on Linux.
_CREATE_NO_WINDOW = 0x08000000
_NO_WINDOW = {'creationflags': _CREATE_NO_WINDOW} if sys.platform == 'win32' else {}


class CaptureCardClient:
    """Sends show commands to a CaptureCard subprocess over stdin."""

    def __init__(self, settings_manager=None):
        self._sm = settings_manager
        self._proc: subprocess.Popen | None = None
        self._launch()

    def _launch(self):
        try:
            env = dict(os.environ)
            # Force XWayland only on Linux. Windows must retain its native Qt
            # platform plugin; setting xcb there makes the card process exit.
            if sys.platform != 'win32' and os.environ.get('WAYLAND_DISPLAY'):
                env['QT_QPA_PLATFORM'] = 'xcb'
                env['DISPLAY'] = os.environ.get('DISPLAY', ':0')
            monitor = self._sm.get('notification_monitor', 'auto') if self._sm else 'auto'
            env['FTHR_CARD_SCREEN_NAME'] = monitor
            self._proc = subprocess.Popen(
                _LAUNCH_CMD,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                env=env,
                **_NO_WINDOW,
            )
        except Exception as e:
            print(f'[CaptureCard] Failed to launch card process: {e}')
            self._proc = None

    def restart(self):
        """Terminate the card subprocess; it will relaunch on the next show call."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._launch()

    def _send(self, cmd: str) -> None:
        if self._proc is None:
            return
        # Relaunch a terminated helper before sending the next notification.
        if self._proc.poll() is not None:
            self._launch()
            if self._proc is None:
                return
        try:
            self._proc.stdin.write(cmd + '\n')
            self._proc.stdin.flush()
        except Exception:
            self._proc = None

    def show_clip(self, duration_s: int, fps: int, resolution: str) -> None:
        self._send(f'clip|{duration_s}|{fps}|{resolution}')

    def show_screenshot(self) -> None:
        self._send('screenshot')

    def show_error(self, detail: str = '') -> None:
        self._send(f'error|{detail}')

    def show_upload(self, filename: str = '') -> None:
        self._send(f'upload|{filename}')

    def show_prompt(self, text: str) -> None:
        self._send(f'prompt|{text}')

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.stdin.write('quit\n')
            proc.stdin.flush()
            # The child joins its blocking stdin reader after app.quit(). EOF
            # releases that reader immediately; leaving the pipe open forced
            # every normal shutdown to wait for the timeout.
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
        try:
            proc.wait(timeout=0.75)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()

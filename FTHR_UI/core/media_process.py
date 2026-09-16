"""One cancellable, bounded subprocess runner for optional media work."""

import subprocess
import sys
import threading
import time

_MEDIA_PROCESS_LOCK = threading.Lock()
_MEDIA_PROCESS_ACTIVE = 0
_MEDIA_PROCESS_PEAK = 0


def _media_process_started() -> None:
    global _MEDIA_PROCESS_ACTIVE, _MEDIA_PROCESS_PEAK
    with _MEDIA_PROCESS_LOCK:
        _MEDIA_PROCESS_ACTIVE += 1
        _MEDIA_PROCESS_PEAK = max(_MEDIA_PROCESS_PEAK, _MEDIA_PROCESS_ACTIVE)


def _media_process_finished() -> None:
    global _MEDIA_PROCESS_ACTIVE
    with _MEDIA_PROCESS_LOCK:
        _MEDIA_PROCESS_ACTIVE = max(0, _MEDIA_PROCESS_ACTIVE - 1)


def media_process_snapshot() -> tuple[int, int]:
    with _MEDIA_PROCESS_LOCK:
        return _MEDIA_PROCESS_ACTIVE, _MEDIA_PROCESS_PEAK


def _stop_owned_process(process: subprocess.Popen, *, timeout: float = 0.75) -> None:
    """Terminate, then kill and reap one library-owned media child."""
    if process.poll() is not None:
        try:
            process.communicate(timeout=0.05)
        except (OSError, subprocess.SubprocessError):
            # The child already exited; unread pipe cleanup must not fail its owner.
            pass
        return
    try:
        process.terminate()
    except OSError:
        # The child may exit between poll and terminate; still attempt to reap it.
        pass
    try:
        process.communicate(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        # The graceful deadline expired; escalate to kill below.
        pass
    try:
        process.kill()
    except OSError:
        # A concurrent child exit makes kill unnecessary; still reap below.
        pass
    try:
        process.communicate(timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        # There is no safe way to force a broken OS handle further; the
        # process object is still retained until the caller has reaped it.
        pass


def run_media_process(
        command: list[str], cancel_event: threading.Event, *,
        timeout_seconds: float = 8.0) -> tuple[int, bytes, bytes] | None:
    """Run ffprobe/ffmpeg with polling cancellation and bounded stderr."""
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) \
        if sys.platform == 'win32' else 0
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=creationflags)
    except (OSError, ValueError):
        # Optional enrichment falls back to original media when a child cannot start.
        return None
    _media_process_started()
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    try:
        while True:
            if cancel_event.is_set() or time.monotonic() >= deadline:
                _stop_owned_process(process)
                return None
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                # Keep diagnostics bounded even if an encoder is unusually
                # verbose; stdout is the thumbnail/probe payload and has a
                # separate media-size guard below.
                return process.returncode or 0, stdout, stderr[-32_768:]
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            _stop_owned_process(process)
        _media_process_finished()



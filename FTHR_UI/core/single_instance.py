"""
single_instance.py — cross-platform "only one FTHR Clips at a time" guard.

Why this exists
---------------
Two running instances are actively destructive, not merely redundant:

  * Both spawn a capture engine. Two engines encode the same screen in
    parallel — double GPU/NVENC load, and on machines with a single NVENC
    session limit the second engine fails in a way that looks like a bug.
  * Both map ``FTHR_SharedMemory_v4``. The command/response fields are a
    single-writer contract; two UIs writing ``ui_command`` interleave and
    each one consumes the other's ``engine_response``, so saves time out.
  * On Linux the second instance calls ``os.unlink()`` on
    ``/tmp/fthr_hotkey.sock`` and rebinds it, silently stealing every
    hotkey from the first instance.

The guard is intentionally dumb and dependency-free: acquire on startup,
release on exit, never block.

Platform mechanics
------------------
Windows  A named kernel mutex (``CreateMutexW``). The kernel drops it when
         the process dies for any reason, including a hard kill — so a
         crashed instance never leaves a stale lock behind.

Linux    ``flock(LOCK_EX | LOCK_NB)`` on ``~/.fthr/fthr.lock``. The kernel
         releases the lock when the fd closes, which includes process death,
         giving the same crash-safety as the Windows mutex. A leftover lock
         *file* is harmless — only the flock state matters, so there is no
         stale-PID-file problem to reason about.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Global kernel namespace would need extra privileges and would also collide
# across user sessions on a shared machine; Local\ is per-session, which is
# exactly the scope we want (one instance per logged-in user).
_WIN_MUTEX_NAME = 'Local\\FTHR_Clips_SingleInstance_v1'
_LOCK_FILE = Path.home() / '.fthr' / 'fthr.lock'

_ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Best-effort single-instance guard.

    Usage::

        guard = SingleInstance()
        if not guard.acquire():
            print('already running')
            return 1
        # ... run the app ...
        guard.release()

    ``acquire()`` returns True when this process owns the instance slot.
    It never raises: if the platform primitive is unavailable for any
    reason we fail *open* (return True) rather than refusing to start the
    app — a broken guard must not be the thing that keeps a user from
    recording. The optional primitive names let tests coexist with an
    installed, running FTHR instance; production callers use the defaults.
    """

    def __init__(
        self,
        *,
        win_mutex_name: str = _WIN_MUTEX_NAME,
        lock_file: Path = _LOCK_FILE,
    ) -> None:
        self._acquired = False
        self._handle: Optional[int] = None      # Windows mutex HANDLE
        self._fd: Optional[int] = None          # Linux lock-file fd
        self._win_mutex_name = win_mutex_name
        self._lock_file = Path(lock_file)

    # ── public API ────────────────────────────────────────────────────────

    def acquire(self) -> bool:
        if self._acquired:
            return True
        if sys.platform == 'win32':
            ok = self._acquire_windows()
        else:
            ok = self._acquire_posix()
        self._acquired = ok
        return ok

    def release(self) -> None:
        """Drop the lock. Safe to call when never acquired, and safe to call
        twice — exit paths are messy and this must never be the thing that
        raises on shutdown."""
        if sys.platform == 'win32':
            if self._handle:
                try:
                    import ctypes
                    from ctypes import wintypes
                    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                    # Same x64 truncation trap as in _acquire_windows: without
                    # argtypes the handle is passed as a 32-bit int.
                    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
                    kernel32.ReleaseMutex.restype = wintypes.BOOL
                    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                    kernel32.CloseHandle.restype = wintypes.BOOL
                    kernel32.ReleaseMutex(self._handle)
                    kernel32.CloseHandle(self._handle)
                except Exception:
                    pass
                self._handle = None
        else:
            if self._fd is not None:
                try:
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
        self._acquired = False

    # Context-manager support so callers can't forget to release.
    def __enter__(self) -> 'SingleInstance':
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    # ── platform implementations ──────────────────────────────────────────

    def _acquire_windows(self) -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            # use_last_error=True is load-bearing, not decoration. ctypes saves
            # and restores the thread's last-error value around every foreign
            # call, so calling kernel32.GetLastError() *through ctypes* reads a
            # value ctypes has already put back — i.e. whatever unrelated Win32
            # call ran before this one. That made the guard's answer depend on
            # the interpreter's recent history: it passed under one Python and
            # spuriously reported "already running" under another. The private
            # ctypes copy read by get_last_error() is the real one.
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

            # Explicit signatures matter on x64: the default restype is c_int,
            # which truncates a 64-bit HANDLE to 32 bits. The truncated value
            # is still truthy, so the bug would only surface later as a failing
            # ReleaseMutex/CloseHandle on shutdown.
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            # CreateMutexW returns a valid handle even when the mutex already
            # exists, so the only signal is the last error.
            handle = kernel32.CreateMutexW(None, False, self._win_mutex_name)
            last_error = ctypes.get_last_error()
            if not handle:
                return True   # fail open: cannot create the primitive at all
            if last_error == _ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True
        except Exception:
            return True   # fail open

    def _acquire_posix(self) -> bool:
        try:
            import fcntl
        except ImportError:
            return True   # fail open (no flock on this platform)
        try:
            self._lock_file.parent.mkdir(parents=True, exist_ok=True)
            # 0o600: the lock lives in the user's own state dir; no reason for
            # any other account to read or write it.
            fd = os.open(str(self._lock_file), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return True   # fail open: read-only home shouldn't block startup
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Held by another instance — this is the one real "no" case.
            try:
                os.close(fd)
            except OSError:
                pass
            return False
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            return True   # fail open on anything unexpected

        # Record the PID purely as a diagnostic aid for bug reports. The lock
        # itself is the flock, never this number — so a torn or stale write
        # here cannot cause a false "already running".
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
        except OSError:
            pass

        self._fd = fd
        return True

"""Regression tests for the single-instance guard (AUDIT-001).

Two instances used to run happily side by side, each spawning its own capture
engine and — on Linux — the second one unlinking and rebinding the first one's
hotkey socket.
"""
import os
import signal
import sys
import subprocess
import textwrap
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'FTHR_UI'))

from core.single_instance import SingleInstance  # noqa: E402


def test_first_acquire_succeeds():
    guard = SingleInstance()
    try:
        assert guard.acquire() is True
    finally:
        guard.release()


def test_acquire_is_idempotent_for_the_same_owner():
    """Re-acquiring from the instance that already holds the lock must not
    report a conflict with itself."""
    guard = SingleInstance()
    try:
        assert guard.acquire() is True
        assert guard.acquire() is True
    finally:
        guard.release()


def test_release_is_safe_without_acquire():
    # Exit paths call release() unconditionally; it must never raise.
    SingleInstance().release()


def test_double_release_is_safe():
    guard = SingleInstance()
    guard.acquire()
    guard.release()
    guard.release()


def test_context_manager_releases():
    with SingleInstance() as guard:
        assert guard._acquired is True
    assert guard._acquired is False


def test_second_process_is_refused_while_first_holds_lock():
    """The real contract: a *separate process* must be turned away.

    Threads in one process share the Windows mutex handle and, on Linux,
    flock is per-open-file-description — so only a subprocess actually
    exercises the guard the way two app launches do.
    """
    ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'FTHR_UI'))

    # Child: acquire, report, then hold the lock until told to exit.
    child_code = textwrap.dedent(f'''
        import sys
        sys.path.insert(0, {ui_dir!r})
        from core.single_instance import SingleInstance
        g = SingleInstance()
        print('ACQUIRED' if g.acquire() else 'REFUSED', flush=True)
        sys.stdin.readline()
        g.release()
    ''')

    child = subprocess.Popen(
        [sys.executable, '-c', child_code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        first = child.stdout.readline().strip()
        assert first == 'ACQUIRED', f'child failed to take the lock: {first!r}'

        # While the child holds it, this process must be refused.
        guard = SingleInstance()
        try:
            assert guard.acquire() is False, \
                'second instance was allowed to start while the first was running'
        finally:
            guard.release()
    finally:
        try:
            child.stdin.write('\n')
            child.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def test_lock_is_released_when_holder_dies():
    """A crashed instance must not lock the user out permanently.

    Both primitives are kernel-owned (named mutex / flock on an fd), so
    process death releases them — this pins that behaviour so nobody
    "improves" the guard into a stale PID file later.
    """
    ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'FTHR_UI'))
    child_code = textwrap.dedent(f'''
        import os, sys
        sys.path.insert(0, {ui_dir!r})
        from core.single_instance import SingleInstance
        g = SingleInstance()
        print('ACQUIRED' if g.acquire() else 'REFUSED', os.getpid(), flush=True)
        sys.stdin.readline()
    ''')

    child = subprocess.Popen(
        [sys.executable, '-c', child_code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    status, _, holder_pid = child.stdout.readline().strip().partition(' ')
    assert status == 'ACQUIRED'
    holder_pid = int(holder_pid)

    # The holder reports its OWN pid rather than us trusting Popen.pid.
    # In a Windows virtualenv, Scripts\python.exe is a launcher stub that
    # spawns the real interpreter as a separate process: Popen.pid is the
    # stub, and killing it leaves the actual lock holder running. Killing the
    # reported pid tests what the docstring claims — the kernel releases the
    # primitive when the *holding* process dies — on any interpreter layout.
    _kill_pid(holder_pid)
    # Reap before polling. On Linux the holder IS our direct child, and a killed
    # child stays a zombie — still a live pid to kill(pid, 0) — until its parent
    # waits on it. Polling first therefore never terminates.
    child.kill()          # tidy up the stub, if there was one
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    _wait_for_pid_gone(holder_pid, timeout=10)

    guard = SingleInstance()
    try:
        assert guard.acquire() is True, \
            'lock survived the death of its holder — users would be locked out after a crash'
    finally:
        guard.release()


def _kill_pid(pid: int) -> None:
    """Hard-kill a pid — no cleanup handlers run, exactly like a real crash."""
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                       capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGKILL)


def _wait_for_pid_gone(pid: int, timeout: float) -> None:
    """Block until *pid* is really gone.

    Process death and lock release are not instantaneous, and asserting
    immediately after the kill makes the test racy rather than wrong.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f'pid {pid} still alive after {timeout}s')


def _pid_alive(pid: int) -> bool:
    if sys.platform == 'win32':
        out = subprocess.run(
            ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
            capture_output=True, text=True, check=False).stdout
        return str(pid) in out
    # A zombie still answers kill(pid, 0), so ask /proc for the process state
    # before falling back to the signal probe.
    try:
        with open(f'/proc/{pid}/stat', 'rb') as fh:
            # comm may contain spaces and parentheses; state is the field
            # immediately after the closing paren.
            state = fh.read().rpartition(b')')[2].split()[0:1]
        return state != [b'Z'] if state else False
    except FileNotFoundError:
        return False
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

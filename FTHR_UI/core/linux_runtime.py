"""Secure per-user runtime directory and hotkey socket path (AUDIT-003).

The problem with the old path
-----------------------------
The hotkey socket lived at a fixed ``/tmp/fthr_hotkey.sock``. AUDIT-003 fixed
the *mode* — umask 0177 around ``bind()`` plus an explicit chmod 0600 — so
another user could no longer connect and trigger a screenshot of this user's
screen. That fix is correct and is kept.

It does not fix the *path*, and the path is the harder half:

* ``/tmp`` is world-writable. Any local user can create ``/tmp/fthr_hotkey.sock``
  first — as a regular file, a directory, or a symlink pointing anywhere.
* The old code then ran ``os.unlink(path)`` unconditionally. Two outcomes, both
  bad. With the sticky bit set (normal for /tmp) the unlink fails with EPERM,
  ``bind()`` fails, and hotkeys are dead: **any local user can deny this user's
  hotkeys indefinitely**. Without the sticky bit, FTHR happily deletes another
  user's file.
* A symlink at that path turns the unlink — or a later chmod — into an
  operation on a file of the attacker's choosing.

Moving into a directory that only this user can write removes the whole class:
an attacker cannot create, replace or symlink a path inside a 0700 directory
they do not own.

Path selection
--------------
1. ``$XDG_RUNTIME_DIR/fthr/`` — the correct location. The base directory is
   created by the system per login session, owned by the user, mode 0700, and
   cleaned up at logout. Sockets belong here.
2. ``~/.fthr/run/`` — fallback when ``XDG_RUNTIME_DIR`` is unset (some
   containers, ``su`` without a session, minimal init systems). Still
   user-owned and 0700. Deliberately **not** ``/tmp``: a private fallback is
   the point.

Both are validated before use: must exist as a real directory (``lstat``, so a
symlink is rejected rather than followed), owned by the current uid, with no
group or other permission bits.

Nothing here requires root, and nothing here should ever be made to.
"""

from __future__ import annotations

import errno
import os
import socket
import stat
import sys
from pathlib import Path

SOCKET_FILENAME = 'hotkey.sock'

# Historical path. Only used to clean up after an older build; never bound.
LEGACY_SOCKET_PATH = '/tmp/fthr_hotkey.sock'


class RuntimeDirError(RuntimeError):
    """The runtime directory is unusable and it is not safe to continue."""


def _validate_dir(d: Path) -> None:
    """Reject anything that is not a private directory owned by this user."""
    st = os.lstat(d)   # lstat: a symlink here is a finding, not a path to follow
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeDirError(f'{d} exists but is not a directory')
    if st.st_uid != os.getuid():
        raise RuntimeDirError(
            f'{d} is owned by uid {st.st_uid}, not by you ({os.getuid()}) — '
            f'refusing to use it')
    if st.st_mode & 0o077:
        raise RuntimeDirError(
            f'{d} has mode {stat.filemode(st.st_mode)}; it must not be '
            f'readable or writable by group or others')


def _candidate_bases() -> list[Path]:
    bases: list[Path] = []
    xdg = os.environ.get('XDG_RUNTIME_DIR')
    if xdg:
        bases.append(Path(xdg) / 'fthr')
    bases.append(Path.home() / '.fthr' / 'run')
    return bases


def runtime_dir(create: bool = True) -> Path:
    """Return the private runtime directory, creating it if asked.

    Tries ``$XDG_RUNTIME_DIR/fthr`` then ``~/.fthr/run``. Raises
    RuntimeDirError only if *every* candidate is unusable — a broken
    XDG_RUNTIME_DIR falls through to the home fallback rather than killing
    hotkeys outright.
    """
    if not create:
        # Pure path query — used to *display* the socket location (generated
        # binds, setup instructions). Validating here would raise merely
        # because nothing has started yet.
        return _candidate_bases()[0]

    problems: list[str] = []
    for base in _candidate_bases():
        try:
            if create:
                # mode=0o700 is applied before anything can be placed inside,
                # and mkdir is atomic — there is no window with looser bits.
                base.mkdir(mode=0o700, parents=True, exist_ok=True)
                # exist_ok=True does NOT reapply the mode to a directory that
                # already existed, so assert it.
                if base.exists():
                    os.chmod(base, 0o700)
            _validate_dir(base)
            return base
        except (OSError, RuntimeDirError) as exc:
            problems.append(f'{base}: {exc}')
    raise RuntimeDirError(
        'no usable private runtime directory. Tried:\n  ' + '\n  '.join(problems))


def hotkey_socket_path(create_dir: bool = True) -> str:
    """Absolute path of the hotkey socket, inside the private runtime dir."""
    return str(runtime_dir(create=create_dir) / SOCKET_FILENAME)


def _is_live_socket(path: str) -> bool:
    """True if something is actually listening on *path*."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        s.connect(path)
        return True
    except OSError:
        # ECONNREFUSED / ENOENT — nobody is listening. Anything else (EACCES on
        # a socket we do not own) also means "not ours to reuse", and the
        # ownership check in prepare_socket_path() is what decides that.
        return False
    finally:
        s.close()


def prepare_socket_path(path: str) -> None:
    """Make *path* safe to bind, or raise.

    Removes only a socket that (a) is a socket, (b) is owned by this user, and
    (c) has nobody listening on it. Anything else is left alone and reported:
    deleting a file we did not create is exactly the behaviour this module
    exists to remove.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return                      # nothing there — the normal case
    except OSError as exc:
        raise RuntimeDirError(f'cannot inspect {path}: {exc}') from exc

    if stat.S_ISLNK(st.st_mode):
        raise RuntimeDirError(
            f'{path} is a symlink. Refusing to touch it — remove it by hand '
            f'after checking where it points.')
    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeDirError(
            f'{path} exists and is not a socket ({stat.filemode(st.st_mode)}). '
            f'Refusing to delete a file FTHR did not create.')
    if st.st_uid != os.getuid():
        raise RuntimeDirError(
            f'{path} is owned by uid {st.st_uid}, not by you. Refusing to '
            f'remove another user\'s socket.')
    if _is_live_socket(path):
        raise RuntimeDirError(
            f'{path} is already in use by a running FTHR Clips instance.')

    # Stale socket, ours, nobody home.
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass                        # someone else cleaned up; fine
    except OSError as exc:
        raise RuntimeDirError(f'cannot remove stale socket {path}: {exc}') from exc


def cleanup_legacy_socket() -> str | None:
    """Remove the old /tmp socket iff it is ours and dead.

    Users upgrading from a build that used /tmp would otherwise leave a stray
    socket behind forever. The same ownership rules apply — if the path is a
    squatted file belonging to someone else, it is reported and left alone.
    """
    if sys.platform == 'win32':
        return None
    try:
        st = os.lstat(LEGACY_SOCKET_PATH)
    except OSError:
        return None
    if (stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid()
            and not _is_live_socket(LEGACY_SOCKET_PATH)):
        try:
            os.unlink(LEGACY_SOCKET_PATH)
            return f'removed stale legacy socket {LEGACY_SOCKET_PATH}'
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                return f'could not remove {LEGACY_SOCKET_PATH}: {exc}'
    else:
        return (f'{LEGACY_SOCKET_PATH} exists but is not a stale socket of '
                f'yours — left untouched')
    return None

"""
Hotkey Manager - global keyboard shortcuts for FTHR Clips.

On Linux/Wayland the preferred trigger path is via compositor binds that send
commands to a Unix socket. This file starts that socket server automatically.

The socket lives in a PRIVATE per-user runtime directory — normally
$XDG_RUNTIME_DIR/fthr/hotkey.sock — not in /tmp. See core/linux_runtime.py for
why: /tmp is world-writable, so the path itself could be squatted or symlinked
by any local user even though the socket mode is 0600. Call
core.linux_runtime.hotkey_socket_path() for the resolved path; do not hardcode
one, and do not print one you did not resolve.

On Hyprland these lines are written automatically to ~/.config/hypr/fthr-hotkeys.conf
whenever a hotkey is changed. The `keyboard` library fallback is kept for
non-Wayland / Windows use.
"""
import keyboard
import socket
import os
import shlex
import sys
import subprocess
import threading
from PySide6.QtCore import QObject, Signal
import json
from pathlib import Path

from core import linux_runtime, linux_tools

_FTHR_HYPR_CONF    = Path.home() / '.config' / 'hypr' / 'fthr-hotkeys.conf'
_HYPR_CONF         = Path.home() / '.config' / 'hypr' / 'hyprland.conf'


class HotkeyManager(QObject):
    """Manages global hotkeys for the application"""
    
    # Signals
    save_clip_triggered = Signal()
    save_extended_clip_triggered = Signal()
    save_screenshot_triggered = Signal()
    confirm_game_detection_triggered  = Signal()
    dismiss_game_detection_triggered  = Signal()
    error_occurred = Signal(str, str, str)   # title, detail, level
    
    def __init__(self):
        super().__init__()
        # Lives next to all the other user state in ~/.fthr. Survives reinstalls.
        self.config_file = Path.home() / '.fthr' / 'hotkeys.json'

        # Sensible defaults nobody's ever bound to anything else. F9/F10/F11 it is.
        self.hotkeys = {
            'save_clip': 'F9',
            'save_extended_clip': 'F10',
            'save_screenshot': 'F11',
            'confirm_game_detection':  'F8',
            'dismiss_game_detection':  'F7',
        }

        # We track what we've actually registered so we can cleanly unhook later.
        # The `keyboard` lib gets cranky if you remove a hotkey you never added.
        self._registered_hotkeys = []

        self._socket_running = False
        self._socket_thread: threading.Thread | None = None

        # Load saved hotkeys
        self._load_hotkeys()
    
    def _load_hotkeys(self):
        """Load hotkeys from config file"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    saved_hotkeys = json.load(f)
                    self.hotkeys.update(saved_hotkeys)
            except Exception as e:
                print(f"Failed to load hotkeys: {e}")
    
    def _save_hotkeys(self):
        """Save hotkeys to config file"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_file.with_suffix('.json.tmp')
        try:
            with open(tmp, 'w') as f:
                json.dump(self.hotkeys, f, indent=2)
            os.replace(str(tmp), str(self.config_file))
        except Exception as e:
            print(f"Failed to save hotkeys: {e}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    
    def set_hotkey(self, action: str, key: str):
        """
        Set a hotkey for an action
        
        Args:
            action: One of 'save_clip', 'save_extended_clip', 'save_screenshot'
            key: The key combination (e.g., 'F9', 'ctrl+shift+s')
        """
        if action not in self.hotkeys:
            print(f"Unknown action: {action}")
            return False

        # Refuse duplicate bindings — the same key on two actions makes one
        # keypress fire both (two saves, the second eaten by the spam guard,
        # which reads as random behavior to the user).
        for other_action, other_key in self.hotkeys.items():
            if other_action != action and other_key == key:
                self.error_occurred.emit(
                    'HOTKEY ALREADY IN USE',
                    f'"{key}" is already bound to {other_action.replace("_", " ")}.'
                    ' Pick a different key.',
                    'warning',
                )
                return False

        # Tear down the old binding first so changing a shortcut cannot leave
        # both keys registered and trigger the action twice.
        old_key = self.hotkeys.get(action)
        if old_key and old_key in self._registered_hotkeys:
            try:
                keyboard.remove_hotkey(old_key)
                self._registered_hotkeys.remove(old_key)
            except Exception:
                pass
        
        # Update hotkey
        self.hotkeys[action] = key
        self._save_hotkeys()

        # Register new hotkey
        if action == 'save_clip':
            self._register_save_clip()
        elif action == 'save_extended_clip':
            self._register_save_extended_clip()
        elif action == 'save_screenshot':
            self._register_save_screenshot()
        elif action == 'confirm_game_detection':
            self._register_confirm_game_detection()
        elif action == 'dismiss_game_detection':
            self._register_dismiss_game_detection()

        self._apply_compositor_config()
        return True
    
    def get_hotkey(self, action: str) -> str:
        """Get the current hotkey for an action"""
        return self.hotkeys.get(action, '')
    
    def _register_keyboard_hotkey(self, key: str, signal):
        """Register one hotkey via the keyboard library. Silent on Linux if it
        fails — the socket server is the primary hotkey path on Linux/Wayland."""
        try:
            keyboard.add_hotkey(key, lambda: signal.emit())
            if key not in self._registered_hotkeys:
                self._registered_hotkeys.append(key)
        except Exception as e:
            self._keyboard_failed = True
            if sys.platform != 'linux':
                # On Windows the keyboard lib is the ONLY hotkey path —
                # a silent failure means hotkeys just don't work. Surface it.
                print(f"Failed to register hotkey {key}: {e}")
                self.error_occurred.emit(
                    'HOTKEY REGISTRATION FAILED',
                    f'Could not register "{key}" — it may be in use by another '
                    'app. Pick a different key in Hotkey settings.',
                    'warning',
                )

    def _register_save_clip(self):
        self._register_keyboard_hotkey(
            self.hotkeys['save_clip'], self.save_clip_triggered)

    def _register_save_extended_clip(self):
        self._register_keyboard_hotkey(
            self.hotkeys['save_extended_clip'], self.save_extended_clip_triggered)

    def _register_save_screenshot(self):
        self._register_keyboard_hotkey(
            self.hotkeys['save_screenshot'], self.save_screenshot_triggered)

    def _register_confirm_game_detection(self):
        self._register_keyboard_hotkey(
            self.hotkeys['confirm_game_detection'],
            self.confirm_game_detection_triggered)

    def _register_dismiss_game_detection(self):
        self._register_keyboard_hotkey(
            self.hotkeys['dismiss_game_detection'],
            self.dismiss_game_detection_triggered)

    def register_all(self):
        """Register all hotkeys"""
        self._keyboard_failed = False
        self._register_save_clip()
        self._register_save_extended_clip()
        self._register_save_screenshot()
        self._register_confirm_game_detection()
        self._register_dismiss_game_detection()
        self._start_socket_server()
        self._apply_compositor_config()
        self._warn_if_linux_hotkeys_dead()

    def _warn_if_linux_hotkeys_dead(self):
        """On Linux the keyboard lib needs root and fails silently by design;
        the socket path only works when a compositor (Hyprland) sends binds.
        On anything else (GNOME/KDE/X11 without manual binds) the user has
        NO working hotkeys and previously got no hint why."""
        if sys.platform == 'win32' or not getattr(self, '_keyboard_failed', False):
            return
        comp = None
        try:
            from core.compositor import detect_compositor
            comp = detect_compositor()
        except Exception:
            pass
        if comp == 'hyprland':
            return   # binds are auto-written; hotkeys work via the socket
        # Be specific about which desktop this is and what to actually do.
        # A vague warning here is why "hotkeys don't work" was the most common
        # Linux report with no actionable follow-up.
        where = {
            'kwin':  'KDE: System Settings -> Shortcuts -> Add Command',
            'gnome': 'GNOME: Settings -> Keyboard -> Custom Shortcuts',
            'x11':   "your window manager's keybinding config",
        }.get(comp or '', "your desktop's custom-shortcut settings")

        if not linux_tools.available('nc'):
            self.error_occurred.emit(
                'GLOBAL HOTKEYS UNAVAILABLE',
                linux_tools.missing_message('nc') +
                ' Without it, compositor binds cannot reach FTHR Clips.',
                'error',
            )
            return

        self.error_occurred.emit(
            'GLOBAL HOTKEYS NEED MANUAL SETUP',
            'Direct key capture needs root on Linux and is disabled by design. '
            f'Bind a key in {where} to this command:    '
            f'{self.socket_command("save_clip")}',
            'warning',
        )

    # ------------------------------------------------------------------
    # Compositor auto-config (Hyprland)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_hyprland_bind(key: str) -> tuple[str, str]:
        """Convert a key string to (modifier, key) for Hyprland bind syntax.

        Examples:
          'F9'           -> ('', 'F9')
          'ctrl+shift+s' -> ('CTRL SHIFT', 'S')
          'alt+F12'      -> ('ALT', 'F12')
        """
        parts = key.split('+')
        if len(parts) == 1:
            return '', parts[0]
        mods = ' '.join(p.upper() for p in parts[:-1])
        k    = parts[-1].upper() if len(parts[-1]) == 1 else parts[-1]
        return mods, k

    def socket_command(self, action: str) -> str:
        """The exact shell command a compositor bind must run for *action*.

        Single source for every generated bind and every instruction we show
        the user. Both the socket path and the `nc` binary are resolved, not
        guessed: the path moved out of /tmp (AUDIT-003b) and a bare `nc` picks
        up whatever is first on PATH.
        """
        nc = linux_tools.path('nc') or 'nc'
        sock = getattr(self, '_socket_path', None)
        if not sock:
            try:
                sock = linux_runtime.hotkey_socket_path(create_dir=False)
            except Exception as exc:
                print(f'[Hotkey] Cannot determine socket path: {exc}')
                sock = '<socket unavailable>'
        return f'echo -n "{action}" | {nc} -U {sock}'

    def setup_instructions(self, compositor: str) -> str:
        """Return compositor instructions built from the real private socket."""
        commands = {
            action: self.socket_command(action)
            for action in ('save_clip', 'save_extended_clip', 'save_screenshot')
        }
        if compositor == 'kwin':
            heading = 'KDE: System Settings → Shortcuts → Custom Shortcuts'
            rendered = commands.values()
        elif compositor == 'gnome':
            heading = 'GNOME: Settings → Keyboard → Custom Shortcuts'
            rendered = (f'bash -c {shlex.quote(command)}'
                        for command in commands.values())
        else:
            heading = 'Bind your preferred keys to these commands:'
            rendered = commands.values()
        return heading + '\n\n' + '\n'.join(rendered)

    def _build_bind_line(self, action: str) -> str:
        key       = self.hotkeys.get(action, '')
        mod, k    = self._to_hyprland_bind(key)
        return f'bind = {mod}, {k}, exec, {self.socket_command(action)}'

    def _write_hyprland_config(self) -> None:
        """Write ~/.config/hypr/fthr-hotkeys.conf with current hotkeys."""
        lines = [
            '# FTHR Clips hotkeys — auto-generated, do not edit manually.',
            '# Change hotkeys inside FTHR Clips → Hotkeys menu.',
            self._build_bind_line('save_clip'),
            self._build_bind_line('save_extended_clip'),
            self._build_bind_line('save_screenshot'),
            '',
        ]
        tmp = _FTHR_HYPR_CONF.with_suffix('.conf.tmp')
        try:
            _FTHR_HYPR_CONF.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text('\n'.join(lines))
            os.replace(str(tmp), str(_FTHR_HYPR_CONF))
            print(f'[Hotkey] Written {_FTHR_HYPR_CONF}')
        except Exception as e:
            print(f'[Hotkey] Failed to write Hyprland config: {e}')
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _ensure_hyprland_source(self) -> None:
        """Add a source line to hyprland.conf if it doesn't already exist.

        Skipped if the user already has fthr_hotkey.sock bind lines directly
        in hyprland.conf — we don't want to add a second set of binds.
        """
        if not _HYPR_CONF.exists():
            return
        try:
            content = _HYPR_CONF.read_text()
        except Exception:
            return
        # Already sourced, or user has manual FTHR binds — either way, skip.
        if 'fthr-hotkeys.conf' in content or 'fthr_hotkey.sock' in content:
            return
        source_line = f'\n# FTHR Clips hotkeys (auto-added)\nsource = {_FTHR_HYPR_CONF}\n'
        try:
            with open(_HYPR_CONF, 'a') as f:
                f.write(source_line)
            print(f'[Hotkey] Added source line to {_HYPR_CONF}')
        except Exception as e:
            print(f'[Hotkey] Could not add source line: {e}')

    def _apply_compositor_config(self) -> None:
        """Write compositor config and apply live. Hyprland only for now."""
        if sys.platform == 'win32':
            return
        try:
            from core.compositor import detect_compositor
            comp = detect_compositor()
        except Exception:
            return
        if comp != 'hyprland':
            return
        self._write_hyprland_config()
        self._ensure_hyprland_source()
        # Apply live without a full reload — one keyword per binding.
        # hyprctl keyword bind accepts the same format as the config file.
        if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            def _reload():
                try:
                    hyprctl = linux_tools.path('hyprctl')
                    if not hyprctl:
                        raise FileNotFoundError(
                            linux_tools.missing_message('hyprctl'))
                    subprocess.run(
                        [hyprctl, 'reload'],
                        capture_output=True, timeout=5,
                    )
                    print('[Hotkey] Hyprland config reloaded.')
                except Exception as e:
                    print(f'[Hotkey] hyprctl reload failed: {e}')
                    self.error_occurred.emit(
                        'HOTKEY CONFIG ERROR',
                        'Hyprland config reload failed. Re-apply manually in Hotkey settings.',
                        'warning',
                    )
            threading.Thread(target=_reload, daemon=True,
                             name='fthr-hyprctl-apply').start()

    def _start_socket_server(self):
        """Listen on the private hotkey socket for compositor bind commands."""
        # Unix-socket path is Linux-only. On Windows the keyboard library is
        # the one and only hotkey path — starting this would raise
        # AttributeError (no AF_UNIX) and flash a bogus error banner.
        if sys.platform == 'win32' or not hasattr(socket, 'AF_UNIX'):
            return

        # One-time migration: a build before AUDIT-003b bound /tmp/fthr_hotkey.sock.
        # Remove it only if it is ours and dead; never touch a squatted path.
        legacy = linux_runtime.cleanup_legacy_socket()
        if legacy:
            print(f'[Hotkey] {legacy}')

        try:
            sock_path = linux_runtime.hotkey_socket_path()
            # Refuses to delete anything that is not a stale socket owned by
            # this user, and refuses to steal a socket a live instance holds.
            linux_runtime.prepare_socket_path(sock_path)
        except linux_runtime.RuntimeDirError as e:
            print(f'[Hotkey] Cannot prepare socket: {e}')
            self.error_occurred.emit(
                'HOTKEY SERVER FAILED',
                f'{e} Hotkeys will not work until this is resolved.',
                'error',
            )
            return
        except OSError as e:
            print(f'[Hotkey] Cannot prepare socket: {e}')
            self.error_occurred.emit(
                'HOTKEY SERVER FAILED',
                'Could not create the private runtime directory. '
                'Hotkeys will not work.',
                'error',
            )
            return

        self._socket_path = sock_path

        try:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            # Owner-only from the moment the node appears. The directory is
            # already 0700 and user-owned, so this is defence in depth rather
            # than the only barrier — but a chmod *after* bind() would still
            # leave a window, so the umask stays.
            _old_umask = os.umask(0o177)
            try:
                srv.bind(sock_path)
            finally:
                os.umask(_old_umask)
            # Some kernels/filesystems ignore umask for AF_UNIX nodes, so
            # assert the mode explicitly as well.
            try:
                os.chmod(sock_path, 0o600)
            except OSError as e:
                print(f'[Hotkey] Could not tighten socket permissions: {e}')
            srv.listen(8)
            srv.settimeout(1.0)
        except Exception as e:
            print(f"[Hotkey] Socket server failed to start: {e}")
            self.error_occurred.emit(
                'HOTKEY SERVER FAILED',
                'Port in use or permission denied. Hotkeys will not work.',
                'error',
            )
            return

        self._socket_running = True
        print(f"[Hotkey] Socket server listening on {sock_path}")

        _dispatch = {
            'save_clip':               self.save_clip_triggered,
            'save_extended_clip':      self.save_extended_clip_triggered,
            'save_screenshot':         self.save_screenshot_triggered,
            'confirm_game_detection':  self.confirm_game_detection_triggered,
            'dismiss_game_detection':  self.dismiss_game_detection_triggered,
        }

        def _serve():
            while self._socket_running:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break
                with conn:
                    try:
                        # Single recv — all commands fit in 256 bytes.
                        # Do NOT loop until EOF: nc without -N holds the
                        # connection open waiting for a server response,
                        # causing both sides to hang indefinitely.
                        conn.settimeout(0.5)
                        data = conn.recv(256).decode().strip()
                        print(f"[Hotkey] Received: {data!r}")
                        if data in _dispatch:
                            # Direct emit is safe: PySide6 AutoConnection detects
                            # the cross-thread call and queues it to the main
                            # thread automatically. QTimer.singleShot does NOT
                            # work from a plain threading.Thread (no event loop).
                            _dispatch[data].emit()
                        else:
                            print(f"[Hotkey] Unknown command: {data!r}")
                    except Exception as e:
                        print(f"[Hotkey] Socket recv error: {e}")
            srv.close()
            try:
                os.unlink(sock_path)
            except FileNotFoundError:
                pass

        self._socket_thread = threading.Thread(target=_serve, daemon=True, name='fthr-hotkey-socket')
        self._socket_thread.start()

    def unregister_all(self):
        """Unregister all hotkeys"""
        for key in self._registered_hotkeys:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass
        self._registered_hotkeys.clear()

    def cleanup(self):
        """Clean up hotkeys on exit"""
        self.unregister_all()
        self._socket_running = False
        if self._socket_thread:
            self._socket_thread.join(timeout=2.0)


# The dropdown options in settings. Not exhaustive on purpose — these are the
# combos that (a) don't collide with common game binds and (b) actually work
# cross-platform with the `keyboard` lib. Add more at your own peril.
AVAILABLE_KEYS = [
    'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
    'ctrl+shift+s', 'ctrl+shift+c', 'ctrl+shift+x',
    'alt+s', 'alt+c', 'alt+x',
    'ctrl+alt+s', 'ctrl+alt+c', 'ctrl+alt+x'
]

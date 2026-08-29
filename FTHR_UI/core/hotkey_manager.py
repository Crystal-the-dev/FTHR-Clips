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
import ctypes
import keyboard
import socket
import os
import re
import shlex
import sys
import subprocess
import threading
import time
from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal, QTimer, Qt
import json
from pathlib import Path

from core import linux_runtime, linux_tools

_FTHR_HYPR_CONF    = Path.home() / '.config' / 'hypr' / 'fthr-hotkeys.conf'
_HYPR_CONF         = Path.home() / '.config' / 'hypr' / 'hyprland.conf'

# Older builds stored each binding as ``{'keyboard': 'F9', 'controller': ...}``
# and used the longer game-detection action names.  Keep the user's keyboard
# choices when upgrading instead of letting the nested record reach Qt.
_LEGACY_ACTION_NAMES = {
    'confirm_game_detection': 'game_capture_accept',
    'dismiss_game_detection': 'game_capture_dismiss',
}

_HOTKEY_ACTIONS = (
    'save_clip',
    'save_extended_clip',
    'save_screenshot',
    'start_recording',
    'stop_recording',
    'confirm_game_detection',
    'dismiss_game_detection',
)

# The legacy Windows selector accepted controller chords as well as keyboard
# combinations.  Keep the same names and ordering so existing controller
# bindings remain readable after an upgrade.
CONTROLLER_BUTTON_ORDER = (
    'LT', 'RT', 'LB', 'RB',
    'Back', 'Start', 'Share', 'LS', 'RS',
    'DPad Up', 'DPad Down', 'DPad Left', 'DPad Right',
    'Y', 'B', 'A', 'X',
)
_CONTROLLER_BUTTON_ALIASES = {
    'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y',
    'lb': 'LB', 'left bumper': 'LB', 'l1': 'LB',
    'rb': 'RB', 'right bumper': 'RB', 'r1': 'RB',
    'lt': 'LT', 'left trigger': 'LT', 'l2': 'LT',
    'rt': 'RT', 'right trigger': 'RT', 'r2': 'RT',
    'back': 'Back', 'select': 'Back', 'view': 'Back',
    'start': 'Start', 'menu': 'Start', 'share': 'Share',
    'capture': 'Share', 'screenshot': 'Share', 'misc': 'Share',
    'misc 1': 'Share',
    'ls': 'LS', 'left stick': 'LS', 'l3': 'LS',
    'rs': 'RS', 'right stick': 'RS', 'r3': 'RS',
    'dpad up': 'DPad Up', 'd-pad up': 'DPad Up', 'up': 'DPad Up',
    'dpad down': 'DPad Down', 'd-pad down': 'DPad Down', 'down': 'DPad Down',
    'dpad left': 'DPad Left', 'd-pad left': 'DPad Left', 'left': 'DPad Left',
    'dpad right': 'DPad Right', 'd-pad right': 'DPad Right', 'right': 'DPad Right',
}
_XINPUT_BUTTON_FLAGS = {
    'DPad Up': 0x0001, 'DPad Down': 0x0002,
    'DPad Left': 0x0004, 'DPad Right': 0x0008,
    'Start': 0x0010, 'Back': 0x0020, 'LS': 0x0040, 'RS': 0x0080,
    'LB': 0x0100, 'RB': 0x0200,
    'A': 0x1000, 'B': 0x2000, 'X': 0x4000, 'Y': 0x8000,
}
_XINPUT_TRIGGER_THRESHOLD = 30
WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDEV_INPUTSINK = 0x00000100
RIDEV_DEVNOTIFY = 0x00002000
RIM_TYPEHID = 2
RAW_GAME_CONTROLLER_USAGES = (
    (0x01, 0x04),  # Joystick
    (0x01, 0x05),  # Game Pad
    (0x01, 0x08),  # Multi-axis controller
)
_POINTER_MASK = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
_KEYBOARD_MODIFIERS = ('Ctrl', 'Alt', 'Shift', 'Win')
_KEYBOARD_MODIFIER_ALIASES = {
    'ctrl': 'Ctrl', 'control': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift',
    'win': 'Win', 'windows': 'Win', 'meta': 'Win', 'cmd': 'Win', 'command': 'Win',
}


class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ('wButtons', ctypes.c_ushort),
        ('bLeftTrigger', ctypes.c_ubyte),
        ('bRightTrigger', ctypes.c_ubyte),
        ('sThumbLX', ctypes.c_short),
        ('sThumbLY', ctypes.c_short),
        ('sThumbRX', ctypes.c_short),
        ('sThumbRY', ctypes.c_short),
    ]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ('dwPacketNumber', ctypes.c_uint),
        ('Gamepad', _XINPUT_GAMEPAD),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ('hwnd', ctypes.c_void_p),
        ('message', ctypes.c_uint),
        ('wParam', ctypes.c_size_t),
        ('lParam', ctypes.c_ssize_t),
        ('time', ctypes.c_uint32),
        ('pt_x', ctypes.c_long),
        ('pt_y', ctypes.c_long),
    ]


class _RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ('usUsagePage', ctypes.c_ushort),
        ('usUsage', ctypes.c_ushort),
        ('dwFlags', ctypes.c_uint),
        ('hwndTarget', ctypes.c_void_p),
    ]


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ('dwType', ctypes.c_uint),
        ('dwSize', ctypes.c_uint),
        ('hDevice', ctypes.c_void_p),
        ('wParam', ctypes.c_size_t),
    ]


_USER32 = ctypes.WinDLL('user32', use_last_error=True) if sys.platform == 'win32' else None
if _USER32 is not None:
    _USER32.RegisterRawInputDevices.argtypes = (
        ctypes.POINTER(_RAWINPUTDEVICE), ctypes.c_uint, ctypes.c_uint)
    _USER32.RegisterRawInputDevices.restype = ctypes.c_bool
    _USER32.GetRawInputData.argtypes = (
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint), ctypes.c_uint)
    _USER32.GetRawInputData.restype = ctypes.c_uint
    _USER32.GetRawInputDeviceInfoW.argtypes = (
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint))
    _USER32.GetRawInputDeviceInfoW.restype = ctypes.c_uint


def _load_xinput_get_state():
    """Return the first usable XInput reader, or ``None`` off Windows."""
    if sys.platform != 'win32':
        return None
    for dll_name in ('xinput1_4', 'xinput9_1_0', 'xinput1_3'):
        try:
            dll = ctypes.WinDLL(dll_name)
            get_state = dll.XInputGetState
            get_state.argtypes = (ctypes.c_uint, ctypes.POINTER(_XINPUT_STATE))
            get_state.restype = ctypes.c_uint
            return get_state
        except Exception:
            continue
    return None


def _qt_key_value(key) -> int:
    return int(key.value) if hasattr(key, 'value') else int(key)


def _build_qt_key_names() -> dict[int, str]:
    names = {
        _qt_key_value(Qt.Key.Key_Control): 'Ctrl',
        _qt_key_value(Qt.Key.Key_Alt): 'Alt',
        _qt_key_value(Qt.Key.Key_Shift): 'Shift',
        _qt_key_value(Qt.Key.Key_Meta): 'Win',
        _qt_key_value(Qt.Key.Key_Escape): 'Esc',
        _qt_key_value(Qt.Key.Key_Return): 'Enter',
        _qt_key_value(Qt.Key.Key_Enter): 'Enter',
        _qt_key_value(Qt.Key.Key_Space): 'Space',
        _qt_key_value(Qt.Key.Key_Tab): 'Tab',
        _qt_key_value(Qt.Key.Key_Backspace): 'Backspace',
        _qt_key_value(Qt.Key.Key_Delete): 'Delete',
        _qt_key_value(Qt.Key.Key_Insert): 'Insert',
        _qt_key_value(Qt.Key.Key_Home): 'Home',
        _qt_key_value(Qt.Key.Key_End): 'End',
        _qt_key_value(Qt.Key.Key_PageUp): 'Page Up',
        _qt_key_value(Qt.Key.Key_PageDown): 'Page Down',
        _qt_key_value(Qt.Key.Key_Up): 'Up',
        _qt_key_value(Qt.Key.Key_Down): 'Down',
        _qt_key_value(Qt.Key.Key_Left): 'Left',
        _qt_key_value(Qt.Key.Key_Right): 'Right',
    }
    for number in range(10):
        names[_qt_key_value(getattr(Qt.Key, f'Key_{number}'))] = str(number)
    for code in range(ord('A'), ord('Z') + 1):
        letter = chr(code)
        names[_qt_key_value(getattr(Qt.Key, f'Key_{letter}'))] = letter
    for number in range(1, 25):
        names[_qt_key_value(getattr(Qt.Key, f'Key_F{number}'))] = f'F{number}'
    return names


_QT_KEY_NAMES = _build_qt_key_names()


def normalize_keyboard_combo(combo) -> str:
    """Canonicalise a keyboard chord for display, storage and registration."""
    if combo is None:
        return ''
    if isinstance(combo, (list, tuple, set)):
        raw_parts = [str(part).strip() for part in combo if str(part).strip()]
    else:
        raw_parts = [part.strip() for part in str(combo).split('+') if part.strip()]
    seen, modifiers, keys = set(), [], []
    for raw_part in raw_parts:
        key = raw_part.lower().replace('_', ' ')
        canonical = _KEYBOARD_MODIFIER_ALIASES.get(key)
        if canonical is None:
            if len(raw_part) == 1 and raw_part.isalpha():
                canonical = raw_part.upper()
            elif len(raw_part) == 1 and raw_part.isdigit():
                canonical = raw_part
            elif key.startswith('f') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
                canonical = f'F{int(key[1:])}'
            else:
                canonical = raw_part.strip().title()
        if canonical in seen:
            continue
        seen.add(canonical)
        (modifiers if canonical in _KEYBOARD_MODIFIERS else keys).append(canonical)
    return '+'.join([mod for mod in _KEYBOARD_MODIFIERS if mod in modifiers] + keys)


def format_keyboard_combo(combo) -> str:
    return normalize_keyboard_combo(combo) or 'Unset'


def normalize_controller_combo(combo) -> str:
    """Canonicalise a controller chord using the legacy selector's order."""
    if combo is None:
        return ''
    if isinstance(combo, (list, tuple, set)):
        raw_parts = [str(part).strip() for part in combo if str(part).strip()]
    else:
        raw_parts = [part.strip() for part in str(combo).split('+') if part.strip()]
    seen = set()
    raw_buttons = []
    for raw_part in raw_parts:
        canonical = _CONTROLLER_BUTTON_ALIASES.get(
            raw_part.lower().replace('_', ' '), raw_part.strip())
        if canonical in CONTROLLER_BUTTON_ORDER:
            seen.add(canonical)
        elif _is_raw_controller_token(canonical) and canonical not in raw_buttons:
            raw_buttons.append(canonical)
    ordered = [button for button in CONTROLLER_BUTTON_ORDER if button in seen]
    return '+'.join(ordered + raw_buttons)


def _is_raw_controller_token(value: str) -> bool:
    return bool(re.fullmatch(r'HID:[^:]+:B\d+\.\d+', str(value).strip(), re.IGNORECASE))


def _format_controller_button(value: str) -> str:
    value = str(value)
    return f'Raw {value.rsplit(":", 1)[-1]}' if _is_raw_controller_token(value) else value


def format_controller_combo(combo) -> str:
    normalized = normalize_controller_combo(combo)
    if not normalized:
        return 'Add'
    return ' + '.join(_format_controller_button(button)
                      for button in normalized.split('+'))


class HotkeyManager(QObject, QAbstractNativeEventFilter):
    """Manages global keyboard and Windows controller hotkeys."""
    
    # Signals
    save_clip_triggered = Signal()
    save_extended_clip_triggered = Signal()
    save_screenshot_triggered = Signal()
    start_recording_triggered = Signal()
    stop_recording_triggered = Signal()
    confirm_game_detection_triggered  = Signal()
    dismiss_game_detection_triggered  = Signal()
    error_occurred = Signal(str, str, str)   # title, detail, level
    controller_buttons_changed = Signal(object)
    
    def __init__(self):
        super().__init__()
        # Lives next to all the other user state in ~/.fthr. Survives reinstalls.
        self.config_file = Path.home() / '.fthr' / 'hotkeys.json'

        # Sensible defaults nobody's ever bound to anything else. F9/F10/F11 it is.
        self.hotkeys = {
            'save_clip': 'F9',
            'save_extended_clip': 'F10',
            'save_screenshot': 'F11',
            # Recording controls are opt-in so an upgrade never claims a key
            # the user already relies on in a game or another recorder.
            'start_recording': '',
            'stop_recording': '',
            'confirm_game_detection':  'F8',
            'dismiss_game_detection':  'F7',
        }
        self.controller_hotkeys = {action: '' for action in self.hotkeys}

        # We track what we've actually registered so we can cleanly unhook later.
        # The `keyboard` lib gets cranky if you remove a hotkey you never added.
        self._registered_hotkeys = []

        self._socket_running = False
        self._socket_thread: threading.Thread | None = None
        self._input_capture_depth = 0
        self._xinput_get_state = _load_xinput_get_state()
        self._xinput_controller_buttons: set[str] = set()
        self._raw_input_widget = None
        self._raw_input_registered = False
        self._raw_device_names = {}
        self._raw_hid_reports = {}
        self._raw_controller_buttons: set[str] = set()
        self._raw_controller_buttons_by_device = {}
        self._controller_buttons: set[str] = set()
        self._controller_active_actions: set[str] = set()
        self._controller_timer = QTimer(self)
        self._controller_timer.setInterval(40)
        self._controller_timer.timeout.connect(self._poll_controller_buttons)

        # Load saved hotkeys
        self._load_hotkeys()
    
    def _load_hotkeys(self):
        """Load hotkeys from config file"""
        # This also keeps the migration helper usable in isolated tests and
        # maintenance scripts that construct a lightweight manager instance.
        if not hasattr(self, 'controller_hotkeys'):
            self.controller_hotkeys = {action: '' for action in self.hotkeys}
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    saved_hotkeys = json.load(f)
                if not isinstance(saved_hotkeys, dict):
                    raise ValueError('hotkeys config must contain an object')

                migrated = False
                keyboard_map = saved_hotkeys.get('keyboard')
                controller_map = saved_hotkeys.get('controller')
                for action in self.hotkeys:
                    source_action = action
                    if source_action not in saved_hotkeys:
                        source_action = _LEGACY_ACTION_NAMES.get(action, action)
                        migrated |= source_action != action and source_action in saved_hotkeys

                    value = saved_hotkeys.get(source_action)
                    if isinstance(keyboard_map, dict):
                        value = keyboard_map.get(source_action, value)
                        migrated = True
                    if isinstance(value, dict):
                        keyboard_value = value.get('keyboard')
                        controller_value = value.get('controller')
                    else:
                        keyboard_value = value
                        controller_value = (
                            controller_map.get(source_action)
                            if isinstance(controller_map, dict) else None)
                    if isinstance(keyboard_value, str) and keyboard_value.strip():
                        self.hotkeys[action] = normalize_keyboard_combo(keyboard_value)
                    elif value is not None:
                        migrated = True
                    if isinstance(controller_value, str):
                        self.controller_hotkeys[action] = normalize_controller_combo(
                            controller_value)
                        migrated = True

                if migrated:
                    self._save_hotkeys()
            except Exception as e:
                print(f"Failed to load hotkeys: {e}")
    
    def _save_hotkeys(self):
        """Save hotkeys to config file"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_file.with_suffix('.json.tmp')
        try:
            has_controller_bindings = any(self.controller_hotkeys.values())
            if has_controller_bindings:
                payload = {
                    action: {
                        'keyboard': self.hotkeys[action],
                        'controller': self.controller_hotkeys[action],
                    }
                    for action in self.hotkeys
                }
            else:
                payload = self.hotkeys
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
            os.replace(str(tmp), str(self.config_file))
        except Exception as e:
            print(f"Failed to save hotkeys: {e}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    
    def set_hotkey(self, action: str, key: str, device: str = 'keyboard'):
        """
        Set a hotkey for an action
        
        Args:
            action: A key from ``_HOTKEY_ACTIONS``.
            key: The key combination (e.g., 'F9', 'ctrl+shift+s')
        """
        if action not in self.hotkeys:
            print(f"Unknown action: {action}")
            return False

        if device == 'controller':
            normalized = normalize_controller_combo(key)
            for other_action, other_key in self.controller_hotkeys.items():
                if (normalized and other_action != action
                        and other_key == normalized):
                    self.error_occurred.emit(
                        'CONTROLLER HOTKEY ALREADY IN USE',
                        f'"{format_controller_combo(normalized)}" is already bound to '
                        f'{other_action.replace("_", " ")}. Pick a different binding.',
                        'warning',
                    )
                    return False
            self.controller_hotkeys[action] = normalized
            self._controller_active_actions.discard(action)
            self._save_hotkeys()
            self._ensure_controller_polling()
            return True
        if device != 'keyboard':
            print(f"Unknown hotkey device: {device}")
            return False

        key = normalize_keyboard_combo(key)

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
        elif action == 'start_recording':
            self._register_start_recording()
        elif action == 'stop_recording':
            self._register_stop_recording()
        elif action == 'confirm_game_detection':
            self._register_confirm_game_detection()
        elif action == 'dismiss_game_detection':
            self._register_dismiss_game_detection()

        self._apply_compositor_config()
        return True
    
    def get_hotkey(self, action: str, device: str = 'keyboard') -> str:
        """Get the current keyboard or controller hotkey for an action."""
        if device == 'controller':
            return self.controller_hotkeys.get(action, '')
        return self.hotkeys.get(action, '')

    def get_controller_hotkey(self, action: str) -> str:
        return self.get_hotkey(action, device='controller')

    def set_controller_hotkey(self, action: str, key: str) -> bool:
        return self.set_hotkey(action, key, device='controller')
    
    def _register_keyboard_hotkey(self, key: str, signal):
        """Register one hotkey via the keyboard library. Silent on Linux if it
        fails — the socket server is the primary hotkey path on Linux/Wayland."""
        if not key:
            return
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

    def _register_start_recording(self):
        self._register_keyboard_hotkey(
            self.hotkeys['start_recording'], self.start_recording_triggered)

    def _register_stop_recording(self):
        self._register_keyboard_hotkey(
            self.hotkeys['stop_recording'], self.stop_recording_triggered)

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
        self._unregister_keyboard_hotkeys()
        self._keyboard_failed = False
        self._register_save_clip()
        self._register_save_extended_clip()
        self._register_save_screenshot()
        self._register_start_recording()
        self._register_stop_recording()
        self._register_confirm_game_detection()
        self._register_dismiss_game_detection()
        self._start_socket_server()
        self._apply_compositor_config()
        self._ensure_controller_polling()
        self._warn_if_linux_hotkeys_dead()

    def begin_input_capture(self):
        """Pause dispatch while a selector records a new keyboard/controller chord."""
        if self._input_capture_depth == 0:
            self._unregister_keyboard_hotkeys()
            self._controller_active_actions.clear()
            self._ensure_controller_polling(force=True)
        self._input_capture_depth += 1

    def end_input_capture(self):
        if self._input_capture_depth == 0:
            return
        self._input_capture_depth -= 1
        if self._input_capture_depth == 0:
            self._controller_active_actions.clear()
            self.register_all()

    def qt_key_name(self, event) -> str:
        key_name = _QT_KEY_NAMES.get(_qt_key_value(event.key()))
        if key_name:
            return key_name
        text = event.text()
        if text and text.strip():
            char = text.strip()
            if len(char) == 1:
                return char.upper() if char.isalpha() else char
        return ''

    def qt_modifier_names(self, event) -> list[str]:
        modifiers = event.modifiers()
        names = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            names.append('Ctrl')
        if modifiers & Qt.KeyboardModifier.AltModifier:
            names.append('Alt')
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            names.append('Shift')
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            names.append('Win')
        return names

    def nativeEventFilter(self, _event_type, message):
        """Receive raw HID reports for controllers that do not expose XInput."""
        if sys.platform != 'win32' or not self._raw_input_registered:
            return False, 0
        try:
            native_message = _MSG.from_address(int(message))
        except Exception:
            # Qt may pass a non-address message on a platform plugin we do not own.
            return False, 0
        if native_message.message == WM_INPUT:
            self._handle_raw_input(native_message.lParam)
        return False, 0

    def _emit_action(self, action: str):
        """Emit an action once when a controller chord is first completed."""
        now = time.monotonic()
        if now - getattr(self, '_last_emit_at', {}).get(action, 0.0) < 0.35:
            return
        if not hasattr(self, '_last_emit_at'):
            self._last_emit_at = {}
        self._last_emit_at[action] = now
        signals = {
            'save_clip': self.save_clip_triggered,
            'save_extended_clip': self.save_extended_clip_triggered,
            'save_screenshot': self.save_screenshot_triggered,
            'start_recording': self.start_recording_triggered,
            'stop_recording': self.stop_recording_triggered,
            'confirm_game_detection': self.confirm_game_detection_triggered,
            'dismiss_game_detection': self.dismiss_game_detection_triggered,
        }
        signal = signals.get(action)
        if signal:
            signal.emit()

    def _has_controller_bindings(self) -> bool:
        return any(self.controller_hotkeys.values())

    def _ensure_controller_polling(self, *, force: bool = False) -> bool:
        if not force and not self._has_controller_bindings():
            self._stop_controller_polling()
            return False
        registered = False
        if self._xinput_get_state is not None and not self._controller_timer.isActive():
            self._controller_timer.start()
            registered = True
        elif self._xinput_get_state is not None:
            registered = True
        return self._ensure_raw_input() or registered

    def _stop_controller_polling(self):
        self._controller_timer.stop()
        self._xinput_controller_buttons.clear()
        self._raw_controller_buttons.clear()
        self._raw_controller_buttons_by_device.clear()
        self._raw_hid_reports.clear()
        if self._controller_buttons:
            self._controller_buttons.clear()
            self.controller_buttons_changed.emit(set())
        self._controller_active_actions.clear()

    def _ensure_raw_input(self) -> bool:
        """Register a hidden native target for generic Windows HID pads."""
        if sys.platform != 'win32' or _USER32 is None:
            return False
        if self._raw_input_registered:
            return True
        try:
            from PySide6.QtWidgets import QApplication, QWidget
            if QApplication.instance() is None:
                return False
            if self._raw_input_widget is None:
                widget = QWidget()
                widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
                widget.setWindowTitle('FTHR Raw Controller Input')
                widget.resize(1, 1)
                widget.hide()
                self._raw_input_widget = widget
            native_window = int(self._raw_input_widget.winId())
            devices = (_RAWINPUTDEVICE * len(RAW_GAME_CONTROLLER_USAGES))()
            for index, (usage_page, usage) in enumerate(RAW_GAME_CONTROLLER_USAGES):
                devices[index].usUsagePage = usage_page
                devices[index].usUsage = usage
                devices[index].dwFlags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY
                devices[index].hwndTarget = native_window
            if not _USER32.RegisterRawInputDevices(
                    devices, len(RAW_GAME_CONTROLLER_USAGES),
                    ctypes.sizeof(_RAWINPUTDEVICE)):
                print(f'Raw controller input registration failed, WinError={ctypes.get_last_error()}')
                return False
            QCoreApplication.instance().installNativeEventFilter(self)
            self._raw_input_registered = True
            print('Registered raw controller input fallback')
            return True
        except Exception as exc:
            print(f'Raw controller input unavailable: {exc}')
            return False

    def _handle_raw_input(self, raw_handle):
        raw_ptr = ctypes.c_void_p(int(raw_handle) & _POINTER_MASK)
        size = ctypes.c_uint(0)
        header_size = ctypes.sizeof(_RAWINPUTHEADER)
        result = _USER32.GetRawInputData(
            raw_ptr, RID_INPUT, None, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF or size.value <= header_size:
            return
        buffer = ctypes.create_string_buffer(size.value)
        result = _USER32.GetRawInputData(
            raw_ptr, RID_INPUT, buffer, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF:
            return
        raw = buffer.raw[:size.value]
        header = _RAWINPUTHEADER.from_buffer_copy(raw[:header_size])
        if header.dwType != RIM_TYPEHID or len(raw) < header_size + 8:
            return
        report_size = ctypes.c_uint.from_buffer_copy(raw, header_size).value
        report_count = ctypes.c_uint.from_buffer_copy(raw, header_size + 4).value
        if report_size <= 0 or report_count <= 0:
            return
        device_key = self._raw_device_key(header.hDevice)
        data_offset = header_size + 8
        for index in range(report_count):
            start = data_offset + index * report_size
            end = start + report_size
            if end <= len(raw):
                self._process_raw_hid_report(device_key, raw[start:end])

    def _raw_device_key(self, device_handle) -> str:
        handle = int(device_handle or 0) & _POINTER_MASK
        if handle in self._raw_device_names:
            return self._raw_device_names[handle]
        device_key = f'DEV_{handle:x}'
        try:
            name_length = ctypes.c_uint(0)
            _USER32.GetRawInputDeviceInfoW(
                ctypes.c_void_p(handle), RIDI_DEVICENAME, None,
                ctypes.byref(name_length))
            if name_length.value:
                name_buffer = ctypes.create_unicode_buffer(name_length.value + 1)
                result = _USER32.GetRawInputDeviceInfoW(
                    ctypes.c_void_p(handle), RIDI_DEVICENAME, name_buffer,
                    ctypes.byref(name_length))
                if result != 0xFFFFFFFF:
                    match = re.search(
                        r'VID_([0-9A-F]{4}).*PID_([0-9A-F]{4})',
                        name_buffer.value.upper())
                    if match:
                        device_key = f'VID_{match.group(1)}&PID_{match.group(2)}'
        except Exception:
            # Device identity is optional; the stable handle key remains usable.
            pass
        self._raw_device_names[handle] = device_key
        return device_key

    @staticmethod
    def _raw_controller_token(device_key: str, byte_index: int, bit_index: int) -> str:
        return f'HID:{device_key}:B{byte_index}.{bit_index}'

    def _process_raw_hid_report(self, device_key: str, report: bytes):
        previous = self._raw_hid_reports.get(device_key)
        self._raw_hid_reports[device_key] = report
        if previous is None:
            return
        active = set(self._raw_controller_buttons_by_device.get(device_key, set()))
        changed = False
        for byte_index in range(max(len(previous), len(report))):
            old_value = previous[byte_index] if byte_index < len(previous) else 0
            new_value = report[byte_index] if byte_index < len(report) else 0
            if old_value == new_value:
                continue
            for bit_index in range(8):
                mask = 1 << bit_index
                token = self._raw_controller_token(device_key, byte_index, bit_index)
                was_down, is_down = bool(old_value & mask), bool(new_value & mask)
                if is_down and not was_down:
                    active.add(token)
                    changed = True
                elif was_down and not is_down and token in active:
                    active.remove(token)
                    changed = True
        if changed:
            self._raw_controller_buttons_by_device[device_key] = active
            self._raw_controller_buttons = set().union(
                *self._raw_controller_buttons_by_device.values())
            self._publish_controller_buttons()

    def _poll_controller_buttons(self):
        if self._xinput_get_state is None:
            return
        pressed = set()
        for user_index in range(4):
            state = _XINPUT_STATE()
            try:
                result = self._xinput_get_state(user_index, ctypes.byref(state))
            except Exception:
                # A controller can disconnect between polling slots; try the rest.
                continue
            if result != 0:
                continue
            buttons = int(state.Gamepad.wButtons)
            for name, flag in _XINPUT_BUTTON_FLAGS.items():
                if buttons & flag:
                    pressed.add(name)
            if state.Gamepad.bLeftTrigger >= _XINPUT_TRIGGER_THRESHOLD:
                pressed.add('LT')
            if state.Gamepad.bRightTrigger >= _XINPUT_TRIGGER_THRESHOLD:
                pressed.add('RT')

        self._xinput_controller_buttons = pressed
        self._publish_controller_buttons()

    def _publish_controller_buttons(self):
        pressed = set(self._xinput_controller_buttons) | set(self._raw_controller_buttons)
        if pressed != self._controller_buttons:
            self._controller_buttons = pressed
            self.controller_buttons_changed.emit(set(pressed))
        matching = {
            action for action, binding in self.controller_hotkeys.items()
            if binding and set(binding.split('+')).issubset(pressed)
        }
        if self._input_capture_depth:
            self._controller_active_actions = matching
            return
        for action in matching - self._controller_active_actions:
            self._emit_action(action)
        self._controller_active_actions = matching

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
            'start_recording':         self.start_recording_triggered,
            'stop_recording':          self.stop_recording_triggered,
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

    def _unregister_keyboard_hotkeys(self):
        """Remove only process-wide keyboard registrations, retaining sockets."""
        for key in self._registered_hotkeys:
            try:
                keyboard.remove_hotkey(key)
            except Exception:
                pass
        self._registered_hotkeys.clear()

    def unregister_all(self):
        """Unregister keyboard and controller hotkeys."""
        self._unregister_keyboard_hotkeys()
        self._stop_controller_polling()

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

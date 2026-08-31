from __future__ import annotations

import ctypes
import json

import pytest

from FTHR_UI.core import hotkey_manager as hotkeys


pytestmark = pytest.mark.skipif(
    hotkeys.sys.platform != 'win32', reason='Windows native hotkey contract')


class _NativeTarget:
    def winId(self):
        return 1234


class _User32:
    def __init__(self, *, registration_result=True):
        self.registration_result = registration_result
        self.registrations = []
        self.unregistrations = []
        self.raw_registrations = 0

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, virtual_key):
        self.registrations.append(
            (hwnd.value, hotkey_id, modifiers, virtual_key))
        return self.registration_result

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.unregistrations.append((hwnd.value, hotkey_id))
        return True

    def RegisterRawInputDevices(self, _devices, count, _size):
        self.raw_registrations += int(count)
        return True


def _manager(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    manager = hotkeys.HotkeyManager()
    monkeypatch.setattr(
        manager, '_ensure_windows_message_target', lambda: _NativeTarget())
    return manager


def test_windows_hotkey_parts_support_function_and_modifier_chords():
    modifiers, virtual_key = hotkeys.windows_hotkey_parts('F9')
    assert modifiers == hotkeys.MOD_NOREPEAT
    assert virtual_key == 0x78

    modifiers, virtual_key = hotkeys.windows_hotkey_parts('ctrl+shift+s')
    assert modifiers == (
        hotkeys.MOD_NOREPEAT | hotkeys.MOD_CONTROL | hotkeys.MOD_SHIFT)
    assert virtual_key == ord('S')


@pytest.mark.parametrize('binding', ['', 'Ctrl', 'F9+F10'])
def test_windows_hotkey_parts_rejects_incomplete_or_multi_key_chords(binding):
    with pytest.raises(ValueError):
        hotkeys.windows_hotkey_parts(binding)


def test_native_registration_tracks_action_and_unregisters_cleanly(
        qtbot, tmp_path, monkeypatch):
    user32 = _User32()
    monkeypatch.setattr(hotkeys, '_USER32', user32)
    manager = _manager(tmp_path, monkeypatch)

    assert manager._register_windows_hotkey('save_clip', 'F9')
    hotkey_id = hotkeys._WINDOWS_HOTKEY_IDS['save_clip']
    assert user32.registrations == [
        (1234, hotkey_id, hotkeys.MOD_NOREPEAT, 0x78)]
    assert manager._windows_hotkey_actions == {hotkey_id: 'save_clip'}
    assert manager._windows_hotkey_combos == {'save_clip': 'F9'}

    manager._raw_input_widget = _NativeTarget()
    manager._unregister_windows_hotkey('save_clip')

    assert user32.unregistrations == [(1234, hotkey_id)]
    assert manager._windows_hotkey_actions == {}
    assert manager._windows_hotkey_combos == {}


def test_wm_hotkey_dispatches_while_main_window_is_not_involved(
        qtbot, tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    hotkey_id = hotkeys._WINDOWS_HOTKEY_IDS['save_clip']
    manager._windows_hotkey_actions[hotkey_id] = 'save_clip'
    manager._windows_hotkey_combos['save_clip'] = 'F9'
    received = []
    manager.save_clip_triggered.connect(lambda: received.append('save_clip'))
    message = hotkeys._MSG()
    message.message = hotkeys.WM_HOTKEY
    message.wParam = hotkey_id

    handled, result = manager.nativeEventFilter(
        b'windows_dispatcher_MSG', ctypes.addressof(message))

    assert (handled, result) == (False, 0)
    assert received == ['save_clip']


@pytest.mark.parametrize(
    ('message_type', 'parameter', 'reason'),
    [
        (hotkeys.WM_POWERBROADCAST,
         hotkeys.PBT_APMRESUMEAUTOMATIC, 'system resume'),
        (hotkeys.WM_WTSSESSION_CHANGE,
         hotkeys.WTS_SESSION_UNLOCK, 'session unlock'),
    ],
)
def test_resume_and_unlock_schedule_native_registration_recovery(
        qtbot, tmp_path, monkeypatch, message_type, parameter, reason):
    manager = _manager(tmp_path, monkeypatch)
    refreshes = []
    monkeypatch.setattr(
        manager, '_schedule_windows_hotkey_refresh',
        lambda value: refreshes.append(value))
    message = hotkeys._MSG()
    message.message = message_type
    message.wParam = parameter

    manager.nativeEventFilter(
        b'windows_generic_MSG', ctypes.addressof(message))

    assert refreshes == [reason]


def test_f12_uses_raw_input_without_reviving_low_level_hook(
        qtbot, tmp_path, monkeypatch):
    user32 = _User32(registration_result=False)
    monkeypatch.setattr(hotkeys, '_USER32', user32)
    manager = _manager(tmp_path, monkeypatch)
    received = []
    manager.save_screenshot_triggered.connect(
        lambda: received.append('screenshot'))

    assert manager._register_windows_hotkey('save_screenshot', 'F12')
    assert user32.raw_registrations == 1
    assert manager._windows_raw_hotkey_actions == {
        'save_screenshot': (0, 0x7B)}

    key_down = hotkeys._RAWKEYBOARD()
    key_down.VKey = 0x7B
    manager._process_raw_keyboard_input(key_down)
    manager._process_raw_keyboard_input(key_down)  # auto-repeat is suppressed
    key_up = hotkeys._RAWKEYBOARD()
    key_up.VKey = 0x7B
    key_up.Flags = hotkeys.RI_KEY_BREAK
    manager._process_raw_keyboard_input(key_up)

    assert received == ['screenshot']


def test_registration_conflict_is_reported_instead_of_silently_ignored(
        qtbot, tmp_path, monkeypatch):
    user32 = _User32(registration_result=False)
    monkeypatch.setattr(hotkeys, '_USER32', user32)
    manager = _manager(tmp_path, monkeypatch)
    errors = []
    manager.error_occurred.connect(
        lambda title, detail, level: errors.append((title, detail, level)))

    assert not manager._register_windows_hotkey('save_clip', 'F9')

    assert errors
    assert errors[0][0] == 'HOTKEY REGISTRATION FAILED'
    assert 'another app may already own it' in errors[0][1]
    assert errors[0][2] == 'warning'
    assert manager._windows_hotkey_actions == {}


def test_conflicting_binding_is_not_saved_and_old_binding_is_restored(
        qtbot, tmp_path, monkeypatch):
    user32 = _User32()
    results = iter((False, True))

    def _register(hwnd, hotkey_id, modifiers, virtual_key):
        user32.registrations.append(
            (hwnd.value, hotkey_id, modifiers, virtual_key))
        return next(results)

    user32.RegisterHotKey = _register
    monkeypatch.setattr(hotkeys, '_USER32', user32)
    manager = _manager(tmp_path, monkeypatch)
    manager._save_hotkeys()

    assert not manager.set_hotkey('save_clip', 'F6')

    assert manager.hotkeys['save_clip'] == 'F9'
    saved = json.loads(manager.config_file.read_text(encoding='utf-8'))
    assert saved['save_clip'] == 'F9'
    assert [registration[3] for registration in user32.registrations] == [
        0x75, 0x78]

"""Stable Windows monitor identities for the capture-source UI (AUDIT-048)."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Iterable


_DISPLAY_DEVICE_ACTIVE = 0x00000001
_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
_DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004
_EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001


@dataclass(frozen=True)
class DisplayDeviceRecord:
    gdi_name: str
    friendly_name: str
    monitor_device_path: str
    active: bool
    primary: bool = False


@dataclass(frozen=True)
class MonitorChoice:
    gdi_name: str
    friendly_name: str
    device_path: str
    primary: bool


def normalize_monitor_device_path(device_path: str) -> str:
    """Match the engine's case/slash-insensitive persistent identity."""
    return device_path.strip().replace('/', '\\').lower()


def build_monitor_choices(records: Iterable[DisplayDeviceRecord]) -> list[MonitorChoice]:
    """Return active monitors once each, keyed by stable device interface path."""
    choices: list[MonitorChoice] = []
    seen: set[str] = set()
    for record in records:
        device_path = normalize_monitor_device_path(record.monitor_device_path)
        if not record.active or not device_path or device_path in seen:
            continue
        seen.add(device_path)
        choices.append(MonitorChoice(
            gdi_name=record.gdi_name,
            friendly_name=record.friendly_name or record.gdi_name,
            device_path=device_path,
            primary=record.primary,
        ))
    return choices


class _DisplayDeviceW(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('DeviceName', wintypes.WCHAR * 32),
        ('DeviceString', wintypes.WCHAR * 128),
        ('StateFlags', wintypes.DWORD),
        ('DeviceID', wintypes.WCHAR * 128),
        ('DeviceKey', wintypes.WCHAR * 128),
    ]


def enumerate_windows_monitor_records() -> list[DisplayDeviceRecord]:
    """Enumerate active display targets and their stable interface paths."""
    if sys.platform != 'win32':
        return []

    enum_display_devices = ctypes.WinDLL('user32', use_last_error=True).EnumDisplayDevicesW
    enum_display_devices.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(_DisplayDeviceW),
        wintypes.DWORD,
    ]
    enum_display_devices.restype = wintypes.BOOL

    records: list[DisplayDeviceRecord] = []
    adapter_index = 0
    while True:
        adapter = _DisplayDeviceW()
        adapter.cb = ctypes.sizeof(adapter)
        if not enum_display_devices(None, adapter_index, ctypes.byref(adapter), 0):
            break
        adapter_index += 1
        attached = bool(adapter.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)
        active_adapter = bool(adapter.StateFlags & _DISPLAY_DEVICE_ACTIVE)
        if not attached or not active_adapter:
            continue

        monitor_index = 0
        while True:
            monitor = _DisplayDeviceW()
            monitor.cb = ctypes.sizeof(monitor)
            if not enum_display_devices(
                adapter.DeviceName,
                monitor_index,
                ctypes.byref(monitor),
                _EDD_GET_DEVICE_INTERFACE_NAME,
            ):
                break
            monitor_index += 1
            records.append(DisplayDeviceRecord(
                gdi_name=adapter.DeviceName,
                friendly_name=monitor.DeviceString,
                monitor_device_path=monitor.DeviceID,
                active=bool(monitor.StateFlags & _DISPLAY_DEVICE_ACTIVE),
                primary=bool(adapter.StateFlags & _DISPLAY_DEVICE_PRIMARY_DEVICE),
            ))
    return records


def enumerate_windows_monitors() -> list[MonitorChoice]:
    return build_monitor_choices(enumerate_windows_monitor_records())

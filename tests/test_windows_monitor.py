from core.windows_monitor import (
    DisplayDeviceRecord,
    build_monitor_choices,
    default_windows_monitor_path,
    normalize_monitor_device_path,
)


def test_monitor_choices_use_normalized_stable_device_paths():
    records = [
        DisplayDeviceRecord(
            gdi_name=r"\\.\DISPLAY2",
            friendly_name="Secondary Panel",
            monitor_device_path=r"  \\?\DISPLAY#ABC/DEF  ",
            active=True,
            x=-2560,
            width=2560,
            height=1080,
        )
    ]

    choices = build_monitor_choices(records)

    assert choices[0].device_path == r"\\?\display#abc\def"
    assert choices[0].gdi_name == r"\\.\DISPLAY2"
    assert (choices[0].x, choices[0].y,
            choices[0].width, choices[0].height) == (-2560, 0, 2560, 1080)


def test_monitor_choices_exclude_inactive_and_deduplicate_paths():
    records = [
        DisplayDeviceRecord("DISPLAY1", "One", "PATH-A", True),
        DisplayDeviceRecord("DISPLAY1", "Duplicate", "path-a", True),
        DisplayDeviceRecord("DISPLAY2", "Inactive", "PATH-B", False),
    ]

    choices = build_monitor_choices(records)

    assert [(choice.gdi_name, choice.device_path) for choice in choices] == [
        ("DISPLAY1", "path-a")
    ]


def test_normalize_monitor_device_path_rejects_blank_values():
    assert normalize_monitor_device_path("  ") == ""


def test_default_monitor_prefers_primary_then_first_active():
    choices = build_monitor_choices([
        DisplayDeviceRecord(
            'DISPLAY2', 'Secondary', r'\\?\DISPLAY#SECOND', True,
            primary=False),
        DisplayDeviceRecord(
            'DISPLAY1', 'Primary', r'\\?\DISPLAY#PRIMARY', True,
            primary=True),
    ])

    assert default_windows_monitor_path(choices) == r'\\?\display#primary'
    assert default_windows_monitor_path(choices[:1]) == r'\\?\display#second'
    assert default_windows_monitor_path([]) == ''

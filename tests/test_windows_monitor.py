from core.windows_monitor import (
    DisplayDeviceRecord,
    build_monitor_choices,
    normalize_monitor_device_path,
)


def test_monitor_choices_use_normalized_stable_device_paths():
    records = [
        DisplayDeviceRecord(
            gdi_name=r"\\.\DISPLAY2",
            friendly_name="Secondary Panel",
            monitor_device_path=r"  \\?\DISPLAY#ABC/DEF  ",
            active=True,
        )
    ]

    choices = build_monitor_choices(records)

    assert choices[0].device_path == r"\\?\display#abc\def"
    assert choices[0].gdi_name == r"\\.\DISPLAY2"


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

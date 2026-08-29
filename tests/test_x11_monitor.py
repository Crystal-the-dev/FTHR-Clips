from __future__ import annotations

import subprocess

import pytest

from core.x11_monitor import (
    X11MonitorError,
    choose_x11_output,
    is_native_x11_session,
    make_x11_capture_target,
    parse_xrandr_query,
    query_x11_outputs,
)


XRANDR_TWO_OUTPUTS = """\
Screen 0: minimum 8 x 8, current 4480 x 1440, maximum 32767 x 32767
DP-1 connected 1920x1080+0+180 (normal left inverted right x axis y axis)
   1920x1080     60.00*+
HDMI-A-1 connected primary 2560x1440+1920+0 right (normal left inverted right x axis y axis)
   2560x1440     59.95*+
DP-2 disconnected (normal left inverted right x axis y axis)
"""


def test_native_x11_detection_rejects_xwayland():
    assert is_native_x11_session({'DISPLAY': ':0', 'XDG_SESSION_TYPE': 'x11'})
    assert not is_native_x11_session({
        'DISPLAY': ':0',
        'WAYLAND_DISPLAY': 'wayland-0',
        'XDG_SESSION_TYPE': 'wayland',
    })


def test_xrandr_parser_keeps_identity_geometry_rotation_and_primary():
    outputs = parse_xrandr_query(XRANDR_TWO_OUTPUTS)

    assert [(item.name, item.x, item.y, item.width, item.height)
            for item in outputs] == [
        ('DP-1', 0, 180, 1920, 1080),
        ('HDMI-A-1', 1920, 0, 2560, 1440),
    ]
    assert outputs[0].rotation == 'normal'
    assert outputs[1].rotation == 'right'
    assert outputs[1].primary


def test_default_is_primary_not_output_zero():
    outputs = parse_xrandr_query(XRANDR_TWO_OUTPUTS)

    assert choose_x11_output(outputs, '').name == 'HDMI-A-1'


def test_selected_output_missing_fails_instead_of_using_primary():
    outputs = parse_xrandr_query(XRANDR_TWO_OUTPUTS)

    with pytest.raises(X11MonitorError, match='not active'):
        choose_x11_output(outputs, 'DP-9')


def test_negative_layout_is_rebased_to_root_coordinates():
    outputs = parse_xrandr_query("""\
DP-LEFT connected 1920x1080-1920+0 (normal left inverted right x axis y axis)
DP-UP connected 2560x1440+0-1440 (normal left inverted right x axis y axis)
DP-PRIMARY connected primary 2560x1440+0+0 (normal left inverted right x axis y axis)
""")

    left = make_x11_capture_target(outputs, 'DP-LEFT')
    above = make_x11_capture_target(outputs, 'DP-UP')
    primary = make_x11_capture_target(outputs, '')

    assert (left.root_x, left.root_y) == (0, 1440)
    assert (above.root_x, above.root_y) == (1920, 0)
    assert (primary.root_x, primary.root_y) == (1920, 1440)
    assert left.engine_argument == '@x11:0,1440,1920,1080'


def test_connected_output_without_active_mode_is_ignored():
    assert parse_xrandr_query(
        'DP-1 connected (normal left inverted right x axis y axis)\n'
    ) == []


def test_xrandr_query_is_bounded_and_uses_explicit_arguments(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, XRANDR_TWO_OUTPUTS, '')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    outputs = query_x11_outputs('/usr/bin/xrandr')

    assert outputs[0].name == 'DP-1'
    assert calls[0][0] == ['/usr/bin/xrandr', '--current', '--query']
    assert calls[0][1]['timeout'] == 2.0
    assert calls[0][1]['check'] is False

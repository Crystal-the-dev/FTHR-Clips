import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
from unittest.mock import patch, MagicMock
from core.focus_monitor import FocusMonitor, _get_active_window_title


def test_get_active_window_hyprland_success():
    mock_result = MagicMock()
    mock_result.stdout = b'{"title": "Minecraft", "class": "java"}'
    mock_result.returncode = 0
    with patch('core.focus_monitor.subprocess.run', return_value=mock_result), \
         patch('core.focus_monitor.detect_compositor', return_value='hyprland'):
        title = _get_active_window_title()
    assert title == 'Minecraft'


def test_get_active_window_hyprland_failure_returns_none():
    with patch('core.focus_monitor.subprocess.run', side_effect=Exception('not found')), \
         patch('core.focus_monitor.detect_compositor', return_value='hyprland'):
        title = _get_active_window_title()
    assert title is None


def test_get_active_window_xdotool_success():
    def fake_run(cmd, **kw):
        m = MagicMock()
        if cmd == ['xdotool', 'getactivewindow']:
            m.stdout = b'12345678\n'; m.returncode = 0
        elif 'getwindowname' in cmd:
            m.stdout = b'Counter-Strike 2\n'; m.returncode = 0
        else:
            m.stdout = b''; m.returncode = 1
        return m
    with patch('core.focus_monitor.subprocess.run', side_effect=fake_run), \
         patch('core.focus_monitor.detect_compositor', return_value='kwin'), \
         patch('core.focus_monitor.shutil.which', return_value='/usr/bin/xdotool'):
        title = _get_active_window_title()
    assert title == 'Counter-Strike 2'


def test_get_active_window_no_xtools_returns_none():
    with patch('core.focus_monitor.detect_compositor', return_value='kwin'), \
         patch('core.focus_monitor.shutil.which', return_value=None):
        title = _get_active_window_title()
    assert title is None


def test_focus_monitor_emits_focus_lost():
    mon = FocusMonitor('Minecraft')
    lost = []
    mon.focus_lost.connect(lambda: lost.append(True))
    with patch('core.focus_monitor._get_active_window_title', return_value='Discord'):
        mon._poll()
    assert lost == [True]


def test_focus_monitor_emits_focus_regained():
    mon = FocusMonitor('Minecraft')
    mon._focused = False
    regained = []
    mon.focus_regained.connect(lambda: regained.append(True))
    with patch('core.focus_monitor._get_active_window_title', return_value='Minecraft'):
        mon._poll()
    assert regained == [True]


def test_focus_monitor_none_title_stays_quiet():
    mon = FocusMonitor('Minecraft')
    signals = []
    mon.focus_lost.connect(lambda: signals.append('lost'))
    mon.focus_regained.connect(lambda: signals.append('regained'))
    with patch('core.focus_monitor._get_active_window_title', return_value=None):
        mon._poll()
    assert signals == []

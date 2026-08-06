import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
from unittest.mock import patch, MagicMock
from core.game_detector import _enumerate_linux_windows


def test_hyprland_returns_windows():
    mock = MagicMock()
    mock.stdout = b'''[
        {"title": "Counter-Strike 2", "class": "cs2", "address": "0x1A2B",
         "fullscreen": true, "fullscreenMode": 1},
        {"title": "Discord", "class": "discord", "address": "0x3C4D",
         "fullscreen": false, "fullscreenMode": 0}
    ]'''
    mock.returncode = 0
    with patch('core.game_detector.subprocess.run', return_value=mock), \
         patch('core.game_detector.detect_compositor', return_value='hyprland'):
        windows = _enumerate_linux_windows()
    assert len(windows) == 2
    game = next(w for w in windows if w['title'] == 'Counter-Strike 2')
    assert game['is_game'] is True
    discord = next(w for w in windows if w['title'] == 'Discord')
    assert discord['is_game'] is False


def test_hyprland_failure_returns_empty():
    with patch('core.game_detector.subprocess.run', side_effect=Exception), \
         patch('core.game_detector.detect_compositor', return_value='hyprland'):
        assert _enumerate_linux_windows() == []


def test_xdotool_path_kde():
    def fake_run(cmd, **kw):
        m = MagicMock(); m.returncode = 0
        if cmd[:2] == ['xdotool', 'search']:
            m.stdout = b'111\n222\n'
        elif cmd == ['xdotool', 'getwindowname', '111']:
            m.stdout = b'Counter-Strike 2\n'
        elif cmd == ['xdotool', 'getwindowname', '222']:
            m.stdout = b'Firefox\n'
        elif '111' in cmd and 'xprop' in cmd[0]:
            m.stdout = b'_NET_WM_STATE(ATOM) = _NET_WM_STATE_FULLSCREEN\n'
        elif '222' in cmd and 'xprop' in cmd[0]:
            m.stdout = b'_NET_WM_STATE(ATOM) = \n'
        else:
            m.stdout = b''
        return m
    with patch('core.game_detector.subprocess.run', side_effect=fake_run), \
         patch('core.game_detector.detect_compositor', return_value='kwin'), \
         patch('core.game_detector.shutil.which', return_value='/usr/bin/xdotool'):
        windows = _enumerate_linux_windows()
    assert len(windows) == 2
    cs2 = next(w for w in windows if 'Counter' in w['title'])
    assert cs2['is_game'] is True
    ff = next(w for w in windows if 'Firefox' in w['title'])
    assert ff['is_game'] is False


def test_no_xdotool_returns_empty():
    with patch('core.game_detector.detect_compositor', return_value='kwin'), \
         patch('core.game_detector.shutil.which', return_value=None):
        assert _enumerate_linux_windows() == []

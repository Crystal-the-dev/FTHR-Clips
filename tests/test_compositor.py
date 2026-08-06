import sys, os, importlib
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))
from unittest.mock import patch


def _detect(env: dict, which_map: dict = None) -> str:
    import core.compositor as c
    importlib.reload(c)  # reset module-level cache
    which_map = which_map or {}
    with patch.dict(os.environ, env, clear=True), \
         patch('shutil.which', side_effect=lambda x: which_map.get(x)):
        return c.detect_compositor()


def test_hyprland_via_env():
    result = _detect({'HYPRLAND_INSTANCE_SIGNATURE': 'abc123',
                      'WAYLAND_DISPLAY': 'wayland-1'})
    assert result == 'hyprland'


def test_kde_via_desktop_env():
    result = _detect({'XDG_CURRENT_DESKTOP': 'KDE',
                      'WAYLAND_DISPLAY': 'wayland-1'},
                     which_map={'kwin_wayland': '/usr/bin/kwin_wayland'})
    assert result == 'kwin'


def test_kde_via_session_desktop():
    result = _detect({'XDG_SESSION_DESKTOP': 'plasma',
                      'WAYLAND_DISPLAY': 'wayland-1'})
    assert result == 'kwin'


def test_gnome():
    result = _detect({'XDG_CURRENT_DESKTOP': 'GNOME',
                      'WAYLAND_DISPLAY': 'wayland-1'})
    assert result == 'gnome'


def test_x11_pure():
    result = _detect({'DISPLAY': ':0'})
    assert result == 'x11'


def test_unknown_wayland():
    result = _detect({'WAYLAND_DISPLAY': 'wayland-1',
                      'XDG_CURRENT_DESKTOP': 'Weston'})
    assert result == 'wayland-unknown'


def test_no_display():
    result = _detect({})
    assert result == 'none'

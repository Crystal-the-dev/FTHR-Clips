from __future__ import annotations

from core.windows_autostart import (
    BACKGROUND_ARGUMENT,
    VALUE_NAME,
    build_background_command,
    read_enabled,
    set_enabled,
)


class _Registry:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values: dict[str, str] = {}
        self.write_calls = 0

    def OpenKey(self, _root, _path, *_args):
        return object()

    def CloseKey(self, _key):
        return None

    def QueryValueEx(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, _key, name, _reserved, _kind, value):
        self.write_calls += 1
        self.values[name] = value

    def DeleteValue(self, _key, name):
        self.write_calls += 1
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_background_command_quotes_an_installed_path_with_spaces():
    command = build_background_command(r'C:\Program Files\FTHR Clips\FTHRClips.exe')
    assert command == (
        '"C:\\Program Files\\FTHR Clips\\FTHRClips.exe" '
        f'{BACKGROUND_ARGUMENT}')


def test_enable_disable_and_readback_use_the_exact_os_command():
    registry = _Registry()
    executable = r'C:\Program Files\FTHR Clips\FTHRClips.exe'

    assert set_enabled(True, registry=registry, executable=executable, frozen=True)
    assert registry.values[VALUE_NAME] == build_background_command(executable)
    assert read_enabled(registry=registry, executable=executable, frozen=True)

    assert set_enabled(False, registry=registry, executable=executable, frozen=True)
    assert not read_enabled(registry=registry, executable=executable, frozen=True)


def test_stale_or_wrong_registry_value_is_not_reported_as_enabled():
    registry = _Registry()
    registry.values[VALUE_NAME] = '"C:\\Old Install\\FTHRClips.exe" --background'

    assert not read_enabled(
        registry=registry,
        executable=r'C:\Program Files\FTHR Clips\FTHRClips.exe',
        frozen=True,
    )


def test_development_launch_never_changes_windows_startup():
    registry = _Registry()

    assert not set_enabled(
        True,
        registry=registry,
        executable=r'C:\dev\FTHR\python.exe',
        frozen=False,
    )
    assert registry.values == {}
    assert registry.write_calls == 0

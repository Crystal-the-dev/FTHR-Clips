from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'FTHR_UI'))

import main as app_main  # noqa: E402


class _Settings:
    def __init__(self):
        self.values = {
            'game_detection_mode': 'auto',
            'game_detection_enabled': True,
            'game_detection_custom_games': [],
            'game_detection_fallback_desktop': False,
        }
        self.saved = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save_settings(self):
        self.saved += 1


class _Hotkeys:
    hotkeys = {
        'confirm_game_detection': 'F8',
        'dismiss_game_detection': 'F7',
    }


def test_browse_executable_adds_and_persists_game(
    qapp, tmp_path: Path, monkeypatch,
) -> None:
    executable = tmp_path / ('game.exe' if sys.platform == 'win32' else 'game')
    executable.write_bytes(b'MZ')
    if sys.platform != 'win32':
        executable.chmod(0o755)
    settings = _Settings()
    popup = app_main.GameDetectionPopup(settings, _Hotkeys())
    monkeypatch.setattr(
        app_main.QFileDialog, 'getOpenFileName',
        lambda *_args, **_kwargs: (str(executable), ''),
    )

    popup._browse_executable()
    qapp.processEvents()

    rules = settings.values['game_detection_custom_games']
    assert len(rules) == 1
    assert rules[0]['exe_path'] == str(executable)
    assert settings.saved == 1
    assert popup.browser_status.text().startswith('Added ')


def test_cancelled_game_picker_keeps_form_usable(qapp, monkeypatch) -> None:
    settings = _Settings()
    popup = app_main.GameDetectionPopup(settings, _Hotkeys())
    monkeypatch.setattr(
        app_main.QFileDialog, 'getOpenFileName',
        lambda *_args, **_kwargs: ('', ''),
    )

    popup._browse_executable()
    qapp.processEvents()

    assert settings.values['game_detection_custom_games'] == []
    assert popup.browser_status.text() == 'Selection cancelled.'
    assert popup.isEnabled()


def test_invalid_game_selection_shows_useful_error(qapp, tmp_path: Path) -> None:
    selected = tmp_path / 'missing.exe'
    settings = _Settings()
    popup = app_main.GameDetectionPopup(settings, _Hotkeys())

    message = popup._validate_game_selection(str(selected), folder=False)

    assert 'no longer exists' in message
    assert settings.values['game_detection_custom_games'] == []

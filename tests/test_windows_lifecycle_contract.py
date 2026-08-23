from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (ROOT / 'FTHR_UI' / 'main.py').read_text(encoding='utf-8')
BRIDGE_SOURCE = (ROOT / 'FTHR_UI' / 'core' / 'capture_bridge.py').read_text(
    encoding='utf-8')
ENGINE_SOURCE = (ROOT / 'FTHRcapture' / 'FTHRclips' / 'src' / 'main.cpp').read_text(
    encoding='utf-8')
ENGINE_HEADER = (ROOT / 'FTHRcapture' / 'FTHRclips' / 'include' / 'shared_memory.h').read_text(
    encoding='utf-8')


def test_background_mode_defers_heavy_main_ui_but_starts_core_services():
    assert 'def __init__(self, *, background_start: bool = False)' in MAIN_SOURCE
    assert 'if not self._background_start:\n            self.ensure_main_ui()' in MAIN_SOURCE
    assert 'self._setup_hotkeys()\n        self._start_background_services()' in MAIN_SOURCE
    assert "background_start = '--background' in sys.argv" in MAIN_SOURCE


def test_close_to_tray_and_explicit_exit_have_separate_paths():
    assert 'WindowHiddenToTray' in MAIN_SOURCE
    assert 'def request_full_exit(self)' in MAIN_SOURCE
    assert 'self._tray_icon.hide()' in MAIN_SOURCE
    assert 'def _perform_full_shutdown(self)' in MAIN_SOURCE
    assert '_FINALIZATION_GRACE_SECONDS = 1.5' in MAIN_SOURCE


def test_native_engine_has_a_graceful_shutdown_command_without_layout_change():
    assert 'SHUTDOWN = 11' in ENGINE_HEADER
    assert 'SHUTDOWN = 11' in BRIDGE_SOURCE
    assert 'def request_engine_shutdown(self)' in BRIDGE_SOURCE
    assert 'case fthr::CommandType::SHUTDOWN:' in ENGINE_SOURCE
    assert 'while (!shutdown_requested)' in ENGINE_SOURCE
    assert 'engine.Shutdown();' in ENGINE_SOURCE

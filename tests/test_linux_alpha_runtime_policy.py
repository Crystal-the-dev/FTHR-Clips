from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AUDIO_CPP = (ROOT / 'FTHRcapture_linux/src/audio_capture.cpp').read_text()
AUDIO_H = (ROOT / 'FTHRcapture_linux/src/audio_capture.h').read_text()
BACKEND_CPP = (ROOT / 'FTHRcapture_linux/src/capture_backend.cpp').read_text()
CMAKE = (ROOT / 'FTHRcapture_linux/CMakeLists.txt').read_text()
MAIN_CPP = (ROOT / 'FTHRcapture_linux/src/main.cpp').read_text()


def test_auto_desktop_audio_resolves_default_sink_monitor():
    assert 'pa_context_get_server_info' in AUDIO_CPP
    assert 'pa_context_get_sink_info_by_name' in AUDIO_CPP
    assert 'monitor_source_name' in AUDIO_CPP
    assert 'source device (nullptr = default)' not in AUDIO_CPP
    assert 'source = nullptr' not in AUDIO_CPP


def test_audio_open_failure_is_synchronous_and_video_only_diagnostic_exists():
    start = AUDIO_CPP[AUDIO_CPP.index('bool AudioCapture::Start'):]
    assert start.index('pa_simple_new') < start.index('std::thread')
    engine = (ROOT / 'FTHRcapture_linux/src/capture_engine.cpp').read_text()
    assert 'FTHR_STARTUP_WARNING: DESKTOP_AUDIO_UNAVAILABLE' in engine


def test_audio_history_matches_maximum_replay_duration():
    assert 'kMaxSeconds  = 300' in AUDIO_H


def test_multiband_request_is_forced_off_at_native_boundary():
    assert 'cfg.multiband_enabled = false;' in MAIN_CPP


def test_x11grab_is_default_off_and_compilation_is_conditional():
    assert 'FTHR_EXPERIMENTAL_X11GRAB' in CMAKE
    option = CMAKE[CMAKE.index('option(FTHR_EXPERIMENTAL_X11GRAB'):]
    assert ' OFF)' in option[:160]
    assert '#if FTHR_EXPERIMENTAL_X11GRAB' in BACKEND_CPP
    assert 'x11grab disabled for alpha' in BACKEND_CPP

import ctypes
import sys

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'FTHR_UI'))

from core.capture_bridge import CaptureBridge, SharedMemoryLayout, CommandType, ResponseType


def _make_fake_layout():
    buf = (ctypes.c_byte * ctypes.sizeof(SharedMemoryLayout))()
    layout = SharedMemoryLayout.from_buffer(buf)
    layout.is_initialized = True
    return layout, buf


class _FakeBridge(CaptureBridge):
    """CaptureBridge subclass backed by in-process fake layout."""
    def __init__(self, layout):
        self._layout = layout
        self._initialized = True


def test_set_encoder_config_writes_fields():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    result = bridge.set_encoder_config('hevc', 5)
    assert result is True
    assert layout.cfg_codec_pref == 2           # hevc = 2
    assert layout.cfg_preset     == 5
    assert layout.ui_command     == CommandType.RECONFIGURE_ENCODER


def test_set_encoder_config_auto_maps_to_zero():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    bridge.set_encoder_config('auto', 4)
    assert layout.cfg_codec_pref == 0


def test_get_active_codec_reads_string():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    layout.active_codec = b'hevc_nvenc'
    assert bridge.get_active_codec() == 'hevc_nvenc'


def test_get_active_codec_empty_when_not_set():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    assert bridge.get_active_codec() == ''


def test_set_encoder_config_clamps_preset():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    bridge.set_encoder_config('h264', 0)    # below min
    assert layout.cfg_preset == 1
    bridge.set_encoder_config('h264', 99)   # above max
    assert layout.cfg_preset == 7


def test_save_clip_timeout_survives_backward_clock_jump(monkeypatch):
    """H-01: save_clip() must not hang when system clock jumps backward (NTP).
    Uses a watchdog thread to detect an infinite loop within 5 seconds."""
    import time as _time
    import threading

    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    # engine_response stays NONE — engine never acks, so timeout must fire

    original_time = _time.time
    call_count = [0]

    def patched_time():
        call_count[0] += 1
        # After a few calls simulate NTP adjusting clock backward by 10 seconds
        if call_count[0] > 5:
            return original_time() - 10.0
        return original_time()

    monkeypatch.setattr(_time, 'time', patched_time)

    result = [None]
    def run():
        result[0] = bridge.save_clip('/tmp/test.mp4', 30)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive(), (
        "save_clip() hung — time.time() clock-jump caused infinite loop; "
        "use time.monotonic() instead"
    )
    assert result[0] is False


def test_wait_for_clip_timeout_survives_backward_clock_jump(monkeypatch):
    """H-01: wait_for_clip_completion() must not hang when clock jumps backward."""
    import time as _time
    import threading

    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)

    original_time = _time.time
    call_count = [0]

    def patched_time():
        call_count[0] += 1
        if call_count[0] > 5:
            return original_time() - 10.0
        return original_time()

    monkeypatch.setattr(_time, 'time', patched_time)

    result = [None]
    def run():
        result[0] = bridge.wait_for_clip_completion(timeout_ms=500)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive(), (
        "wait_for_clip_completion() hung — clock-jump with time.time(); "
        "use time.monotonic() instead"
    )
    assert result[0] is False


def test_wait_for_clip_completion_returns_false_on_error():
    """wait_for_clip_completion() must return False immediately on ERROR_OCCURRED,
    not spin until the timeout fires."""
    import threading

    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    layout.engine_response = ResponseType.ERROR_OCCURRED

    result = [None]
    def run():
        result[0] = bridge.wait_for_clip_completion(timeout_ms=30000)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=2.0)

    assert not t.is_alive(), (
        "wait_for_clip_completion() hung on ERROR_OCCURRED instead of returning immediately"
    )
    assert result[0] is False


def test_wait_for_clip_completion_returns_true_on_saved():
    """wait_for_clip_completion() returns True when CLIP_SAVED arrives."""
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    layout.engine_response = ResponseType.CLIP_SAVED

    assert bridge.wait_for_clip_completion(timeout_ms=500) is True
    assert layout.engine_response == ResponseType.NONE


# ── AUDIT-002: late async save failures must not vanish ────────────────────
#
# save_clip() only waits for SAVE_STARTED. A failure during the background
# write set ERROR_OCCURRED with nobody reading it, so the UI kept its "SAVED"
# banner and the clip silently never existed.

def test_poll_async_result_none_when_idle():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    assert bridge.poll_async_result() is None


def test_poll_async_result_reports_late_error():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    layout.engine_response = ResponseType.ERROR_OCCURRED
    layout.engine_string = _encode_engine_string('disk full while writing clip')

    verdict = bridge.poll_async_result()
    assert verdict is not None, 'a late ERROR_OCCURRED was swallowed'
    kind, detail = verdict
    assert kind == 'error'
    assert 'disk full' in detail


def test_poll_async_result_reports_save_completion():
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    layout.engine_response = ResponseType.CLIP_SAVED

    kind, _ = bridge.poll_async_result()
    assert kind == 'saved'


def test_poll_async_result_consumes_the_event_once():
    """A single engine verdict must produce exactly one UI notification —
    the status poll runs twice a second and would otherwise spam the error bar."""
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    layout.engine_response = ResponseType.ERROR_OCCURRED

    assert bridge.poll_async_result()[0] == 'error'
    assert bridge.poll_async_result() is None
    assert layout.engine_response == ResponseType.NONE


def test_poll_async_result_ignores_unrelated_responses():
    """STATUS_UPDATE / RECORDING_STARTED must not be mistaken for a save verdict."""
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    for resp in (ResponseType.STATUS_UPDATE,
                 ResponseType.RECORDING_STARTED,
                 ResponseType.SAVE_STARTED):
        layout.engine_response = resp
        assert bridge.poll_async_result() is None, f'{resp!r} misread as a save verdict'
        # and it must be left alone for whoever actually owns it
        assert layout.engine_response == resp


def test_poll_async_result_survives_garbled_engine_string():
    """A corrupt/garbled detail string must still yield a usable verdict rather
    than raising into the status timer and killing the poll loop."""
    layout, buf = _make_fake_layout()
    bridge = _FakeBridge(layout)
    layout.engine_response = ResponseType.ERROR_OCCURRED
    if sys.platform != 'win32':
        layout.engine_string = b'\xff\xfe invalid utf8 \xc3'

    verdict = bridge.poll_async_result()
    assert verdict is not None
    assert verdict[0] == 'error'


def _encode_engine_string(text: str):
    """engine_string is wchar on Windows and raw bytes on Linux."""
    return text if sys.platform == 'win32' else text.encode('utf-8')

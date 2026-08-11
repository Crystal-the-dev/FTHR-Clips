"""One CLIP_SAVED starts exactly one post-processing route (AUDIT-011).

The three routes — mic mux, multiband mux, finalize (watermark/crop/camera) —
each end by setting the `clip_ready` event, and `clip_ready` is the single gate
the upload manager waits on. If two routes fire for one clip the event is set
twice and the upload starts against a half-written file.

`select_post_route` is imported from main.py directly; importing main.py builds
no window, so this stays cheap.
"""

import itertools
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'FTHR_UI'))

pytest.importorskip('PyQt6.QtCore')
from main import MainWindow, select_post_route

ROUTES = {'mic', 'multiband', 'finalize'}
FLAGS = ('audio_on', 'multiband_enabled', 'mic_running',
         'watermark', 'auto_crop', 'camera')


@pytest.mark.parametrize('combo', list(itertools.product([False, True], repeat=6)))
def test_exactly_one_route_for_every_settings_combination(combo):
    """All 64 settings combinations. Never zero routes, never two."""
    # strict=True: if FLAGS and the parametrised tuple width ever drift apart,
    # zip() would silently drop a flag and the combination would not be tested.
    kwargs = dict(zip(FLAGS, combo, strict=True))
    route, has_async = select_post_route(**kwargs)

    assert route in ROUTES, f'unknown route {route!r} for {kwargs}'
    assert isinstance(has_async, bool)


def test_mic_and_multiband_are_mutually_exclusive():
    """Both enabled: multiband wins, the mic mux must not also run. Running
    both would have two ffmpeg workers rewriting the same file."""
    route, _ = select_post_route(
        audio_on=True, multiband_enabled=True, mic_running=True,
        watermark=False, auto_crop=False, camera=False)
    assert route == 'multiband'


def test_mic_route_only_when_mic_is_actually_running():
    route, _ = select_post_route(
        audio_on=True, multiband_enabled=False, mic_running=False,
        watermark=False, auto_crop=False, camera=False)
    assert route == 'finalize'

    route, _ = select_post_route(
        audio_on=True, multiband_enabled=False, mic_running=True,
        watermark=False, auto_crop=False, camera=False)
    assert route == 'mic'


def test_audio_disabled_disables_both_mux_routes():
    for multiband, mic in itertools.product([False, True], repeat=2):
        route, _ = select_post_route(
            audio_on=False, multiband_enabled=multiband, mic_running=mic,
            watermark=False, auto_crop=False, camera=False)
        assert route == 'finalize', (
            'audio off must not select an audio mux route')


def test_plain_clip_is_ready_immediately():
    """No mux, nothing to apply — the upload must not be made to wait on a
    worker that will not rewrite anything."""
    route, has_async = select_post_route(
        audio_on=False, multiband_enabled=False, mic_running=False,
        watermark=False, auto_crop=False, camera=False)
    assert route == 'finalize'
    assert has_async is False


@pytest.mark.parametrize('flag', ['watermark', 'auto_crop', 'camera'])
def test_finalize_defers_upload_when_it_will_rewrite_the_file(flag):
    kwargs = dict(audio_on=False, multiband_enabled=False, mic_running=False,
                  watermark=False, auto_crop=False, camera=False)
    kwargs[flag] = True
    route, has_async = select_post_route(**kwargs)
    assert route == 'finalize'
    assert has_async is True, (
        f'{flag} rewrites the clip — the upload must wait for clip_ready')


def test_mux_routes_always_defer_the_upload():
    for kwargs, expected in (
        (dict(audio_on=True, multiband_enabled=False, mic_running=True,
              watermark=False, auto_crop=False, camera=False), 'mic'),
        (dict(audio_on=True, multiband_enabled=True, mic_running=False,
              watermark=False, auto_crop=False, camera=False), 'multiband'),
    ):
        route, has_async = select_post_route(**kwargs)
        assert route == expected
        assert has_async is True


def test_completion_handler_dispatches_one_route_only():
    """Source-level guard on _on_clip_written: the three route calls must sit
    in one if/elif/else chain. Three independent `if` blocks is exactly the
    bug this replaced, and it type-checks fine."""
    src = (Path(__file__).resolve().parent.parent / 'FTHR_UI' / 'main.py'
           ).read_text(encoding='utf-8', errors='replace')
    start = src.index('def _on_clip_written')
    body = src[start:start + 6000]
    dispatch = body[body.index('args = ('):]

    assert dispatch.count('self._mux_mic_into_clip(') == 1
    assert dispatch.count('self._mux_multiband_into_clip(') == 1
    assert dispatch.count('self._finalize_clip(') == 1
    assert "elif route == 'multiband'" in dispatch, (
        'routes must be an if/elif/else chain, not independent ifs')
    assert dispatch.index("if route == 'mic'") < dispatch.index('elif route')


def test_only_one_call_site_starts_post_processing():
    """Post-processing must be started from the completion handler and nowhere
    else — not from the submit path, which is where it used to live."""
    src = (Path(__file__).resolve().parent.parent / 'FTHR_UI' / 'main.py'
           ).read_text(encoding='utf-8', errors='replace')
    for call in ('self._mux_mic_into_clip(', 'self._mux_multiband_into_clip(',
                 'self._finalize_clip('):
        assert src.count(call) == 1, (
            f'{call} is invoked from more than one place — a clip could be '
            f'post-processed twice')
    assert src.count('self.upload_manager.notify_clip_saved(') == 1


def test_partial_file_cannot_enter_post_processing():
    ready = threading.Event()

    assert not MainWindow._allow_completed_clip_pipeline(
        '/clips/foo.mp4.partial', ready)
    assert ready.is_set()
    assert MainWindow._allow_completed_clip_pipeline('/clips/foo.mp4')


def test_every_post_processing_entrypoint_has_the_completed_clip_guard():
    src = (Path(__file__).resolve().parent.parent / 'FTHR_UI' / 'main.py'
           ).read_text(encoding='utf-8', errors='replace')
    entrypoints = ('_mux_mic_into_clip', '_mux_multiband_into_clip', '_finalize_clip')
    for name in entrypoints:
        start = src.index(f'def {name}')
        body = src[start:start + 1200]
        assert '_allow_completed_clip_pipeline' in body

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtMultimedia import QMediaPlayer

from core import playback_proxy


def test_keyframe_gap_requires_timing_evidence():
    assert playback_proxy.needs_seek_proxy([
        {'pts_time': '0', 'flags': 'K_'}, {'pts_time': '4', 'flags': '__'}])
    assert not playback_proxy.needs_seek_proxy([
        {'pts_time': str(i), 'flags': 'K_'} for i in range(8)])
    assert not playback_proxy.needs_seek_proxy([
        {'pts_time': 'nan', 'flags': 'K_'}, {'pts_time': '8', 'flags': '__'}])


def test_preview_switch_waits_for_pause_and_preserves_pending_seek():
    from ui.clip_viewer import ClipViewer
    sources = []
    state = [QMediaPlayer.PlayingState]
    viewer = SimpleNamespace(
        _closing=False, clip_path='original.mp4', _playback_path='original.mp4',
        _prepared_playback_path='preview.mp4', _pending_seek_ms=4500,
        player=SimpleNamespace(playbackState=lambda: state[0], position=lambda: 100),
        _seek_timer=SimpleNamespace(stop=lambda: None),
        _set_player_state=lambda *_: None,
    )
    viewer._set_media_source = lambda: sources.append(viewer._playback_path)
    ClipViewer._apply_prepared_playback_source(viewer)
    assert sources == []
    state[0] = QMediaPlayer.PausedState
    ClipViewer._apply_prepared_playback_source(viewer)
    assert sources == ['preview.mp4']
    assert viewer.clip_path == 'original.mp4'
    assert viewer._renderer_resume_position_ms == 4500
    assert viewer._pending_seek_ms is None


def test_proxy_is_published_atomically_and_invalidated_by_source_change(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'original')
    monkeypatch.setattr(playback_proxy, 'CACHE_DIR', tmp_path / 'previews')
    monkeypatch.setattr(playback_proxy, 'get_ffprobe_exe', lambda: 'ffprobe')
    monkeypatch.setattr(playback_proxy, 'get_ffmpeg_exe', lambda: 'ffmpeg')
    monkeypatch.setattr(playback_proxy, 'software_video_args', lambda *_: ['-g', '30'])
    calls = []

    def run(command, cancel, **kwargs):
        calls.append(command)
        if command[0] == 'ffprobe':
            return 0, json.dumps({'format': {'duration': '60'}, 'packets': [
                {'pts_time': '0', 'flags': 'K_'},
                {'pts_time': '7', 'flags': '__'}]}).encode(), b''
        assert playback_proxy.cached_playback_path(str(source)) == str(source)
        Path(command[-1]).write_bytes(b'complete preview')
        return 0, b'', b''

    monkeypatch.setattr(playback_proxy, 'run_media_process', run)
    result = playback_proxy.prepare_playback_path(str(source), threading.Event())
    assert result != str(source)
    assert Path(result).read_bytes() == b'complete preview'
    assert source.read_bytes() == b'original'
    assert playback_proxy.prepare_playback_path(str(source), threading.Event()) == result
    assert len(calls) == 3
    source.write_bytes(b'new source generation')
    assert playback_proxy.cached_playback_path(str(source)) == str(source)


def test_cancelled_proxy_never_replaces_source_or_leaves_partial(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'original')
    monkeypatch.setattr(playback_proxy, 'CACHE_DIR', tmp_path / 'previews')
    monkeypatch.setattr(playback_proxy, 'get_ffprobe_exe', lambda: 'ffprobe')
    monkeypatch.setattr(playback_proxy, 'get_ffmpeg_exe', lambda: 'ffmpeg')
    monkeypatch.setattr(playback_proxy, 'software_video_args', lambda *_: [])
    cancel = threading.Event()

    def run(command, *args, **kwargs):
        if command[0] == 'ffprobe':
            return 0, json.dumps({'format': {'duration': '60'}, 'packets': [
                {'pts_time': '0', 'flags': 'K_'},
                {'pts_time': '7', 'flags': '__'}]}).encode(), b''
        Path(command[-1]).write_bytes(b'incomplete')
        cancel.set()
        return None

    monkeypatch.setattr(playback_proxy, 'run_media_process', run)
    assert playback_proxy.prepare_playback_path(str(source), cancel) == str(source)
    assert source.read_bytes() == b'original'
    assert list((tmp_path / 'previews').iterdir()) == []

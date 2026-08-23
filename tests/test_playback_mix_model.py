from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4
import json
import subprocess

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'FTHR_UI'))

from core.playback_mix_model import (
    LIMITER_CEILING,
    PlaybackSource,
    ProbedAudioStream,
    SourceMixState,
    build_playback_sources,
    ffmpeg_mix_filter,
    mix_interleaved_float,
    mono_to_stereo,
    resample_linear,
    source_icon_key,
)
from core.ffmpeg_playback import (
    BoundedPCMQueue,
    FFmpegPlaybackController,
    InProcessFFmpegMixer,
    PlaybackError,
    probe_audio_streams,
)


def _stream(index: int, title: str | None = None) -> ProbedAudioStream:
    return ProbedAudioStream(container_index=index, audio_index=index - 1, title=title)


def _manifest(*entries: tuple[int, str, str]) -> dict:
    return {'sources': [
        {'source_uuid': str(uuid4()), 'stream_index': index,
         'source_type': source_type, 'display_name': name,
         'icon_reference': None}
        for index, source_type, name in entries
    ]}


def test_current_windows_default_mix_and_microphone_are_both_editable():
    sources = build_playback_sources(
        _manifest((1, 'system', 'Default Mix'), (2, 'microphone', 'Microphone')),
        (_stream(1), _stream(2)),
    )
    assert [(source.display_name, source.source_type) for source in sources] == [
        ('Default Mix', 'system'), ('Microphone', 'microphone')]


def test_rich_app_stems_exclude_only_compatibility_default_mix():
    sources = build_playback_sources(
        _manifest((1, 'system', 'Default Mix'), (2, 'application', 'VALORANT'),
                  (3, 'application', 'Discord'), (4, 'microphone', 'Microphone')),
        tuple(_stream(index) for index in range(1, 5)),
    )
    assert [source.display_name for source in sources] == [
        'VALORANT', 'Discord', 'Microphone']


def test_manifest_keeps_unavailable_source_visible_but_not_mixable():
    sources = build_playback_sources(
        _manifest((1, 'system', 'Default Mix'), (2, 'microphone', 'Microphone')),
        (_stream(1),),
    )
    assert sources[1].available is False
    filters, label = ffmpeg_mix_filter(sources, {}, 100)
    assert label == '[aout]'
    assert '0:a:0' in filters[0]
    assert 'Microphone' not in ''.join(filters)


def test_imported_tracks_use_titles_then_generic_names():
    sources = build_playback_sources(None, (
        ProbedAudioStream(container_index=3, audio_index=0, title='Spanish'),
        ProbedAudioStream(container_index=5, audio_index=1),
    ))
    assert [source.display_name for source in sources] == ['Spanish', 'Track 2']
    assert [source.audio_index for source in sources] == [0, 1]


def test_source_icon_keys_use_manifest_references_then_safe_fallbacks():
    assert source_icon_key(PlaybackSource(
        'mic', 'Microphone', 'microphone', 1, 0, 'microphone')) == 'microphone'
    assert source_icon_key(PlaybackSource(
        'system', 'Default Mix', 'system', 2, 1, 'system-audio')) == 'system'
    assert source_icon_key(PlaybackSource(
        'app', 'Discord', 'application', 3, 2, 'windows-app-icon')) == 'application'
    assert source_icon_key(PlaybackSource(
        'import', 'Track 1', 'track', 4, 3)) == 'track'


def test_one_two_four_and_eight_tracks_follow_equal_power_headroom():
    for tracks in (1, 2, 4, 8):
        output = mix_interleaved_float([[0.5, 0.5]] * tracks, [1.0] * tracks)
        assert output == [min(LIMITER_CEILING, 0.5 * tracks / tracks ** 0.5)] * 2


def test_gain_mute_master_and_limiter_are_non_destructive_mix_state():
    sources = (PlaybackSource('system', 'System Audio', 'system', 1, 0),
               PlaybackSource('mic', 'Microphone', 'microphone', 2, 1))
    states = {'mic': SourceMixState(gain_percent=100, muted=True)}
    filters, label = ffmpeg_mix_filter(sources, states, 50)
    assert label == '[aout]'
    assert 'volume=0.000000' in filters[1]
    assert 'volume=0.500000' in filters[-1]
    assert 'alimiter=limit=0.98' in filters[-1]
    assert mix_interleaved_float([[2.0], [2.0]], [1.0, 1.0]) == [LIMITER_CEILING]


def test_mono_and_44100_reference_contracts():
    assert mono_to_stereo([0.25, -0.5]) == [0.25, 0.25, -0.5, -0.5]
    resampled = resample_linear([0.0, 1.0], 44_100, 48_000)
    assert len(resampled) == 2
    assert resampled[0] == 0.0


def test_bounded_pcm_queue_reports_underruns_without_unbounded_growth():
    queue = BoundedPCMQueue(bytes_per_frame=8)
    assert queue.read(8) == b''
    assert queue.underruns == 1
    assert queue.append(b'12345678') is True
    assert queue.frames == 1
    assert queue.read(16) == b'12345678'
    assert queue.frames == 0
    assert queue.append(b'broken') is False


def test_audio_output_fatal_error_is_reported_without_crashing_qt(qtbot):
    """The controller must surface a disappeared/broken device cleanly."""
    from PySide6.QtCore import QObject
    from PySide6.QtMultimedia import QAudio

    class FailedSink:
        def error(self):
            return QAudio.Error.FatalError

    controller = FFmpegPlaybackController.__new__(FFmpegPlaybackController)
    QObject.__init__(controller)
    controller._sink = FailedSink()
    received = []
    controller.audio_failed.connect(received.append)
    controller._on_sink_state(QAudio.State.StoppedState)
    assert received == ['Audio output device stopped unexpectedly.']


def test_probe_uses_actual_container_indexes_and_titles(monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({'streams': [
            {'index': 0, 'codec_type': 'video'},
            {'index': 2, 'codec_type': 'audio', 'tags': {'title': 'System'}},
            {'index': 5, 'codec_type': 'audio', 'tags': {'handler_name': 'Mic'}},
        ]})

    monkeypatch.setattr('core.ffmpeg_playback.get_ffprobe_exe', lambda: 'ffprobe')
    monkeypatch.setattr('core.ffmpeg_playback.subprocess.run', lambda *_args, **_kwargs: Result())
    streams = probe_audio_streams('clip.mp4')
    assert [(stream.container_index, stream.audio_index, stream.title) for stream in streams] == [
        (2, 0, 'System'), (5, 1, 'Mic')]


def test_in_process_bridge_decodes_mixes_mutes_and_seeks_real_media(tmp_path):
    """Integration coverage for the production bridge; skips before native build."""
    try:
        from core.ffmpeg_tools import get_ffmpeg_exe
        ffmpeg = get_ffmpeg_exe()
    except Exception as error:
        import pytest
        pytest.skip(f'no reviewed FFmpeg runtime: {error}')
    media = tmp_path / 'two-stem.mp4'
    command = [
        ffmpeg, '-y', '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
        '-t', '2', '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0',
        '-c:v', 'mpeg4', '-c:a', 'aac', str(media),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=20)
    sources = (
        PlaybackSource('system', 'System Audio', 'system', 1, 0),
        PlaybackSource('mic', 'Microphone', 'microphone', 2, 1),
    )
    try:
        mixer = InProcessFFmpegMixer(str(media), sources)
    except PlaybackError as error:
        import pytest
        pytest.skip(str(error))
    try:
        def _collect(gains: tuple[float, float]) -> bytes:
            mixer.seek(250)
            blocks = [mixer.pull(1024, gains, 1.0) for _ in range(16)]
            assert all(blocks)
            return b''.join(blocks)

        mixed = _collect((1.0, 1.0))
        system_only = _collect((1.0, 0.0))
        mic_only = _collect((0.0, 1.0))
        mixer.seek(1_000)
        sought = mixer.pull(1024, (1.0, 1.0), 1.0)
        # The production seek sequence flushes every decoder/resampler.  Make
        # several direction changes before pulling again so stale PCM cannot
        # leak from an older source position.
        for position_ms in (0, 1_400, 500, 1_700, 250):
            mixer.seek(position_ms)
        rapidly_sought = mixer.pull(1024, (1.0, 1.0), 1.0)
    finally:
        mixer.close()
    assert mixed and system_only and mic_only and sought and rapidly_sought
    assert len(sought) == 1024 * 2 * 4

    def _tone_energy(pcm: bytes, frequency: float) -> float:
        mono = np.frombuffer(pcm, dtype='<f4').reshape(-1, 2).mean(axis=1)
        phase = np.exp(-2j * np.pi * frequency * np.arange(len(mono)) / 48_000)
        return float(abs(np.vdot(phase, mono)))

    assert _tone_energy(mixed, 440) > 10 and _tone_energy(mixed, 880) > 10
    assert _tone_energy(system_only, 440) > _tone_energy(system_only, 880) * 5
    assert _tone_energy(mic_only, 880) > _tone_energy(mic_only, 440) * 5

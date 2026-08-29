from __future__ import annotations

from pathlib import Path
import subprocess

from core.ffmpeg_playback import _resample_pcm
from core.playback_mix_model import PlaybackSource
from ui.clip_viewer import (
    ClipViewer,
    _atempo_chain,
    _pitch_changed_chain,
    _speed_audio_chain,
    _speed_adjusted_duration,
)


def test_speed_helpers_cover_ffmpeg_limits_and_duration():
    assert _atempo_chain(1.0) is None
    assert _atempo_chain(1.5) == 'atempo=1.5'
    assert _atempo_chain(0.25) == 'atempo=0.5,atempo=0.5'
    assert _pitch_changed_chain(2.0) == (
        'aresample=48000,asetrate=96000,aresample=48000')
    assert _speed_audio_chain(1.5, preserve_pitch=True) == 'atempo=1.5'
    assert _speed_audio_chain(1.5, preserve_pitch=False) == (
        'aresample=48000,asetrate=72000,aresample=48000')
    assert _speed_adjusted_duration(10.0, 2.0) == 5.0
    assert _speed_adjusted_duration(10.0, 0.5) == 20.0


def test_live_mixer_pcm_resampling_advances_or_repeats_source_time():
    import numpy as np

    source = np.arange(16, dtype='<f4').reshape(8, 2).tobytes()
    fast, fast_frames = _resample_pcm(source, 2.0)
    slow, slow_frames = _resample_pcm(source, 0.5)

    assert fast_frames == 4
    assert slow_frames == 16
    assert len(fast) == fast_frames * 2 * 4
    assert len(slow) == slow_frames * 2 * 4


def test_speed_export_encodes_video_and_audio_for_single_segment():
    viewer = type('Viewer', (), {
        'clip_path': 'clip.mp4',
        '_speed_rate': 2.0,
        '_audio_tracks': (),
        '_playback_sources': (),
    })()

    command = ClipViewer._build_export_cmd(
        viewer, 'ffmpeg', 0.0, 10.0, 'out.mp4', None, ['-c:v', 'libx264'])
    joined = ' '.join(command)

    assert '-t 5.0' in joined
    assert 'setpts=PTS/2' in joined
    assert command[command.index('-af') + 1] == 'atempo=2'
    assert command[command.index('-c:a') + 1] == 'aac'


def test_speed_export_can_follow_pitch_instead_of_preserving_it():
    viewer = type('Viewer', (), {
        'clip_path': 'clip.mp4',
        '_speed_rate': 2.0,
        '_preserve_pitch': False,
        '_audio_tracks': (),
        '_playback_sources': (),
    })()

    command = ClipViewer._build_export_cmd(
        viewer, 'ffmpeg', 0.0, 10.0, 'out.mp4', None, ['-c:v', 'libx264'])
    joined = ' '.join(command)

    assert 'aresample=48000,asetrate=96000,aresample=48000' in joined
    assert 'atempo=' not in joined


def test_speed_export_scales_concatenated_mixed_audio():
    sources = (
        PlaybackSource('system', 'System Audio', 'system', 1, 0),
        PlaybackSource('mic', 'Microphone', 'microphone', 2, 1),
    )
    viewer = type('Viewer', (), {
        'clip_path': 'clip.mp4',
        '_speed_rate': 0.5,
        '_audio_tracks': (('system', 0), ('mic', 1)),
        '_playback_sources': sources,
        '_source_volumes': {'system': 100, 'mic': 100},
        '_source_mutes': {'system': False, 'mic': False},
        '_master_volume': 100,
    })()

    command = ClipViewer._build_export_cmd(
        viewer, 'ffmpeg', 0.0, 4.0, 'out.mp4', None, ['-c:v', 'libx264'],
        segments=[(0.0, 2.0), (4.0, 6.0)])
    joined = ' '.join(command)

    assert 'setpts=PTS/0.5' in joined
    assert '[aout]atempo=0.5[aspeed]' in joined
    assert command[command.index('-map') + 3] == '[aspeed]'


def test_real_ffmpeg_speed_export_handles_mixed_multi_segment_audio(tmp_path: Path):
    try:
        from core.ffmpeg_tools import get_ffmpeg_exe, get_ffprobe_exe
        ffmpeg = get_ffmpeg_exe()
        ffprobe = get_ffprobe_exe()
    except Exception as error:
        import pytest
        pytest.skip(f'no reviewed FFmpeg runtime: {error}')

    source = tmp_path / 'source.mp4'
    output = tmp_path / 'segments.mp4'
    subprocess.run([
        ffmpeg, '-y', '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
        '-t', '2', '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0',
        '-c:v', 'mpeg4', '-c:a', 'aac', str(source),
    ], check=True, capture_output=True, timeout=20)

    sources = (
        PlaybackSource('system', 'System Audio', 'system', 1, 0),
        PlaybackSource('mic', 'Microphone', 'microphone', 2, 1),
    )
    viewer = type('Viewer', (), {
        'clip_path': str(source),
        '_speed_rate': 0.5,
        '_audio_tracks': (('system', 0), ('mic', 1)),
        '_playback_sources': sources,
        '_source_volumes': {'system': 100, 'mic': 100},
        '_source_mutes': {'system': False, 'mic': False},
        '_master_volume': 100,
    })()
    command = ClipViewer._build_export_cmd(
        viewer, ffmpeg, 0.0, 1.0, str(output), None,
        ['-c:v', 'mpeg4', '-q:v', '3'],
        segments=[(0.0, 0.5), (1.0, 1.5)])
    subprocess.run(command, check=True, capture_output=True, timeout=40)

    probe = subprocess.run([
        ffprobe, '-v', 'error', '-show_entries',
        'format=duration:stream=codec_type', '-of', 'json', str(output),
    ], check=True, capture_output=True, text=True, timeout=20)
    import json
    metadata = json.loads(probe.stdout)
    assert 1.8 <= float(metadata['format']['duration']) <= 2.2
    assert sum(stream.get('codec_type') == 'audio'
               for stream in metadata['streams']) == 1


def test_real_ffmpeg_speed_export_shortens_video_and_audio(tmp_path: Path):
    try:
        from core.ffmpeg_tools import get_ffmpeg_exe, get_ffprobe_exe
        ffmpeg = get_ffmpeg_exe()
        ffprobe = get_ffprobe_exe()
    except Exception as error:
        import pytest
        pytest.skip(f'no reviewed FFmpeg runtime: {error}')

    source = tmp_path / 'source.mp4'
    output = tmp_path / 'fast.mp4'
    subprocess.run([
        ffmpeg, '-y', '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-t', '2', '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'mpeg4', '-c:a', 'aac', str(source),
    ], check=True, capture_output=True, timeout=20)

    viewer = type('Viewer', (), {
        'clip_path': str(source),
        '_speed_rate': 2.0,
        '_audio_tracks': (),
        '_playback_sources': (),
    })()
    command = ClipViewer._build_export_cmd(
        viewer, ffmpeg, 0.0, 2.0, str(output), None,
        ['-c:v', 'mpeg4', '-q:v', '3'])
    subprocess.run(command, check=True, capture_output=True, timeout=30)

    probe = subprocess.run([
        ffprobe, '-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',
        str(output),
    ], check=True, capture_output=True, text=True, timeout=20)
    assert 0.85 <= float(probe.stdout.strip()) <= 1.15

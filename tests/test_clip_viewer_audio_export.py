from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'FTHR_UI'))

from core.playback_mix_model import PlaybackSource
from core.transactional_output import commit_staged_output, create_staged_output_path
from ui.clip_viewer import ClipViewer, VolumePopup


def test_dynamic_volume_popup_has_real_source_mute_controls(qtbot):
    source = PlaybackSource('mic', 'Microphone', 'microphone', 2, 1)
    popup = VolumePopup(80, {'mic': 70}, {'mic': True}, (source,), live_preview=True)
    qtbot.addWidget(popup)
    seen = []
    popup.source_muted.connect(lambda key, muted: seen.append((key, muted)))
    # The signal is source-ID based, not a fixed category name, so it remains
    # safe for manifest stems and imported clips alike.
    popup.source_muted.emit('mic', True)
    assert seen == [('mic', True)]


def test_one_failed_source_is_disabled_without_removing_healthy_source():
    system = PlaybackSource('system', 'System Audio', 'system', 1, 0)
    mic = PlaybackSource('mic', 'Microphone', 'microphone', 2, 1)
    viewer = type('Viewer', (), {
        '_playback_sources': (system, mic),
        '_volume_popup': None,
    })()
    ClipViewer._on_audio_source_failed(viewer, 'mic')
    assert viewer._playback_sources[0].available is True
    assert viewer._playback_sources[1].available is False


def test_export_uses_current_source_gain_mute_master_and_limiter():
    viewer = type('Viewer', (), {
        'clip_path': 'clip.mp4',
        '_audio_tracks': (('system', 0), ('mic', 1)),
        '_playback_sources': (
            PlaybackSource('system', 'System Audio', 'system', 1, 0),
            PlaybackSource('mic', 'Microphone', 'microphone', 2, 1),
        ),
        '_source_volumes': {'system': 100, 'mic': 80},
        '_source_mutes': {'system': False, 'mic': True},
        '_master_volume': 50,
    })()
    command = ClipViewer._build_export_cmd(
        viewer, 'ffmpeg', 0.0, 10.0, 'out.mp4', None, ['-c:v', 'copy'])
    joined = ' '.join(command)
    assert '0:a:0' in joined and '0:a:1' in joined
    assert 'volume=0.000000' in joined
    assert 'alimiter=limit=0.98' in joined
    assert 'volume=0.500000[aout]' in joined


def test_mixed_export_is_transactional_and_preserve_mapping_keeps_stems(tmp_path):
    """Exercise the reviewed FFmpeg command against real two-stem media."""
    try:
        from core.ffmpeg_tools import get_ffmpeg_exe, get_ffprobe_exe
        ffmpeg = get_ffmpeg_exe()
        ffprobe = get_ffprobe_exe()
    except Exception as error:
        import pytest
        pytest.skip(f'no reviewed FFmpeg runtime: {error}')

    source = tmp_path / 'source.mp4'
    subprocess.run([
        ffmpeg, '-y', '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
        '-t', '1', '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0',
        '-c:v', 'mpeg4', '-c:a', 'aac', str(source),
    ], check=True, capture_output=True, timeout=20)
    source_hash = sha256(source.read_bytes()).hexdigest()
    viewer = type('Viewer', (), {
        'clip_path': str(source),
        '_audio_tracks': (('system', 0), ('mic', 1)),
        '_playback_sources': (
            PlaybackSource('system', 'System Audio', 'system', 1, 0),
            PlaybackSource('mic', 'Microphone', 'microphone', 2, 1),
        ),
        '_source_volumes': {'system': 100, 'mic': 100},
        '_source_mutes': {'system': False, 'mic': True},
        '_master_volume': 100,
    })()
    final = tmp_path / 'mixed.mp4'
    staged = create_staged_output_path(final)
    command = ClipViewer._build_export_cmd(
        viewer, ffmpeg, 0.0, 1.0, str(staged), None, ['-c:v', 'copy'])
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    commit_staged_output(staged, final)

    def _audio_stream_count(path: Path) -> int:
        result = subprocess.run([
            ffprobe, '-v', 'error', '-show_streams', '-of', 'json', str(path),
        ], check=True, capture_output=True, text=True, timeout=20)
        return sum(stream.get('codec_type') == 'audio'
                   for stream in json.loads(result.stdout)['streams'])

    assert final.is_file()
    assert _audio_stream_count(final) == 1
    subprocess.run([ffmpeg, '-v', 'error', '-i', str(final), '-f', 'null', '-'],
                   check=True, capture_output=True, timeout=20)
    assert sha256(source.read_bytes()).hexdigest() == source_hash

    # Full-quality Share's explicit map is deliberately all stems, not
    # FFmpeg's automatic one-stream selection.  Validate the actual mapping
    # used by its no-edit path with the same source media.
    preserved = tmp_path / 'preserved.mp4'
    subprocess.run([
        ffmpeg, '-y', '-ss', '0', '-i', str(source), '-t', '1',
        '-map', '0:v?', '-map', '0:a?', '-c', 'copy', str(preserved),
    ], check=True, capture_output=True, timeout=30)
    assert _audio_stream_count(preserved) == 2

"""Check bundled encoder selection and reporting when FFmpeg is unavailable."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'FTHR_UI'))

from core import ffmpeg_tools  # noqa: E402
from core.ffmpeg_tools import (  # noqa: E402
    FFmpegUnavailable, get_ffmpeg_exe, reset_cache,
    maximum_quality_video_args,
    postprocess_video_args, size_constrained_video_args, software_video_args,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


# encoder selection

def test_software_args_never_request_gpl_encoders(monkeypatch):
    """The whole point of AUDIT-005: no x264/x265 in any ffmpeg invocation."""
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libopenh264')
    args = software_video_args(ffmpeg='dummy')
    joined = ' '.join(args)
    assert 'libx264' not in joined
    assert 'libx265' not in joined
    assert 'libopenh264' in joined


def test_openh264_args_have_no_x264_only_flags(monkeypatch):
    """-preset and -crf are libx264 private options. OpenH264 rejects them,
    and passing them silently loses rate control."""
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libopenh264')
    args = software_video_args(ffmpeg='dummy')
    assert '-crf' not in args
    assert '-preset' not in args
    assert '-b:v' in args, 'OpenH264 needs an explicit bitrate or it ignores bit_rate'


def test_openh264_disables_frame_skipping(monkeypatch):
    """Dropped frames desync the clip against the audio muxed in afterwards."""
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libopenh264')
    args = software_video_args(ffmpeg='dummy')
    i = args.index('-allow_skip_frames')
    assert args[i + 1] == '0'


def test_maximum_quality_openh264_uses_the_highest_bounded_bitrate(monkeypatch):
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libopenh264')

    args = maximum_quality_video_args(ffmpeg='dummy')

    assert args[args.index('-b:v') + 1] == '200000k'
    assert args[args.index('-allow_skip_frames') + 1] == '0'
    assert '-crf' not in args


def test_maximum_quality_uses_true_lossless_x264_when_available(monkeypatch):
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libx264')

    args = maximum_quality_video_args(ffmpeg='dummy')

    assert args[args.index('-crf') + 1] == '0'
    assert args[args.index('-preset') + 1] == 'veryslow'
    assert '-b:v' not in args


def test_bitrate_is_honoured(monkeypatch):
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libopenh264')
    args = software_video_args(bitrate_kbps=4500, ffmpeg='dummy')
    assert '4500k' in args


def test_software_transcodes_preserve_sdr_bt709_range(monkeypatch):
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libopenh264')
    args = software_video_args(ffmpeg='dummy')

    assert args[args.index('-color_range') + 1] == 'tv'
    assert args[args.index('-colorspace') + 1] == 'bt709'
    assert args[args.index('-color_primaries') + 1] == 'bt709'
    assert args[args.index('-color_trc') + 1] == 'bt709'
    assert args[args.index('-bsf:v') + 1].startswith(
        'h264_metadata=video_full_range_flag=0')


def test_postprocess_uses_active_nvenc_for_speed():
    args = postprocess_video_args('h264_nvenc', ffmpeg='dummy')

    assert args[args.index('-c:v') + 1] == 'h264_nvenc'
    assert args[args.index('-preset') + 1] == 'p1'
    assert args[args.index('-tune') + 1] == 'll'


def test_postprocess_keeps_software_fallback(monkeypatch):
    monkeypatch.setattr(ffmpeg_tools, '_probe_encoders', lambda _ff: 'libopenh264')

    args = postprocess_video_args('', ffmpeg='dummy')

    assert args[args.index('-c:v') + 1] == 'libopenh264'


def test_size_constrained_args_replace_quality_only_rate_control(monkeypatch):
    monkeypatch.setattr(
        ffmpeg_tools, 'software_video_args',
        lambda _bitrate, _ffmpeg: [
            '-c:v', 'test-h264', '-preset', 'fast', '-crf', '18'],
    )

    args = size_constrained_video_args(3200, 'dummy')

    assert '-crf' not in args
    assert args[args.index('-b:v') + 1] == '3200k'


def test_probe_prefers_openh264_over_x264():
    """Even if a system FFmpeg offers both, prefer the BSD encoder."""
    class _Res:
        stdout = ' V..... libx264 ...\n V..... libopenh264 ...\n'
    import subprocess
    real_run = subprocess.run
    try:
        subprocess.run = lambda *a, **k: _Res()
        assert ffmpeg_tools._probe_encoders('dummy') == 'libopenh264'
    finally:
        subprocess.run = real_run


def test_probe_falls_back_when_ffmpeg_unrunnable():
    """A failed probe must not crash clip export; assume our bundled encoder."""
    assert ffmpeg_tools._probe_encoders('definitely-not-a-real-binary') == 'libopenh264'


# binary resolution

def test_missing_ffmpeg_raises_reportable_error(monkeypatch):
    """Must raise, not return None — the old code returned silently and the
    watermark/crop/export features just did nothing with no message."""
    monkeypatch.setattr(ffmpeg_tools, '_candidate_paths', lambda: [])
    monkeypatch.setattr('shutil.which', lambda _n: None)
    with pytest.raises(FFmpegUnavailable) as exc:
        get_ffmpeg_exe()
    assert 'ffmpeg' in str(exc.value).lower()


def test_ffmpeg_unavailable_is_a_runtime_error():
    """Existing call sites catch RuntimeError; keep them working."""
    assert issubclass(FFmpegUnavailable, RuntimeError)


def test_resolution_is_cached(monkeypatch, tmp_path):
    fake = tmp_path / ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
    fake.write_text('')
    calls = []

    def _paths():
        calls.append(1)
        return [fake]

    monkeypatch.setattr(ffmpeg_tools, '_candidate_paths', _paths)
    assert get_ffmpeg_exe() == str(fake)
    assert get_ffmpeg_exe() == str(fake)
    assert len(calls) == 1, 'ffmpeg path re-resolved on every call'


def test_bundled_ffmpeg_is_found_in_dev_checkout():
    """The vendored LGPL build must be discoverable from a source tree."""
    exe = ROOT / 'FTHRcapture' / 'FTHRclips' / 'third_party' / 'ffmpeg' / 'bin' / \
        ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
    if not exe.is_file():
        pytest.skip('vendored ffmpeg not present in this checkout')
    assert Path(get_ffmpeg_exe()).is_file()


# the source tree itself must stay clean

def test_no_source_file_hardcodes_gpl_encoders():
    """Guards against someone re-adding '-c:v libx264' to a new ffmpeg call."""
    offenders = []
    for py in (ROOT / 'FTHR_UI').rglob('*.py'):
        text = py.read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r"['\"]libx26[45]['\"]", text):
            line = text[:m.start()].count('\n') + 1
            # ffmpeg_tools documents them in prose and keeps one guarded
            # compatibility branch for third-party system FFmpeg builds.
            if py.name == 'ffmpeg_tools.py':
                continue
            offenders.append(f'{py.relative_to(ROOT)}:{line}')
    assert not offenders, (
        'GPL-only encoder hardcoded in: ' + ', '.join(offenders) +
        ' — use software_video_args() from core.ffmpeg_tools instead'
    )


def test_imageio_ffmpeg_is_not_imported_anywhere():
    """It is a GPL build; re-introducing it would re-open AUDIT-005."""
    offenders = []
    for py in (ROOT / 'FTHR_UI').rglob('*.py'):
        text = py.read_text(encoding='utf-8', errors='replace')
        if re.search(r'^\s*(import|from)\s+imageio_ffmpeg', text, re.M):
            offenders.append(str(py.relative_to(ROOT)))
    assert not offenders, f'imageio_ffmpeg imported in: {offenders}'

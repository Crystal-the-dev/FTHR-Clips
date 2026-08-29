from __future__ import annotations

import json

import pytest

from core.media_metadata import (
    format_bitrate,
    format_fps,
    parse_ffprobe_video_metadata,
)


def _payload(*, avg: str, real: str = '60/1', video_bitrate='6000000',
             format_bitrate='6200000', duration='30.0') -> str:
    return json.dumps({
        'streams': [
            {
                'index': 0,
                'codec_type': 'video',
                'width': 1920,
                'height': 1080,
                'avg_frame_rate': avg,
                'r_frame_rate': real,
                'duration': duration,
                'bit_rate': video_bitrate,
            },
            {
                'index': 1,
                'codec_type': 'audio',
                'bit_rate': '192000',
            },
        ],
        'format': {
            'duration': duration,
            'size': '23250000',
            'bit_rate': format_bitrate,
        },
    })


@pytest.mark.parametrize(('rational', 'expected'), (
    ('24/1', 24.0),
    ('25/1', 25.0),
    ('30000/1001', 29.97002997),
    ('30/1', 30.0),
    ('50/1', 50.0),
    ('60000/1001', 59.94005994),
    ('60/1', 60.0),
))
def test_average_stream_fps_handles_standard_rates(rational, expected):
    metadata = parse_ffprobe_video_metadata(_payload(avg=rational))

    assert metadata is not None
    assert metadata.average_fps == pytest.approx(expected)
    assert metadata.fps_source == 'avg_frame_rate'


def test_vfr_uses_average_rate_instead_of_codec_real_rate():
    metadata = parse_ffprobe_video_metadata(
        _payload(avg='30000/1001', real='60/1'))

    assert metadata is not None
    assert metadata.average_fps == pytest.approx(29.97002997)
    assert metadata.real_fps == 60.0
    assert format_fps(metadata.average_fps) == '29.97 FPS'


def test_missing_average_rate_falls_back_to_valid_real_rate():
    metadata = parse_ffprobe_video_metadata(_payload(avg='0/0', real='25/1'))

    assert metadata is not None
    assert metadata.average_fps == 25.0
    assert metadata.fps_source == 'r_frame_rate'


def test_missing_rates_and_video_bitrate_are_not_invented_from_audio_file():
    metadata = parse_ffprobe_video_metadata(
        _payload(avg='0/0', real='N/A', video_bitrate='N/A'))

    assert metadata is not None
    assert metadata.average_fps is None
    assert metadata.video_bitrate_bps is None
    assert metadata.total_bitrate_bps == 6_200_000
    assert format_fps(metadata.average_fps) == 'Unavailable'
    assert format_bitrate(metadata.video_bitrate_bps) == 'Unavailable'


def test_total_bitrate_file_size_fallback_remains_explicitly_total():
    document = json.loads(_payload(
        avg='60/1', video_bitrate='N/A', format_bitrate='N/A', duration='10'))
    document['format']['size'] = '1000000'

    metadata = parse_ffprobe_video_metadata(document)

    assert metadata is not None
    assert metadata.video_bitrate_bps is None
    assert metadata.total_bitrate_bps == 800_000


def test_invalid_or_video_less_documents_fail_gracefully():
    assert parse_ffprobe_video_metadata('{') is None
    assert parse_ffprobe_video_metadata({'streams': []}) is None

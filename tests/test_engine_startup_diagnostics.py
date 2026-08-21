from core.engine_startup_diagnostics import (
    extract_startup_failure,
    extract_startup_warnings,
)


def test_extracts_last_structured_engine_failure():
    output = """engine setup\nFTHR_STARTUP_ERROR: ENCODER_INIT_FAILED: old\nnoise\nFTHR_STARTUP_ERROR: REQUESTED_CODEC_UNSUPPORTED: AV1 is not supported\n"""

    failure = extract_startup_failure(output)

    assert failure.code == 'REQUESTED_CODEC_UNSUPPORTED'
    assert failure.detail == 'AV1 is not supported'
    assert failure.title == 'REQUESTED CODEC UNSUPPORTED'


def test_missing_structured_failure_has_actionable_generic_result():
    failure = extract_startup_failure('unexpected native process exit')

    assert failure.code == 'ENGINE_START_FAILED'
    assert 'before connecting' in failure.detail


def test_detail_is_bounded_for_ui_display():
    output = 'FTHR_STARTUP_ERROR: ENCODER_INIT_FAILED: ' + ('x' * 4000)

    failure = extract_startup_failure(output)

    assert len(failure.detail) == 1000


def test_extracts_linux_video_only_audio_warning():
    output = (
        'FTHR_STARTUP_WARNING: DESKTOP_AUDIO_UNAVAILABLE: '
        'Default output monitor could not be opened; capture continues video-only.\n')

    warnings = extract_startup_warnings(output)

    assert len(warnings) == 1
    assert warnings[0].code == 'DESKTOP_AUDIO_UNAVAILABLE'
    assert 'video-only' in warnings[0].detail

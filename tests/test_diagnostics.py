"""Diagnostics: redaction, restraint, and stack traces (AUDIT-007).

The support strategy is "send us your log". These tests defend two properties
of that log: it must be safe to paste in public, and it must stay readable
when something fails two hundred times a second.
"""

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'FTHR_UI'))

from core.diagnostics import (  # noqa: E402
    StateLogger, configure, get_logger, log_unexpected, redact_secret,
    reset_for_tests, sanitize_headers_for_log, sanitize_url_for_log,
)


@pytest.fixture(autouse=True)
def _clean_logging():
    reset_for_tests()
    yield
    reset_for_tests()


# Redaction — a leaked credential in a public issue is unrecoverable

REAL_LOOKING_JWT = ('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
                    'eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r')


@pytest.mark.parametrize('text, secret', [
    (f'Authorization: Bearer {REAL_LOOKING_JWT}', REAL_LOOKING_JWT),
    (f'headers={{"Authorization": "Bearer {REAL_LOOKING_JWT}"}}', REAL_LOOKING_JWT),
    ('upload_auth_header=Bearer sk-live-9f8e7d6c', 'sk-live-9f8e7d6c'),
    ('{"api_key": "AKIA1234567890ABCD"}', 'AKIA1234567890ABCD'),
    ("password='hunter2'", 'hunter2'),
    ('Cookie: session=abc123def456', 'abc123def456'),
    ('token=ghp_16C7e42F292c69', 'ghp_16C7e42F292c69'),
    ('https://' + 'user:s3cr3t@' + 'example.com/upload', 's3cr3t'),
    ('POST https://api.example.com/v1/clips?access_token=xyzzy789', 'xyzzy789'),
])
def test_redact_secret_removes_the_credential(text, secret):
    out = redact_secret(text)
    assert secret not in out, f'secret survived redaction: {out}'
    assert '<redacted>' in out


def test_redaction_keeps_the_diagnostic_context():
    """Redaction must not destroy the information the line exists to carry."""
    out = redact_secret(f'upload failed 401: Authorization: Bearer {REAL_LOOKING_JWT}')
    assert 'upload failed 401' in out
    assert 'Authorization' in out, 'knowing which header was sent is the diagnostic'


def test_redact_secret_never_raises_on_odd_input():
    class Explodes:
        def __repr__(self):
            raise RuntimeError('boom')

    assert redact_secret(Explodes()) == '<redacted>'
    assert isinstance(redact_secret(None), str)
    assert isinstance(redact_secret(12345), str)


def test_sanitize_url_drops_query_and_credentials():
    url = ('https://' + 'user:pw@' +
           'up.example.com/api/clips?token=abc&clip=my%20clip.mp4')
    out = sanitize_url_for_log(url)
    assert 'pw' not in out and 'abc' not in out
    assert 'up.example.com/api/clips' in out, 'the host and path are the diagnostic'
    assert 'my%20clip' not in out, 'clip names are user content'


def test_sanitize_headers_preserves_names_not_values():
    out = sanitize_headers_for_log({
        'Authorization': f'Bearer {REAL_LOOKING_JWT}',
        'Cookie': 'sid=deadbeef',
        'Content-Type': 'video/mp4',
    })
    assert out['Authorization'] == '<redacted>'
    assert out['Cookie'] == '<redacted>'
    assert out['Content-Type'] == 'video/mp4', 'harmless headers stay readable'


def test_handler_redacts_even_when_the_call_site_forgot(tmp_path):
    """The filter is the backstop for call sites — and libraries — that log a
    raw URL or header without thinking about it."""
    log_file = tmp_path / 'fthr.log'
    assert configure(log_file=log_file) is True
    get_logger('test').error('upload failed for %s',
                             f'https://x.example/u?token={REAL_LOOKING_JWT}')
    logging.getLogger().handlers[0].flush()

    written = log_file.read_text(encoding='utf-8')
    assert REAL_LOOKING_JWT not in written
    assert 'upload failed' in written


def test_handler_preserves_numeric_placeholder_types(tmp_path):
    log_file = tmp_path / 'fthr.log'
    assert configure(log_file=log_file) is True
    get_logger('test').info('frames=%d luma=%.2f', 27, 12.5)
    logging.getLogger().handlers[0].flush()

    written = log_file.read_text(encoding='utf-8')
    assert 'frames=27 luma=12.50' in written


# Restraint — a 50 ms poll must not produce a 50 ms log

class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _capture(logger_name='restraint'):
    logger = logging.getLogger(logger_name)
    logger.handlers.clear()
    logger.propagate = False
    records = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger.addHandler(H())
    logger.setLevel(logging.DEBUG)
    return logger, records


def test_repeated_failure_is_logged_once():
    logger, records = _capture()
    clock = _FakeClock()
    state = StateLogger(logger, 'engine connection', clock=clock)

    for _ in range(200):          # 200 polls at 50 ms = 10 seconds
        state.failed('shared memory not mapped')
        clock.t += 0.05

    assert len(records) == 1, f'{len(records)} lines for one condition'
    assert 'engine connection failed' in records[0].getMessage()


def test_recovery_is_logged_once_with_duration():
    logger, records = _capture()
    clock = _FakeClock()
    state = StateLogger(logger, 'engine connection', clock=clock)

    state.failed('not mapped')
    for _ in range(248):
        clock.t += 0.05
        state.failed('not mapped')
    state.ok()

    assert len(records) == 2
    msg = records[1].getMessage()
    assert 'recovered after 12.4 s' in msg
    assert '248 repeat occurrences suppressed' in msg


def test_a_new_reason_is_new_information():
    """Silence is for repetition, not for a changed diagnosis."""
    logger, records = _capture()
    state = StateLogger(logger, 'engine connection', clock=_FakeClock())

    state.failed('connection refused')
    state.failed('connection refused')
    state.failed('shared-memory layout mismatch')

    assert len(records) == 2
    assert 'layout mismatch' in records[1].getMessage()


def test_ok_without_a_prior_failure_is_silent():
    logger, records = _capture()
    state = StateLogger(logger, 'engine connection')
    for _ in range(50):
        assert state.ok() is False
    assert records == []


def test_state_logger_redacts_its_detail():
    logger, records = _capture()
    state = StateLogger(logger, 'upload')
    state.failed(f'rejected: Authorization: Bearer {REAL_LOOKING_JWT}')
    assert REAL_LOOKING_JWT not in records[0].getMessage()


def test_failure_and_recovery_cycle_repeats():
    logger, records = _capture()
    clock = _FakeClock()
    state = StateLogger(logger, 'engine connection', clock=clock)
    for _ in range(3):
        state.failed('gone')
        clock.t += 1.0
        state.ok()
    assert len(records) == 6, 'each cycle must report both edges'


# Unexpected failures must carry a stack trace

def test_log_unexpected_includes_a_traceback():
    logger, records = _capture()
    try:
        raise ValueError('invariant violated')
    except ValueError as e:
        log_unexpected(logger, 'save state transition', e, op_id=7)

    assert len(records) == 1
    assert records[0].exc_info is not None, 'no traceback — the whole point'
    assert records[0].levelno == logging.ERROR
    assert 'op_id=7' in records[0].getMessage()


def test_log_unexpected_redacts_its_context():
    logger, records = _capture()
    try:
        raise RuntimeError('nope')
    except RuntimeError as e:
        log_unexpected(logger, 'upload', e,
                       url=f'https://x.example/u?token={REAL_LOOKING_JWT}')
    assert REAL_LOOKING_JWT not in records[0].getMessage()


# Setup must degrade, not crash

def test_configure_is_idempotent(tmp_path):
    log_file = tmp_path / 'fthr.log'
    assert configure(log_file=log_file) is True
    before = len(logging.getLogger().handlers)
    configure(log_file=log_file)
    assert len(logging.getLogger().handlers) == before


def test_configure_reports_failure_instead_of_vanishing(tmp_path, capsys):
    """An unwritable log directory must not stop the app — and must not be
    silent either, or a tester with no log has nothing to report."""
    blocker = tmp_path / 'blocked'
    blocker.write_text('not a directory', encoding='utf-8')

    assert configure(log_file=blocker / 'logs' / 'fthr.log') is False
    assert 'File logging unavailable' in capsys.readouterr().err


def test_log_file_lines_carry_time_level_thread_and_module(tmp_path):
    """Format is a feature: a save crosses three threads and an interleaved
    log without thread names cannot be read."""
    log_file = tmp_path / 'fthr.log'
    configure(log_file=log_file)
    get_logger('core.upload_manager').warning('retry 2/3')
    logging.getLogger().handlers[0].flush()

    line = log_file.read_text(encoding='utf-8').strip().splitlines()[-1]
    assert 'WARNING' in line
    assert 'core.upload_manager' in line
    assert 'retry 2/3' in line
    assert 'MainThread' in line

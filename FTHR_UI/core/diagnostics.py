"""Diagnostics: the logging the support strategy depends on (AUDIT-007).

The whole support strategy is "send us your log", so what is in that log is a
product feature, not housekeeping. Three things make it useful and this module
supplies all three:

1. **Structure.** Time, level, module and thread on every line. A clip save
   crosses the Qt main thread, a mux worker and an upload worker; without the
   thread name an interleaved log is unreadable.

2. **Redaction.** The upload feature stores a bearer token and posts to a
   user-supplied URL. A log that helps us debug an upload must never be a log
   the user cannot safely paste into a public issue.

3. **Restraint.** Several code paths poll at 50 ms. An error there must be
   reported once, not two hundred times a second, or the one interesting line
   is buried and the log rotates away before anyone reads it.

Relationship to `print()`. The app historically logged by printing, with
stdout/stderr teed into `~/.fthr/logs/fthr.log` (see `_LogTee` in main.py).
That file is what testers are told to send, so this module writes into the
*same* file rather than starting a second one. Existing `print()` calls keep
working and keep landing in the log; new diagnostics go through here.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import threading
import time
from pathlib import Path

#: Where testers are told to look. Same file the print-tee writes to.
LOG_DIR = Path.home() / '.fthr' / 'logs'
LOG_FILE = LOG_DIR / 'fthr.log'

_LOG_FORMAT = ('%(asctime)s %(levelname)-8s [%(threadName)s] '
               '%(name)s: %(message)s')
_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

_configured = False
_configure_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
#
# These run over anything heading for the log. They are deliberately
# conservative: a false positive costs a line of usefulness, a false negative
# costs a leaked credential in a public bug report.

_REDACTED = '<redacted>'

#: "Bearer eyJhbG..." / "token abc123" — the scheme is kept, the value is not.
_RE_BEARER = re.compile(
    r'\b(Bearer|Token|Basic|ApiKey)\s+\S+', re.IGNORECASE)

#: Header-ish "Authorization: <value>" in any serialised form.
_RE_HEADER = re.compile(
    r'\b(Authorization|Cookie|Set-Cookie|X-Api-Key|X-Auth-Token|Proxy-Authorization)'
    r'\s*[:=]\s*("[^"]*"|\'[^\']*\'|[^,;\s}\]]+)', re.IGNORECASE)

#: key=value / "key": "value" for secret-looking names.
_RE_KV = re.compile(
    r'\b(token|api_key|apikey|password|passwd|secret|auth|access_token|'
    r'refresh_token|upload_auth_header)\b(\s*["\']?\s*[:=]\s*)'
    r'("[^"]*"|\'[^\']*\'|[^,;&\s}\]]+)', re.IGNORECASE)

#: Credentials embedded in a URL's user-info section.
_RE_URL_CREDS = re.compile(r'(?<=://)([^/\s:@]+):([^/\s@]+)@')


def redact_secret(value) -> str:
    """Strip anything credential-shaped out of `value`.

    Safe to call on arbitrary text — a message, a URL, a repr of a dict. It
    never raises: a redactor that throws inside an error handler would replace
    the diagnostic it was protecting.
    """
    try:
        text = value if isinstance(value, str) else repr(value)
        text = _RE_URL_CREDS.sub(f'{_REDACTED}:{_REDACTED}@', text)
        # Order matters. "Authorization: Bearer eyJ..." holds the secret one
        # token *past* the header value, and the header pattern stops at the
        # first space — so redacting the header first leaves the credential
        # sitting in the log. Kill the scheme+value pair first, then the
        # header and key=value forms collapse what is left.
        text = _RE_BEARER.sub(lambda m: f'{m.group(1)} {_REDACTED}', text)
        text = _RE_HEADER.sub(lambda m: f'{m.group(1)}: {_REDACTED}', text)
        text = _RE_KV.sub(lambda m: f'{m.group(1)}{m.group(2)}{_REDACTED}', text)
        return text
    except Exception:  # pragma: no cover - defensive, see docstring
        return _REDACTED


def sanitize_url_for_log(url) -> str:
    """Reduce a URL to scheme://host/path — no query, no credentials.

    The query string is where upload tokens live, and the path can carry a
    clip name. Both are dropped rather than filtered, because guessing which
    query parameter is a secret is how secrets get logged.
    """
    try:
        text = url if isinstance(url, str) else str(url)
        text = _RE_URL_CREDS.sub(f'{_REDACTED}:{_REDACTED}@', text)
        base, sep, _query = text.partition('?')
        return base + ('?' + _REDACTED if sep else '')
    except Exception:  # pragma: no cover
        return _REDACTED


def sanitize_headers_for_log(headers) -> dict:
    """Return headers with every sensitive value replaced.

    Names are preserved — knowing that an Authorization header was *sent* is
    exactly the diagnostic that matters; its value never is.
    """
    sensitive = {'authorization', 'cookie', 'set-cookie', 'x-api-key',
                 'x-auth-token', 'proxy-authorization', 'api-key'}
    try:
        return {
            k: (_REDACTED if str(k).lower() in sensitive else redact_secret(v))
            for k, v in dict(headers).items()
        }
    except Exception:  # pragma: no cover
        return {'<headers>': _REDACTED}


class _RedactingFilter(logging.Filter):
    """Last line of defence: redact every record on its way to the file.

    Call sites are expected to redact deliberately. This catches the ones that
    forget — including third-party libraries logging a request URL.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_secret(record.msg)
            if record.args:
                def redact_arg(value):
                    # Preserve numeric types for logging placeholders such as
                    # %d and %.2f. Converting every argument to redacted text
                    # made otherwise valid health diagnostics fail to format.
                    if value is None or isinstance(value, (bool, int, float)):
                        return value
                    return redact_secret(value)

                if isinstance(record.args, dict):
                    record.args = {k: redact_arg(v)
                                   for k, v in record.args.items()}
                else:
                    record.args = tuple(redact_arg(a) for a in record.args)
        except Exception:  # pragma: no cover
            record.msg = _REDACTED
            record.args = None
        return True


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def configure(log_file: Path | None = None, level: int = logging.DEBUG) -> bool:
    """Attach a rotating file handler to the root logger. Idempotent.

    Returns True if file logging is active. Never raises: logging that
    prevents startup is worse than no logging, which is why the original
    `_setup_file_logging()` swallowed everything. The difference is that this
    reports the failure to stderr instead of vanishing.
    """
    global _configured
    with _configure_lock:
        if _configured:
            return True

        path = log_file or LOG_FILE
        root = logging.getLogger()
        root.setLevel(level)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=2 * 1024 * 1024, backupCount=2,
                encoding='utf-8', errors='replace', delay=True)
        except OSError as e:
            # Read-only home, full disk, sandboxed profile. The app still runs;
            # say so on stderr so a tester who finds no log knows why.
            print(f'[Diagnostics] File logging unavailable at {path}: '
                  f'{type(e).__name__}: {e}', file=sys.stderr)
            _configured = True
            return False

        handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        handler.addFilter(_RedactingFilter())
        root.addHandler(handler)
        _configured = True
        return True


def get_logger(name: str) -> logging.Logger:
    """Logger for a module. Use `get_logger(__name__)`."""
    return logging.getLogger(name)


def reset_for_tests() -> None:
    """Undo configure(). Tests only."""
    global _configured
    with _configure_lock:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:  # pragma: no cover
                pass
        _configured = False


# ---------------------------------------------------------------------------
# Restraint: report a recurring condition once, and report its recovery
# ---------------------------------------------------------------------------

class StateLogger:
    """Log a condition on transition, not on every poll.

    Several paths here poll at 50-500 ms. Logging a failure on each tick buries
    everything else and rotates the interesting history out of the file within
    minutes. This logs the first occurrence, stays silent while the condition
    persists, and logs a single recovery line with how long it lasted.

        engine = StateLogger(log, 'engine connection')
        ...
        engine.failed('shared memory not mapped')   # logged once
        engine.failed('shared memory not mapped')   # silent
        engine.ok()                                 # "recovered after 12.4 s"

    Not thread-safe by intent: each instance belongs to one polling loop.
    """

    def __init__(self, logger: logging.Logger, subject: str,
                 level: int = logging.WARNING,
                 recovery_level: int = logging.INFO,
                 clock=time.monotonic):
        self._log = logger
        self._subject = subject
        self._level = level
        self._recovery_level = recovery_level
        self._clock = clock
        self._failing = False
        self._since = 0.0
        self._detail = ''
        self._suppressed = 0

    @property
    def is_failing(self) -> bool:
        return self._failing

    def failed(self, detail: str = '', exc_info=None) -> bool:
        """Report the condition. Returns True if this call actually logged.

        A *changed* detail is logged again — "connection refused" turning into
        "layout mismatch" is new information, not repetition.
        """
        detail = redact_secret(detail) if detail else ''
        if self._failing and detail == self._detail:
            self._suppressed += 1
            return False
        first = not self._failing
        if first:
            self._since = self._clock()
        self._failing = True
        self._detail = detail
        suffix = f': {detail}' if detail else ''
        if first:
            self._log.log(self._level, '%s failed%s', self._subject, suffix,
                          exc_info=exc_info)
        else:
            self._log.log(self._level, '%s still failing, new reason%s',
                          self._subject, suffix, exc_info=exc_info)
        return True

    def ok(self) -> bool:
        """Report the condition as healthy. Logs only on recovery."""
        if not self._failing:
            return False
        elapsed = self._clock() - self._since
        suppressed = self._suppressed
        self._failing = False
        self._detail = ''
        self._suppressed = 0
        extra = (f' ({suppressed} repeat occurrences suppressed)'
                 if suppressed else '')
        self._log.log(self._recovery_level,
                      '%s recovered after %.1f s%s',
                      self._subject, elapsed, extra)
        return True


def log_unexpected(logger: logging.Logger, operation: str, exc: BaseException,
                   **context) -> None:
    """Report a failure that should not happen, with a stack trace.

    For category E — a violated invariant, an impossible state, a bug. The
    trace is the point: without it the report says something failed and gives
    no way to find out why.

    Expected failures (a missing optional tool, an already-deleted temp file)
    must NOT come through here; they belong at debug level without a trace, or
    the log stops being readable and people stop reading it.
    """
    detail = ' '.join(f'{k}={redact_secret(v)}' for k, v in context.items())
    logger.exception('%s failed unexpectedly%s',
                     operation, f' ({detail})' if detail else '')

"""Lustful API and local account helpers for the optional uploader package."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_BASE_URL = 'https://fthr.lustful.wtf'
API_KEY_ENV = 'FTHRCLIPS_UPLOAD_API_KEY'
CREDENTIALS_FILE = Path.home() / '.fthr' / 'uploader' / 'credentials.json'

# Lustful uses an application-level bearer token. Keeping the fallback inside
# the separately installed network package prevents it from entering Core.
_BUILTIN_API_KEY_PARTS = (
    'JcrPXRxFtGfSCYSfX4GG2k1QS54sSaPn',
    'ZafhMdpUmUyEvDswff3YfuJMNcRmc0fP',
)


class FthrApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = int(status or 0)


def resolve_api_key(explicit: str = '') -> str:
    return (
        explicit
        or os.environ.get(API_KEY_ENV, '')
        or ''.join(_BUILTIN_API_KEY_PARTS)
    ).strip()


def load_credentials(path: Path | None = None) -> dict[str, Any] | None:
    target = path or CREDENTIALS_FILE
    try:
        value = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    return value if value.get('account_id') and value.get('hwid') else None


def save_credentials(
        account_id: str,
        hardware_id: str,
        role: str = 'user',
        path: Path | None = None) -> dict[str, Any]:
    target = path or CREDENTIALS_FILE
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    value = {
        'account_id': account_id.strip(),
        'hwid': hardware_id.strip(),
        'role': (role or 'user').strip(),
        'verified_at': now,
        'lustful_terms_version': '2026-07-27',
        'lustful_privacy_version': '2026-07-27',
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(str(temporary), str(target))
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return value


def clear_credentials(path: Path | None = None) -> None:
    try:
        (path or CREDENTIALS_FILE).unlink()
    except FileNotFoundError:
        pass


class FthrUploadClient:
    """Small synchronous client for Lustful's documented JSON endpoints."""

    def __init__(
            self,
            api_key: str = '',
            base_url: str = DEFAULT_BASE_URL,
            opener: Callable[..., Any] | None = None,
            timeout: int = 120):
        self.api_key = resolve_api_key(api_key)
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip('/')
        self._opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def register(self, account_id: str, hardware_id: str) -> dict[str, Any]:
        return self._request_json(
            'POST', '/api/register',
            {'account_id': _account_id(account_id), 'hwid': _required(hardware_id, 'HWID')})

    def verify(self, account_id: str, hardware_id: str) -> dict[str, Any]:
        return self._request_json(
            'POST', '/api/verify',
            {'account_id': _account_id(account_id), 'hwid': _required(hardware_id, 'HWID')})

    def _request_json(
            self,
            method: str,
            endpoint: str,
            payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise FthrApiError(f'Lustful is not configured ({API_KEY_ENV} is missing).')
        if not self.base_url.startswith('https://'):
            raise FthrApiError('Lustful requests require HTTPS.')
        body = json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            f'{self.base_url}{endpoint}',
            data=body,
            method=method,
            headers={
                'Accept': 'application/json',
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'Content-Length': str(len(body)),
                'User-Agent': 'FTHR-Clips/Desktop-Uploader',
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status = int(getattr(response, 'status', 200))
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise FthrApiError(
                _api_error_message(int(exc.code), exc.read(), endpoint), int(exc.code)) from exc
        except urllib.error.URLError as exc:
            raise FthrApiError(f'Could not reach Lustful: {getattr(exc, "reason", exc)}') from exc
        except TimeoutError as exc:
            raise FthrApiError('Lustful timed out.') from exc
        data = _decode_json(raw)
        if not 200 <= status < 300 or not isinstance(data, dict):
            raise FthrApiError(_api_error_message(status, raw, endpoint), status)
        if data.get('ok') is False:
            raise FthrApiError(str(data.get('message') or data.get('error') or 'Request failed.'))
        return data


def _required(value: Any, label: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise FthrApiError(f'{label} is required.')
    return text


def _account_id(value: str) -> str:
    account_id = _required(value, 'Account ID')
    if len(account_id) > 128 or any(ord(char) < 32 for char in account_id):
        raise FthrApiError('Account ID is invalid.')
    return account_id


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode('utf-8', errors='replace'))
    except (ValueError, TypeError):
        return None


def _api_error_message(status: int, raw: bytes, endpoint: str) -> str:
    defaults = {
        ('/api/register', 400): 'Account ID and hardware ID are required.',
        ('/api/register', 409): 'That account ID or this PC is already registered.',
        ('/api/verify', 403): 'This account is registered to a different device.',
        ('/api/verify', 404): 'No Lustful account exists with that ID.',
    }
    if (endpoint, status) in defaults:
        return defaults[(endpoint, status)]
    data = _decode_json(raw)
    if isinstance(data, dict):
        message = data.get('message') or data.get('error')
        if message:
            return str(message)
    if status == 401:
        return 'Lustful rejected the app authorization.'
    return f'Lustful returned HTTP {status}.'

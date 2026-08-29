"""Provider networking owned exclusively by the optional uploader package."""

from __future__ import annotations

import http.client
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fthr_upload_api import (
    DEFAULT_BASE_URL,
    FthrUploadClient,
    load_credentials,
    resolve_api_key,
)


CATBOX_URL = 'https://catbox.moe/user/api.php'
HISTORY_FILE = Path.home() / '.fthr' / 'uploader' / 'upload_history.json'
_BOUNDARY = b'FTHRClipBoundary8f61d4'
_RETRY_DELAYS = (5, 15, 45)
_PROVIDER_LIMIT_BYTES = {
    'lustful': 100 * 1024 * 1024,
    'catbox': 200 * 1024 * 1024,
}


class UploadRuntime:
    def __init__(
            self,
            settings: dict[str, Any],
            hardware_id_provider: Callable[[], str] | None = None):
        self.settings = dict(settings)
        self._hardware_id_provider = hardware_id_provider

    def upload(self, file_path: str) -> tuple[bool, str, dict[str, Any]]:
        path = Path(file_path)
        if not path.is_file():
            return False, 'File no longer exists.', {}
        provider = self._provider()
        limit = _PROVIDER_LIMIT_BYTES[provider]
        try:
            actual = path.stat().st_size
        except OSError as exc:
            return False, f'File size could not be checked: {exc}', {}
        if actual > limit:
            return False, (
                f'{provider.title()} accepts files up to {limit // (1024 * 1024)} MB; '
                f'this file is {actual / (1024 * 1024):.1f} MB. '
                'Use Automatically compress in FTHR Clips.'), {}
        final_error = 'Upload failed.'
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                result = (
                    self._upload_catbox(path)
                    if provider == 'catbox'
                    else self._upload_lustful(path)
                )
                entry = {
                    'status': 'ok',
                    'provider': provider,
                    'uploaded_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    **result,
                }
                self._record_success(str(path), entry)
                if self.settings.get('upload_auto_delete', False):
                    try:
                        path.unlink()
                    except OSError as exc:
                        return True, f'Uploaded, but local delete failed: {exc}', entry
                return True, str(result.get('url') or 'Upload complete.'), entry
            except Exception as exc:
                final_error = str(exc)
                if delay is None:
                    break
                sys.stderr.write(
                    f'[Uploader] Attempt {attempt} failed; retrying in {delay}s: {exc}\n')
                time.sleep(delay)
        return False, final_error, {}

    def test_connection(self) -> tuple[bool, str]:
        provider = self._provider()
        if provider == 'lustful':
            credentials = load_credentials()
            if not credentials:
                return False, 'Create or log in to a Lustful account first.'
            hardware_id = self._hardware_id()
            if credentials.get('hwid') != hardware_id:
                return False, 'This Lustful account is registered to a different PC.'
            data = self._lustful_client().verify(str(credentials['account_id']), hardware_id)
            return True, f'Lustful connected ({data.get("role", "user")}).'
        request = urllib.request.Request(CATBOX_URL, method='HEAD')
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return True, f'Catbox reachable (HTTP {response.status}).'
        except urllib.error.HTTPError as exc:
            return True, f'Catbox reachable (HTTP {exc.code}).'
        except Exception as exc:
            return False, f'Could not reach Catbox: {exc}'

    def _provider(self) -> str:
        provider = str(self.settings.get('upload_provider', 'catbox')).lower()
        if provider == 'fthr':
            provider = 'lustful'
        if provider not in {'catbox', 'lustful'}:
            raise ValueError('Only Catbox and Lustful are supported.')
        return provider

    def _hardware_id(self) -> str:
        if not self._hardware_id_provider:
            raise PermissionError(
                'Install Hardware Identity after accepting Lustful’s policies.')
        return self._hardware_id_provider()

    def _lustful_client(self) -> FthrUploadClient:
        return FthrUploadClient(
            api_key=str(self.settings.get('lustful_api_key', '')),
            base_url=DEFAULT_BASE_URL,
        )

    def _upload_catbox(self, path: Path) -> dict[str, Any]:
        fields = {'reqtype': 'fileupload'}
        userhash = str(self.settings.get('catbox_userhash', '')).strip()
        if userhash:
            fields['userhash'] = userhash
        status, raw = _multipart_post(
            CATBOX_URL, fields, 'fileToUpload', path, headers={})
        text = raw.decode('utf-8', errors='replace').strip()
        if not 200 <= status < 300 or not text.startswith('https://'):
            raise RuntimeError(text or f'Catbox returned HTTP {status}.')
        return {'url': text, 'raw_url': text, 'favorite': False}

    def _upload_lustful(self, path: Path) -> dict[str, Any]:
        credentials = load_credentials()
        if not credentials:
            raise RuntimeError('Create or log in to a Lustful account first.')
        account_id = str(credentials.get('account_id', '')).strip()
        hardware_id = self._hardware_id()
        if credentials.get('hwid') != hardware_id:
            raise PermissionError('This Lustful account is registered to a different PC.')
        self._lustful_client().verify(account_id, hardware_id)
        status, raw = _multipart_post(
            f'{DEFAULT_BASE_URL}/api/upload',
            {},
            'file',
            path,
            headers={
                'Authorization': f'Bearer {resolve_api_key(str(self.settings.get("lustful_api_key", "")))}',
                'X-Account-Id': account_id,
                'Accept': 'application/json',
            },
        )
        try:
            data = json.loads(raw.decode('utf-8', errors='replace'))
        except ValueError as exc:
            raise RuntimeError(f'Lustful returned HTTP {status} with invalid JSON.') from exc
        if not 200 <= status < 300 or not isinstance(data, dict) or data.get('ok') is False:
            message = data.get('message') or data.get('error') if isinstance(data, dict) else ''
            raise RuntimeError(str(message or f'Lustful returned HTTP {status}.'))
        return {
            'url': str(data.get('url') or data.get('raw_url') or ''),
            'raw_url': str(data.get('raw_url') or ''),
            'file_id': data.get('id'),
            'expires_at': str(data.get('expires_at') or ''),
            'favorite': False,
        }

    def _record_success(self, path: str, entry: dict[str, Any]) -> None:
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding='utf-8'))
            if not isinstance(history, dict):
                history = {}
        except (OSError, ValueError, TypeError):
            history = {}
        history[path] = entry
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = HISTORY_FILE.with_suffix('.tmp')
        temporary.write_text(json.dumps(history, indent=2), encoding='utf-8')
        os.replace(str(temporary), str(HISTORY_FILE))


def _multipart_post(
        url: str,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
        headers: dict[str, str]) -> tuple[int, bytes]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Uploader provider URL must use HTTPS.')
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            b'--' + _BOUNDARY + b'\r\n'
            + f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8')
            + str(value).encode('utf-8') + b'\r\n')
    safe_name = file_path.name.replace('"', '_').replace('\\', '_')
    content_type = mimetypes.guess_type(file_path.name)[0] or 'application/octet-stream'
    file_header = (
        b'--' + _BOUNDARY + b'\r\n'
        + f'Content-Disposition: form-data; name="{file_field}"; '
          f'filename="{safe_name}"\r\n'.encode('utf-8')
        + f'Content-Type: {content_type}\r\n\r\n'.encode('ascii'))
    footer = b'\r\n--' + _BOUNDARY + b'--\r\n'
    content_length = sum(map(len, parts)) + len(file_header) + file_path.stat().st_size + len(footer)
    request_path = parsed.path or '/'
    if parsed.query:
        request_path += '?' + parsed.query
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=120)
    try:
        connection.putrequest('POST', request_path)
        connection.putheader(
            'Content-Type', f'multipart/form-data; boundary={_BOUNDARY.decode("ascii")}')
        connection.putheader('Content-Length', str(content_length))
        connection.putheader('User-Agent', 'FTHR-Clips/Desktop-Uploader')
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        for part in parts:
            connection.send(part)
        connection.send(file_header)
        with file_path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                connection.send(chunk)
        connection.send(footer)
        response = connection.getresponse()
        return int(response.status), response.read()
    finally:
        connection.close()

"""One-shot local service for the optional Lustful hardware-identity capability.

This is the only FTHR package allowed to read a stable operating-system machine
identifier. It derives a namespaced UUID locally and writes only that derived
value to stdout. It contains no networking code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any


PLUGIN_ID = 'com.fthrclips.hardware-identity'
PLUGIN_VERSION = '1.0.0'
POLICY_VERSION = 'lustful-2026-07-27-hwid-v1'
_HWID_NAMESPACE = uuid.UUID('04e2ab8c-113d-45ea-a375-480a876fbe36')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Hardware Identity activation receipt is invalid.')
    return value


def _validate_activation(receipt_path: Path, request: dict[str, Any]) -> None:
    if not receipt_path.is_file():
        raise PermissionError(
            'Hardware Identity has not been installed through Lustful consent.')
    receipt = _read_object(receipt_path)
    if receipt.get('plugin_id') != PLUGIN_ID:
        raise PermissionError('Hardware Identity activation belongs to another package.')
    if receipt.get('plugin_version') != PLUGIN_VERSION:
        raise PermissionError('Hardware Identity activation version does not match.')
    if receipt.get('policy_version') != POLICY_VERSION:
        raise PermissionError('Lustful hardware-identity consent must be renewed.')
    if request.get('activation_id') != receipt.get('activation_id'):
        raise PermissionError('Hardware Identity activation token is invalid.')
    expected = str(receipt.get('executable_sha256', '')).lower()
    if not expected or _sha256(Path(sys.executable)).lower() != expected:
        raise PermissionError('Hardware Identity executable integrity check failed.')


def _stable_machine_component() -> str:
    if sys.platform == 'win32':
        try:
            import winreg

            access = winreg.KEY_READ | getattr(winreg, 'KEY_WOW64_64KEY', 0)
            with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r'SOFTWARE\Microsoft\Cryptography',
                    0,
                    access) as key:
                machine_guid, _ = winreg.QueryValueEx(key, 'MachineGuid')
            value = str(machine_guid or '').strip().lower()
            if value:
                return f'windows-machine-guid:{value}'
        except (ImportError, OSError):
            pass
        raise RuntimeError('Could not read the stable Windows hardware identity.')

    for candidate in (Path('/etc/machine-id'), Path('/var/lib/dbus/machine-id')):
        try:
            value = candidate.read_text(encoding='utf-8').strip().lower()
        except OSError:
            continue
        if value:
            return f'machine-id:{value}'
    raise RuntimeError('Could not read a stable operating-system hardware identity.')


def hardware_uuid(component: str | None = None) -> str:
    """Return the derived UUID; ``component`` is an explicit unit-test seam."""
    fingerprint = component or _stable_machine_component()
    return str(uuid.uuid5(_HWID_NAMESPACE, fingerprint))


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--activation-receipt', required=True)
    args = parser.parse_args()
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise ValueError('Hardware Identity request must be a JSON object.')
        _validate_activation(
            Path(args.activation_receipt).expanduser().resolve(strict=False),
            request,
        )
        if request.get('action') != 'derive_hardware_id':
            raise ValueError('Unsupported Hardware Identity action.')
        response = {'ok': True, 'hardware_id': hardware_uuid()}
    except Exception as exc:
        response = {'ok': False, 'message': str(exc)}
    sys.stdout.write(json.dumps(response, separators=(',', ':')) + '\n')
    sys.stdout.flush()
    return 0 if response.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""IPC client for the separately installed Hardware Identity capability.

This uploader module does not read registry, machine-id, hostname, or network
adapter values. It can only request a derived ID from the independently
activated one-shot helper.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_ID = 'com.fthrclips.hardware-identity'
PLUGIN_VERSION = '1.0.0'
POLICY_VERSION = 'lustful-2026-07-27-hwid-v1'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _receipt(config: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    receipt_path = Path(str(config.get('receipt', ''))).expanduser().resolve(strict=False)
    value = json.loads(receipt_path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise PermissionError('Hardware Identity receipt is invalid.')
    if value.get('plugin_id') != PLUGIN_ID:
        raise PermissionError('Hardware Identity package ID does not match.')
    if value.get('plugin_version') != PLUGIN_VERSION:
        raise PermissionError('Hardware Identity package version does not match.')
    if value.get('policy_version') != POLICY_VERSION:
        raise PermissionError('Hardware Identity consent must be renewed.')
    if value.get('activation_id') != config.get('activation_id'):
        raise PermissionError('Hardware Identity activation token is invalid.')
    executable = Path(str(value.get('executable', '')))
    expected = str(value.get('executable_sha256', '')).lower()
    if not executable.is_file() or not expected or _sha256(executable).lower() != expected:
        raise PermissionError('Hardware Identity installation failed its integrity check.')
    return receipt_path, value


def derive_hardware_id(config: dict[str, Any]) -> str:
    receipt_path, receipt = _receipt(config)
    creation_flags = (
        getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0)
    completed = subprocess.run(
        [str(receipt['executable']), '--activation-receipt', str(receipt_path)],
        input=json.dumps({
            'action': 'derive_hardware_id',
            'activation_id': receipt['activation_id'],
        }),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        creationflags=creation_flags,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(
            completed.stderr.strip() or 'Hardware Identity returned no response.')
    try:
        response = json.loads(lines[-1])
    except ValueError as exc:
        raise RuntimeError('Hardware Identity returned an invalid response.') from exc
    if not isinstance(response, dict) or not response.get('ok'):
        raise RuntimeError(str(
            response.get('message', 'Hardware Identity request failed.')
            if isinstance(response, dict) else
            'Hardware Identity returned an invalid response.'))
    hardware_id = str(response.get('hardware_id', '')).strip()
    if not hardware_id:
        raise RuntimeError('Hardware Identity returned an empty identifier.')
    return hardware_id

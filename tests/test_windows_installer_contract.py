from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_installer_lifecycle_source_contract_passes():
    completed = subprocess.run(
        [sys.executable, 'tools/verify_windows_installer_lifecycle.py'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'stable existing AppId is retained' in completed.stdout
    assert 'installer never names the user clip root as a deletion target' in completed.stdout

from __future__ import annotations

import uuid
import os
from pathlib import Path
import subprocess
import sys

from core.instance_activation import (
    InstanceActivationServer,
    request_existing_instance_activation,
)


def test_second_launch_can_request_existing_window_activation(qtbot):
    server = InstanceActivationServer(f'FTHRClips_Test_{uuid.uuid4().hex}')
    received: list[bool] = []
    server.activation_requested.connect(lambda: received.append(True))

    assert server.start()
    try:
        ui_root = Path(__file__).resolve().parents[1] / 'FTHR_UI'
        child_code = (
            'import sys; '
            f'sys.path.insert(0, {str(ui_root)!r}); '
            'from PySide6.QtCore import QCoreApplication; '
            'app = QCoreApplication([]); '
            'from core.instance_activation import request_existing_instance_activation; '
            f'sys.exit(0 if request_existing_instance_activation({server._name!r}) else 1)'
        )
        child = subprocess.Popen(
            [sys.executable, '-c', child_code],
            env={**os.environ, 'PYTHONPATH': str(ui_root)},
        )
        try:
            qtbot.waitUntil(lambda: child.poll() is not None, timeout=2000)
            assert child.returncode == 0
            qtbot.waitUntil(lambda: received == [True], timeout=1000)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
    finally:
        server.stop()


def test_activation_request_fails_bounded_when_no_instance_owns_the_endpoint():
    assert not request_existing_instance_activation(
        f'FTHRClips_Missing_{uuid.uuid4().hex}', timeout_ms=10)

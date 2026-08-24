from __future__ import annotations

from ui import capture_card_client


class _FakeStdin:
    def __init__(self):
        self.value = ''
        self.closed = False

    def write(self, value):
        self.value += value

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stdin = _FakeStdin()
        self.wait_calls = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_windows_capture_card_keeps_native_qt_platform(monkeypatch):
    captured = {}
    process = _FakeProcess()

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(capture_card_client.sys, 'platform', 'win32')
    monkeypatch.setattr(capture_card_client.subprocess, 'Popen', fake_popen)
    monkeypatch.setenv('QT_QPA_PLATFORM', 'windows')

    client = capture_card_client.CaptureCardClient()

    assert captured['env']['QT_QPA_PLATFORM'] == 'windows'
    client.close()
    assert process.stdin.value == 'quit\n'
    assert process.stdin.closed


def test_close_does_not_respawn_an_already_dead_card(monkeypatch):
    launches = 0

    def fake_popen(*args, **kwargs):
        nonlocal launches
        launches += 1
        return _FakeProcess(returncode=1)

    monkeypatch.setattr(capture_card_client.subprocess, 'Popen', fake_popen)
    client = capture_card_client.CaptureCardClient()

    client.close()

    assert launches == 1
    assert client._proc is None

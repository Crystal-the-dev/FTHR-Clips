"""AUDIT-050 isolated QAudioSink output smoke test."""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices


def main() -> int:
    app = QCoreApplication(sys.argv)
    device = QMediaDevices.defaultAudioOutput()
    fmt = QAudioFormat()
    fmt.setSampleRate(48_000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    result = {
        "device_null": device.isNull(),
        "device_description": device.description(),
        "format_valid": fmt.isValid(),
        "format_supported": device.isFormatSupported(fmt),
        "state_before": None,
        "error_before": None,
    }
    if not device.isNull():
        sink = QAudioSink(device, fmt)
        result["state_before"] = sink.state().name
        result["error_before"] = sink.error().name
        sink.start()
        result["state_after_start"] = sink.state().name
        result["error_after_start"] = sink.error().name
        QTimer.singleShot(100, lambda: (sink.stop(), app.quit()))
        QTimer.singleShot(500, app.quit)
        app.exec()
        result["state_after_stop"] = sink.state().name
        result["error_after_stop"] = sink.error().name
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

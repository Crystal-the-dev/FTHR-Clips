"""AUDIT-050 isolated Qt track-selection probe.

This intentionally does not touch the production viewer. It records what the
pinned PySide6/QMediaPlayer backend exposes for a multitrack file and which
audio track is active by default.
"""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtMultimedia import QMediaMetaData, QMediaPlayer


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: qmediaplayer_probe.py <media-file>", file=sys.stderr)
        return 64

    app = QCoreApplication(sys.argv)
    player = QMediaPlayer()
    result: dict[str, object] = {
        "pyside6": __import__("PySide6").__version__,
        "audio_tracks": [],
        "active_audio_track": None,
        "media_status": None,
        "error": None,
    }

    def read_tracks() -> None:
        tracks: list[dict[str, object]] = []
        for index, metadata in enumerate(player.audioTracks()):
            row: dict[str, object] = {"index": index}
            for label, key in (
                ("title", QMediaMetaData.Key.Title),
                ("language", QMediaMetaData.Key.Language),
                ("description", QMediaMetaData.Key.Description),
            ):
                try:
                    value = metadata.value(key)
                except Exception:
                    value = None
                if value is not None and str(value):
                    row[label] = str(value)
            tracks.append(row)
        result["audio_tracks"] = tracks
        result["active_audio_track"] = player.activeAudioTrack()
        QTimer.singleShot(100, app.quit)

    def record_status(status: QMediaPlayer.MediaStatus) -> None:
        result["media_status"] = status.name

    def record_error(*_args: object) -> None:
        result["error"] = player.errorString()

    player.tracksChanged.connect(read_tracks)
    player.mediaStatusChanged.connect(record_status)
    player.errorOccurred.connect(record_error)
    player.setSource(QUrl.fromLocalFile(sys.argv[1]))
    QTimer.singleShot(6000, app.quit)
    app.exec()
    result["final_audio_track_count"] = len(player.audioTracks())
    result["final_active_audio_track"] = player.activeAudioTrack()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

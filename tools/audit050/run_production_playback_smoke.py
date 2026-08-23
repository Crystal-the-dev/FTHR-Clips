"""Exercise production FFmpeg->QAudioSink playback with a real local clip."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'FTHR_UI'))

from PySide6.QtCore import QCoreApplication, QTimer

from core.ffmpeg_playback import (
    FFmpegPlaybackController,
    PlaybackError,
    discover_playback_sources,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('media', type=Path)
    parser.add_argument('--milliseconds', type=int, default=1500)
    args = parser.parse_args()
    if not args.media.is_file():
        parser.error(f'media does not exist: {args.media}')
    try:
        sources = discover_playback_sources(str(args.media))
    except PlaybackError as error:
        print(json.dumps({'ready': False, 'error': str(error)}))
        return 2
    app = QCoreApplication([])
    report = {'sources': [source.display_name for source in sources],
              'ready': False, 'audio_error': None}
    try:
        controller = FFmpegPlaybackController(str(args.media), sources, app)
    except PlaybackError as error:
        print(json.dumps({**report, 'error': str(error)}))
        return 2

    def _ready(ready: bool, detail: str) -> None:
        report['ready'] = ready
        report['ready_detail'] = detail
        if ready:
            controller.play(0)

    controller.ready_changed.connect(_ready)
    controller.audio_failed.connect(lambda detail: report.update(audio_error=detail))

    def _finish() -> None:
        report['estimated_position_ms'] = controller._worker.estimated_position_ms
        report['queued_frames'] = controller._queue.frames
        report['queue_underruns'] = controller._queue.underruns
        report['sink_state'] = (controller._sink.state().name
                                if controller._sink is not None else 'not-started')
        controller.stop()
        print(json.dumps(report, sort_keys=True))
        app.quit()

    QTimer.singleShot(max(100, args.milliseconds), _finish)
    app.exec()
    return 0 if report['ready'] and not report['audio_error'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""
Standalone host process for the CaptureCard widget.

Spawned by CaptureCardClient; receives one-line commands on stdin and shows
the card in its own Qt event loop — completely isolated from the main process.

Protocol (one UTF-8 line per command):
    clip|<duration_s>|<fps>|<resolution>
    screenshot
    error[|<detail>]
    upload[|<filename>]
    prompt|<text>
    quit
"""

import sys
import os

# Add project root to sys.path so ui/ and core/ modules resolve correctly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread, Signal

from ui.app_style import apply_app_style, configure_qt_for_linux_ui
from ui.capture_card import CaptureCard


class _StdinReader(QThread):
    """Reads lines from stdin on a background thread and emits them as signals."""
    command   = Signal(str)
    eof_ready = Signal()   # fired when stdin closes (parent process died)

    def run(self):
        try:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    self.command.emit(line)
        except Exception:
            pass
        self.eof_ready.emit()


def main():
    configure_qt_for_linux_ui()
    app = QApplication(sys.argv)
    apply_app_style(app)
    card = CaptureCard()

    def handle(cmd: str):
        if cmd == 'quit':
            app.quit()
        elif cmd == 'screenshot':
            card.show_screenshot()
        elif cmd.startswith('clip|'):
            parts = cmd.split('|', 3)
            if len(parts) == 4:
                try:
                    card.show_clip(int(parts[1]), int(parts[2]), parts[3])
                except ValueError:
                    pass
        elif cmd.startswith('error'):
            detail = cmd[6:] if len(cmd) > 6 else ''
            card.show_error(detail)
        elif cmd.startswith('upload'):
            filename = cmd[7:] if len(cmd) > 7 else ''
            card.show_upload(filename)
        elif cmd.startswith('prompt|'):
            text = cmd[7:]
            card.show_prompt(text)

    reader = _StdinReader()
    reader.command.connect(handle)
    reader.eof_ready.connect(app.quit)   # exit when parent process closes stdin
    reader.start()

    ret = app.exec()
    reader.wait(1000)
    sys.exit(ret)


if __name__ == '__main__':
    main()

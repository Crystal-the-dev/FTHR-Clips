"""Shared pytest setup.

The app is not an installed package — `FTHR_UI` is a plain source directory
that main.py runs from, and PyInstaller is pointed at it via `pathex`. Tests
therefore have to put it on sys.path themselves. Every test module used to do
its own `sys.path.insert`, which works but makes import order load-bearing.
Doing it once here is the same trick, just in one place.

Existing per-module inserts are harmless duplicates and were left alone.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Widget tests construct real QWidgets. On a headless CI runner Qt aborts
# without a platform plugin; offscreen is the supported answer and is harmless
# on a desktop. setdefault so a developer can still force xcb/windows to watch
# a test render.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

for _p in (ROOT / 'FTHR_UI', ROOT / 'tools'):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

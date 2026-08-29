"""Transactional, collision-safe PNG publication for screenshots.

Screenshots are captured on the Qt GUI thread because QScreen requires it.  PNG
encoding and filesystem I/O happen in :class:`ScreenshotPngSaveWorker`, and a
completed image is only made visible at its final name after its staged file is
fully written.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage


class _PngWritable(Protocol):
    def save(self, file_name: str, fmt: str) -> bool: ...


class ScreenshotSaveError(RuntimeError):
    """A user-actionable screenshot save failure."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f'{code}: {detail}')


@dataclass(frozen=True)
class ScreenshotPaths:
    """A final PNG name and its same-directory, non-library staging name."""

    final: Path
    staged: Path


def _ensure_output_directory(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ScreenshotSaveError(
            'OUTPUT_DIRECTORY_UNAVAILABLE',
            f'Could not create the screenshot folder: {error}',
        ) from error
    if not directory.is_dir():
        raise ScreenshotSaveError(
            'OUTPUT_DIRECTORY_UNAVAILABLE',
            f'Screenshot output path is not a directory: {directory}',
        )


def _next_available_path(directory: Path, stem: str) -> Path:
    candidate = directory / f'{stem}.png'
    suffix = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = directory / f'{stem}_{suffix:02d}.png'
        suffix += 1
    return candidate


def _create_staged_path(final_path: Path) -> Path:
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f'.{final_path.stem}.',
            suffix='.png.partial',
            dir=final_path.parent,
        )
        os.close(descriptor)
    except OSError as error:
        raise ScreenshotSaveError(
            'OUTPUT_DIRECTORY_UNAVAILABLE',
            f'Could not create a staged screenshot file: {error}',
        ) from error
    return Path(temporary)


def reserve_screenshot_paths(
    directory: str | Path,
    *,
    now: datetime | None = None,
    stem: str | None = None,
) -> ScreenshotPaths:
    """Reserve a collision-safe final name and a controlled staging file.

    The final filename is readable and timestamped down to microseconds.  The
    sequential suffix is deterministic for the single-instance app if a file
    with the same timestamp already exists.  The actual temporary filename is
    allocated by the OS in the configured output directory.
    """

    output_directory = Path(directory)
    _ensure_output_directory(output_directory)
    timestamp = (now or datetime.now()).strftime('%Y%m%d_%H%M%S_%f')
    final_stem = stem or f'screenshot_from_{timestamp}'
    final_path = _next_available_path(output_directory, final_stem)
    return ScreenshotPaths(final=final_path, staged=_create_staged_path(final_path))


def reserve_cropped_screenshot_paths(final_path: str | Path) -> ScreenshotPaths:
    """Reserve a sibling output for the editor's existing cropped-image mode."""

    final = Path(final_path)
    _ensure_output_directory(final.parent)
    cropped_stem = f'{final.stem}_cropped'
    cropped_final = _next_available_path(final.parent, cropped_stem)
    return ScreenshotPaths(
        final=cropped_final,
        staged=_create_staged_path(cropped_final),
    )


def write_png_to_staged(image: _PngWritable, staged_path: str | Path) -> None:
    """Encode one complete PNG into a non-final, same-directory staging file."""

    staged = Path(staged_path)
    try:
        saved = image.save(str(staged), 'PNG')
    except Exception as error:
        staged.unlink(missing_ok=True)
        raise ScreenshotSaveError(
            'IMAGE_ENCODE_FAILED', f'Could not encode the screenshot: {error}') from error
    if not saved:
        staged.unlink(missing_ok=True)
        raise ScreenshotSaveError(
            'IMAGE_ENCODE_FAILED', 'Qt could not encode the screenshot as PNG.')


def publish_staged_png(staged_path: str | Path, final_path: str | Path) -> None:
    """Atomically expose a fully-written staged PNG at its final path.

    Windows rename is atomic and refuses an existing destination, including on
    external filesystems that do not support hard links. POSIX rename replaces
    by definition, so there a same-directory hard link publishes the fully
    closed staging inode only when the final name does not already exist. Both
    remain no-overwrite if another process races the reservation.
    """

    staged = Path(staged_path)
    final = Path(final_path)
    try:
        if os.name == 'nt':
            os.rename(staged, final)
            return
        os.link(staged, final)
    except FileExistsError as error:
        raise ScreenshotSaveError(
            'OUTPUT_COLLISION',
            f'Refusing to overwrite an existing file: {final.name}',
        ) from error
    except OSError as error:
        raise ScreenshotSaveError(
            'WRITE_FAILED', f'Could not publish the screenshot: {error}') from error
    try:
        staged.unlink()
    except OSError as error:
        # Roll back the exact link created above. A failure never leaves a
        # final-looking name while reporting that publication failed.
        final.unlink(missing_ok=True)
        raise ScreenshotSaveError(
            'WRITE_FAILED', f'Could not remove screenshot staging file: {error}'
        ) from error


class ScreenshotPngSaveWorker(QThread):
    """Encode a GUI-thread snapshot without blocking the UI event loop."""

    succeeded = Signal(str)
    failed = Signal(str, str)

    def __init__(self, image: QImage, staged_path: str | Path, parent=None):
        super().__init__(parent)
        # QImage is implicitly shared.  Make this worker's ownership explicit
        # before the QPixmap and GUI event handler continue.
        self._image = image.copy()
        self._staged_path = Path(staged_path)

    def run(self) -> None:
        try:
            write_png_to_staged(self._image, self._staged_path)
        except ScreenshotSaveError as error:
            self.failed.emit(error.code, error.detail)
        else:
            self.succeeded.emit(str(self._staged_path))

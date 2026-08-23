"""Clip filename contract and conservative stale-partial recovery."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


VIDEO_SUFFIXES = ('.mp4', '.mkv', '.avi')
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg')
PARTIAL_SUFFIX = '.partial'
_AUDIO_MANIFEST_SUFFIX = '.fthr-audio.json'
_FTHR_CLIP_MARKER = '_clip_from_'
DEFAULT_PARTIAL_MAX_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class PartialCleanupResult:
    """Files removed and secondary failures encountered during recovery."""

    removed: tuple[Path, ...]
    failures: tuple[tuple[Path, str], ...]


def is_partial_clip_path(path: str | Path) -> bool:
    """Return whether ``path`` uses FTHR's untrusted partial-file suffix."""

    return Path(path).name.casefold().endswith(PARTIAL_SUFFIX)


def is_completed_video_path(path: str | Path) -> bool:
    """Return whether ``path`` names a supported, completed video clip."""

    candidate = Path(path)
    return (
        not is_partial_clip_path(candidate)
        and candidate.suffix.casefold() in VIDEO_SUFFIXES
    )


def is_library_media_path(path: str | Path) -> bool:
    """Return whether a file may be discovered by the clip library."""

    candidate = Path(path)
    if is_partial_clip_path(candidate):
        return False
    suffix = candidate.suffix.casefold()
    return suffix in VIDEO_SUFFIXES or suffix in IMAGE_SUFFIXES


def is_fthr_owned_partial_path(path: str | Path) -> bool:
    """Return whether ``path`` matches the engine's FTHR clip temp naming."""

    name = Path(path).name.casefold()
    return name.endswith('.mp4.partial') and _FTHR_CLIP_MARKER in name


def is_fthr_owned_orphan_audio_manifest_path(path: str | Path) -> bool:
    """Return whether a sidecar can be safely recovered when its MP4 is absent.

    A paired native save publishes the manifest immediately before the MP4. A
    hard process kill in that narrow window leaves this harmless orphan. The
    marker requirement keeps third-party sidecars outside FTHR's ownership.
    """

    name = Path(path).name.casefold()
    return name.endswith('.mp4' + _AUDIO_MANIFEST_SUFFIX) and _FTHR_CLIP_MARKER in name


def cleanup_stale_partial_clips(
    clips_root: str | Path,
    *,
    minimum_age_seconds: float = DEFAULT_PARTIAL_MAX_AGE_SECONDS,
    now: float | None = None,
) -> PartialCleanupResult:
    """Remove old FTHR-owned partial clips without touching active/new files.

    Args:
        clips_root: Root of the FTHR clip library.
        minimum_age_seconds: Minimum file age required before removal.
        now: Optional wall-clock timestamp used by deterministic tests.

    Returns:
        Removed paths and non-fatal stat/removal failures.

    Raises:
        ValueError: If ``minimum_age_seconds`` is negative.
    """

    if minimum_age_seconds < 0:
        raise ValueError('minimum_age_seconds must be non-negative')

    root = Path(clips_root)
    if not root.is_dir():
        return PartialCleanupResult(removed=(), failures=())

    cutoff = (time.time() if now is None else now) - minimum_age_seconds
    removed: list[Path] = []
    failures: list[tuple[Path, str]] = []

    try:
        candidates = tuple(root.rglob('*.mp4.partial')) + tuple(
            root.rglob('*.mp4' + _AUDIO_MANIFEST_SUFFIX))
    except OSError as error:
        return PartialCleanupResult(
            removed=(), failures=((root, str(error)),))

    for candidate in candidates:
        is_partial = is_fthr_owned_partial_path(candidate)
        is_orphan_manifest = is_fthr_owned_orphan_audio_manifest_path(candidate)
        if (not is_partial and not is_orphan_manifest) or candidate.is_symlink():
            continue
        try:
            if not candidate.is_file() or candidate.stat().st_mtime > cutoff:
                continue
            if is_orphan_manifest:
                media = candidate.with_name(
                    candidate.name[:-len(_AUDIO_MANIFEST_SUFFIX)])
                if media.exists():
                    continue
            candidate.unlink()
            removed.append(candidate)
        except OSError as error:
            failures.append((candidate, str(error)))

    return PartialCleanupResult(
        removed=tuple(removed), failures=tuple(failures))

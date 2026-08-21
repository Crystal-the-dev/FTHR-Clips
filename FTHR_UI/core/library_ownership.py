"""Conservative ownership rules for destructive library actions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Iterable


class MediaOwnership(Enum):
    FTHR_OWNED = auto()
    LINKED_IMPORTED = auto()
    EXTERNAL = auto()


@dataclass(frozen=True)
class DeleteResult:
    deleted: bool
    ownership: MediaOwnership
    reason: str = ''


def _resolved(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.normpath(str(_resolved(path))))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        # pathlib uses ValueError to report that path is outside root.
        return False


def classify_media_path(
    path: str | os.PathLike[str],
    owned_root: str | os.PathLike[str],
    imported_roots: Iterable[str | os.PathLike[str]],
) -> MediaOwnership:
    resolved = _resolved(path)
    for imported_root in imported_roots:
        if _within(resolved, _resolved(imported_root)):
            return MediaOwnership.LINKED_IMPORTED
    if _within(resolved, _resolved(owned_root)):
        return MediaOwnership.FTHR_OWNED
    return MediaOwnership.EXTERNAL


def generic_delete_owned_media(
    path: str | os.PathLike[str],
    owned_root: str | os.PathLike[str],
    imported_roots: Iterable[str | os.PathLike[str]],
) -> DeleteResult:
    ownership = classify_media_path(path, owned_root, imported_roots)
    if ownership is not MediaOwnership.FTHR_OWNED:
        return DeleteResult(False, ownership, 'Linked or external media is protected')
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return DeleteResult(False, ownership, 'The clip no longer exists')
    except OSError as exc:
        return DeleteResult(False, ownership, str(exc))
    return DeleteResult(True, ownership)


def add_import_root(
    roots: Iterable[str | os.PathLike[str]], path: str | os.PathLike[str]
) -> list[str]:
    result = [str(_resolved(root)) for root in roots]
    keys = {_path_key(root) for root in result}
    resolved = str(_resolved(path))
    if _path_key(resolved) not in keys:
        result.append(resolved)
    return result


def remove_import_root(
    roots: Iterable[str | os.PathLike[str]], path: str | os.PathLike[str]
) -> list[str]:
    remove_key = _path_key(path)
    return [str(_resolved(root)) for root in roots if _path_key(root) != remove_key]

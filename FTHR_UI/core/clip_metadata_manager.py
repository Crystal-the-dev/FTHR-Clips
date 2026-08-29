from __future__ import annotations

import json
import os
import time
from pathlib import Path


_EDITOR_DRAFT_LIMIT = 250


class ClipMetadataManager:
    def __init__(self):
        self._file = Path.home() / '.fthr' / 'clip_metadata.json'
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self._file.read_text())
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self._data, indent=2))
        os.replace(tmp, self._file)

    def get(self, path: str) -> dict:
        entry = self._data.get(path, {})
        if not isinstance(entry, dict):
            entry = {}
        return {'tag': entry.get('tag', ''), 'description': entry.get('description', '')}

    def set(self, path: str, tag: str = None, description: str = None):
        entry = self._data.get(path)
        if not isinstance(entry, dict):
            entry = {}
            self._data[path] = entry
        if tag is not None:
            entry['tag'] = tag
        if description is not None:
            entry['description'] = description
        self._save()

    @staticmethod
    def _file_identity(path: str) -> dict | None:
        """Return the inexpensive identity used to reject stale edit drafts."""

        try:
            stat = os.stat(path)
        except OSError:
            # Missing/replaced media has no valid draft identity to return.
            return None
        return {'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}

    def get_editor_draft(self, path: str) -> dict | None:
        """Load a draft only when it still belongs to the same source file."""

        entry = self._data.get(path, {})
        draft = entry.get('editor_draft') if isinstance(entry, dict) else None
        if not isinstance(draft, dict) or draft.get('version') != 1:
            return None
        if draft.get('file') != self._file_identity(path):
            return None
        state = draft.get('state')
        return state if isinstance(state, dict) else None

    def set_editor_draft(self, path: str, state: dict) -> bool:
        """Atomically persist a small non-destructive clip-editor snapshot."""

        identity = self._file_identity(path)
        if identity is None or not isinstance(state, dict):
            return False
        entry = self._data.get(path)
        if not isinstance(entry, dict):
            entry = {}
            self._data[path] = entry
        entry['editor_draft'] = {
            'version': 1,
            'file': identity,
            'updated_at': time.time(),
            'state': state,
        }
        self._prune_editor_drafts()
        self._save()
        return True

    def _prune_editor_drafts(self) -> None:
        """Bound draft storage without touching tag/description metadata."""

        drafts = []
        for path, entry in self._data.items():
            if not isinstance(entry, dict):
                continue
            draft = entry.get('editor_draft')
            if not isinstance(draft, dict):
                continue
            updated = draft.get('updated_at', 0)
            drafts.append((updated if isinstance(updated, (int, float)) else 0, path))
        drafts.sort(reverse=True)
        for _updated, stale_path in drafts[_EDITOR_DRAFT_LIMIT:]:
            stale_entry = self._data[stale_path]
            stale_entry.pop('editor_draft', None)
            if not stale_entry:
                self._data.pop(stale_path, None)

    def clear_editor_draft(self, path: str) -> None:
        """Remove an editor draft while preserving tags and descriptions."""

        entry = self._data.get(path)
        if not isinstance(entry, dict) or 'editor_draft' not in entry:
            return
        del entry['editor_draft']
        if not entry:
            self._data.pop(path, None)
        self._save()

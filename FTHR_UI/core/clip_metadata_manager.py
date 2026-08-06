from __future__ import annotations
import json
import os
from pathlib import Path

class ClipMetadataManager:
    def __init__(self):
        self._file = Path.home() / '.fthr' / 'clip_metadata.json'
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self._file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self._data, indent=2))
        os.replace(tmp, self._file)

    def get(self, path: str) -> dict:
        entry = self._data.get(path, {})
        return {'tag': entry.get('tag', ''), 'description': entry.get('description', '')}

    def set(self, path: str, tag: str = None, description: str = None):
        entry = self._data.setdefault(path, {})
        if tag is not None:
            entry['tag'] = tag
        if description is not None:
            entry['description'] = description
        self._save()

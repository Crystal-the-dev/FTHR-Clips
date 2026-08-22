"""AUDIT-050 MP4 + manifest publication fault-injection probe.

The probe uses temporary files and a content hash. It models publication and
startup readiness; it does not change AUDIT-028 production helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def ready(media: Path, manifest: Path) -> bool:
    if not media.is_file() or not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        payload.get("version") == 1
        and payload.get("media_sha256") == sha256(media)
    )


def publish(root: Path, fail_at: str | None = None) -> tuple[bool, str]:
    media_stage = root / "clip.mp4.stage"
    manifest_stage = root / "clip.fthr-audio.json.stage"
    media = root / "clip.mp4"
    manifest = root / "clip.fthr-audio.json"
    media_stage.write_bytes(b"synthetic-audio-050")
    if fail_at == "media-write":
        return False, "media-write"
    fsync_file(media_stage)
    payload = {
        "version": 1,
        "media_sha256": sha256(media_stage),
        "audio_sources": [],
    }
    manifest_stage.write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )
    if fail_at == "manifest-write":
        return False, "manifest-write"
    fsync_file(manifest_stage)
    if fail_at == "before-manifest-rename":
        return False, "before-manifest-rename"
    os.replace(manifest_stage, manifest)
    if fail_at == "after-manifest-rename":
        return False, "after-manifest-rename"
    if fail_at == "before-media-rename":
        return False, "before-media-rename"
    os.replace(media_stage, media)
    if fail_at == "after-media-rename":
        return False, "after-media-rename"
    return True, "published"


def recover(root: Path) -> dict[str, object]:
    media = root / "clip.mp4"
    manifest = root / "clip.fthr-audio.json"
    stages = sorted(path.name for path in root.glob("*.stage"))
    return {
        "ready": ready(media, manifest),
        "media_exists": media.exists(),
        "manifest_exists": manifest.exists(),
        "staged_files": stages,
    }


def main() -> int:
    points = [
        None,
        "media-write",
        "manifest-write",
        "before-manifest-rename",
        "after-manifest-rename",
        "before-media-rename",
        "after-media-rename",
    ]
    rows: list[dict[str, object]] = []
    for point in points:
        with tempfile.TemporaryDirectory(prefix="audit050-publish-") as path:
            root = Path(path)
            published, phase = publish(root, point)
            state = recover(root)
            rows.append(
                {
                    "fault": point or "none",
                    "publish_returned": published,
                    "phase": phase,
                    **state,
                }
            )
    print(json.dumps({"cases": rows}, indent=2, sort_keys=True))
    complete = {"none", "after-media-rename"}
    return 0 if all((row["fault"] in complete) == row["ready"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Selection policy for the native combined-audio finalization pass.

The native capture engine can retain system, microphone and application
streams in one clip.  Combined mode must collapse only the streams that are
not already represented by the recorded Default Mix.  This module is kept
Qt-free so the policy can be tested without constructing the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AudioStreamDescriptor:
    """An audio stream with both its container and audio-ordinal indexes."""

    container_index: int
    audio_index: int
    title: str | None = None
    handler_name: str | None = None


_SYSTEM_LABELS = {'system audio', 'default mix', 'system'}
_MICROPHONE_LABELS = {'microphone', 'mic', 'mic audio'}


def _source_type(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ''
    return str(entry.get('source_type') or '').strip().casefold()


def _manifest_stream_indexes(manifest: dict[str, Any]) -> set[int]:
    """Return verified manifest indexes for the system/mic base sources."""
    selected: set[int] = set()
    for entry in manifest.get('sources', ()):
        if _source_type(entry) not in {'system', 'microphone'}:
            continue
        if isinstance(entry, dict) and isinstance(entry.get('stream_index'), int):
            selected.add(entry['stream_index'])
    return selected


def _label_set(stream: AudioStreamDescriptor) -> set[str]:
    return {
        value.strip().casefold()
        for value in (stream.title, stream.handler_name)
        if isinstance(value, str) and value.strip()
    }


def select_combined_audio_streams(
    manifest: dict[str, Any] | None,
    streams: Iterable[AudioStreamDescriptor],
) -> tuple[int, ...]:
    """Select audio ordinals that may be mixed into a combined track.

    A validated FTHR manifest is authoritative.  If it contains only
    application stems, returning an empty selection prevents those stems from
    being summed on top of the Default Mix.  Legacy clips have no sidecar, so
    the stable stream labels are used first; only an entirely unlabeled legacy
    clip falls back to all audio streams for compatibility with older builds.
    """
    ordered = tuple(streams)
    if manifest is not None:
        manifest_indexes = _manifest_stream_indexes(manifest)
        return tuple(stream.audio_index for stream in ordered
                     if stream.container_index in manifest_indexes)

    labeled: list[int] = []
    for stream in ordered:
        labels = _label_set(stream)
        if labels & (_SYSTEM_LABELS | _MICROPHONE_LABELS):
            labeled.append(stream.audio_index)
    if labeled:
        return tuple(labeled)
    return tuple(stream.audio_index for stream in ordered)


def descriptors_from_ffprobe_streams(
    streams: Iterable[dict[str, Any]],
) -> tuple[AudioStreamDescriptor, ...]:
    """Convert ffprobe's stream objects into stable audio ordinals."""
    result: list[AudioStreamDescriptor] = []
    audio_index = 0
    for stream in streams:
        if not isinstance(stream, dict) or stream.get('codec_type') != 'audio':
            continue
        container_index = stream.get('index')
        if not isinstance(container_index, int):
            continue
        tags = stream.get('tags')
        tags = tags if isinstance(tags, dict) else {}
        result.append(AudioStreamDescriptor(
            container_index=container_index,
            audio_index=audio_index,
            title=tags.get('title') if isinstance(tags.get('title'), str) else None,
            handler_name=(tags.get('handler_name')
                          if isinstance(tags.get('handler_name'), str) else None),
        ))
        audio_index += 1
    return tuple(result)

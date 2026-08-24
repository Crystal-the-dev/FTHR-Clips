"""In-process FFmpeg playback feeding one Qt ``QAudioSink``.

``QMediaPlayer`` remains the viewer's video renderer and sole clock master.
This module owns no video clock and never invokes FFmpeg on Qt's UI thread:
one Python worker calls the small native bridge, which uses a single FFmpeg
demuxer and one decoder per selected stream.  A bounded PCM queue is the only
boundary between that worker and QAudioSink's pull callback.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Callable, Sequence

import numpy as np

from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from core.audio_manifest import read_manifest_for_media
from core.ffmpeg_tools import FFmpegUnavailable, get_ffprobe_exe
from core.playback_mix_model import (
    CANONICAL_CHANNELS,
    CANONICAL_SAMPLE_RATE,
    PlaybackSource,
    ProbedAudioStream,
    SourceMixState,
    build_playback_sources,
)


_NO_WINDOW = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
_FRAMES_PER_BLOCK = 1024
_MAX_QUEUED_FRAMES = CANONICAL_SAMPLE_RATE // 2


class PlaybackError(RuntimeError):
    """The clip can remain viewable, but editable audio is unavailable."""


class BoundedPCMQueue:
    """Small thread-safe audio queue; it never decodes in a Qt audio callback."""

    def __init__(self, bytes_per_frame: int):
        self._bytes_per_frame = bytes_per_frame
        self._data = bytearray()
        self._lock = threading.Lock()
        self.underruns = 0

    @property
    def frames(self) -> int:
        with self._lock:
            return len(self._data) // self._bytes_per_frame

    @property
    def byte_count(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def append(self, pcm: bytes) -> bool:
        if not pcm or len(pcm) % self._bytes_per_frame:
            return False
        with self._lock:
            if len(self._data) + len(pcm) > _MAX_QUEUED_FRAMES * self._bytes_per_frame:
                return False
            self._data.extend(pcm)
            return True

    def read(self, size: int) -> bytes:
        if size <= 0:
            return b''
        with self._lock:
            count = min(size, len(self._data))
            if count == 0:
                self.underruns += 1
                return b''
            data = bytes(self._data[:count])
            del self._data[:count]
            return data


class _AudioPullDevice(QIODevice):
    """Qt pull device whose callback touches only ``BoundedPCMQueue``."""

    def __init__(self, queue: BoundedPCMQueue, parent: QObject):
        super().__init__(parent)
        self._queue = queue
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def readData(self, maxlen: int) -> bytes:  # noqa: N802 - Qt virtual name
        return self._queue.read(maxlen)

    def writeData(self, _data: bytes, _maxlen: int) -> int:  # noqa: N802
        return -1

    def isSequential(self) -> bool:  # noqa: N802
        return True

    def bytesAvailable(self) -> int:  # noqa: N802
        return self._queue.byte_count + super().bytesAvailable()


def _bridge_candidates() -> tuple[Path, ...]:
    suffix = 'FTHRPlaybackMixer.dll' if sys.platform == 'win32' else 'libFTHRPlaybackMixer.so'
    candidates: list[Path] = []
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        candidates.extend((Path(bundle) / 'engine' / suffix, Path(bundle) / suffix))
    executable_dir = Path(sys.executable).resolve().parent
    candidates.extend((executable_dir / 'engine' / suffix, executable_dir / suffix))
    root = Path(__file__).resolve().parents[2]
    candidates.extend((
        root / 'FTHRcapture' / 'FTHRPlaybackMixer' / 'x64' / 'Release' / suffix,
        root / 'FTHRcapture_linux' / 'build' / suffix,
    ))
    return tuple(candidates)


def _load_bridge() -> ctypes.CDLL:
    errors: list[str] = []
    source_root = Path(__file__).resolve().parents[2]
    for path in _bridge_candidates():
        if not path.is_file():
            continue
        try:
            if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
                # The bridge and FFmpeg runtime are either together in the
                # frozen ``engine`` directory or beside the development engine.
                for directory in (path.parent,
                                  source_root / 'FTHRcapture' / 'x64' / 'Release',
                                  source_root / 'FTHRcapture' / 'FTHRclips'
                                  / 'third_party' / 'ffmpeg' / 'bin'):
                    if directory.is_dir():
                        os.add_dll_directory(str(directory))
            return ctypes.CDLL(str(path))
        except OSError as error:
            errors.append(f'{path.name}: {error}')
    detail = '; '.join(errors) or 'bridge binary was not built or bundled'
    raise PlaybackError(f'In-process audio mixer unavailable: {detail}')


class InProcessFFmpegMixer:
    """``ctypes`` ownership wrapper around the native in-process FFmpeg bridge."""

    def __init__(self, media_path: str, sources: Sequence[PlaybackSource]):
        selected = [source for source in sources
                    if source.available and source.container_index is not None]
        if not selected:
            raise PlaybackError('This clip has no decodable audio stream.')
        self.sources = tuple(selected)
        self._library = _load_bridge()
        self._configure_signatures()
        indexes = (ctypes.c_int * len(self.sources))(
            *(source.container_index for source in self.sources))
        error = ctypes.create_string_buffer(512)
        self._handle = self._library.fthr_playback_open(
            os.fsencode(media_path), indexes, len(self.sources), error, len(error))
        if not self._handle:
            raise PlaybackError(error.value.decode(errors='replace') or 'could not open clip audio')

    def _configure_signatures(self) -> None:
        library = self._library
        library.fthr_playback_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
                                               ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t]
        library.fthr_playback_open.restype = ctypes.c_void_p
        library.fthr_playback_pull.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
                                               ctypes.c_int, ctypes.POINTER(ctypes.c_float),
                                               ctypes.c_int, ctypes.c_float]
        library.fthr_playback_pull.restype = ctypes.c_int
        library.fthr_playback_seek.argtypes = [ctypes.c_void_p, ctypes.c_int64,
                                               ctypes.c_char_p, ctypes.c_size_t]
        library.fthr_playback_seek.restype = ctypes.c_int
        library.fthr_playback_source_failed.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.fthr_playback_source_failed.restype = ctypes.c_int
        library.fthr_playback_close.argtypes = [ctypes.c_void_p]
        library.fthr_playback_close.restype = None

    def pull(self, frames: int, gains: Sequence[float], master_gain: float) -> bytes | None:
        output = (ctypes.c_float * (frames * CANONICAL_CHANNELS))()
        native_gains = (ctypes.c_float * len(gains))(*gains)
        result = self._library.fthr_playback_pull(
            self._handle, output, frames, native_gains, len(gains), master_gain)
        if result < 0:
            raise PlaybackError('FFmpeg decoder failed while mixing clip audio')
        if result == 0:
            return None
        return ctypes.string_at(output, result * CANONICAL_CHANNELS * ctypes.sizeof(ctypes.c_float))

    def seek(self, position_ms: int) -> None:
        error = ctypes.create_string_buffer(512)
        if not self._library.fthr_playback_seek(
                self._handle, max(0, position_ms), error, len(error)):
            raise PlaybackError(error.value.decode(errors='replace') or 'could not seek clip audio')

    def failed_source_indexes(self) -> tuple[int, ...]:
        return tuple(index for index in range(len(self.sources))
                     if self._library.fthr_playback_source_failed(self._handle, index))

    def close(self) -> None:
        if getattr(self, '_handle', None):
            self._library.fthr_playback_close(self._handle)
            self._handle = None


def probe_audio_streams(media_path: str) -> tuple[ProbedAudioStream, ...]:
    """Read only container metadata once; FFmpeg decode remains in-process."""

    try:
        probe = get_ffprobe_exe()
    except FFmpegUnavailable as error:
        raise PlaybackError(str(error)) from error
    try:
        result = subprocess.run(
            [probe, '-v', 'error', '-show_entries',
             'stream=index,codec_type:stream_tags=title,handler_name',
             '-of', 'json', media_path],
            capture_output=True, text=True, timeout=4, **_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as error:
        raise PlaybackError(f'could not inspect clip audio streams: {error}') from error
    if result.returncode != 0:
        raise PlaybackError('could not inspect clip audio streams')
    try:
        raw_streams = json.loads(result.stdout).get('streams', [])
    except (TypeError, json.JSONDecodeError) as error:
        raise PlaybackError('clip stream metadata was not valid JSON') from error
    streams: list[ProbedAudioStream] = []
    for stream in raw_streams:
        if stream.get('codec_type') != 'audio' or not isinstance(stream.get('index'), int):
            continue
        tags = stream.get('tags') if isinstance(stream.get('tags'), dict) else {}
        title = tags.get('title') or tags.get('handler_name')
        streams.append(ProbedAudioStream(
            container_index=stream['index'], audio_index=len(streams),
            title=title if isinstance(title, str) else None))
    return tuple(streams)


def discover_playback_sources(media_path: str) -> tuple[PlaybackSource, ...]:
    """Combine a valid FTHR sidecar, if any, with actual container streams."""

    return build_playback_sources(read_manifest_for_media(media_path),
                                  probe_audio_streams(media_path))


class _DecodeWorker(threading.Thread):
    """The only Python thread allowed to call native FFmpeg playback APIs."""

    def __init__(self, media_path: str, sources: Sequence[PlaybackSource],
                 queue: BoundedPCMQueue,
                 encode_for_device: Callable[[bytes], bytes],
                 mix_values: Callable[[], tuple[list[float], float]],
                 ready: Callable[[str | None], None],
                 failed: Callable[[str], None], source_failed: Callable[[str], None],
                 eof: Callable[[], None]):
        super().__init__(name='FTHR-audio-decode', daemon=True)
        self._media_path = media_path
        self._sources = tuple(sources)
        self._queue = queue
        self._encode_for_device = encode_for_device
        self._mix_values = mix_values
        self._ready = ready
        self._failed = failed
        self._source_failed = source_failed
        self._eof = eof
        self._condition = threading.Condition()
        self._playing = False
        self._stopping = False
        self._seek_ms: int | None = 0
        self._decoded_frames = 0
        self._reported_failed_sources: set[int] = set()

    def play(self, position_ms: int) -> None:
        with self._condition:
            self._seek_ms = max(0, position_ms)
            self._playing = True
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            self._playing = False
            self._condition.notify_all()

    def seek(self, position_ms: int) -> None:
        with self._condition:
            self._seek_ms = max(0, position_ms)
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._playing = False
            self._condition.notify_all()

    @property
    def estimated_position_ms(self) -> int:
        with self._condition:
            queued = self._queue.frames
            return max(0, round((self._decoded_frames - queued) * 1000 / CANONICAL_SAMPLE_RATE))

    def run(self) -> None:
        mixer: InProcessFFmpegMixer | None = None
        try:
            mixer = InProcessFFmpegMixer(self._media_path, self._sources)
            self._ready(None)
            while True:
                with self._condition:
                    while not self._stopping and not self._playing:
                        self._condition.wait()
                    if self._stopping:
                        return
                    seek_ms = self._seek_ms
                    self._seek_ms = None
                if seek_ms is not None:
                    self._queue.clear()
                    mixer.seek(seek_ms)
                    with self._condition:
                        self._decoded_frames = round(seek_ms * CANONICAL_SAMPLE_RATE / 1000)
                if self._queue.frames >= _MAX_QUEUED_FRAMES - _FRAMES_PER_BLOCK:
                    time.sleep(0.005)
                    continue
                gains, master = self._mix_values()
                block = mixer.pull(_FRAMES_PER_BLOCK, gains, master)
                for index in mixer.failed_source_indexes():
                    if index not in self._reported_failed_sources:
                        self._reported_failed_sources.add(index)
                        self._source_failed(self._sources[index].source_id)
                if block is None:
                    with self._condition:
                        self._playing = False
                    self._eof()
                    continue
                if self._queue.append(self._encode_for_device(block)):
                    with self._condition:
                        self._decoded_frames += _FRAMES_PER_BLOCK
        except PlaybackError as error:
            self._ready(str(error))
            self._failed(str(error))
        except Exception as error:  # Defensive: never take down the Qt event loop.
            self._ready(str(error))
            self._failed(f'audio playback stopped: {error}')
        finally:
            if mixer is not None:
                mixer.close()


class FFmpegPlaybackController(QObject):
    """Qt-facing controller for one clip's in-process editable audio."""

    ready_changed = Signal(bool, str)
    audio_failed = Signal(str)
    source_failed = Signal(str)
    reached_eof = Signal()

    def __init__(self, media_path: str, sources: Sequence[PlaybackSource], parent: QObject):
        super().__init__(parent)
        self.sources = tuple(source for source in sources if source.available)
        self._states = {source.source_id: SourceMixState() for source in self.sources}
        self._master_gain = 1.0
        self._mix_lock = threading.Lock()
        self._output_device, self._output_format, self._encode_for_device = self._select_output_format()
        self._queue = BoundedPCMQueue(self._output_format.bytesPerFrame())
        self._device = _AudioPullDevice(self._queue, self)
        self._sink: QAudioSink | None = None
        self._sink_timer = QTimer(self)
        self._sink_timer.setInterval(20)
        self._sink_timer.timeout.connect(self._keep_sink_running)
        self._worker = _DecodeWorker(
            media_path, self.sources, self._queue, self._encode_for_device, self._mix_values,
            self._worker_ready, self._worker_failed, self.source_failed.emit, self.reached_eof.emit)
        self._worker.start()

    def _select_output_format(
            self) -> tuple[object, QAudioFormat, Callable[[bytes], bytes]]:
        device = QMediaDevices.defaultAudioOutput()
        float_format = QAudioFormat()
        float_format.setSampleRate(CANONICAL_SAMPLE_RATE)
        float_format.setChannelCount(CANONICAL_CHANNELS)
        float_format.setSampleFormat(QAudioFormat.SampleFormat.Float)
        if device.isFormatSupported(float_format):
            return device, float_format, lambda pcm: pcm

        int16_format = QAudioFormat()
        int16_format.setSampleRate(CANONICAL_SAMPLE_RATE)
        int16_format.setChannelCount(CANONICAL_CHANNELS)
        int16_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isFormatSupported(int16_format):
            def _to_int16(pcm: bytes) -> bytes:
                samples = np.frombuffer(pcm, dtype='<f4')
                return (np.clip(samples, -1.0, 1.0) * 32767.0).astype('<i2').tobytes()
            return device, int16_format, _to_int16
        raise PlaybackError('Default output supports neither 48 kHz float nor 16-bit stereo audio.')

    def _mix_values(self) -> tuple[list[float], float]:
        with self._mix_lock:
            return ([self._states[source.source_id].gain for source in self.sources],
                    self._master_gain)

    def _worker_ready(self, error: str | None) -> None:
        self.ready_changed.emit(error is None, error or '')

    def _worker_failed(self, error: str) -> None:
        self.audio_failed.emit(error)

    def set_master_percent(self, value: int) -> None:
        with self._mix_lock:
            self._master_gain = max(0, min(100, value)) / 100.0

    def set_source_state(self, source_id: str, gain_percent: int | None = None,
                         muted: bool | None = None) -> None:
        with self._mix_lock:
            current = self._states.get(source_id, SourceMixState())
            self._states[source_id] = SourceMixState(
                gain_percent=current.gain_percent if gain_percent is None else gain_percent,
                muted=current.muted if muted is None else muted)

    def source_state(self, source_id: str) -> SourceMixState:
        with self._mix_lock:
            return self._states.get(source_id, SourceMixState())

    def play(self, position_ms: int) -> None:
        self._queue.clear()
        self._discard_output_buffer()
        self._worker.play(position_ms)
        self._sink_timer.start()

    def pause(self) -> None:
        self._worker.pause()
        self._queue.clear()
        self._discard_output_buffer()

    def seek(self, position_ms: int) -> None:
        self._queue.clear()
        self._discard_output_buffer()
        self._worker.seek(position_ms)

    def sync_to_video_position(self, position_ms: int) -> None:
        # QMediaPlayer is the sole master.  The worker estimate already removes
        # the Python queue, but QAudioSink owns another device buffer after it
        # pulls those bytes.  Treating that buffered audio as already played
        # made the controller seek unnecessarily and left stale pre-seek audio
        # queued in the device, which was audible as short clicks under load.
        if abs(self._estimated_output_position_ms() - position_ms) > 120:
            self.seek(position_ms)

    def _estimated_output_position_ms(self) -> int:
        buffered_frames = 0
        if self._sink is not None:
            try:
                capacity = max(0, int(self._sink.bufferSize()))
                free = max(0, min(capacity, int(self._sink.bytesFree())))
                bytes_per_frame = max(1, self._output_format.bytesPerFrame())
                buffered_frames = (capacity - free) // bytes_per_frame
            except (AttributeError, RuntimeError, TypeError, ValueError):
                # A device can disappear between the state check and these Qt
                # calls.  Error reporting remains owned by _on_sink_state().
                buffered_frames = 0
        buffered_ms = round(buffered_frames * 1000 / CANONICAL_SAMPLE_RATE)
        return max(0, self._worker.estimated_position_ms - buffered_ms)

    def _discard_output_buffer(self) -> None:
        if self._sink is not None:
            # reset(), unlike suspend(), discards bytes already accepted by the
            # platform backend.  The sink is restarted after fresh PCM arrives.
            self._sink.reset()

    def _keep_sink_running(self) -> None:
        if self._queue.frames == 0:
            return
        if self._sink is None:
            self._sink = QAudioSink(self._output_device, self._output_format, self)
            self._sink.stateChanged.connect(self._on_sink_state)
            self._sink.start(self._device)
        elif self._sink.state() == QAudio.State.SuspendedState:
            self._sink.resume()
        elif self._sink.state() == QAudio.State.IdleState:
            # An empty pull queue is an underrun, not a device-loss error.
            # Resume only after a worker-filled block exists; no decode runs in
            # the audio callback and no timing sleep is used for recovery.
            self._sink.start(self._device)
        elif (self._sink.state() == QAudio.State.StoppedState
              and self._sink.error() == QAudio.Error.NoError):
            # Play/pause/seek use reset() to discard stale device-buffered PCM.
            self._sink.start(self._device)

    def _on_sink_state(self, state: QAudio.State) -> None:
        if state != QAudio.State.StoppedState or self._sink is None:
            return
        if self._sink.error() in (QAudio.Error.OpenError, QAudio.Error.IOError,
                                  QAudio.Error.FatalError):
            self.audio_failed.emit('Audio output device stopped unexpectedly.')

    def stop(self) -> None:
        self._sink_timer.stop()
        self._queue.clear()
        self._worker.stop()
        self._worker.join(timeout=0.5)
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        self._device.close()

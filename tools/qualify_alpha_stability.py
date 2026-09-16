#!/usr/bin/env python3
"""Qualify Windows clipping with bounded runs and diagnostic artifacts.

Write outside the repository by default. Stop on a failed health check,
retaining engine logs, samples, and the JSON report.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from qualify_windows_hardware import (  # noqa: E402
    CAPTURE_HEALTH_BACKEND_FAILED,
    CODECS,
    CREATE_NO_WINDOW,
    CaptureBridge,
    build_engine_command,
    choose_monitor,
    connect_bridge,
    machine_inventory,
    process_cpu_seconds,
    process_memory_bytes,
    process_thread_count,
    verify_no_running_engine,
)
from core.windows_monitor import enumerate_windows_monitors  # noqa: E402


DEFAULT_WARMUP_SECONDS = 70
DEFAULT_SOAK_SECONDS = 15 * 60
DEFAULT_TOTAL_CAPTURE_SECONDS = 30 * 60
DEFAULT_STALL_TIMEOUT_SECONDS = 15.0
@dataclass(frozen=True)
class PacketPoint:
    pts: float | None
    duration: float | None
    flags: str = ""
    stream_index: int | None = None


@dataclass
class ClipResult:
    name: str
    requested_seconds: int
    save_latency_seconds: float
    file_size_bytes: int
    video_packets: int
    audio_packets: int
    video_duration_seconds: float | None
    audio_duration_seconds: float | None
    maximum_video_gap_seconds: float | None
    first_video_pts_seconds: float | None
    last_video_pts_seconds: float | None
    full_decode: bool
    video_timeline_valid: bool
    audio_duration_sane: bool
    partial_absent: bool
    publish_count: int
    validation_error: str | None = None


def run_checked(
    command: list[str], *, timeout: float = 120.0, cwd: Path | None = None,
) -> str:
    """Run a tool without shell interpolation and return stdout."""
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
        creationflags=CREATE_NO_WINDOW,
    )
    return completed.stdout


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", "N/A", "nan", "NaN"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_packet_points(payload: dict[str, Any], codec_type: str) -> list[PacketPoint]:
    """Extract packet PTS without sorting or silently filling missing values."""
    points: list[PacketPoint] = []
    for packet in payload.get("packets", []):
        if packet.get("codec_type") != codec_type:
            continue
        points.append(PacketPoint(
            _float_or_none(packet.get("pts_time")),
            _float_or_none(packet.get("duration_time")),
            str(packet.get("flags", "")),
            int(packet["stream_index"])
            if str(packet.get("stream_index", "")).isdigit() else None,
        ))
    return points


def timeline_health(
    packets: Iterable[PacketPoint],
    *,
    requested_seconds: int,
    expected_fps: int,
    max_gap_seconds: float | None = None,
) -> dict[str, Any]:
    """Validate real packet timestamps and report gaps.

    The packet order is retained.  Missing or backwards timestamps fail the
    check instead of being sorted or regenerated, which catches fake CFR
    expansion and sparse replay output.
    """
    points = list(packets)
    if not points:
        return {
            "valid": False, "reason": "no_video_packets", "packet_count": 0,
            "first_pts": None, "last_pts": None, "maximum_gap": None,
            "coverage_seconds": 0.0, "effective_fps": 0.0,
        }
    if any(point.pts is None for point in points):
        return {
            "valid": False, "reason": "video_packet_missing_pts",
            "packet_count": len(points), "first_pts": None, "last_pts": None,
            "maximum_gap": None, "coverage_seconds": 0.0,
            "effective_fps": 0.0,
        }
    timestamps = [point.pts for point in points]
    assert all(value is not None for value in timestamps)
    deltas = [
        right - left
        for left, right in zip(timestamps, timestamps[1:], strict=False)
    ]
    if any(delta <= 0 for delta in deltas):
        return {
            "valid": False, "reason": "video_pts_not_strictly_increasing",
            "packet_count": len(points), "first_pts": timestamps[0],
            "last_pts": timestamps[-1],
            "maximum_gap": max(deltas, default=0.0),
            "coverage_seconds": max(0.0, timestamps[-1] - timestamps[0]),
            "effective_fps": 0.0,
        }
    maximum_gap = max(deltas, default=0.0)
    coverage = max(0.0, timestamps[-1] - timestamps[0])
    effective_fps = ((len(points) - 1) / coverage) if coverage > 0 else 0.0
    # A short save can be partial at the start of a running ring, but it must
    # contain a real sequence.  Permit one frame less than the requested
    # interval and reject long frozen intervals in every clip.
    min_coverage = max(0.5, requested_seconds - max(1.0, 2.0 / max(expected_fps, 1)))
    permitted_gap = max_gap_seconds if max_gap_seconds is not None else max(
        0.25, 4.0 / max(expected_fps, 1))
    valid = coverage >= min_coverage and maximum_gap <= permitted_gap
    reason = None
    if coverage < min_coverage:
        reason = "video_coverage_short"
    elif maximum_gap > permitted_gap:
        reason = "video_gap_too_large"
    elif len(points) > 4 and effective_fps < max(1.0, expected_fps * 0.5):
        valid = False
        reason = "video_effective_rate_too_low"
    return {
        "valid": valid,
        "reason": reason,
        "packet_count": len(points),
        "first_pts": timestamps[0],
        "last_pts": timestamps[-1],
        "maximum_gap": maximum_gap,
        "coverage_seconds": coverage,
        "effective_fps": effective_fps,
    }


def _stream_duration(stream: dict[str, Any]) -> float | None:
    duration = _float_or_none(stream.get("duration"))
    if duration is not None:
        return duration
    tags = stream.get("tags") or {}
    return _float_or_none(tags.get("DURATION"))


def _packet_stream_duration(packets: list[PacketPoint]) -> float | None:
    timed_packets = [packet for packet in packets if packet.pts is not None]
    if not timed_packets:
        return None
    start = timed_packets[0].pts
    assert start is not None
    end = max(
        packet.pts + (packet.duration or 0.0)
        for packet in timed_packets
        if packet.pts is not None
    )
    return max(0.0, end - start)


def _duration_sane(
    video_duration: float | None,
    audio_duration: float | None,
    requested_seconds: int,
) -> bool:
    if video_duration is None:
        return False
    if audio_duration is None:
        return True
    # AAC priming is normally well below a quarter second.  Cap the allowance
    # at one second so a multi-second missing track cannot pass a long clip.
    tolerance = max(0.25, min(1.0, requested_seconds * 0.02))
    return abs(video_duration - audio_duration) <= tolerance


def strict_probe_clip(
    ffprobe: Path,
    ffmpeg: Path,
    path: Path,
    *,
    name: str,
    requested_seconds: int,
    save_latency: float,
    expected_fps: int,
    require_audio: bool = True,
    publish_count: int = 1,
) -> ClipResult:
    """Probe and fully decode a clip, failing on FFmpeg errors."""
    probe = json.loads(run_checked([
        str(ffprobe), "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path),
    ]))
    packet_payload = json.loads(run_checked([
        str(ffprobe), "-v", "error", "-show_packets",
        "-show_entries",
        "packet=codec_type,stream_index,pts_time,duration_time,flags", "-of", "json",
        str(path),
    ]))
    streams = probe.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise RuntimeError(f"{name}: no video stream")
    audio_streams = [
        stream for stream in streams if stream.get("codec_type") == "audio"
    ]
    video_packets = parse_packet_points(packet_payload, "video")
    audio_packets = parse_packet_points(packet_payload, "audio")
    health = timeline_health(
        video_packets,
        requested_seconds=requested_seconds,
        expected_fps=expected_fps,
    )
    video_duration = _stream_duration(video_stream)
    audio_durations: list[float | None] = []
    for stream in audio_streams:
        stream_index = stream.get("index")
        stream_packets = [
            packet for packet in audio_packets
            if stream_index is None
            or packet.stream_index == int(stream_index)
        ]
        audio_durations.append(
            _stream_duration(stream) or _packet_stream_duration(stream_packets))
    audio_duration = max(
        (duration or 0.0 for duration in audio_durations), default=0.0,
    ) or None
    audio_sane = (
        bool(audio_streams) if require_audio else True
    ) and all(
        duration is not None
        and _duration_sane(video_duration, duration, requested_seconds)
        for duration in audio_durations
    )
    partial_absent = not Path(f"{path}.partial").exists()
    decode_error: str | None = None
    try:
        # -xerror makes a decode warning fatal; -err_detect explode asks the
        # demuxer/decoder to reject recoverable corruption as well.
        run_checked([
            str(ffmpeg), "-v", "error", "-xerror", "-err_detect", "explode",
            "-i", str(path), "-map", "0", "-f", "null", "NUL",
        ], timeout=max(300.0, requested_seconds * 20.0))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        decode_error = str(error)
    valid = bool(health["valid"] and audio_sane and partial_absent and not decode_error)
    validation_error = decode_error
    if validation_error is None and not health["valid"]:
        validation_error = str(health["reason"])
    if validation_error is None and not audio_sane:
        validation_error = "audio_duration_mismatch"
    if validation_error is None and not partial_absent:
        validation_error = "partial_file_present"
    return ClipResult(
        name=name,
        requested_seconds=requested_seconds,
        save_latency_seconds=round(save_latency, 3),
        file_size_bytes=path.stat().st_size,
        video_packets=len(video_packets),
        audio_packets=len(audio_packets),
        video_duration_seconds=video_duration,
        audio_duration_seconds=audio_duration,
        maximum_video_gap_seconds=health["maximum_gap"],
        first_video_pts_seconds=health["first_pts"],
        last_video_pts_seconds=health["last_pts"],
        full_decode=decode_error is None,
        video_timeline_valid=bool(health["valid"]),
        audio_duration_sane=audio_sane,
        partial_absent=partial_absent,
        publish_count=publish_count,
        validation_error=validation_error if not valid else None,
    )


def process_handle_count(process: subprocess.Popen) -> int | None:
    """Return handles owned by the engine process when Windows exposes it."""
    if sys.platform != "win32":
        return None
    getter = getattr(ctypes.windll.kernel32, "GetProcessHandleCount", None)
    if getter is None:
        return None
    count = wintypes.DWORD()
    if not getter(wintypes.HANDLE(process._handle), ctypes.byref(count)):
        return None
    return int(count.value)


def process_snapshot(process: subprocess.Popen, now: float | None = None) -> dict[str, Any]:
    working_set, private_bytes = process_memory_bytes(process)
    return {
        "captured_monotonic": now if now is not None else time.monotonic(),
        "working_set_bytes": working_set,
        "private_bytes": private_bytes,
        "process_cpu_seconds": process_cpu_seconds(process),
        "process_thread_count": process_thread_count(process),
        "process_handle_count": process_handle_count(process),
    }


class QualificationSampler:
    """Bounded one-sample-per-interval process/engine health recorder."""

    def __init__(
        self,
        bridge: CaptureBridge,
        process: subprocess.Popen,
        path: Path,
        started_at: float | None = None,
    ):
        self.bridge = bridge
        self.process = process
        self.path = path
        self.started_at = started_at if started_at is not None else time.monotonic()
        self.samples: list[dict[str, Any]] = []
        self._last_progress_at = time.monotonic()
        self._last_frame_count: int | None = None

    def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        status = self.bridge.get_status()
        item = {
            "captured_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(now - self.started_at, 3),
            "status": status,
            **process_snapshot(self.process, now),
        }
        if self.samples:
            previous = self.samples[-1]
            wall_delta = now - float(previous["captured_monotonic"])
            cpu_now = item.get("process_cpu_seconds")
            cpu_before = previous.get("process_cpu_seconds")
            item["process_cpu_percent"] = (
                round(max(0.0, float(cpu_now) - float(cpu_before))
                / wall_delta * 100.0, 3)
                if wall_delta > 0 and cpu_now is not None and cpu_before is not None
                else None
            )
        else:
            item["process_cpu_percent"] = None
        frames = status.get("frames_captured")
        if (
            isinstance(frames, int)
            and (
                self._last_frame_count is None
                or frames > self._last_frame_count
            )
        ):
            self._last_progress_at = now
        self._last_frame_count = frames if isinstance(frames, int) else self._last_frame_count
        item["seconds_since_frame_progress"] = round(now - self._last_progress_at, 3)
        self.samples.append(item)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, separators=(",", ":")) + "\n")
        return item

    def check(
        self,
        *,
        max_stall_seconds: float,
        require_frame_progress: bool = True,
    ) -> dict[str, Any]:
        item = self.sample()
        if self.process.poll() is not None:
            raise RuntimeError(f"engine exited with code {self.process.returncode}")
        status = item["status"]
        if not status.get("connected", False):
            raise RuntimeError("capture bridge disconnected")
        flags = int(status.get("capture_health_flags", 0))
        if flags & CAPTURE_HEALTH_BACKEND_FAILED:
            raise RuntimeError(f"capture health failed with flags {flags:#x}")
        if require_frame_progress and item["seconds_since_frame_progress"] > max_stall_seconds:
            raise TimeoutError(
                "capture produced no fresh frames for "
                f"{item['seconds_since_frame_progress']:.1f}s")
        return item


def wait_for_frames(
    bridge: CaptureBridge,
    process: subprocess.Popen,
    minimum_frames: int,
    timeout: float,
    *,
    sampler: QualificationSampler | None = None,
    interval: float = 1.0,
    max_stall_seconds: float = DEFAULT_STALL_TIMEOUT_SECONDS,
    activity_check: Callable[[], None] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if activity_check is not None:
            activity_check()
        if sampler is None:
            if process.poll() is not None:
                raise RuntimeError(f"engine exited with code {process.returncode}")
            status = bridge.get_status()
            if status.get("capture_health_flags", 0) & CAPTURE_HEALTH_BACKEND_FAILED:
                raise RuntimeError(
                    f"capture health failed with flags {status['capture_health_flags']:#x}")
        else:
            status = sampler.check(
                max_stall_seconds=max_stall_seconds,
                require_frame_progress=True,
            )["status"]
        if int(status.get("frames_captured", 0)) >= minimum_frames:
            return
        time.sleep(min(interval, max(0.01, deadline - time.monotonic())))
    raise TimeoutError(f"capture did not reach {minimum_frames} frames")


def _drain_save_responses(bridge: CaptureBridge) -> None:
    while bridge.peek_save_response() is not None:
        bridge.consume_save_response()


def save_and_wait(
    bridge: CaptureBridge,
    path: Path,
    requested_seconds: int,
    *,
    timeout: float = 90.0,
    progress_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Submit one save and require exactly one terminal saved response.

    File probing is intentionally separate.  During a long strict FFmpeg
    decode the engine must continue to be sampled for stalls.
    """
    _drain_save_responses(bridge)
    started = time.monotonic()
    if not bridge.save_clip(str(path), requested_seconds):
        raise RuntimeError(f"could not submit save for {path.name}")
    terminal_responses = 0
    deadline = started + timeout
    next_progress_check = started
    while time.monotonic() < deadline:
        if progress_check is not None and time.monotonic() >= next_progress_check:
            progress_check()
            next_progress_check = time.monotonic() + 1.0
        response = bridge.peek_save_response()
        if response is None:
            time.sleep(0.05)
            continue
        kind, detail = response
        bridge.consume_save_response()
        if kind == "started":
            continue
        terminal_responses += 1
        if terminal_responses != 1:
            raise RuntimeError(f"save published more than once for {path.name}")
        if kind == "error":
            raise RuntimeError(detail or f"engine rejected {path.name}")
        if kind == "saved":
            if not path.is_file():
                raise RuntimeError(f"engine reported saved but {path} is missing")
            # The shared-memory response is single-slot.  Give a second
            # terminal response a short chance to surface before validating
            # the file, so a duplicate publication cannot be mistaken for a
            # successful exactly-once save.
            settle_deadline = time.monotonic() + 0.25
            while time.monotonic() < settle_deadline:
                duplicate = bridge.peek_save_response()
                if duplicate is not None:
                    duplicate_kind, duplicate_detail = duplicate
                    bridge.consume_save_response()
                    if duplicate_kind != "started":
                        terminal_responses += 1
                        raise RuntimeError(
                            f"save published {terminal_responses} times for {path.name}: "
                            f"{duplicate_detail or duplicate_kind}")
                time.sleep(0.01)
            return {
                "path": path,
                "requested_seconds": requested_seconds,
                "save_latency_seconds": time.monotonic() - started,
                "publish_count": terminal_responses,
            }
    raise TimeoutError(f"save did not complete for {path.name}")


def save_and_probe(
    bridge: CaptureBridge,
    ffprobe: Path,
    ffmpeg: Path,
    path: Path,
    requested_seconds: int,
    *,
    expected_fps: int,
    require_audio: bool = True,
    timeout: float = 90.0,
) -> ClipResult:
    """Submit, wait, and strictly validate one save outside a soak."""
    saved = save_and_wait(
        bridge, path, requested_seconds, timeout=timeout)
    result = strict_probe_clip(
        ffprobe, ffmpeg, path, name=path.stem,
        requested_seconds=requested_seconds,
        save_latency=float(saved["save_latency_seconds"]),
        expected_fps=expected_fps,
        require_audio=require_audio,
        publish_count=int(saved["publish_count"]),
    )
    if result.publish_count != 1 or result.validation_error:
        raise RuntimeError(
            f"{path.name} failed clip validation: {result.validation_error}")
    return result


def _default_output() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(base) / f"FTHR-alpha-stability-qualification-{stamp}"


def start_moving_source(ffplay: Path, log_path: Path) -> subprocess.Popen:
    """Start a deterministic visible test pattern for the selected monitor."""
    if not ffplay.is_file():
        raise FileNotFoundError(
            f"moving source requires ffplay.exe; expected {ffplay}")
    command = [
        str(ffplay), "-hide_banner", "-loglevel", "warning", "-alwaysontop",
        "-window_title", "FTHR alpha qualification moving source",
        "-left", "40", "-top", "40",
        "-f", "lavfi", "-i",
        "testsrc2=size=1280x720:rate=60", "-t", "3600", "-an",
    ]
    log = log_path.open("w", encoding="utf-8", errors="replace")
    # This window is intentionally visible: the operator must see the moving
    # source on the monitor being qualified.  stdout/stderr remain bounded in
    # the artifact file and the handle is closed during cleanup.
    try:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    except Exception:
        raise
    finally:
        # Popen duplicates the inherited handle.  Closing the parent copy keeps
        # the artifact writable and avoids a leaked file descriptor.
        log.close()
    return process


def stop_process(process: subprocess.Popen | None, timeout: float = 5.0) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)


def shutdown_engine(
    bridge: CaptureBridge | None,
    process: subprocess.Popen | None,
) -> dict[str, Any]:
    """Apply the existing bounded shutdown policy and return evidence."""
    result: dict[str, Any] = {
        "shutdown_requested": False,
        "graceful_exit": False,
        "escalated": None,
        "exit_code": None,
    }
    if process is None:
        return result
    if bridge is not None and process.poll() is None:
        result["shutdown_requested"] = bool(bridge.request_engine_shutdown())
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        result["escalated"] = "terminate"
        process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            result["escalated"] = "kill"
            process.kill()
            process.wait(timeout=5.0)
    result["graceful_exit"] = result["escalated"] is None and process.poll() is not None
    result["exit_code"] = process.returncode
    return result


def require_process_alive(process: subprocess.Popen | None, label: str) -> None:
    if process is None or process.poll() is not None:
        raise RuntimeError(f"{label} exited during qualification")


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def _save_plan(args: argparse.Namespace) -> list[int]:
    return ([5] * args.short_saves) + ([30] * args.thirty_saves) + ([60] * args.sixty_saves)


def qualify(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(
            f"qualification output already contains files: {args.output}; "
            "choose a fresh --output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "qualification-report.json"
    sample_path = args.output / "engine-samples.jsonl"
    engine_log_path = args.output / "engine.log"
    ffplay = args.ffplay or args.ffmpeg.with_name("ffplay.exe")
    report: dict[str, Any] = {
        "schema": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "machine": machine_inventory(),
        "policy": {
            "moving_source": "ffplay testsrc2 visible window",
            "strict_decode": "ffmpeg -xerror -err_detect explode",
            "failure_preserves_evidence": True,
            "output_directory": str(args.output),
        },
        "requested": {
            "codec": args.codec, "fps": args.fps, "warmup_seconds": args.warmup_seconds,
            "soak_seconds": args.soak_seconds,
            "total_capture_seconds": args.total_capture_seconds,
            "short_saves": args.short_saves, "thirty_saves": args.thirty_saves,
            "sixty_saves": args.sixty_saves,
        },
        "samples": str(sample_path), "engine_log": str(engine_log_path),
        "clips": [], "save_publish_counts": {}, "status": "RUNNING",
    }
    _write_report(report_path, report)
    process: subprocess.Popen | None = None
    bridge: CaptureBridge | None = None
    moving_source: subprocess.Popen | None = None
    engine_log = None
    shutdown_recorded = False
    try:
        verify_no_running_engine()
        monitor_path, monitor_name = choose_monitor(args.monitor)
        selected_monitor = next(
            (item for item in enumerate_windows_monitors()
             if item.device_path == monitor_path),
            None,
        )
        if selected_monitor is not None and not selected_monitor.primary:
            raise RuntimeError(
                "alpha qualification moving source is restricted to the primary "
                "monitor; select the primary monitor or omit --monitor")
        report["monitor"] = {"name": monitor_name, "device_path": monitor_path}
        report["moving_source_monitor_policy"] = "primary monitor only"
        _write_report(report_path, report)
        command = build_engine_command(args, args.codec, monitor_path)
        engine_log = engine_log_path.open("w", encoding="utf-8", errors="replace")
        try:
            process = subprocess.Popen(
                command, stdout=engine_log, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            raise
        finally:
            # The child owns its inherited handle after Popen returns.
            engine_log.close()
            engine_log = None
        bridge = connect_bridge(process)
        active_codec = bridge.get_active_codec()
        report["active_codec_at_connect"] = active_codec
        sampler = QualificationSampler(bridge, process, sample_path, time.monotonic())
        moving_source = start_moving_source(ffplay, args.output / "moving-source.log")
        started = time.monotonic()
        wait_for_frames(
            bridge, process, max(args.fps * args.warmup_seconds, args.fps * 5),
            max(30.0, args.warmup_seconds + 30.0), sampler=sampler,
            max_stall_seconds=args.stall_timeout_seconds,
            activity_check=lambda: require_process_alive(moving_source, "moving source"),
        )
        active_codec = bridge.get_active_codec()
        report["active_codec"] = active_codec
        if args.codec not in active_codec.lower():
            raise RuntimeError(
                f"requested {args.codec}, engine reports {active_codec or 'empty'}")
        report["warmup_completed_seconds"] = round(time.monotonic() - started, 3)
        _write_report(report_path, report)

        plan = _save_plan(args)
        pending_clips: list[dict[str, Any]] = []
        plan_start = time.monotonic()
        soak_deadline = plan_start + args.soak_seconds
        next_index = 0
        # Spread saves through the moving-source soak.  A save is only started
        # after health sampling, and every result is persisted immediately.
        while time.monotonic() < soak_deadline or next_index < len(plan):
            if moving_source is None or moving_source.poll() is not None:
                raise RuntimeError("moving source exited during soak")
            sampler.check(max_stall_seconds=args.stall_timeout_seconds)
            if next_index < len(plan):
                elapsed = time.monotonic() - plan_start
                target = (next_index + 1) * args.soak_seconds / max(len(plan), 1)
                if elapsed >= target or time.monotonic() + 90.0 >= soak_deadline:
                    duration = plan[next_index]
                    clip_path = args.output / f"clip_{next_index + 1:02d}_{duration}s.mp4"
                    try:
                        saved = save_and_wait(
                            bridge, clip_path, duration,
                            progress_check=lambda: sampler.check(
                                max_stall_seconds=args.stall_timeout_seconds),
                        )
                    except Exception as error:
                        report["failed_save"] = {
                            "name": clip_path.name,
                            "requested_seconds": duration,
                            "error": f"{type(error).__name__}: {error}",
                        }
                        _write_report(report_path, report)
                        raise
                    pending_clips.append(saved)
                    report["save_publish_counts"][clip_path.name] = saved["publish_count"]
                    report["saved_clips"] = [
                        {
                            "name": item["path"].name,
                            "requested_seconds": item["requested_seconds"],
                            "save_latency_seconds": item["save_latency_seconds"],
                            "publish_count": item["publish_count"],
                        }
                        for item in pending_clips
                    ]
                    next_index += 1
                    _write_report(report_path, report)
                    continue
            if time.monotonic() >= soak_deadline and next_index >= len(plan):
                break
            time.sleep(min(1.0, max(0.05, soak_deadline - time.monotonic())))

        # End the moving visual and leave the desktop idle/static for the rest
        # of the requested 30-minute active-capture window.
        stop_process(moving_source)
        moving_source = None
        report["moving_source_stopped_seconds"] = round(
            time.monotonic() - started, 3)
        remaining = max(0.0, args.total_capture_seconds - (time.monotonic() - started))
        idle_deadline = time.monotonic() + remaining
        while time.monotonic() < idle_deadline:
            sampler.check(
                max_stall_seconds=args.stall_timeout_seconds,
                require_frame_progress=False,
            )
            time.sleep(min(1.0, max(0.05, idle_deadline - time.monotonic())))
        final_sample = sampler.sample()
        report["final_capture_sample"] = final_sample
        report["actual_capture_elapsed_seconds"] = round(
            time.monotonic() - started, 3)
        shutdown = shutdown_engine(bridge, process)
        shutdown_recorded = True
        report["shutdown"] = shutdown
        report["engine_stopped_before_validation"] = True
        if not shutdown["graceful_exit"]:
            raise RuntimeError("engine did not exit gracefully before clip validation")
        bridge.shutdown()
        bridge = None
        # Probe only after capture has completed so strict FFmpeg work cannot
        # create a blind interval in the health sampler.
        for saved in pending_clips:
            try:
                result = strict_probe_clip(
                    args.ffprobe, args.ffmpeg, saved["path"],
                    name=saved["path"].stem,
                    requested_seconds=saved["requested_seconds"],
                    save_latency=saved["save_latency_seconds"],
                    expected_fps=args.fps,
                    require_audio=args.audio,
                    publish_count=saved["publish_count"],
                )
            except Exception as error:
                report["failed_probe"] = {
                    "name": saved["path"].name,
                    "requested_seconds": saved["requested_seconds"],
                    "error": f"{type(error).__name__}: {error}",
                }
                _write_report(report_path, report)
                raise
            report["clips"].append(asdict(result))
            _write_report(report_path, report)
            if result.publish_count != 1 or result.validation_error:
                raise RuntimeError(
                    f"{saved['path'].name} failed clip validation: "
                    f"{result.validation_error}")
        report["status"] = "PASSED"
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        return report
    except Exception as error:
        report["status"] = "FAILED"
        report["failure"] = f"{type(error).__name__}: {error}"
        report["failed_utc"] = datetime.now(timezone.utc).isoformat()
        raise
    finally:
        stop_process(moving_source)
        if not shutdown_recorded:
            shutdown = shutdown_engine(bridge, process)
            report["shutdown"] = shutdown
            shutdown_recorded = True
            if report.get("status") == "PASSED" and not shutdown["graceful_exit"]:
                report["status"] = "FAILED"
                report["failure"] = "engine did not exit gracefully"
        if bridge is not None:
            bridge.shutdown()
        if process is not None and process.poll() is None:
            stop_process(process, timeout=10.0)
        if engine_log is not None:
            engine_log.close()
        _write_report(report_path, report)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P0 Windows alpha stability qualification; artifacts are external by default")
    parser.add_argument("--codec", choices=list(CODECS), default="h264")
    parser.add_argument("--monitor", help="normalized monitor device-interface path")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--bitrate", type=int, default=16000)
    parser.add_argument("--scaling-mode", choices=["stretch", "fit"], default="stretch")
    parser.add_argument("--warmup-seconds", type=int, default=DEFAULT_WARMUP_SECONDS)
    parser.add_argument("--soak-seconds", type=int, default=DEFAULT_SOAK_SECONDS)
    parser.add_argument("--total-capture-seconds", type=int, default=DEFAULT_TOTAL_CAPTURE_SECONDS)
    parser.add_argument("--stall-timeout-seconds", type=float, default=DEFAULT_STALL_TIMEOUT_SECONDS)
    parser.add_argument("--short-saves", type=int, default=20)
    parser.add_argument("--thirty-saves", type=int, default=5)
    parser.add_argument("--sixty-saves", type=int, default=3)
    parser.add_argument("--audio", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--separate-audio", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--engine", type=Path, default=ROOT / "FTHRcapture/x64/Release/FTHRclips.exe")
    ffmpeg_bin = ROOT / "FTHRcapture/FTHRclips/third_party/ffmpeg/bin"
    parser.add_argument("--ffmpeg", type=Path, default=ffmpeg_bin / "ffmpeg.exe")
    parser.add_argument("--ffprobe", type=Path, default=ffmpeg_bin / "ffprobe.exe")
    parser.add_argument("--ffplay", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=_default_output())
    args = parser.parse_args(argv)
    if any(value < 0 for value in (
        args.warmup_seconds, args.soak_seconds, args.total_capture_seconds,
        args.short_saves, args.thirty_saves, args.sixty_saves,
    )):
        parser.error("durations and save counts must be non-negative")
    if args.total_capture_seconds < args.warmup_seconds + args.soak_seconds:
        parser.error(
            "--total-capture-seconds must include warmup and soak "
            "(the remaining time is the idle/static phase)")
    if args.stall_timeout_seconds <= 0:
        parser.error("--stall-timeout-seconds must be positive")
    if args.fps <= 0:
        parser.error("--fps must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        raise SystemExit("This qualification harness runs only on Windows.")
    args = parse_args(argv)
    for required in (args.engine, args.ffmpeg, args.ffprobe):
        if not required.is_file():
            raise SystemExit(f"Required executable not found: {required}")
    args.output = args.output.resolve()
    try:
        args.output.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise SystemExit(
            f"Qualification output must be outside the repository: {args.output}")
    try:
        report = qualify(args)
    except Exception as error:
        print(f"Qualification FAILED: {error}", file=sys.stderr)
        print(f"Evidence: {args.output}", file=sys.stderr)
        return 1
    if report.get("status") != "PASSED":
        print(
            f"Qualification FAILED: {report.get('failure', 'qualification did not pass')}",
            file=sys.stderr,
        )
        print(f"Evidence: {args.output}", file=sys.stderr)
        return 1
    print(f"Qualification passed. Report: {args.output / 'qualification-report.json'}")
    print(json.dumps({
        "status": report.get("status"),
        "clips": len(report.get("clips", [])),
        "output": str(args.output),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

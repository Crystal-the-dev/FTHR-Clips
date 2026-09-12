#!/usr/bin/env python3
"""Run the repeatable AUDIT-049 Windows hardware replay qualification."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / 'FTHR_UI'
if str(UI_ROOT) not in sys.path:
    sys.path.insert(0, str(UI_ROOT))

from core.capture_bridge import CaptureBridge  # noqa: E402
from core.windows_monitor import enumerate_windows_monitors  # noqa: E402


CODECS = {'h264': 1, 'hevc': 2, 'av1': 3}
CREATE_NO_WINDOW = 0x08000000
CAPTURE_HEALTH_BACKEND_FAILED = 1 << 2


@dataclass
class ClipResult:
    name: str
    requested_seconds: int
    save_latency_seconds: float
    duration_seconds: float
    codec_name: str
    width: int
    height: int
    audio_streams: int
    keyframe_count: int
    maximum_keyframe_gap_seconds: float
    full_decode: bool
    partial_absent: bool


def run_checked(command: list[str], *, timeout: float = 120.0) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    return completed.stdout


def verify_no_running_engine() -> None:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenFileMappingW.restype = wintypes.HANDLE
    handle = kernel32.OpenFileMappingW(
        0x0004, False, CaptureBridge.SHARED_MEM_NAME)
    if handle:
        kernel32.CloseHandle(handle)
        raise RuntimeError(
            'Another FTHR engine is already running. Close FTHR before qualification.')


def connect_bridge(process: subprocess.Popen, timeout: float = 12.0) -> CaptureBridge:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'engine exited during startup with code {process.returncode}')
        bridge = CaptureBridge()
        if bridge.initialize():
            return bridge
        time.sleep(0.2)
    raise TimeoutError('engine shared memory did not become ready')


def wait_for_frames(
    bridge: CaptureBridge,
    process: subprocess.Popen,
    minimum_frames: int,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'engine exited with code {process.returncode}')
        status = bridge.get_status()
        if status.get('frames_captured', 0) >= minimum_frames:
            return
        if (status.get('capture_health_flags', 0)
                & CAPTURE_HEALTH_BACKEND_FAILED):
            raise RuntimeError(
                f'capture health failed with flags '
                f'{status["capture_health_flags"]:#x}')
        time.sleep(0.25)
    raise TimeoutError(f'capture did not reach {minimum_frames} frames')


def save_and_wait(
    bridge: CaptureBridge,
    path: Path,
    duration_seconds: int,
    timeout: float = 90.0,
) -> float:
    while bridge.peek_save_response() is not None:
        bridge.consume_save_response()
    started = time.monotonic()
    if not bridge.save_clip(str(path), duration_seconds):
        raise RuntimeError(f'could not submit save for {path.name}')
    deadline = started + timeout
    while time.monotonic() < deadline:
        response = bridge.peek_save_response()
        if response is None:
            time.sleep(0.05)
            continue
        kind, detail = response
        bridge.consume_save_response()
        if kind == 'error':
            raise RuntimeError(detail or f'engine rejected {path.name}')
        if kind == 'saved':
            if not path.is_file():
                raise RuntimeError(f'engine reported saved but {path} is missing')
            return time.monotonic() - started
    raise TimeoutError(f'save did not complete for {path.name}')


def probe_clip(
    ffprobe: Path,
    ffmpeg: Path,
    path: Path,
    name: str,
    requested_seconds: int,
    save_latency: float,
) -> ClipResult:
    probe = json.loads(run_checked([
        str(ffprobe), '-v', 'error', '-show_format', '-show_streams',
        '-of', 'json', str(path),
    ]))
    video = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
    audio_streams = sum(
        stream['codec_type'] == 'audio' for stream in probe['streams'])
    duration = float(probe['format']['duration'])
    keyframe_text = run_checked([
        str(ffprobe), '-v', 'error', '-select_streams', 'v:0',
        '-skip_frame', 'nokey', '-show_entries', 'frame=pts_time',
        '-of', 'csv=p=0', str(path),
    ])
    timestamps = [
        float(line.strip().rstrip(','))
        for line in keyframe_text.splitlines()
        if line.strip().rstrip(',')
    ]
    maximum_gap = max(
        (right - left for left, right in zip(
            timestamps, timestamps[1:], strict=False)),
        default=0.0,
    )
    run_checked([
        str(ffmpeg), '-v', 'error', '-i', str(path),
        '-map', '0:v:0', '-f', 'null', 'NUL',
    ], timeout=300.0)
    return ClipResult(
        name=name,
        requested_seconds=requested_seconds,
        save_latency_seconds=round(save_latency, 3),
        duration_seconds=duration,
        codec_name=video['codec_name'],
        width=int(video['width']),
        height=int(video['height']),
        audio_streams=audio_streams,
        keyframe_count=len(timestamps),
        maximum_keyframe_gap_seconds=round(maximum_gap, 6),
        full_decode=True,
        partial_absent=not Path(f'{path}.partial').exists(),
    )


_DIAGNOSTIC_EVENT_PREFIX = 'FTHR_DIAGNOSTIC_EVENT '


def parse_adapter_topology_event(log_text: str) -> dict | None:
    """Return the last resolved adapter-topology event from an engine log.

    The engine emits one JSON object per diagnostic line.  Keep this parser
    deliberately independent of the rest of the qualification run so a
    synthetic log line can exercise it without starting the native engine.
    A later event wins because topology recovery can emit a fresh resolution.
    """
    resolved: dict | None = None
    for line in log_text.splitlines():
        marker = line.find(_DIAGNOSTIC_EVENT_PREFIX)
        if marker < 0:
            continue
        payload = line[marker + len(_DIAGNOSTIC_EVENT_PREFIX):].strip()
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if (isinstance(event, dict)
                and event.get('event') == 'adapter_topology_resolved'):
            resolved = event
    return resolved


def read_adapter_topology_event(log_path: Path) -> dict | None:
    try:
        return parse_adapter_topology_event(
            log_path.read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return None


def event_resolution(event: dict | None, prefix: str) -> list[int] | None:
    if event is None:
        return None
    width = event.get(f'{prefix}_width')
    height = event.get(f'{prefix}_height')
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return [width, height]
    return None


def process_memory_bytes(process: subprocess.Popen) -> tuple[int | None, int | None]:
    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ('cb', wintypes.DWORD),
            ('PageFaultCount', wintypes.DWORD),
            ('PeakWorkingSetSize', ctypes.c_size_t),
            ('WorkingSetSize', ctypes.c_size_t),
            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
            ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
            ('PagefileUsage', ctypes.c_size_t),
            ('PeakPagefileUsage', ctypes.c_size_t),
            ('PrivateUsage', ctypes.c_size_t),
        ]
    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(
        wintypes.HANDLE(process._handle),
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        return None, None
    return counters.WorkingSetSize, counters.PrivateUsage


def process_cpu_seconds(process: subprocess.Popen) -> float | None:
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    ok = ctypes.windll.kernel32.GetProcessTimes(
        wintypes.HANDLE(process._handle),
        ctypes.byref(creation), ctypes.byref(exit_time),
        ctypes.byref(kernel), ctypes.byref(user),
    )
    if not ok:
        return None

    def ticks(value: wintypes.FILETIME) -> int:
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    return (ticks(kernel) + ticks(user)) / 10_000_000.0


def process_thread_count(process: subprocess.Popen) -> int | None:
    """Return the live Win32 thread count without adding a runtime dependency."""
    create_snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    snapshot = create_snapshot(0x00000004, 0)
    invalid = ctypes.c_void_p(-1).value
    snapshot_value = (
        snapshot.value if hasattr(snapshot, 'value') else snapshot)
    try:
        snapshot_value = int(snapshot_value)
    except (TypeError, ValueError):
        return None
    if snapshot_value in (0, -1, invalid):
        return None
    snapshot_handle = wintypes.HANDLE(snapshot_value)

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ('dwSize', wintypes.DWORD),
            ('cntUsage', wintypes.DWORD),
            ('th32ThreadID', wintypes.DWORD),
            ('th32OwnerProcessID', wintypes.DWORD),
            ('tpBasePri', wintypes.LONG),
            ('tpDeltaPri', wintypes.LONG),
            ('dwFlags', wintypes.DWORD),
        ]

    first = ctypes.windll.kernel32.Thread32First
    next_thread = ctypes.windll.kernel32.Thread32Next
    close_handle = ctypes.windll.kernel32.CloseHandle
    first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry32)]
    first.restype = wintypes.BOOL
    next_thread.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry32)]
    next_thread.restype = wintypes.BOOL
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    entry = ThreadEntry32()
    entry.dwSize = ctypes.sizeof(ThreadEntry32)
    count = 0
    try:
        if not first(snapshot_handle, ctypes.byref(entry)):
            return 0
        while True:
            if entry.th32OwnerProcessID == process.pid:
                count += 1
            entry.dwSize = ctypes.sizeof(ThreadEntry32)
            if not next_thread(snapshot_handle, ctypes.byref(entry)):
                break
        return count
    finally:
        close_handle(snapshot_handle)


def process_snapshot(process: subprocess.Popen) -> dict:
    working_set, private_bytes = process_memory_bytes(process)
    return {
        'working_set_bytes': working_set,
        'private_bytes': private_bytes,
        'process_cpu_seconds': process_cpu_seconds(process),
        'process_thread_count': process_thread_count(process),
    }


def capture_snapshot(
    bridge: CaptureBridge,
    process: subprocess.Popen,
    started: float,
) -> dict:
    status = bridge.get_status()
    return {
        'captured_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'status': status,
        **process_snapshot(process),
    }


def run_soak(
    bridge: CaptureBridge,
    process: subprocess.Popen,
    started: float,
    seconds: int,
    fps: int,
) -> dict:
    """Poll bounded health state during a requested fresh-frame soak."""
    soak_started = time.monotonic()
    start = capture_snapshot(bridge, process, started)
    start_status = start['status']
    if not start_status.get('connected', False):
        raise RuntimeError('capture bridge disconnected at soak start')
    start_frames = int(start_status.get('frames_captured', 0))
    last_frames = start_frames
    last_progress = soak_started
    deadline = soak_started + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f'engine exited during soak with code {process.returncode}')
        status = bridge.get_status()
        if not status.get('connected', False):
            raise RuntimeError('capture bridge disconnected during soak')
        if status.get('capture_health_flags', 0) & CAPTURE_HEALTH_BACKEND_FAILED:
            raise RuntimeError(
                f'capture health failed during soak with flags '
                f'{status["capture_health_flags"]:#x}')
        current_frames = int(status.get('frames_captured', 0))
        if current_frames > last_frames:
            last_frames = current_frames
            last_progress = time.monotonic()
        elif time.monotonic() - last_progress > 15.0:
            raise TimeoutError(
                f'capture produced no fresh frames for 15 seconds during soak '
                f'(fps={fps})')
        time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))

    end = capture_snapshot(bridge, process, started)
    end_status = end['status']
    end_frames = int(end_status.get('frames_captured', 0))
    if end_frames <= start_frames:
        raise TimeoutError(
            f'capture produced no fresh frames during {seconds}s soak')
    return {
        'requested_seconds': seconds,
        'actual_seconds': round(time.monotonic() - soak_started, 3),
        'fresh_frames_captured': end_frames - start_frames,
        'start': start,
        'end': end,
    }


def machine_inventory() -> dict:
    powershell = (
        "$os=Get-CimInstance Win32_OperatingSystem;"
        "$gpu=Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,PNPDeviceID;"
        "[pscustomobject]@{Caption=$os.Caption;Version=$os.Version;"
        "Build=$os.BuildNumber;GPU=$gpu}|ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        return json.loads(run_checked(
            ['powershell', '-NoProfile', '-Command', powershell], timeout=30.0))
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return {'platform': platform.platform()}


def choose_monitor(explicit: str | None) -> tuple[str, str]:
    monitors = enumerate_windows_monitors()
    if explicit:
        match = next((item for item in monitors if item.device_path == explicit), None)
        return explicit, match.friendly_name if match else 'explicit device path'
    selected = next((item for item in monitors if item.primary), None)
    if selected is None:
        raise RuntimeError('no Windows monitor device-interface path was found')
    return selected.device_path, selected.friendly_name


def qualify_codec(args, codec: str, monitor_path: str, monitor_name: str) -> dict:
    codec_dir = args.output / codec
    codec_dir.mkdir(parents=True, exist_ok=True)
    log_path = codec_dir / 'engine.log'
    command = [
        str(args.engine), str(args.fps), '62',
        str(args.width), str(args.height), str(args.bitrate), '2048',
        '0', '0', '1' if args.scaling_mode == 'fit' else '0',
        monitor_path, str(CODECS[codec]), '4', '0',
        '1' if args.audio else '0',
    ]
    started = time.monotonic()
    clips: list[ClipResult] = []
    with log_path.open('w', encoding='utf-8', errors='replace') as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        bridge = None
        try:
            bridge = connect_bridge(process)
            active_codec = bridge.get_active_codec()
            if codec not in active_codec.lower():
                raise RuntimeError(
                    f'requested {codec}, but engine reports {active_codec or "empty"}')

            wait_for_frames(bridge, process, args.fps * 5, 30.0)
            warmup = codec_dir / f'{codec}_warmup.mp4'
            latency = save_and_wait(bridge, warmup, 30)
            clips.append(probe_clip(
                args.ffprobe, args.ffmpeg, warmup, 'warmup', 30, latency))

            wait_for_frames(bridge, process, args.fps * 35, 75.0)
            clip30 = codec_dir / f'{codec}_30s.mp4'
            latency = save_and_wait(bridge, clip30, 30)
            clips.append(probe_clip(
                args.ffprobe, args.ffmpeg, clip30, 'full-30', 30, latency))

            wait_for_frames(bridge, process, args.fps * 65, 90.0)
            clip60 = codec_dir / f'{codec}_60s.mp4'
            latency = save_and_wait(bridge, clip60, 60)
            clips.append(probe_clip(
                args.ffprobe, args.ffmpeg, clip60, 'full-60', 60, latency))

            frames_before_rapid = int(
                bridge.get_status().get('frames_captured', 0))
            for index in range(1, 11):
                rapid = codec_dir / f'{codec}_rapid_{index}.mp4'
                latency = save_and_wait(bridge, rapid, 5)
                clips.append(probe_clip(
                    args.ffprobe, args.ffmpeg, rapid,
                    f'rapid-{index}', 5, latency))

            # A valid ring can still save old footage after capture has frozen.
            # Require fresh frames after the rapid-save burst so a stale replay
            # buffer cannot produce a false qualification PASS.
            wait_for_frames(
                bridge,
                process,
                frames_before_rapid + max(args.fps, 1),
                12.0,
            )

            soak = None
            if args.soak_seconds > 0:
                soak = run_soak(
                    bridge, process, started, args.soak_seconds, args.fps)

            final_process = process_snapshot(process)
            working_set = final_process['working_set_bytes']
            private_bytes = final_process['private_bytes']
            cpu_seconds = final_process['process_cpu_seconds']
            status = bridge.get_status()
            wall_seconds = time.monotonic() - started
            # The probe describes the encoded file, not the capture source.
            # Flush our handle before reading the native engine's event line;
            # if no topology event was emitted, report capture dimensions as
            # unavailable instead of copying the encoded dimensions into it.
            log.flush()
            topology_event = read_adapter_topology_event(log_path)
            diagnostic_capture_resolution = event_resolution(
                topology_event, 'capture')
            diagnostic_encode_resolution = event_resolution(
                topology_event, 'encoder')
            probed_encode_resolution = (
                [clips[-1].width, clips[-1].height] if clips else None)
            encode_resolution_matches_diagnostic = (
                diagnostic_encode_resolution is None
                or probed_encode_resolution == diagnostic_encode_resolution)
            suffix = active_codec.lower().rsplit('_', 1)[-1]
            active_backend = {
                'nvenc': 'native-nvenc',
                'amf': 'ffmpeg-amf',
                'qsv': 'ffmpeg-qsv',
            }.get(suffix, 'unknown')
            active_vendor = {
                'nvenc': 'NVIDIA',
                'amf': 'AMD',
                'qsv': 'Intel',
            }.get(suffix, 'unknown')
            return {
                'requested_codec': codec,
                'requested_scaling_mode': args.scaling_mode,
                'active_codec': active_codec,
                'active_backend': active_backend,
                'capture_adapter_vendor': active_vendor,
                'encoder_adapter_vendor': active_vendor,
                'same_adapter': True,
                'raw_replay_pool_allocated': False,
                'monitor': monitor_name,
                'monitor_device_path': monitor_path,
                'monitor_id': (
                    topology_event.get('monitor_id')
                    if topology_event else None),
                'windows_display': (
                    topology_event.get('windows_display')
                    if topology_event else None),
                'dxgi_output': (
                    topology_event.get('dxgi_output')
                    if topology_event else None),
                'monitor_adapter_luid': (
                    topology_event.get('monitor_adapter_luid')
                    if topology_event else None),
                'capture_device_luid': (
                    topology_event.get('capture_device_luid')
                    if topology_event else None),
                'encoder_adapter_luid': (
                    topology_event.get('encoder_adapter_luid')
                    if topology_event else None),
                'diagnostic_capture_backend': (
                    topology_event.get('capture_backend')
                    if topology_event else None),
                'diagnostic_encoder_backend': (
                    topology_event.get('encoder_backend')
                    if topology_event else None),
                'capture_resolution': diagnostic_capture_resolution,
                'encode_resolution': probed_encode_resolution,
                'diagnostic_encode_resolution': diagnostic_encode_resolution,
                'encode_resolution_matches_diagnostic': (
                    encode_resolution_matches_diagnostic
                    if topology_event else None),
                'fps': args.fps,
                'bitrate_kbps': args.bitrate,
                'capture_generation': status.get('capture_generation'),
                'frames_captured': status.get('frames_captured'),
                'capture_health_flags': status.get('capture_health_flags'),
                'working_set_bytes': working_set,
                'private_bytes': private_bytes,
                'process_cpu_seconds': cpu_seconds,
                'process_thread_count': final_process['process_thread_count'],
                'average_process_cpu_percent': (
                    round(cpu_seconds / wall_seconds * 100.0, 3)
                    if cpu_seconds is not None and wall_seconds > 0 else None),
                'wall_seconds': round(wall_seconds, 3),
                'clips': [asdict(item) for item in clips],
                'soak': soak,
                'engine_log': str(log_path),
                'gpu_metrics': 'Collect GPU 3D/copy/video-encode counters externally.',
            }
        finally:
            if bridge is not None:
                bridge.shutdown()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='AUDIT-049 Windows hardware replay qualification')
    parser.add_argument('--codec', choices=['all', *CODECS], default='all')
    parser.add_argument('--monitor', help='normalized monitor device-interface path')
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--width', type=int, default=0)
    parser.add_argument('--height', type=int, default=0)
    parser.add_argument('--bitrate', type=int, default=16000)
    parser.add_argument(
        '--scaling-mode', choices=['stretch', 'fit'], default='stretch',
        help='requested capture-to-encode scaling geometry')
    parser.add_argument(
        '--soak-seconds', type=int, default=0,
        help='bounded fresh-frame capture soak after rapid saves (default: 0)')
    parser.add_argument('--audio', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        '--engine', type=Path,
        default=ROOT / 'FTHRcapture/x64/Release/FTHRclips.exe')
    ffmpeg_bin = ROOT / 'FTHRcapture/FTHRclips/third_party/ffmpeg/bin'
    parser.add_argument('--ffmpeg', type=Path, default=ffmpeg_bin / 'ffmpeg.exe')
    parser.add_argument('--ffprobe', type=Path, default=ffmpeg_bin / 'ffprobe.exe')
    parser.add_argument(
        '--output', type=Path,
        default=ROOT / 'build/windows-hardware-qualification')
    return parser.parse_args()


def main() -> int:
    if sys.platform != 'win32':
        raise SystemExit('This qualification harness runs only on Windows.')
    args = parse_args()
    if args.soak_seconds < 0:
        raise SystemExit('--soak-seconds must be zero or greater')
    for required in (args.engine, args.ffmpeg, args.ffprobe):
        if not required.is_file():
            raise SystemExit(f'Required executable not found: {required}')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    verify_no_running_engine()
    monitor_path, monitor_name = choose_monitor(args.monitor)
    codecs = list(CODECS) if args.codec == 'all' else [args.codec]
    report = {
        'schema': 1,
        'generated_utc': datetime.now(timezone.utc).isoformat(),
        'machine': machine_inventory(),
        'policy': 'same-adapter-only; no automatic cross-adapter/raw fallback',
        'requested_scaling_mode': args.scaling_mode,
        'soak_seconds': args.soak_seconds,
        'results': [],
    }
    report_path = args.output / 'qualification-report.json'
    try:
        for codec in codecs:
            print(f'Qualifying {codec.upper()} on {monitor_name}...')
            report['results'].append(
                qualify_codec(args, codec, monitor_path, monitor_name))
            report_path.write_text(
                json.dumps(report, indent=2), encoding='utf-8')
    except Exception as error:
        report['failure'] = f'{type(error).__name__}: {error}'
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        raise
    print(f'Qualification passed. Report: {report_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

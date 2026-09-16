"""Deterministic checks for the Windows alpha qualification harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import qualify_alpha_stability as qualification


def packet_series(count: int, step: float = 1 / 60) -> list[qualification.PacketPoint]:
    return [qualification.PacketPoint(index * step, step) for index in range(count)]


def test_timeline_accepts_real_60fps_coverage() -> None:
    result = qualification.timeline_health(
        packet_series(301), requested_seconds=5, expected_fps=60)

    assert result["valid"] is True
    assert result["coverage_seconds"] == pytest.approx(5.0)
    assert result["maximum_gap"] == pytest.approx(1 / 60)


def test_timeline_rejects_sparse_four_packet_replay() -> None:
    result = qualification.timeline_health(
        [qualification.PacketPoint(0.0, 1.0),
         qualification.PacketPoint(1.34, 1.0),
         qualification.PacketPoint(2.68, 1.0),
         qualification.PacketPoint(4.02, 1.0)],
        requested_seconds=5,
        expected_fps=60,
    )

    assert result["valid"] is False
    assert result["reason"] == "video_gap_too_large"


def test_timeline_rejects_multi_second_frozen_gap_even_with_coverage() -> None:
    packets = packet_series(121)
    packets[60:] = [qualification.PacketPoint(10.0 + index / 60, 1 / 60)
                    for index in range(len(packets) - 60)]
    result = qualification.timeline_health(
        packets, requested_seconds=10, expected_fps=60)

    assert result["valid"] is False
    assert result["reason"] == "video_gap_too_large"


def test_timeline_rejects_low_effective_rate_without_a_multi_second_gap() -> None:
    packets = [qualification.PacketPoint(index * 0.2, 0.2) for index in range(6)]

    result = qualification.timeline_health(
        packets, requested_seconds=1, expected_fps=60)

    assert result["valid"] is False
    assert result["reason"] == "video_effective_rate_too_low"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.25", 1.25), ("", None), ("N/A", None), ("inf", None), ("nan", None)],
)
def test_float_parser_rejects_non_finite_values(value: str, expected: float | None) -> None:
    assert qualification._float_or_none(value) == expected


def test_packet_parser_preserves_order_and_stream_identity() -> None:
    payload = {"packets": [
        {"codec_type": "video", "stream_index": 0, "pts_time": "0", "duration_time": "0.016"},
        {"codec_type": "audio", "stream_index": 1, "pts_time": "0", "duration_time": "0.02"},
        {"codec_type": "video", "stream_index": 0, "pts_time": "0.016", "duration_time": "0.016"},
    ]}

    video = qualification.parse_packet_points(payload, "video")

    assert [packet.pts for packet in video] == [0.0, 0.016]
    assert [packet.stream_index for packet in video] == [0, 0]


def test_save_plan_has_required_default_shape() -> None:
    args = qualification.parse_args([])

    assert qualification._save_plan(args) == [5] * 20 + [30] * 5 + [60] * 3
    assert args.warmup_seconds >= 65
    assert args.soak_seconds >= 15 * 60
    assert args.total_capture_seconds >= args.warmup_seconds + args.soak_seconds
    assert not str(args.output).startswith(str(qualification.ROOT))


@pytest.mark.parametrize(
    "arguments",
    [
        ["--warmup-seconds", "70", "--soak-seconds", "900", "--total-capture-seconds", "969"],
        ["--stall-timeout-seconds", "0"],
        ["--short-saves", "-1"],
    ],
)
def test_parse_args_rejects_unsafe_or_incomplete_qualification(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        qualification.parse_args(arguments)


def test_default_output_can_be_configured_outside_repository(tmp_path: Path) -> None:
    args = qualification.parse_args(["--output", str(tmp_path)])

    assert args.output == tmp_path


def test_zero_frame_stream_is_classified_as_a_stall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Clock:
        values = iter([0.0, 1.0, 17.0])

        def monotonic(self) -> float:
            return next(self.values)

    class HealthBridge:
        def get_status(self) -> dict[str, object]:
            return {"connected": True, "frames_captured": 0, "capture_health_flags": 0}

    monkeypatch.setattr(qualification.time, "monotonic", Clock().monotonic)
    monkeypatch.setattr(
        qualification, "process_snapshot",
        lambda process, now=None: {"captured_monotonic": now, "process_cpu_seconds": 0.0},
    )
    sampler = qualification.QualificationSampler(
        HealthBridge(), FakeProcess(), tmp_path / "samples.jsonl", started_at=0.0)

    sampler.check(max_stall_seconds=15.0)
    with pytest.raises(TimeoutError, match="no fresh frames"):
        sampler.check(max_stall_seconds=15.0)


class FakeProcess:
    _handle = 0
    pid = 123

    def poll(self) -> None:
        return None


class FakeBridge:
    def __init__(self, responses: list[tuple[str, str]]) -> None:
        self.responses_to_publish = list(responses)
        self.responses: list[tuple[str, str]] = []
        self.submitted: list[tuple[str, int]] = []

    def peek_save_response(self) -> tuple[str, str] | None:
        return self.responses[0] if self.responses else None

    def consume_save_response(self, expected_kind=None) -> bool:
        if self.responses:
            self.responses.pop(0)
            return True
        return False

    def save_clip(self, path: str, duration: int) -> bool:
        self.submitted.append((path, duration))
        self.responses.extend(self.responses_to_publish)
        return True


def test_save_and_wait_observes_one_terminal_publication(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"encoded")
    bridge = FakeBridge([("started", ""), ("saved", "")])

    result = qualification.save_and_wait(bridge, path, 5, timeout=1.0)

    assert result["publish_count"] == 1
    assert result["path"] == path
    assert bridge.submitted == [(str(path), 5)]


def test_save_and_wait_rejects_duplicate_terminal_publication(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"encoded")
    bridge = FakeBridge([("saved", ""), ("saved", "again")])

    with pytest.raises(RuntimeError, match="published 2 times"):
        qualification.save_and_wait(bridge, path, 5, timeout=1.0)


def test_strict_probe_requires_audio_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"encoded")
    probe_payload = {
        "streams": [{"index": 0, "codec_type": "video", "duration": "5.0"}],
        "format": {"duration": "5.0"},
    }
    packet_payload = {"packets": [
        {"codec_type": "video", "stream_index": 0, "pts_time": str(i / 60), "duration_time": str(1 / 60)}
        for i in range(301)
    ]}
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> str:
        commands.append(command)
        if "-show_streams" in command:
            return json.dumps(probe_payload)
        if "-show_packets" in command:
            return json.dumps(packet_payload)
        return ""

    monkeypatch.setattr(qualification, "run_checked", fake_run)
    result = qualification.strict_probe_clip(
        Path("ffprobe"), Path("ffmpeg"), path,
        name="clip", requested_seconds=5, save_latency=0.1,
        expected_fps=60, require_audio=True,
    )

    assert result.audio_duration_sane is False
    assert result.validation_error == "audio_duration_mismatch"
    assert any("-xerror" in command for command in commands)


def test_process_handle_count_is_unavailable_off_windows() -> None:
    assert qualification.process_handle_count(FakeProcess()) is None


def test_main_returns_failure_for_a_report_that_did_not_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    executable = Path(__file__).resolve()
    monkeypatch.setattr(qualification.sys, "platform", "win32")
    monkeypatch.setattr(
        qualification,
        "parse_args",
        lambda _: type("Args", (), {
            "engine": executable,
            "ffmpeg": executable,
            "ffprobe": executable,
            "output": tmp_path,
        })(),
    )
    monkeypatch.setattr(
        qualification,
        "qualify",
        lambda args: {"status": "FAILED", "failure": "synthetic failure"},
    )

    assert qualification.main([]) == 1

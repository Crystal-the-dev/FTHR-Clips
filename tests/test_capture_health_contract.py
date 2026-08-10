"""Cross-language capture-health contract and native recovery-policy tests."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from core.capture_health import CaptureHealthFlag


ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "FTHRcapture_linux"
WINDOWS = ROOT / "FTHRcapture" / "FTHRclips"


def test_v4_mapping_name_is_used_by_all_producers_and_consumer() -> None:
    sources = (
        ROOT / "FTHR_UI" / "core" / "capture_bridge.py",
        LINUX / "src" / "main.cpp",
        WINDOWS / "src" / "main.cpp",
    )
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "FTHR_SharedMemory_v4" in text
        assert "FTHR_SharedMemory_v3" not in text


def test_both_engines_publish_all_typed_health_fields() -> None:
    fields = {
        "capture_health_flags",
        "capture_generation",
        "content_sample_sequence",
        "content_suspicious_streak",
        "content_luma_mean",
        "content_luma_variance",
    }
    for main in (LINUX / "src" / "main.cpp", WINDOWS / "src" / "main.cpp"):
        text = main.read_text(encoding="utf-8")
        assert all(f"layout->{field}" in text for field in fields)


def test_typed_health_flag_values_match_python_and_both_engines() -> None:
    expected = {
        "CAPTURE_HEALTH_ACTIVE": int(CaptureHealthFlag.ACTIVE),
        "CAPTURE_HEALTH_RECOVERING": int(CaptureHealthFlag.RECOVERING),
        "CAPTURE_HEALTH_BACKEND_FAILED": int(CaptureHealthFlag.BACKEND_FAILED),
        "CAPTURE_HEALTH_CONTENT_SUSPECT": int(CaptureHealthFlag.CONTENT_SUSPECT),
        "CAPTURE_HEALTH_PAUSED": int(CaptureHealthFlag.PAUSED),
    }
    for header in (
        LINUX / "src" / "shared_memory.h",
        WINDOWS / "include" / "shared_memory.h",
    ):
        compact = "".join(header.read_text(encoding="utf-8").split())
        for bit, (name, value) in enumerate(expected.items()):
            assert value == 1 << bit
            assert f"{name}=1u<<{bit}" in compact


def test_linux_terminal_failure_clears_capture_running_state() -> None:
    engine = (LINUX / "src" / "capture_engine.cpp").read_text(encoding="utf-8")
    assert "decision.exhausted" in engine
    terminal = engine[engine.index("if (decision.exhausted)"):]
    assert "CAPTURE_HEALTH_BACKEND_FAILED" in terminal
    assert "running_.store(false)" in terminal
    assert "ring_->Clear()" in engine


def test_native_fake_backend_recovery_policy(tmp_path: Path) -> None:
    compiler = shutil.which("g++") or shutil.which("c++")
    if compiler is None:
        pytest.skip("native C++ compiler not installed")
    source = LINUX / "tests" / "capture_recovery_test.cpp"
    output = tmp_path / ("capture_recovery_test.exe" if shutil.which("g++") else "capture_recovery_test")
    subprocess.run(
        [compiler, "-std=c++20", "-Wall", "-Wextra", "-Wpedantic",
         f"-I{LINUX / 'src'}", str(source), "-o", str(output)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    subprocess.run([str(output)], check=True, timeout=10)

"""Clean-clone contracts for the release CI workflow."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
AUDIT050_TOOLS = ROOT / "tools" / "audit050"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_linux_release_ci_fetches_and_uses_pinned_ffmpeg() -> None:
    workflow = _workflow()
    linux_job = workflow[workflow.index("  linux-engine:") : workflow.index(
        "  windows-engine:"
    )]

    assert "fetch_third_party.py --ffmpeg-linux" in linux_job
    assert "-DFTHR_FFMPEG_ROOT=${{ github.workspace }}" in linux_job
    assert "ctest --test-dir" in linux_job
    assert "verify_release_licenses.py --tree ." in linux_job
    assert "git diff --exit-code" in linux_job


def test_windows_bundle_is_built_only_after_the_engine() -> None:
    workflow = _workflow()
    python_job = workflow[workflow.index("  python:") : workflow.index(
        "  linux-engine:"
    )]
    windows_job = workflow[workflow.index("  windows-engine:") : workflow.index(
        "  release-verification:"
    )]

    assert "PyInstaller" not in python_job
    assert windows_job.index("msbuild FTHRcapture") < windows_job.index(
        "python -m PyInstaller"
    )
    assert "verify_release_licenses.py --windows-dist" in windows_job


def test_windows_package_uses_the_solution_playback_mixer_output() -> None:
    spec = (ROOT / "FTHR.spec").read_text(encoding="utf-8")
    loader = (ROOT / "FTHR_UI" / "core" / "ffmpeg_playback.py").read_text(
        encoding="utf-8"
    )

    stale_path = "'FTHRcapture' / 'FTHRPlaybackMixer' / 'x64'"
    solution_path = "'FTHRcapture' / 'x64' / 'Release'"
    assert solution_path in spec
    assert solution_path in loader
    assert stale_path not in spec
    assert stale_path not in loader


def test_release_ci_runs_response_and_exception_contracts() -> None:
    workflow = _workflow()
    release_job = workflow[workflow.index("  release-verification:") :]

    assert "verify_engine_response_contract.py" in release_job
    assert "verify_exception_handling.py" in release_job


def test_native_spike_compilers_keep_intermediates_below_temp() -> None:
    windows_spikes = (AUDIT050_TOOLS / "run_windows_spikes.ps1").read_text(
        encoding="utf-8"
    )
    playback_spike = (AUDIT050_TOOLS / "run_playback_spike.ps1").read_text(
        encoding="utf-8"
    )

    assert '&& cd /d "{1}" && {2}' in windows_spikes
    assert "$devcmd, $buildRoot, $commandBody" in windows_spikes
    assert '&& cd /d "{1}" && cl ' in playback_spike
    assert "-f $devcmd, $buildRoot, $ffmpegInclude" in playback_spike

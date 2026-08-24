<div align="center">

# FTHR Clips

**Local-first instant replay for gamers.**

[![License: GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-blue.svg)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-alpha-44CC88)](docs/SUPPORTED_PLATFORMS.md)
[![Linux](https://img.shields.io/badge/Linux-experimental-lightgrey)](docs/SUPPORTED_PLATFORMS.md)
[![CI](https://github.com/FTHR-Community/FTHR-Clips/actions/workflows/ci.yml/badge.svg)](https://github.com/FTHR-Community/FTHR-Clips/actions/workflows/ci.yml)

FTHR Clips keeps a configurable replay history in the background. Press a
hotkey after something happens and the application saves the moment as a local
clip—without requiring an account, subscription, or cloud upload.

</div>

> [!WARNING]
> FTHR Clips is in pre-alpha qualification. There is currently no
> public-release-qualified build. Treat the support states below literally.

## Current product status

`READY` means the current source path has been exercised in its stated cohort.
`UNVERIFIED` means code and automated coverage exist, but required physical
testing has not happened. `EXPERIMENTAL` and `PLATFORM-LIMITED` are not broad
support claims.

| Capability | Status | Current reality |
|---|---|---|
| Background replay and configurable clip duration | **READY** | Windows replay, 30/60-second saves, background operation, and audio were physically exercised on the NVIDIA qualification host. |
| Selected-monitor recording and screenshots | **READY** | Windows monitor resolution and selected-monitor screenshot paths are integrated and tested. |
| Tray operation, global hotkeys, and autostart | **READY** | Windows source-mode workflows are exercised. The final installed-app walkthrough remains open. |
| Clip browser, trim, processing, and export | **READY** | Clip readiness is transactional, linked originals are protected, and export paths are regression-tested. |
| NVIDIA H.264 / HEVC / AV1 | **READY** | Physically qualified on Windows with a same-adapter NVIDIA RTX 4060 Ti. |
| AMD H.264 / HEVC / AV1 | **UNVERIFIED** | AMF paths are integrated and automated-tested; physical AMD hardware qualification has not run. |
| Intel H.264 / HEVC / AV1 | **UNVERIFIED** | QSV paths are integrated and automated-tested; physical Intel hardware qualification has not run. |
| System-output and microphone tracks | **READY** | Separate synchronized tracks and in-app mixed playback are implemented and exercised on the Windows qualification host. |
| Windows 11 per-application audio stems | **UNVERIFIED** | Official process-loopback capture and dynamic source controls are code-integrated; physical Windows 11 qualification remains open. |
| Windows 10 per-application audio | **PLATFORM-LIMITED** | Windows 10 intentionally exposes Master, System Audio, and Microphone rather than unsupported per-app stems. |
| Windows capture-border suppression | **PLATFORM-LIMITED** | The supported WGC policy is implemented where the operating system exposes it; final Windows 11 physical confirmation remains open. |
| Linux Wayland capture | **EXPERIMENTAL** | Engine builds, IPC, tests, audio-open, and bounded failure were verified under WSL2. Visible capture on a representative real desktop is still unverified. |
| Linux X11 capture | **DISABLED** | `x11grab` is disabled for alpha because bounded cancellation is unresolved. |

Windows hybrid/cross-adapter capture is not supported for alpha. See
[`docs/SUPPORTED_PLATFORMS.md`](docs/SUPPORTED_PLATFORMS.md) and
[`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) for the evidence and remaining limits.

## Features

- Configurable background replay history, frame rate, resolution, bitrate, and
  scaling
- Normal and extended clip saves through global hotkeys
- Selected-monitor capture and selected-monitor screenshots
- H.264, HEVC, and AV1 hardware-encoding paths for NVIDIA, AMD, and Intel on
  Windows, with the qualification limits above
- System-output and microphone capture with synchronized multi-track clip
  infrastructure
- Dynamic playback mixing for tracks actually present in a saved clip
- System tray/background operation and persistent Windows autostart
- Clip browser, viewer, trim editor, transactional processing, and export
- Optional watermark, auto-crop, and webcam-overlay processing
- Presets and game/window detection
- Optional upload to an endpoint configured by the user

## How it works

```text
┌─────────────────────┐     Shared Memory v4      ┌────────────────────────┐
│   FTHR_UI (Python)  │ ◄───────────────────────► │  Native capture engine │
│   PySide6 frontend  │                            │  Windows: WGC + DXGI   │
│   Library / editor  │                            │  Linux: Wayland        │
│   Settings / upload │                            │  Platform audio input  │
└─────────────────────┘                            └────────────────────────┘
```

The native capture engine owns capture, encoding, replay buffering, and clip
publication. The PySide6 application owns settings, the library, playback,
editing, notifications, and lifecycle. A versioned shared-memory contract keeps
the two processes synchronized.

On Windows, capture uses WGC/DXGI with NVENC, AMF, or QSV and WASAPI audio. On
Linux, the alpha engine supports compositors exposing `wlr-screencopy` or
`ext-image-copy-capture`, with PulseAudio or PipeWire's PulseAudio compatibility
layer for system audio.

## Building from source

Use Python 3.14 for the Windows alpha artifact. Python 3.12 remains the tested
CI floor. Install the pinned dependencies rather than resolving loose package
versions:

```powershell
python -m pip install -r requirements-alpha.txt -r requirements-dev.txt
python tools/fetch_third_party.py --all
```

### Windows

Windows development requires Visual Studio 2022 with the C++ desktop workload.
The native solution is `FTHRcapture/FTHRcapture.sln`; packaging uses
`FTHR.spec` and `installer_windows.iss`.

### Linux

Linux builds require CMake, a C++ toolchain, Wayland development packages, and
PulseAudio development headers. The build downloads and verifies the pinned
LGPL FFmpeg distribution before assembling the AppImage:

```bash
bash build_linux.sh
```

See [`BUILDING.md`](BUILDING.md) for the complete setup, build, packaging, and
verification commands.

## Privacy and ownership

**Your recordings belong to you. Privacy isn't a premium feature.**

FTHR Clips is local-first: capture and clip saving run on the user's machine.
There is no required account, telemetry, analytics, or cloud service. Upload is
optional and only targets the endpoint a user configures.

The alpha currently stores an optional upload token in plaintext in the local
settings file. Do not use a high-value credential there. This limitation is
documented rather than hidden.

Useful software should not automatically mean another monthly subscription.
FTHR Clips is being built in the open-source tradition so users can inspect,
modify, and understand the software running on their machines.

## Verification

```bash
python -m pytest tests/
python -m ruff check .
python tools/verify_release_licenses.py --tree .
python tools/verify_version_consistency.py
python tools/verify_shared_memory_contract.py
python tools/verify_engine_response_contract.py
python tools/verify_exception_handling.py
python tools/scan_repo_hygiene.py --all-files
```

Hardware and desktop qualification cannot be replaced by CI. A green test run
does not prove that an untested GPU, compositor, or Windows version is supported.

## Contributing

Issues and pull requests are welcome. Please open an issue before a large
architectural change and keep platform claims tied to evidence.

- Python style and checks are configured in `pyproject.toml`.
- C++ changes should follow the surrounding source and include focused native
  coverage where practical.
- Never commit clips, user settings, credentials, build output, or downloaded
  third-party runtime binaries.

Start with [`CONTRIBUTING.md`](CONTRIBUTING.md), [`BUILDING.md`](BUILDING.md), and
[`docs/TESTING.md`](docs/TESTING.md).

## License

FTHR Clips' first-party source is licensed under the
[GNU General Public License v3](LICENSE), identified as `GPL-3.0-only`.

Third-party components retain their respective licences. Their notices, exact
versions, and redistribution information are documented in
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and [`licenses/`](licenses/).

Copyright © 2026 FTHR Community. The program comes without warranty; see
[`LICENSE`](LICENSE) for the complete terms.

## Essential documentation

| Document | Purpose |
|---|---|
| [`BUILDING.md`](BUILDING.md) | Windows/Linux setup, native builds, packaging, and dependency retrieval |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Source layout, conventions, and contribution workflow |
| [`docs/TESTING.md`](docs/TESTING.md) | Automated, native, and physical verification guidance |
| [`docs/SUPPORTED_PLATFORMS.md`](docs/SUPPORTED_PLATFORMS.md) | Evidence-based platform support matrix |
| [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) | Current limitations and unverified paths |
| [`SECURITY.md`](SECURITY.md) | Private vulnerability reporting |
| [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | Third-party licences and notices |

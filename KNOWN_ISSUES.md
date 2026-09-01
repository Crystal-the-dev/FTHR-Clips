# Known Issues — 1.0.0-alpha

This is a local alpha build. It is not a public release, and the items below are the current blockers as of 2026-09-01.

## Release blockers

- **Post-fix Windows qualification is still pending.** NVIDIA replay-stall fixes are integrated and covered by automated tests, but H.264, HEVC and AV1 still need a complete physical rerun on the supported Windows/NVIDIA host before release.
- **HEVC editor playback still needs a fresh physical test.** An earlier build reported a Qt Multimedia playback error; that result neither proves the current build fixed nor still has the problem.
- **Microphone capture still needs a fresh physical test.** An earlier physical run produced a silent microphone track; the current build has not yet been requalified with real microphone input.
- **Release artifacts are not signed.** There is no approved Authenticode identity configured for a public build.

This does not mean the codebase is broken. It means the build is not yet ready for public distribution and needs one more physical qualification pass on the hardware we intend to support.

The machine-readable asset and licence gates are technically clean. That is
release evidence, not legal advice or a substitute for final legal approval.

## Platform and hardware gaps

- AMD and Intel paths are integrated and tested in automation, but not physically qualified.
- Windows 11 per-app audio and borderless capture are not available on the current qualification host.
- The Linux engine, native CTests, AppImage structure, extraction and licence gates are exercised during packaging. Real Wayland/X11 capture, hotkeys, audio and multi-monitor behaviour still require a native physical Linux qualification run; WSL2 does not satisfy that gate.
- A WSL2 direct AppRun smoke test reaches Qt Multimedia successfully, but the environment has no real PipeWire/capture devices and the failed engine connection currently emits a shared-memory cleanup `BufferError`. Treat direct AppImage runtime as unverified until the native Linux qualification run.
- Hybrid or cross-adapter capture is not supported in this alpha.

## Behaviour and limits

- Windows clip paths are capped at 255 characters.
- Only one FTHR Clips instance can run per user.
- Some cleanup issues still remain log-only.
- There has not yet been a multi-hour gaming soak test.

## Privacy

- Upload is off by default.
- There is no telemetry or analytics.
- If the optional uploader is enabled, the token is stored in plaintext in the user settings file.
- Linux hotkeys use an owner-only Unix socket; direct root capture is not used.

Build and package commands are documented in `BUILDING.md`.

## AppImage location

The Linux AppImage is generated locally, not shipped in the repository. Run:

```bash
bash build_linux.sh
```

Then the resulting file will be in:

```text
build_output/FTHRClips-1.0.0-alpha-x86_64.AppImage
```

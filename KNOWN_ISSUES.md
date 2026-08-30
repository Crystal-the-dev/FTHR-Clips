# Known Issues — 1.0.0-alpha

This is a pre-alpha test build. The items below are current as of 2026-08-31;
`NOT RUN` means exactly that and must not be presented as working.

## Public-release blockers

- **Windows NVIDIA replay can stall.** On the current RTX 4060 Ti / Windows 10
  host, HEVC completed the long qualification, but H.264 stopped advancing
  after about 4,079 frames and AV1 after about 704 frames. A stale replay ring
  could still save old footage; the qualification gate now requires fresh
  frames after ten rapid saves.
- **HEVC editor playback failed on the qualification host.** The clip itself
  fully decodes with FFmpeg, but Qt Multimedia reported `PLAYBACK ERROR`.
  H.264 play/pause was physically verified; AV1 editor playback was not run.
- **The microphone stream was silent during the latest physical run.** A
  distinct microphone track was present, but measured digital silence. Device
  selection and real audible content require another physical test.
- **Release artifacts are unsigned.** No approved Authenticode identity is
  configured. Unsigned local friend builds are not public releases.

The machine-readable asset gate is technically clean after unused MP3s and the
unprovenanced Gary default were removed. Gary now uses the generated FTHR logo;
user-selected images are unchanged. This does not replace final legal approval.

## Platform and hardware gaps

- AMD and Intel H.264/HEVC/AV1 paths are code-integrated and automated-tested,
  but no matching physical hardware qualification has run.
- Windows 11 per-app process-loopback audio and the official borderless-capture
  capability are not available on the Windows 10 qualification host.
- Linux engine/build tests exist, but visible capture on a representative
  native Wayland or X11 desktop, global hotkeys, audio content, AppImage
  runtime, multi-monitor behaviour, suspend/resume, and device recovery remain
  unverified.
- Windows hybrid/cross-adapter capture is unsupported for this alpha; there is
  no CPU full-frame fallback.

## Behaviour and limits

- Clip paths are capped at 255 characters on Windows.
- Only one FTHR Clips instance runs per user. A second launch asks the first
  instance to restore its window and then exits.
- Some optional cleanup failures remain log-only. Clip finalization failures
  are surfaced as warnings or failed readiness states.
- No two-hour / 50-save gaming soak has run.

## Privacy

- Upload is disabled by default and there is no telemetry or analytics.
- If the optional uploader is enabled, its auth token is stored in plaintext in
  the user settings file. Do not use a high-value token in an alpha build.
- Linux hotkeys use an owner-only Unix socket; direct root key capture is not
  used.

Build and package commands are documented in `BUILDING.md`.

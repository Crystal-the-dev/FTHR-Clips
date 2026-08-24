# FTHR Clips 1.0.0-alpha

Pre-alpha release candidate for local qualification. This source state is not
yet approved for public distribution.

## Included

- Background replay with transactional clip publication.
- Windows WGC/DXGI capture with H.264, HEVC, and AV1 hardware paths.
- System-output and microphone tracks with synchronized in-app playback.
- Code-integrated Windows 11 per-app audio stems.
- Selected-monitor screenshots, crop editing, tray/background operation, and
  persistent autostart settings.
- Windows installer and Linux AppImage build definitions with gated assets and
  third-party licence notices.

## Qualification status

- Windows 10 with same-adapter NVIDIA hardware is physically qualified for the
  core replay, audio, screenshot, and background workflows.
- AMD and Intel encoder paths are automated-tested but hardware-unverified.
- Windows 11 per-app stems and the official borderless-capture capability still
  require a physical Windows 11 qualification run.
- Linux builds and native tests pass in WSL2, but visible capture on a real
  Wayland desktop, PipeWire per-app audio, and AppImage runtime remain unverified.
- Local Windows artifacts are unsigned until a release operator supplies an
  approved code-signing identity.

Linux hotkeys use an owner-only Unix socket and do not require root or membership
in the `input` group. See `README.md`, `KNOWN_ISSUES.md`, and
`docs/RELEASE_CHECKLIST.md` before installing or distributing this build.

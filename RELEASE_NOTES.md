# FTHR Clips 1.0.0-alpha

Pre-alpha release candidate for local qualification. This source state is not
yet approved for public distribution.

## Included

- Background replay with transactional clip publication.
- Windows WGC/DXGI capture with H.264, HEVC, and AV1 hardware paths.
- Manual recording now reuses the active hardware video and AAC packet streams
  in a bounded fragmented-MP4 writer. Completed keyframe fragments remain
  playable after interruption, and Stop no longer performs a second software
  encode, WAV-sidecar finalization, or whole-file FFmpeg remux.
- System-output and microphone tracks with synchronized in-app playback.
- Code-integrated Windows 11 per-app audio stems.
- Selected-monitor screenshots, crop editing, tray/background operation, and
  persistent autostart settings.
- Clip-backed overlay previews plus camera, image, and mouse-click burn-ins with
  draggable placement and per-input visibility. Keyboard visualization is
  marked as coming soon; clip-saving keyboard hotkeys remain available.
- Settings combo boxes open as simple below-field dropdowns, and Customize
  sections expand or collapse immediately without height animations.
- Audio settings expose persistent per-event notification volumes again.
- Optional exports and shares open with an animated bottom-right Capture Card;
  source clips remain untouched so trimmed videos always animate from time zero.
- Typography offers Oswald as the sole built-in family plus portable TTF/OTF
  imports, and the Source popup now fits tightly to its visible controls.
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

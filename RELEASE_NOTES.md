# FTHR Clips 1.0.0-alpha

This is a local alpha build for qualification work. It is not approved for public release.

## What is in here

- Replay capture and transactional clip saving
- Windows WGC/DXGI capture with H.264, HEVC and AV1 paths
- Manual recording that keeps the active stream and writes a stable MP4 output
- System audio and microphone tracks with in-app playback
- Monitor screenshots, crop editing and background operation
- Persistent autostart and settings
- Overlay previews, webcam and click burn-ins
- Installer and Linux AppImage build definitions with release checks in place

## Current status

This build is still being validated, not distributed publicly.

- NVIDIA H.264/HEVC/AV1 stall fixes are integrated and covered by automated
  lifecycle tests. A complete physical Windows qualification after those fixes
  has **not** run yet.
- HEVC editor playback and audible microphone content also require a new
  physical run; earlier failures are not evidence that the current source is
  fixed or still broken.
- AMD and Intel paths are automated-tested but hardware-unverified.
- Local Windows artifacts remain unsigned.
- The Linux engine builds and all native CTests pass in WSL2. The AppImage also
  passes construction, licence, and extraction checks there, but native
  Wayland/X11 capture, audio, hotkeys, and multi-monitor behaviour remain
  physically unverified.

## Linux AppImage location

The AppImage is not checked in to the repo. It is generated locally by running:

```bash
bash build_linux.sh
```

Then it lands in:

```text
build_output/FTHRClips-1.0.0-alpha-x86_64.AppImage
```

This path is a local build artifact. Passing the package gates does not make it
a public-release-qualified or physically qualified Linux build.

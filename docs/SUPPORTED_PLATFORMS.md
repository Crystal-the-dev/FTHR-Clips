# Supported Platforms

**Read the "tested" column literally.** A row that says `NOT RUN` has never been
executed by anyone on this project. It is not a prediction that it will fail —
it is a statement that nobody knows.

Last updated: 2026-08-29.

## Summary

| Platform | State |
|---|---|
| Windows 10 x64 + same-adapter NVIDIA | **Partial** on RTX 4060 Ti: HEVC replay passed; H.264 and AV1 capture stalled in the latest run |
| Windows + AMD / Intel | Code-integrated and automated-tested; physical hardware `NOT RUN` |
| Windows hybrid/cross-adapter | Unsupported for alpha; no CPU full-frame fallback |
| Linux — engine, IPC and bounded failure | **Verified** on Ubuntu 24.04 / WSL2 |
| Linux — real Wayland desktop capture (visible pixels) | **NOT VERIFIED** |
| Linux — native X11 | Source-integrated with RandR-selected geometry; physical qualification `NOT RUN` |

FTHR Clips must not be advertised as broadly Windows- or Linux-qualified. AMD,
Intel, Windows 11-specific paths, and representative Linux desktops remain
unverified; NVIDIA H.264/AV1 currently have reproducible stall evidence.

## The environment that was actually tested

| Item | Value |
|---|---|
| Distribution | Ubuntu 24.04.3 LTS (Noble Numbat) |
| Kernel | 6.18.33.2-microsoft-standard-WSL2 |
| Architecture | x86_64 |
| Environment | **WSL2 with WSLg** — not a bare-metal Linux desktop |
| Display server | WSLg Weston (Wayland) + XWayland (`WAYLAND_DISPLAY=wayland-0`, `DISPLAY=:0`) |
| Desktop environment | none (`XDG_CURRENT_DESKTOP` unset) |
| Compositor | Weston (RDP backend) |
| Capture backend used | None: WSLg exposes neither supported Wayland protocol and XWayland fallback is deliberately refused |
| Audio | PulseAudio 17.0 via `/mnt/wslg/PulseServer`; current smoke resolved `RDPSink.monitor` |
| GPU | NVIDIA GeForce RTX 4060 Ti, driver 610.62 (WSL passthrough) |
| Encoder used | `av1_nvenc` (hardware) |
| Python | CPython 3.12.3 |
| Qt / PySide6 | Qt 6.11.1 / PySide6 6.11.1 |
| Compiler | GCC 13.3.0 |
| CMake | 3.28.3 |
| FFmpeg | 6.1.1 (Ubuntu, **GPL build** — see licence note) |
| Monitors | one virtual output, 3640×1080 |
| Scaling | 1.0 (no fractional scaling available) |

Regenerate this table on any machine with:

```bash
bash tools/linux_system_report.sh
```

### The caveat that matters most

An older build exercised the XWayland/x11grab pipeline end to end and wrote a
decodable MP4, but **its pixels were black**. Measured directly:

```
first frame luma: min 0, max 0, mean 0.0, distinct values 1
```

XWayland under WSLg had no real root-window content for `x11grab` to read. That
historical result proved plumbing, not a picture, and does not apply to the
current build because it refuses XWayland fallback. Nobody has yet confirmed
that the current Wayland or native-X11 build records visible content on a
representative desktop.

## Support matrix

| Distro | Desktop | Display server | Compositor | Capture backend | Audio | Hotkey method | Tested | Notes |
|---|---|---|---|---|---|---|---|---|
| Ubuntu 24.04 | none | Wayland + XWayland | Weston (WSLg) | none | Pulse monitor opens | socket only (no compositor binds) | **YES** — build, IPC, audio-open and bounded failure only | No supported Wayland protocol; XWayland fallback refused |
| Arch / any | Hyprland | Wayland | Hyprland | wlr-screencopy | PipeWire | auto-written binds + socket | `NOT RUN` | The primary intended target; auto-config code is untested |
| Any | KDE Plasma | Wayland | KWin | ext-image-copy-capture if compositor exposes it | PipeWire | **manual** — see below | `NOT RUN` | Unsupported when the protocol is absent |
| Any | GNOME | Wayland | Mutter | ext-image-copy-capture if compositor exposes it | PipeWire | **manual** | `NOT RUN` | Unsupported when the protocol is absent |
| Any | any | native X11 | any | FFmpeg x11grab with UI-resolved RandR rectangle | PulseAudio/PipeWire | **manual** | source/build/invalid-display tests only; physical `NOT RUN` | Process-isolated cancellation boundary; requires `xrandr`; experimental until real 30/60-second qualification |
| Any | any | Wayland | anything else | none available | — | — | `NOT RUN` | Engine reports this clearly and exits the Wayland path |

### Wayland backend availability

The alpha engine tries, in order: `wlr-screencopy` →
`ext-image-copy-capture`. Observed on WSLg Weston:

```
[WlrBackend] zwlr_screencopy_manager_v1 not available — compositor must support wlr-screencopy
[ExtBackend] ext-image-copy-capture not available
[Backend] No Wayland capture backend available; refusing XWayland/x11grab fallback
[Backend] No capture backend available on this system
[Capture] Recovery exhausted after 3 attempts
[FTHR] Capture backend stopped; exiting engine
```

That is the intended, bounded failure path for an unsupported compositor.
Native X11 uses the dedicated x11grab path only in an actual X11 session; the
UI-owned engine process remains the hard cancellation boundary if XCB itself
stops responding. Physical native-X11 qualification is still required before
making an alpha support claim.

## Hotkeys

Direct key capture needs root on Linux and is disabled by design. Hotkeys work
by having the compositor send a command to FTHR's private Unix socket.

The socket lives at **`$XDG_RUNTIME_DIR/fthr/hotkey.sock`** (fallback
`~/.fthr/run/hotkey.sock`). Directory mode `0700`, socket mode `0600`, both
owned by you. It is **not** in `/tmp` — see `core/linux_runtime.py`.

Get the exact command for your machine from the app (Settings → Hotkeys shows
it) or:

```bash
python3 -c "import sys; sys.path.insert(0,'FTHR_UI'); \
from core.linux_runtime import hotkey_socket_path; print(hotkey_socket_path())"
```

| Desktop | Method | Tested |
|---|---|---|
| Hyprland | FTHR writes `~/.config/hypr/fthr-hotkeys.conf` and reloads via `hyprctl` | `NOT RUN` |
| KDE Plasma | Manual: System Settings → Shortcuts → Add Command | `NOT RUN` |
| GNOME | Manual: Settings → Keyboard → Custom Shortcuts | `NOT RUN` |
| X11 WMs | Manual: your WM's keybinding config | `NOT RUN` |

The command to bind is:

```
echo -n "save_clip" | /usr/bin/nc -U $XDG_RUNTIME_DIR/fthr/hotkey.sock
```

Actions: `save_clip`, `save_extended_clip`, `save_screenshot`,
`confirm_game_detection`, `dismiss_game_detection`.

`nc` is required. FTHR resolves it once through `PATH` and uses the absolute
path; if it is missing the UI says so instead of silently doing nothing.

## Required system packages

Python packages come from `requirements-alpha.txt`. These are **system**
libraries that pip cannot supply:

| Distro | Build + runtime |
|---|---|
| Debian / Ubuntu | `build-essential cmake pkg-config libavcodec-dev libavformat-dev libavutil-dev libavdevice-dev libswscale-dev libswresample-dev libwayland-dev wayland-protocols libwayland-bin libpulse-dev libportaudio2 libegl1 libxcb-cursor0 libxkbcommon-x11-0` |
| Arch | `base-devel cmake pkgconf ffmpeg wayland wayland-protocols libpulse portaudio` |
| Fedora | `gcc-c++ cmake pkgconf ffmpeg-devel wayland-devel wayland-protocols-devel pulseaudio-libs-devel portaudio` |

Optional helpers: `hyprctl` (Hyprland), `xdotool` + `xprop` (X11 window/game
detection), `xrandr` (required by native-X11 selected-monitor capture),
`grim` (Wayland screenshots), `openbsd-netcat` (hotkeys),
`xdg-utils` (open clips folder). `bash tools/linux_system_report.sh` lists which
of these it found.

## Licence note for Linux builds

**Resolved 2026-08-06 (AUDIT-014).** The Linux engine no longer touches the
distribution's FFmpeg. It is compiled against, and ships with, a pinned LGPL
build:

| | |
|---|---|
| Version | `n8.1.2-34-g9b6c8969e0` (BtbN, release branch 8.1) |
| Licence | LGPL v3 or later (`--enable-version3`, no `--enable-gpl`) |
| SONAMEs | libavcodec.so.62, libavformat.so.62, libavutil.so.60, libavdevice.so.62, libavfilter.so.11, libswscale.so.9, libswresample.so.6 |
| Software encoders | libopenh264 (H.264), libkvazaar (HEVC), libsvtav1/libaom (AV1) — no x264, no x265 |
| Hardware encoders | NVENC, VAAPI, QSV all present |
| glibc baseline | 2.28 — but see the portability note below |
| Provenance | `tools/ffmpeg_manifest_linux.json`, archive and per-library sha256 |

Two further FFmpeg copies live inside Python wheels — Qt Multimedia's
(the official PySide6 Qt runtime, LGPLv2.1) and OpenCV's
(opencv-python-headless, LGPLv2.1). Neither
is loaded by the engine; both are documented in the manifest and licence-checked
on every build.

### Minimum Linux base

| Component | Requires |
|---|---|
| Pinned FFmpeg | glibc 2.28 |
| FTHR engine | **glibc 2.38** |
| Whole AppImage | **glibc 2.39** |

The bundle as a whole is limited by what it was **built on**, not by FFmpeg: the
engine and the PyInstaller runtime were compiled on Ubuntu 24.04 (glibc 2.39).
So the AppImage currently needs **glibc ≥ 2.39** — Ubuntu 24.04, Debian 13,
Fedora 40 or newer. Building on an older base image would lower this
considerably; that has not been done and is `NOT RUN`.

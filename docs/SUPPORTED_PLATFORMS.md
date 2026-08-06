# Supported Platforms

**Read the "tested" column literally.** A row that says `NOT RUN` has never been
executed by anyone on this project. It is not a prediction that it will fail —
it is a statement that nobody knows.

Last updated: 2026-08-06.

## Summary

| Platform | State |
|---|---|
| Windows 10/11 x64 | Built and bundled; **not exercised at runtime this cycle** |
| Linux — engine, IPC, clip pipeline | **Verified** on Ubuntu 24.04 / WSL2 / XWayland |
| Linux — real desktop capture (visible pixels) | **NOT VERIFIED** — see the caveat below |
| Linux — Hyprland / KDE / GNOME / bare-metal Wayland | `NOT RUN` |

FTHR Clips is **not** "Linux supported". Exactly one Linux environment has been
exercised, and it is an unusual one.

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
| Capture backend used | **x11grab**, via the X11 fallback |
| Audio | PulseAudio 17.0 via `/mnt/wslg/PulseServer` (RDPSink / RDPSource) |
| GPU | NVIDIA GeForce RTX 4060 Ti, driver 610.62 (WSL passthrough) |
| Encoder used | `av1_nvenc` (hardware) |
| Python | CPython 3.12.3 |
| Qt / PyQt6 | Qt 6.11.0 / PyQt6 6.11.0 |
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

The capture pipeline works end to end: the engine grabs frames, the ring buffer
fills, NVENC encodes, and a valid, fully decodable MP4 is written. **But the
pixels in those frames are black.** Measured directly:

```
first frame luma: min 0, max 0, mean 0.0, distinct values 1
```

XWayland under WSLg has no real root-window content for `x11grab` to read.
So this proves the *plumbing*, not the *picture*. Nobody has yet confirmed that
FTHR Clips records what is actually on a Linux screen.

## Support matrix

| Distro | Desktop | Display server | Compositor | Capture backend | Audio | Hotkey method | Tested | Notes |
|---|---|---|---|---|---|---|---|---|
| Ubuntu 24.04 | none | Wayland + XWayland | Weston (WSLg) | **x11grab** | PulseAudio | socket only (no compositor binds) | **YES** — engine, IPC, clip save, audio | Frames are black; WSLg has no wlr-screencopy |
| Arch / any | Hyprland | Wayland | Hyprland | wlr-screencopy | PipeWire | auto-written binds + socket | `NOT RUN` | The primary intended target; auto-config code is untested |
| Any | KDE Plasma | Wayland | KWin | wlr-screencopy ✗ / ext-image-copy-capture | PipeWire | **manual** — see below | `NOT RUN` | KWin does not implement wlr-screencopy |
| Any | GNOME | Wayland | Mutter | ext-image-copy-capture | PipeWire | **manual** | `NOT RUN` | Mutter does not implement wlr-screencopy |
| Any | any | X11 | any | x11grab | PulseAudio/PipeWire | **manual** | Partially — backend exercised under XWayland only | Never tested on a real X11 session |
| Any | any | Wayland | anything else | none available | — | — | `NOT RUN` | Engine reports this clearly and exits the Wayland path |

### Wayland backend availability

The engine tries, in order: `wlr-screencopy` → `ext-image-copy-capture` → `x11grab`.
Observed on WSLg Weston:

```
[WlrBackend] zwlr_screencopy_manager_v1 not available — compositor must support wlr-screencopy
[ExtBackend] ext-image-copy-capture not available
[Backend] No Wayland capture backend available
[Backend] Using x11grab
```

That is the intended, legible failure path for an unsupported compositor, and it
works. The X11 fallback is load-bearing on any non-wlroots desktop and must not
be removed.

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
detection), `grim` (Wayland screenshots), `openbsd-netcat` (hotkeys),
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
(PyQt6-Qt6, LGPLv2.1) and OpenCV's (opencv-python-headless, LGPLv2.1). Neither
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


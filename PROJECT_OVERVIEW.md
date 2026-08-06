# FTHR Clips — Project Overview

Orientation document for contributors and AI assistants. Reflects the tree at
`FTHR_Clips_source/FTHR_Clips` as of 2026-08-05.

> ⚠️ `Desktop\CLAUDE.md` describes an **older, different** layout (`engine/`,
> `ui/`). It does not match this tree. Trust this file.

---

## Purpose

Background instant replay — open-source ShadowPlay. A rolling ring buffer of the
screen is always recording; a global hotkey writes the last N seconds to
`~/FTHR_Clips` as MP4. No account, no cloud, no subscription. Runs as a tray app.

## Primary user flows

1. **Launch** → engine spawns → status reaches `CAPTURING`.
2. **Save clip** (`F9`) → engine writes last 30 s → optional mic/multiband mux,
   watermark, crop, webcam overlay → optional upload.
3. **Browse** → thumbnail grid → trim/crop/export/share in the clip viewer.
4. **Configure** → capture source, codec, audio routing, hotkeys, presets, theme.

## Layout

```
FTHR_Clips/
├── FTHR_UI/               Python/PyQt6 frontend — 44 files, ~17.3k LOC
│   ├── main.py            MainWindow + entry point (5.4k LOC — monolith)
│   ├── core/              Non-UI logic
│   │   ├── capture_bridge.py     Shared-memory IPC to the engine  ★ contract
│   │   ├── single_instance.py    Startup guard (one instance per user)
│   │   ├── hotkey_manager.py     Global hotkeys + Linux Unix socket
│   │   ├── settings_manager.py   ~/.fthr/settings.json
│   │   ├── upload_manager.py     Background upload (only outbound network path)
│   │   ├── mic_recorder.py       Continuous mic capture for post-mux
│   │   ├── audio_mixer.py, compositor.py, focus_monitor.py,
│   │   │   game_detector.py, camera_recorder.py, theme_manager.py,
│   │   │   presets_manager.py, clip_metadata_manager.py
│   └── ui/                Widgets — clip_viewer (2.9k), customize_page (1.9k),
│                          clip_grid (1.4k), capture_settings_widget (806) …
├── FTHRcapture/           Windows C++ engine (~8.6k LOC), VS solution
│   └── FTHRclips/{src,include}/   capture_engine.cpp is 2.6k LOC
│       └── third_party/ffmpeg/    ⚠️ GPLv3 build — see AUDIT_REPORT AUDIT-005
├── FTHRcapture_linux/     Linux C++ engine (~3.3k LOC), CMake
│   └── src/               backend_wlr / backend_x11 / audio_multi_capture
├── tests/                 16 files, 81 passing (Python only — engine untested)
├── installer_windows.iss  Inno Setup
├── build_linux.sh         → AppImage
└── FTHR.spec / FTHR_linux.spec   PyInstaller
```

## Entry points

| Entry | What it does |
|---|---|
| `FTHR_UI/main.py::main()` | The app. Takes the single-instance lock, then builds `MainWindow`. |
| `main()` with `--card-process` | Re-entry as the capture-card subprocess (frozen Windows exe). |
| `FTHRcapture/.../main.cpp` | Engine process; argv contract defined by the UI. |

## Data flow — saving a clip

```
hotkey  →  HotkeyManager signal
        →  MainWindow._save_clip()
             ├─ debounce (1 s), build path, mkdir, de-collide filename
             ├─ Windows: reject paths > 255 chars (c_wchar*256 field)
             └─ CaptureBridge.save_clip()
                  ├─ write ui_string / ui_param1, then ui_command LAST
                  └─ block ≤1 s for SAVE_STARTED        ← blocks the Qt loop
        →  engine encodes on its own thread, writes the file
        →  _update_status() (2 Hz) polls poll_async_result()
             └─ CLIP_SAVED → refresh grid  |  ERROR_OCCURRED → error bar
        →  async post-processing (exactly one route):
             mic mux  |  multiband mux  |  finalize (watermark/crop/camera)
             └─ sets clip_ready → UploadManager may upload
```

**The field-order of `SharedMemoryLayout` is the entire API contract with the
engine.** It is hand-maintained in `capture_bridge.py` *and* `shared_memory.h`.
A mismatch produces garbage reads, not a crash. Do not reorder fields; command
IDs 4–9 are reserved.

## State on disk

| Path | Contents |
|---|---|
| `~/.fthr/settings.json` | All settings (**incl. upload auth token, plaintext**) |
| `~/.fthr/hotkeys.json` | Hotkey bindings |
| `~/.fthr/upload_history.json` | Upload dedup, pruned after 90 days |
| `~/.fthr/fthr.lock` | Single-instance lock (Linux) |
| `~/.fthr/logs/fthr.log` | Rotating log, 2 MB — **ask testers for this** |
| `~/FTHR_Clips/` | Clips, one subfolder per capture source |

JSON writes go through temp-file + `os.replace()`; corrupt files are moved to
`.corrupt` rather than discarded.

## Platform differences

| | Windows | Linux |
|---|---|---|
| Shared memory | `OpenFileMapping`/`MapViewOfFile`, UTF-16 | `mmap` `/dev/shm`, UTF-8 |
| Path field | **256 wchar — hard cap** | 1024 bytes |
| Hotkeys | `keyboard` lib, unprivileged | needs root → Unix socket + compositor binds |
| Compositor | n/a | Hyprland auto-configured; KDE/GNOME/X11 manual |
| Capture | DXGI / WGC | wlr-screencopy, X11 fallback |
| Instance lock | named kernel mutex | `flock` on `~/.fthr/fthr.lock` |
| Package | Inno Setup `.exe` | AppImage |

## Build & release

```bash
# Tests (both platforms)
QT_QPA_PLATFORM=offscreen pytest tests/

# Linux → build_output/*.AppImage
bash build_linux.sh

# Windows → see BUILD_WINDOWS.md (VS 2022 + Python 3.11 + PyInstaller + Inno)
```

CI (`.github/workflows/ci.yml`) runs the Python suite on Ubuntu for 3.11/3.12
only — it does **not** build either engine or exercise Windows.

## Risk areas

| Area | Why |
|---|---|
| **Licensing** | Bundled FFmpeg is GPLv3; project claims MIT. Blocks release. |
| **C++ engines** | ~12k LOC, zero automated tests, unreviewed. |
| **Shared-memory contract** | Hand-synced across two languages, no runtime version check. |
| **Linux hotkeys** | Only fully works on Hyprland. |
| **`main.py`** | 5.4k LOC; UI, save orchestration, settings and status all in one class. |
| **Silent failures** | 34 bare `except: pass` blocks defeat log-based diagnosis. |
| **No version control** | No history, no bisect, no release tag. |

## Conventions

- Settings live **only** in `~/.fthr/settings.json` via `SettingsManager`.
- Colors/fonts come from `ui/style.py` — never inline new ones.
- `CommandType` must match `shared_memory.h`; slots 4–9 reserved.
- Long-running work belongs off the Qt main thread. `QTimer.singleShot` does
  **not** fire from a plain `threading.Thread` — emit a signal instead.

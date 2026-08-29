# Testing

Three kinds of test, deliberately kept distinct. Conflating them is how a
project ends up claiming a platform works when only its build was checked.

| Kind | What it proves | Where it runs |
|---|---|---|
| **Build test** | The code compiles and links | CI, any machine |
| **Headless test** | Logic is correct; the engine starts, maps shared memory, encodes and writes a valid file | CI, WSL, a VM |
| **Desktop runtime test** | The application actually records what is on a real screen and hotkeys fire | A human, on a real desktop |

A green CI run says nothing about the third row.

## Automated tests

```bash
python -m pytest tests/
python -m ruff check .
```

`tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`, so widget tests work
headless without exporting anything.

Current Windows result: **518 passed / 34 skipped**. Platform-specific tests
skip on the other operating system; record the result of each release run
instead of treating this count as a permanent expectation.

### What the suite covers

| Area | File |
|---|---|
| Shared-memory contract (both platforms, from either platform) | `test_shared_memory_contract.py` |
| Private runtime dir + hotkey socket safety | `test_linux_runtime.py` (POSIX only) |
| External tool resolution under a manipulated `PATH` | `test_linux_tools.py` |
| Single-instance guard, incl. crash recovery with real processes | `test_single_instance.py` |
| FFmpeg helpers, settings, presets, audio mixing, upload retry, … | the rest |

### Tests that can fail

Verifiers that cannot fail are decoration. Two are explicitly tested for their
ability to reject:

* `test_shared_memory_contract.py` mutates a layout four ways (rename a field,
  shrink an array, widen a scalar, append a field) and asserts each is caught.
* `test_linux_runtime.py` asserts the socket preparation **refuses** to delete a
  regular file, a symlink, a directory, or a socket a live instance holds.

## Release gates

All four must exit 0 before a tag:

```bash
python tools/verify_release_licenses.py --tree .     # AUDIT-005: no GPL FFmpeg
python tools/verify_version_consistency.py           # AUDIT-008: one version
python tools/verify_shared_memory_contract.py        # Python <-> C++ layout
python tools/scan_repo_hygiene.py                    # secrets, user data, blobs
```

## Linux verification

### 1. Environment report — always do this first

```bash
bash tools/linux_system_report.sh
```

Records distro, kernel, session type, compositor, GPU, encoders, audio server,
toolchain, helper tools and FTHR runtime state. It reads only; it collects no
username, hostname, IP, machine-id or config contents. Attach it to bug reports.

### 2. Build the engine from scratch

```bash
rm -rf FTHRcapture_linux/build
cmake -S FTHRcapture_linux -B FTHRcapture_linux/build -DCMAKE_BUILD_TYPE=Release
cmake --build FTHRcapture_linux/build --parallel
ldd FTHRcapture_linux/build/FTHRclips | grep 'not found'   # must print nothing
```

### 3. Headless engine + IPC check

The engine runs standalone. Its argv contract is positional:

```
FTHRclips <fps> <buffer_s> <w> <h> <bitrate_kbps> <_> <_> <_> <scaling>
          <output> <codec_pref> <preset> <multiband> <audio_enabled>
```

```bash
FTHRcapture_linux/build/FTHRclips 30 10 1280 720 6000 0 0 0 0 "" 0 4 0 1
```

Expected on a machine exposing neither supported Wayland protocol:

```
[SHM] Created: /dev/shm/FTHR_SharedMemory_v4
[WlrBackend] zwlr_screencopy_manager_v1 not available — compositor must support wlr-screencopy
[ExtBackend] ext-image-copy-capture not available
[Backend] x11grab disabled for alpha: AUDIT-044 bounded cancellation unresolved
[Backend] No capture backend available on this system
[Capture] Recovery exhausted after 3 attempts
[FTHR] Capture backend stopped; exiting engine
```

On a supported compositor, check the shared memory while the engine is active:

```bash
ls -l /dev/shm/FTHR_SharedMemory_v4      # must be yours, mode 600
stat -c %s /dev/shm/FTHR_SharedMemory_v4 # must equal ctypes.sizeof(SharedMemoryLayout) = 4272
```

A size mismatch means the running engine was built from a different
`shared_memory.h`. The bridge refuses to map it and says so rather than reading
garbage.

### 4. Clip verification — mandatory

Never accept "a file appeared" as proof.

```bash
ffprobe -v error -show_entries \
  stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,start_time,duration \
  -of default=noprint_wrappers=1 clip.mp4

ffmpeg -v error -i clip.mp4 -f null -          # full decode, must print nothing
ffplay -autoexit clip.mp4                       # and actually watch it
```

**Also check the picture is not blank.** A pipeline can produce a perfectly
valid all-black video, and that is exactly what happens under WSLg:

```bash
ffmpeg -v error -i clip.mp4 -vframes 1 -pix_fmt gray -f rawvideo - | python3 -c "
import sys; d=sys.stdin.buffer.read()
print('min',min(d),'max',max(d),'distinct',len(set(d)))"
```

`distinct 1` means a uniform frame — the capture produced nothing visible.

### 5. Single-instance guard, with real processes

```bash
python FTHR_UI/main.py &                 # first instance
stat -c 'uid=%u mode=%a' ~/.fthr/fthr.lock   # must be you, 600
python FTHR_UI/main.py                   # must be refused, first keeps working
kill -9 %1                               # hard kill, no cleanup
python FTHR_UI/main.py                   # must acquire — no permanent lock
```

The lock file staying on disk after a crash is harmless: the `flock` is the
lock, not the file.

### 6. Hotkey socket safety

```bash
ls -l $XDG_RUNTIME_DIR/fthr/            # dir 0700, yours
ls -l $XDG_RUNTIME_DIR/fthr/hotkey.sock # socket 0600, yours
ls -l /tmp/fthr_hotkey.sock             # must NOT exist
```

## What only a human on a real desktop can do

None of these are covered by CI, and none have been performed:

- Launch the GUI on a real Wayland session and see a window
- Record a clip with visible content and watch it back
- Trigger a save with a global hotkey bound in Hyprland / KDE / GNOME
- Multi-monitor, monitor switching, resolution changes, fractional scaling
- Fullscreen games, focus changes, lock/unlock
- Suspend and resume
- Device removal: unplug a mic, change the default sink mid-recording
- Install → update → uninstall of the AppImage
- Soak: 2 hours, 50+ clips, watching RSS, fds, threads and zombies

See `RELEASE_CHECKLIST.md` for which of these block a tag.

## Reporting a bug

Attach:

1. `bash tools/linux_system_report.sh > report.txt`
2. `~/.fthr/logs/fthr.log` (rotates at 2 MB)
3. `ffprobe` output for any clip involved
4. The exact command you bound, if it is a hotkey problem

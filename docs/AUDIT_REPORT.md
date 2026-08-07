# FTHR Clips — Consolidated Alpha Audit

**Version under audit:** 1.0.0-alpha
**Authoritative tree:** `C:\Users\Tom\Desktop\FTHR_Clips_source\FTHR_Clips`
**Last updated:** 2026-08-07

This is the single audit document for the project. It merges six passes:

| Pass | Date | Scope | Environment |
|---|---|---|---|
| **I — Code & licensing** | 2026-08-05 | Inventory, static analysis, source review of IPC/save/hotkey/upload, dependency & licence review | Windows 10 Pro 19045, CPython 3.14.3, PyQt6 6.11.0 / Qt 6.11.1 |
| **II — Release engineering** | 2026-08-06 | Source of truth, git, dependency pinning, version centralisation, CI, release gates | same |
| **III — Linux verification** | 2026-08-06 | Linux build, engine runtime, IPC, clip pipeline, audio, single-instance, socket hardening, AppImage | Ubuntu 24.04.3 LTS under WSL2 + WSLg, CPython 3.12.3, Qt 6.11.0, GCC 13.3, CMake 3.28.3 |
| **VI — Diagnosability** | 2026-08-07 | Silent-failure inventory, logging infrastructure, secret redaction, poll-spam restraint, exception gate | Windows 10 Pro 19045, CPython 3.14 |
| **V — Engine publication contract** | 2026-08-07 | Producer inventory of both engines, publication ordering, Windows error diagnostics, static contract gate | Windows 10 Pro 19045 + MSVC 14.44 · Ubuntu 24.04 under WSL2, GCC 13.3 |
| **IV — Save ownership** | 2026-08-07 | Save-response ownership consolidation, save state machine, event-loop responsiveness | Windows 10 Pro 19045, CPython 3.14, offscreen Qt |

> **Scope honesty.** Nothing here is inferred from a passing build. Every item
> that was not executed is marked `NOT RUN` and listed in
> [Verification still outstanding](#verification-still-outstanding).
> Pass III ran in WSL2, which is **not a Linux desktop** — the consequences of
> that are stated explicitly wherever they matter.

---

## Release decision

### `NO-GO` for public distribution · Linux: `CONDITIONAL GO`

Two licence blockers stand between this tree and a publishable download. Neither
is an engineering problem; both are decisions.

**AUDIT-013 — PyQt6 is `GPL-3.0-only`.** Qt itself is LGPLv3; the *bindings* are
not. Any bundle containing PyQt6 is GPLv3 as a whole. Removing the GPL FFmpeg in
AUDIT-005 was necessary but not sufficient. Publishing the download as "MIT"
would be inaccurate.

**AUDIT-014 — no Linux AppImage can be built.** Distribution FFmpeg is a GPL
build; the Linux engine links it and PyInstaller bundles it, so `build_linux.sh`
stops at its own licence gate. There is currently no Linux distributable at all.

Beyond licensing, the honest state is: **the application has been built, tested
and driven programmatically, but nobody has watched it work.** No GUI has been
launched on either platform this cycle, no hotkey has ever fired on Linux, and
no Linux capture has been confirmed to contain a picture.

Options for AUDIT-013 — a product decision:

| Option | Consequence |
|---|---|
| Port the UI to **PySide6** (LGPLv3) | Bundle becomes LGPL-compatible; the MIT claim about FTHR's own source stays honest. Most work. |
| Release deliberately as **GPLv3** | No code work. Every README / RELEASE_NOTES / About / store string must say GPLv3, and full corresponding source must be offered. Also resolves AUDIT-014. |
| Buy a **commercial PyQt licence** | No code work, recurring cost, removes the copyleft obligation. |

---

## What the software is

A background instant-replay recorder — the open-source answer to
ShadowPlay/Medal. It keeps a rolling ring buffer of the screen; a global hotkey
writes the last N seconds to disk as an MP4.

### Architecture

```
┌──────────────────────────┐   Shared memory: FTHR_SharedMemory_v3    ┌────────────────────────────┐
│  FTHR_UI  (Python/PyQt6) │ ◄──────────────────────────────────────► │  Capture engine (C++)      │
│  ~17.3k LOC              │   fixed-layout struct, no serialization  │  Win: ~8.6k LOC (DXGI/WGC) │
│  main.py = 5.4k LOC      │                                          │  Linux: ~3.3k LOC (wlr/X11)│
│  settings / upload / UI  │   Unix socket (Linux hotkeys only)       │  FFmpeg + NVENC/AMF/QSV    │
└──────────────────────────┘ ◄────────────────────────────────────    └────────────────────────────┘
```

The struct in `FTHR_UI/core/capture_bridge.py` must match
`FTHRcapture/FTHRclips/include/shared_memory.h` and
`FTHRcapture_linux/src/shared_memory.h` byte for byte. It was hand-maintained on
three sides and is the most fragile contract in the project — a field-order
mistake produces garbage reads, not a crash. Pass II automated the check.

**Measured layout (pass II/III):** 23 fields · **2712 B** on Windows ·
**4248 B** on Linux · enums aligned · reserved command slots 4–9 intact. The
Linux figure was confirmed against a *running engine*: `/dev/shm/FTHR_SharedMemory_v3`
measured exactly 4248 bytes.

### Windows vs Linux — genuinely different, not a thin abstraction

| | Windows | Linux |
|---|---|---|
| Shared memory | `OpenFileMapping`/`MapViewOfFile`, UTF-16 (`c_wchar`) | `mmap` of `/dev/shm`, UTF-8 bytes |
| Path field size | **256 wchar** — hard cap | 1024 bytes |
| Hotkeys | `keyboard` lib, works unprivileged | needs root → Unix socket driven by compositor binds |
| Compositor | n/a | Hyprland auto-configured; KDE/GNOME/X11 require manual binds |
| Capture | DXGI / Windows Graphics Capture | wlr-screencopy → ext-image-copy-capture → x11grab |
| `is_recording` | set true while recording | **pinned false by design** ("captures continuously") |

The Linux hotkey story remains the weakest part of the product: outside Hyprland
the user must hand-write compositor binds. Pass III made the app say exactly
which desktop it detected and print the exact command to bind.

---

## Findings register

| ID | Severity | Area | Status |
|---|---|---|---|
| AUDIT-001 | P0 | Startup | **FIXED** (I) · **verified on Linux** (III) |
| AUDIT-002 | P1 | Save pipeline | **FIXED** (I) |
| AUDIT-003 | P1 | Linux privacy | **FIXED** (I) — socket mode |
| AUDIT-003b | P1 | Linux privacy | **FIXED** (III) — socket *path* |
| AUDIT-004 | P2 | Windows save | **FIXED** (I) |
| AUDIT-005 | P0 | Licensing | **RESOLVED** (I) |
| AUDIT-006 | P2 | Linux hardening | **FIXED** (III) |
| AUDIT-007 | P2 | Diagnosability | **PARTIALLY FIXED** (VI) |
| AUDIT-008 | P1 | Release engineering | **RESOLVED** (II) |
| AUDIT-009 | P2 | Reproducibility | **RESOLVED** (II) |
| AUDIT-010 | P2 | Diagnostics | **FIXED** (I) · hardened (II) |
| AUDIT-011 | P2 | UI responsiveness | **FIXED** (IV) |
| AUDIT-012 | P2 | Privacy | **OPEN** |
| AUDIT-013 | **P0** | Licensing | **OPEN — RELEASE BLOCKER** |
| AUDIT-014 | P0 | Linux packaging | **CONDITIONALLY RESOLVED** (2026-08-06) — see [AUDIT-014-LINUX-FFMPEG.md](AUDIT-014-LINUX-FFMPEG.md) |
| AUDIT-015 | P1 | Linux save pipeline | **FIXED** (III) |
| AUDIT-016 | P2 | Linux portability | **FIXED** (III) |
| AUDIT-017 | **P1** | Save pipeline / data integrity | **FIXED** (IV) |
| AUDIT-018 | P2 | Engine IPC correctness | **RESOLVED** (V) |
| AUDIT-019 | P2 | Windows engine diagnostics | **FIXED — RUNTIME NOT RUN** (V) |
| AUDIT-020 | P2 | Startup / UI responsiveness | **OPEN** |
| AUDIT-021 | **P1** | Windows save pipeline | **FIXED — RUNTIME NOT RUN** (V) |

---

## Pass I — Code and licensing (2026-08-05)

Tests: **68 → 81 → 93 passed**, 3 skipped.

### AUDIT-001 · P0 · FIXED · Both platforms
**No single-instance guard — two instances corrupt each other**

Two instances are actively destructive: two engines encode the same screen
(double GPU load; on single-session NVENC the second silently fails); both map
`FTHR_SharedMemory_v3`, whose command/response fields are a single-writer
contract, so each UI consumes the other's `engine_response` and saves time out;
and on Linux the second instance unlinks the hotkey socket and rebinds it,
**silently stealing every hotkey from the first**. A double-click on the desktop
icon was enough.

New `core/single_instance.py`: named kernel mutex on Windows,
`flock(LOCK_EX|LOCK_NB)` on `~/.fthr/fthr.lock` on Linux. Both are kernel-owned,
so a hard crash releases them. Wired in before any engine spawn, socket bind or
window creation. Deliberately **fails open** — a broken guard must not be the
thing that stops someone recording.

> **Pass III update — verified on Linux with real processes.** First instance
> acquires; second is refused; first keeps working; `SIGKILL` the holder; a new
> instance acquires. Lock file `~/.fthr/fthr.lock`, uid = user, **mode 600**.
> The file remaining on disk after a crash is harmless — the `flock` is the
> lock, not the file.
>
> Pass II also fixed two real defects in the Windows branch: it read
> `GetLastError()` *through* ctypes (which restores the thread's last-error
> value around every call, so the guard's answer depended on unrelated preceding
> Win32 calls), and it truncated a 64-bit `HANDLE` to `c_int` for lack of
> `argtypes`.

### AUDIT-002 · P1 · FIXED · Both platforms
**A clip that failed to write reported success — perceived data loss**

`save_clip()` waits only for `SAVE_STARTED`; the engine writes afterwards on its
own thread. If that write failed, the engine set `ERROR_OCCURRED` and **nothing
read it**. The UI flashed `SAVED`, played the animation, added a grid entry —
and the clip did not exist. The worst failure mode for a clipping tool.

New `CaptureBridge.poll_async_result()`: non-blocking, consumes the response
exactly once, tolerates a garbled `engine_string`. `_update_status()` surfaces a
real error bar with an **Open folder** action. 6 new tests.

### AUDIT-003 · P1 · FIXED · Linux · Privacy
**World-writable hotkey socket let any local user capture this user's screen**

`/tmp/fthr_hotkey.sock` was bound under the process umask, typically 0755–0777,
in a directory shared by every account. The server accepts any connection and
dispatches with no authentication — so any local account could run
`echo -n "save_screenshot" | nc -U …` and cause this user's screen to be
captured to this user's disk.

`bind()` is now wrapped in `umask(0o177)` — a `chmod` after `bind()` would leave
a race window — followed by an explicit `chmod(0o600)`.

The pass-I report noted its own residual risk: *"predictable-path pre-creation by
another user before FTHR starts is not defended against … a `$XDG_RUNTIME_DIR`
path would fix both properly."* That is AUDIT-003b, below.

### AUDIT-004 · P2 · FIXED · Windows
**Deep paths lost the clip and blamed disk space**

The Windows `ui_string` field is `c_wchar * 256`, so the bridge refuses paths
over 255 characters — correctly, but it returned a bare `False`, indistinguishable
from a write failure, and the UI told the user to check disk space. Reachable
with a long profile name plus a long game name. `_save_clip()` now reports the
actual cause, length and limit.

### AUDIT-005 · P0 · RESOLVED · Both platforms · Licensing
**The distributed binary was GPLv3, shipped under an MIT notice, with no third-party licences**

The bundled FFmpeg was built `--enable-gpl --enable-version3 --enable-libx264
--enable-libx265`. Replaced with the BtbN **LGPL** build
`n8.1.2-21-gce3c09c101` (ABI-identical, avcodec-62). Software fallbacks moved
x264 → **libopenh264**, x265 → **libkvazaar**; NVENC/AMF/QSV untouched.

`imageio-ffmpeg` (also GPL, and — as it turned out — never actually bundled on
Windows, which is why watermark, crop and export silently no-opped) was removed
entirely in favour of `core/ffmpeg_tools.py`.

Delivered: `THIRD_PARTY_NOTICES.md`, `licenses/`, `tools/ffmpeg_manifest.json`
(version + sha256 per shipped binary), and `tools/verify_release_licenses.py`
as an automated gate. Windows engine and PyInstaller bundle rebuilt and
verified. Tests 81 → 93.

> **Pass III note.** This gate did its job on its first real Linux exercise —
> see AUDIT-014.

### AUDIT-006 · P2 · Linux · Security hardening → **FIXED in pass III**
**External tools invoked by bare name — PATH-dependent**

16 `subprocess` call sites invoked `hyprctl`, `xdotool`, `xprop`, `nc`, `grim`,
`xdg-open`, `wmctrl` by bare name. Arguments are fixed, so this is **not**
command injection — the risk is a directory earlier in `PATH` shadowing the real
binary, plus a missing tool surfacing as a swallowed `FileNotFoundError`.
Resolved in pass III; see below.

### AUDIT-007 · P2 · Both · Diagnosability · **PARTIALLY FIXED** (see Pass VI)
**34 bare `try/except: pass` blocks**

Each is a failure that can never appear in a log or a bug report. For an alpha
whose diagnostic strategy is "testers send `~/.fthr/logs/fthr.log`", this is the
single biggest obstacle to acting on reports. Recommendation: a mechanical pass
converting each to a logged debug line.

### AUDIT-010 · P2 · Both · FIXED
**About screen reported the wrong version**

The About label was hardcoded `FTHR Clips v1.0.0` while every other source said
`1.0.0-alpha`. Pass II went further and removed the whole class of drift — see
AUDIT-008.

### AUDIT-011 · P2 · Both · UI responsiveness · **PARTIALLY FIXED**
**`save_clip()` blocks the Qt event loop for up to 1 second**

The `SAVE_STARTED` handshake busy-waits with `time.sleep(0.001)` on the main
thread. Against a hung engine the UI freezes for a full second on every hotkey
press.

> **Pass III.** The most common trigger is gone: on Linux the engine finishes
> fast saves *before* Python observes `SAVE_STARTED`, so the loop used to spin
> the entire second on every short clip — and then report failure. `CLIP_SAVED`
> now counts as an acknowledgement (AUDIT-015). The structural busy-wait on the
> main thread remains; fixing it properly means moving the handshake off the
> main thread. **No measurement was taken** — this is a code-structure finding.

### AUDIT-012 · P2 · Both · Privacy · **OPEN**
**Upload auth header stored in plaintext**

`upload_auth_header` (typically a bearer token) is written to
`~/.fthr/settings.json` in cleartext. Arguably acceptable for a local-first
alpha, but it must be documented — it is, in `KNOWN_ISSUES.md`. Also:
`test_server_connection()` passes a user-supplied URL to `urllib.request.urlopen`
without scheme validation, so `file://` is accepted; restrict to http/https.

### AUDIT-013 · P0 · Both · Licensing · **OPEN — RELEASE BLOCKER**
**PyQt6 is `GPL-3.0-only`, so the distributed build is a GPLv3 work**

Qt itself is LGPLv3 — it is the Python bindings that are GPL. This is
independent of AUDIT-005 and is not fixed by it. FTHR's own source stays MIT
(`LICENSE`), but a *download* must never be advertised as MIT. The third FFmpeg
copy inside Qt Multimedia (avcodec-61, LGPLv2.1) is conformant and documented.

Pass II made the split explicit in the About dialog and added a CI grep that
fails if any distributable is described as MIT.

---

## Pass II — Release engineering (2026-08-06)

Tests: **93 → 106 passed**, 3 skipped. Both AUDIT-008 and AUDIT-009 closed.

### AUDIT-008 · P1 · Process · **RESOLVED**
**The project was not under version control, and stale copies were in play**

The tree had no `.git` directory despite shipping a `.gitignore`, a CI workflow,
issue templates and a README pointing at a GitHub repo. No history, no bisect, no
way to tag what was released. Three stale Desktop copies were in play, plus a
`Desktop\CLAUDE.md` documenting an `engine/` + `ui/` layout two refactors old and shared
memory `_v1` when the contract is `_v3`.

Delivered:

- **Git repository**, branch `main`, honest single-commit import of the existing
  source — no fabricated history. 365 tracked files, 5.8 MB.
- **`.gitignore`** rewritten by category with reasons. It caught a real trap: the
  old `AppDir/` rule would have swallowed `fthr-clips.desktop` and the icon,
  which are *source* read by `build_linux.sh` — a clean clone would not have
  built.
- **`.gitattributes`** committed *before* the import, so line-ending
  normalisation never becomes an unreviewable whole-tree rewrite. CRLF for
  Windows tooling, LF forced for shell scripts.
- **`SOURCE_OF_TRUTH.md`** — names the authoritative tree, lists the
  non-authoritative copies with ready-to-review archive commands (not executed;
  nothing here depends on those paths), and replaces the backup model that
  created the ambiguity: *a backup is a `git clone --mirror` or a `git bundle`,
  never a working directory.*
- **`FTHR_UI/version.py`** as the single source of the product version. `main.py`,
  both PyInstaller specs and `build_linux.sh` derive from it;
  `tools/verify_version_consistency.py` fails the build on any file that
  hardcodes a version or disagrees. The Windows `.exe` now carries a VERSIONINFO
  resource — **verified: ProductVersion / FileVersion = `1.0.0-alpha`**; it
  previously had none at all, so "which build are you running?" was
  unanswerable. Also found: the About page said `Build: 2026.04.28`, four months
  stale.
- **`tools/verify_shared_memory_contract.py`** + 13 tests. Parses both C++
  headers, models compiler alignment, and compares field order, types, array
  extents, offsets, total size, enum values and the reserved slots 4–9 — for
  both platforms, from either platform. **Four negative tests** mutate a layout
  and assert the verifier rejects it; a gate that cannot fail is worse than none.
- **`tools/scan_repo_hygiene.py`** — secrets, user state, media and oversized
  binaries, scanned against what git actually tracks.
- **`tools/fetch_third_party.py`** — re-downloads the FFmpeg runtime (152 MB) and
  vc_redist, verifying every FFmpeg file against `ffmpeg_manifest.json`. This is
  what lets those binaries stay out of git without breaking a clean clone.
- **CI extended**: Python on Ubuntu *and* Windows across 3.12/3.14 installing
  from the lock, plus `pip check`, an `imageio-ffmpeg` absence assertion,
  byte-compile, ruff, pytest, a Windows PyInstaller run, **both C++ engines
  built**, a PE-architecture check, four release gates, a history scan for blobs
  over 2 MB, and a tag guard. Nothing is published from CI on purpose.
- **`RELEASE_CHECKLIST.md`** with `PASS` / `FAIL` / `NOT RUN` per gate, and no tag.

### AUDIT-009 · P2 · Both · **RESOLVED**
**Dependencies unpinned**

`requirements.txt` used `>=` for all 7 entries, so two testers could run
materially different Qt versions and UI bug reports were not reproducible.

Split into `requirements.in` (direct, human-edited), `requirements-alpha.txt`
(the lock — full transitive closure, exact pins, the only release install path)
and `requirements-dev.txt` (pinned tooling). `requirements.txt` remains as a
pointer.

**Verified with two clean venvs:** identical package sets (25 packages,
`Compare-Object` empty), `pip check` clean in both, imports succeed, identical
test results, and a full PyInstaller build from the locked environment.

The exercise immediately paid for itself: one test failed identically in *both*
venvs and passed on the system interpreter. That was not a lock problem — it
exposed the ctypes `GetLastError` defect in AUDIT-001 and a test that killed
`Popen.pid`, which in a Windows virtualenv is a launcher stub rather than the
process holding the mutex.

**Python 3.14 recorded as the alpha interpreter**, because that is what the
shipped bundle actually contains (`python314.dll`, `cpython-314` bytecode).
`BUILDING.md` had told people to use 3.11 *and* to install
`imageio-ffmpeg` — both wrong, the latter dangerously so.

---

## Pass III — Linux verification (2026-08-06)

Tests: **106 → 139 total** — Windows 112 passed / 27 skipped, **Linux 138 passed
/ 1 skipped**. The three tests previously skipped on Windows as "Linux-only"
finally ran.

> **Environment caveat, stated once and meant throughout.** This ran in WSL2
> with WSLg: Weston + XWayland, no desktop environment, no wlroots compositor.
> It is a real Linux kernel, toolchain, filesystem and process model — so build,
> IPC, permissions, encoding and file output are genuinely tested. It is **not**
> a Linux desktop, so compositor behaviour, visible capture and hotkeys are not.
>
> **Data captured during testing:** the WSLg virtual desktop and the PulseAudio
> monitor device. Not the Windows desktop. All artefacts were deleted afterwards.

### Build — PASS

```bash
rm -rf FTHRcapture_linux/build
cmake -S FTHRcapture_linux -B FTHRcapture_linux/build -DCMAKE_BUILD_TYPE=Release
cmake --build FTHRcapture_linux/build --parallel
```

0 errors, 2 warnings (unused `clock_ns()` in `encoder.cpp`; `memset` on the
non-trivial `WlrBackend::FrameBuffer`), Wayland protocol bindings regenerated,
ELF x86-64, **`ldd` reports 0 missing libraries**, no absolute paths baked in.

Observation, not a defect: `target_compile_options` appends `-O2` *after* the
`-O3 -DNDEBUG` contributed by `CMAKE_BUILD_TYPE=Release`, so the effective
optimisation level is `-O2`. Documented in `BUILDING.md`.

### Engine runtime and IPC — PASS

`/dev/shm/FTHR_SharedMemory_v3`: **4248 bytes, exactly
`ctypes.sizeof(SharedMemoryLayout)`**, owner = user, **mode 600**. The
production `CaptureBridge` attached, reported `active_codec=av1_nvenc`, and the
frame counter advanced.

Backend selection behaved exactly as designed on an unsupported compositor:

```
[WlrBackend] zwlr_screencopy_manager_v1 not available — compositor must support wlr-screencopy
[ExtBackend] ext-image-copy-capture not available
[Backend] No Wayland capture backend available
[Backend] Using x11grab
```

That is the legible failure path the audit asked for, and the X11 fallback is
load-bearing on every non-wlroots desktop. It must not be removed.

Documented divergence found: `is_recording` is pinned `false` on Linux by design
(*"captures continuously — no discrete recording state"*) while the Windows
engine sets it. Not a UI bug — the UI derives CAPTURING from the connection —
but the shared-memory contract did not say so anywhere. It does now.

### Clip pipeline — PASS (with one large caveat)

| Case | Result |
|---|---|
| ASCII path | saved, valid |
| `clip_ünïcøde_日本語_🎮.mp4` | saved, valid |
| Path near `PATH_MAX` | refused cleanly, no crash |
| Component over `NAME_MAX` | refused cleanly |
| Read-only directory | refused cleanly |
| Two saves back to back | both fine |

`ffprobe`: AV1 1280×720 @ 30 fps + AAC 48 kHz stereo, both streams
`start_time=0.000000`, `ffmpeg -f null -` decodes with **0 errors**.

> **The caveat.** The frames are **entirely black** — measured luma
> `min 0, max 0, 1 distinct value`. XWayland under WSLg has no root-window
> content for `x11grab` to read. **The plumbing is proven; the picture is not.**
> Nobody has confirmed that FTHR Clips records what is on a Linux screen.

### Audio — PASS (headless)

PulseAudio 17.0 via WSLg. Capture started (48 kHz stereo float32) and produced a
clip with a real AAC track. Device switching, disconnection during capture and
"no microphone present" remain `NOT RUN`.

### AUDIT-003b · P1 · Linux privacy · **FIXED**
**The hotkey socket path, not just its mode**

AUDIT-003 fixed the *mode*. The *path* was still a fixed
`/tmp/fthr_hotkey.sock`, and `/tmp` is world-writable — so any local user could
create that path first, as a file, a directory or a symlink. The old code then
ran an **unconditional `os.unlink()`** on it. Both outcomes are bad:

- With the sticky bit set (normal for `/tmp`) the unlink fails with `EPERM`,
  `bind()` fails, and hotkeys are dead. **Any local user could deny this user's
  hotkeys indefinitely by touching one path.**
- Without it, FTHR deletes a stranger's file.
- A symlink there turns the unlink — or the later chmod — into an operation on a
  file of the attacker's choosing.

New `core/linux_runtime.py`: the socket lives in
`$XDG_RUNTIME_DIR/fthr/hotkey.sock`, falling back to `~/.fthr/run/` when
`XDG_RUNTIME_DIR` is unset (containers, `su` without a session). Deliberately
**not** `/tmp` for the fallback either. Both are validated with `lstat` — a
symlink is a finding, not a path to follow — must be owned by the current uid,
and must have no group or other bits.

`prepare_socket_path()` replaces the unconditional unlink: it removes a path only
if it is a socket, owned by this user, and nobody is listening. A regular file, a
directory, a symlink, a foreign socket or a live one are each reported and left
alone. `cleanup_legacy_socket()` migrates users off `/tmp` under the same rules.

**Verified:** socket `0600`, directory `0700`, both owned by the user, `/tmp`
path absent. 16 new tests, all writing into `tmp_path`, pinning the *refusals*
as hard as the successes.

### AUDIT-006 · P2 · Linux hardening · **FIXED**
**Bare-name external tool invocation**

New `core/linux_tools.py` resolves `hyprctl`, `xdotool`, `xprop`, `grim`, `nc`,
`xdg-open` and `wmctrl` once through `shutil.which()`, caches the result, and
hands callers an **absolute** path. Tools are classified required/optional
relative to their own feature — neither is fatal to startup; FTHR degrades
rather than refusing to run. `missing_message()` names the tool, what breaks, and
what to install. `report()` dumps the whole resolution table plus `PATH`, and
`main()` logs it at startup, so "screenshots don't work" arrives with evidence.

Nothing uses `shell=True`; no user-controlled value reaches `argv[0]`. The module
is import-safe on Windows and short-circuits rather than picking up an unrelated
same-named `.exe`. 13 new tests manipulate `PATH` directly, including shadowing.

The KDE/GNOME warning was rewritten at the same time: it now names the detected
desktop, points at that desktop's shortcut UI, prints the exact command to bind,
and reports a missing `nc` as its own distinct error — instead of implying
hotkeys are simply broken.

### AUDIT-015 · P1 · Linux save pipeline · **FIXED**
**Successful clip saves were reported as failures**

The Linux engine runs `SaveClip` *synchronously* on its command loop: it writes
`SAVE_STARTED`, encodes, then overwrites the same field with `CLIP_SAVED`. The
bridge's acknowledgement loop only accepted `SAVE_STARTED` or `ERROR_OCCURRED`,
so whenever the encode finished inside the 1 ms poll interval, `SAVE_STARTED` was
already gone. The loop spun the full 1 s ceiling and returned `False` — for a
clip that was on disk and valid.

Observed directly: two identical saves, one returning `True`, the next
"Timeout waiting for SAVE_STARTED → False" while producing a 6584-byte, fully
decodable file. The user sees SAVE FAILED plus a one-second stall.

`CLIP_SAVED` now counts as an acknowledgement, and is deliberately **not**
consumed there — `poll_async_result()` is what tells the UI the clip landed.

Two further defects fixed in the same area:

- **`engine_string` was never cleared on success.** It was only ever written on
  failure, so after one failed save every subsequent *success* still carried the
  old `"SaveClip failed: …"` text, and `poll_async_result()` reported
  `('saved', <stale error>)`. One line in the Linux engine clears it.
- **Double `close(fd)`** in `_initialize_linux()` — the `except` branch closed
  the fd and the following `finally` closed it again, so the second close raised
  `EBADF` *out of the finally*, replacing the intended `return False`.

Added: a **layout-size check before `mmap`**. The struct carries no version
field, so the region's size is the only runtime signal. A mismatched engine is
now refused with both sizes and a pointer to the rebuild instructions, instead of
being mapped and read as garbage. The contract itself is unchanged — no field
added, moved, resized or renamed, and command IDs 4–9 untouched.

### AUDIT-016 · P2 · Linux portability · **FIXED**
**The AppImage build was broken on every Debian-family distro**

Two independent causes, both found by actually running `build_linux.sh`:

1. `FTHR_linux.spec` hardcoded `/usr/lib/libportaudio.so.2` — correct on Arch,
   wrong everywhere Debian-derived, which uses `/usr/lib/<multiarch>/`.
   PyInstaller aborted. Now resolved via `sysconfig`'s `MULTIARCH` across the
   common layouts with `ctypes.util.find_library` as a fallback, and a clear
   `SystemExit` naming the package per distro. The Qt6 plugin directory had the
   same Arch-only assumption.
2. The dependency check imported all five modules in one statement (so only the
   first failure showed) and then advised `pip install … sounddevice`.
   `sounddevice` imports fine and fails on a missing **system** library:
   `OSError: PortAudio library not found`. No amount of pip installing fixes
   that. Failures are now split into "Python package missing" and "system library
   missing", each with the right remedy and per-distro package names, and the
   Python branch points at `requirements-alpha.txt`.

### AUDIT-014 · P0 · Linux packaging · **OPEN — RELEASE BLOCKER**
**No Linux AppImage can be built**

With AUDIT-016 fixed, `build_linux.sh` runs to completion: engine built,
PyInstaller bundle produced (580 MB, engine included), stripped, smoke-tested,
AppDir assembled with `LICENSE`, `THIRD_PARTY_NOTICES.md`, all nine licence
texts, `.desktop` and icon — and **no user data, clips, logs, settings or test
files**.

It then stops at its own licence gate:

```
47 checks, 12 failed
FAILURES — do not publish this build:
  - libavcodec.so.60: GPL build flags present -> --enable-gpl, --enable-libx264, ...
ERROR: licence verification failed - refusing to build the AppImage.
```

Distribution FFmpeg is a GPL build (Ubuntu 24.04's is), the engine links it, and
PyInstaller bundles what the engine links. **This gate working correctly on its
first real exercise is the good news.** It also means there is no publishable
Linux build until an LGPL FFmpeg is bundled the way Windows does it (AUDIT-005),
or GPLv3 is accepted — which AUDIT-013 forces anyway.

---

## Verification matrix

| Item | Windows | Linux |
|---|---|---|
| Engine builds clean | **PASS** (VS 2022, 08-05) | **PASS** (CMake, 08-06) |
| Python tests | **PASS** 112 / 27 skipped | **PASS** 138 / 1 skipped |
| Lint (ruff) | **PASS** | **PASS** |
| Shared-memory contract | **PASS** 2712 B | **PASS** 4248 B, confirmed against a running engine |
| Bundle builds | **PASS** (PyInstaller, verified version resource) | **PASS** (PyInstaller) |
| Installer / AppImage | **NOT RUN** (installer never tested) | **FAIL** — licence gate (AUDIT-014) |
| Engine runs, IPC works | **NOT RUN** | **PASS** |
| Clip saved and decodes | **NOT RUN** | **PASS** |
| Clip contains a picture | **NOT RUN** | **FAIL** — frames are black (WSLg limitation) |
| Audio captured | **NOT RUN** | **PASS** (headless) |
| Single-instance guard | **PASS** (tests) | **PASS** (real processes) |
| Hotkey socket permissions | n/a | **PASS** |
| Hotkey actually fires | **NOT RUN** | **NOT RUN** |
| GUI launches | **NOT RUN** | **NOT RUN** |
| Performance / soak | **NOT RUN** | **NOT RUN** |

## Verification still outstanding

Nothing below has been performed. None of it is a prediction of failure.

**Both platforms** — GUI launch · a hotkey actually firing · performance
measurement · a soak run (2 h, 50+ clips, watching RSS, file descriptors,
threads, zombies) · any review or test of the two C++ engines (~12k LOC, zero
automated tests) · CI (the workflow has never executed; there is no remote).

**Windows** — a real clip via hotkey · installer install → launch → update →
uninstall · that uninstall removes what it claims.

**Linux** — every real desktop: Hyprland, KDE Plasma, GNOME, bare-metal X11 and
Wayland · **any capture containing visible content** · both Wayland backends
(neither has ever succeeded anywhere) · the Hyprland auto-config path ·
multi-monitor, monitor switching, resolution changes, fractional scaling ·
fullscreen games, focus changes, lock/unlock · suspend/resume · device removal
during capture · AppImage launch.

---

## Path to GO

### Blocks any public download

1. **Decide AUDIT-013** — PySide6, GPLv3, or a commercial PyQt licence. Whatever
   is chosen, `README`, `LICENSE`, `RELEASE_NOTES` and the About dialog must
   state the true licence of the *download*.
2. **Resolve AUDIT-014** — bundle an LGPL FFmpeg for Linux, or accept GPLv3.
   Follows automatically from option 2 of AUDIT-013.
3. **Run the application.** At minimum: launch the GUI on both platforms, record
   one clip via hotkey on each, watch them back, and complete one installer
   lifecycle on Windows.
4. **One bare-metal Linux desktop** — Hyprland *and* one of KDE/GNOME — recording
   a clip with visible content and firing a hotkey. Until then Linux stays
   `CONDITIONAL GO`.

### Strongly recommended before a wide invite

5. **AUDIT-007** — 92 undocumented silent handlers remain (Pass VI built the
   logging floor and the ratchet; the handlers themselves are still to do).
   The entire support strategy is "send us your log".
6. ~~**AUDIT-011** — move the save handshake off the Qt main thread.~~ Done in
   Pass IV; the handshake was removed rather than moved.
7. **AUDIT-012** — restrict `test_server_connection()` to http/https.
8. A soak run, so any performance claim is backed by a measurement.

### During alpha

9. Tag `v1.0.0-alpha` only once `RELEASE_CHECKLIST.md` is fully green. A tag is a
   claim that outlives whoever made it.
10. Give the C++ engines their first tests.

---

## Repository cleanup — 2026-08-06

Performed after pass III, at the owner's request and with the consequences of
each group confirmed beforehand.

| Removed | Size | Recovery |
|---|---|---|
| `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.vs`, `*.vcxproj.user` | 1.5 MB | regenerated automatically |
| `FTHRcapture/FTHRclips/x64` (obj, tlog, pdb) | 58.6 MB | MSBuild |
| `build/` (PyInstaller work dir) | 16.0 MB | PyInstaller |
| `FTHRcapture_linux/build` | 1.7 MB | CMake |
| `AppDir/AppRun`, `AppDir/usr` (stale generated copies) | <0.1 MB | `build_linux.sh` regenerates them |
| `FTHRCLIPS.exe.txt` (scratch file, not source) | <0.1 MB | — |
| `dist/` (Windows bundle) | 522.0 MB | MSBuild + PyInstaller |
| `Output/` (built installer) | 215.3 MB | Inno Setup |
| `FTHRcapture/x64` (built engine + DLLs) | 151.3 MB | MSBuild |
| `third_party/ffmpeg/bin` | 152.7 MB | `python tools/fetch_third_party.py --ffmpeg` (sha256-verified) |
| `redist/` | 24.4 MB | `python tools/fetch_third_party.py --vcredist` |
| `dist_old_gpl_2026-08-05` (pre-AUDIT-005 GPL build) | 130.7 MB | **not recoverable** — removed on explicit instruction |
| WSL work copy + venv + Linux bundle | ~2.5 GB | recreate per `BUILDING.md` |

**Result: 1,282 MB → 7.7 MB.** Every one of the 365 tracked files is present,
the working tree is clean, and all four release gates still exit 0.

Two tests moved from *passed* to *skipped* — `"ffmpeg not available"` and
`"vendored ffmpeg not present in this checkout"`. Both are environment-dependent
skips behaving correctly; `tools/fetch_third_party.py --ffmpeg` restores them.

`FTHRClips_Roadmap.docx` was **kept**: it is a planning document, not build
output, even though it is git-ignored.

---

## Pass IV — Save-response ownership (2026-08-07)

Tests: **173 → 250 passed**, 30 skipped. Closes AUDIT-011, opens and closes
AUDIT-017.

### AUDIT-011 · P2 · Both · UI responsiveness · **FIXED**
**The save handshake no longer runs on the Qt main thread**

The busy-wait is gone rather than moved: no worker thread was added, because
the work it would do is three shared-memory reads. `save_clip()` is now a pure
command submit that writes the path, the duration and the command code and
returns. A dedicated 50 ms `QTimer` — running only while a save is outstanding
— reads the response, and a Qt-free state machine (`FTHR_UI/core/save_state.py`)
decides what it means.

Measured, not asserted: `tests/test_save_event_loop.py` runs a real
`QCoreApplication`, counts 10 ms timer ticks against an engine that never
answers, and fails if the loop stalls. Against the old implementation the same
guard trips at 1001 ms. This is the measurement Pass III recorded as missing.

### AUDIT-017 · P1 · Both · Save pipeline · **FIXED**
**A new save destroyed the previous save's unconsumed verdict**

`save_clip()` opened with `engine_response = NONE` to clear stale state. But
`engine_response` is a single slot shared with the completion channel, and the
status poll only ran every 500 ms. A `CLIP_SAVED` or `ERROR_OCCURRED` that had
arrived but not yet been read was wiped by the next hotkey press. Consequences,
in order of severity: a failed save reported nothing at all — the user was told
the clip was fine and found it missing later; a successful save never refreshed
the grid; and the engine's error message was lost with it.

The window is small but entirely reachable: the debounce allowed a new save one
second after the previous one, and the poll interval was half a second.

Three things were wrong at once, so all three were fixed together:

1. **Three consumers became one.** `save_clip()` consumed responses,
   `wait_for_clip_completion()` was a second consumer (unused by the app but
   live and tested), and `poll_async_result()` was a third. There is now a
   single reader, `MainWindow._pump_save_responses()`, asserted by a test that
   fails if a second `consume_save_response()` call site appears.
2. **Reading was split into peek and consume.** The response and its string are
   read together as one event and interpreted *before* the field is cleared, so
   an unattributable response can be logged instead of silently swallowed.
3. **Nothing clears the field speculatively.** Before submitting a new command
   the poller drains any pending response first, attributing it to the
   operation it belongs to.

`test_T6_old_clip_saved_is_processed_before_the_next_save` and
`test_T6_save_clip_never_clears_a_pending_response` are the regression tests.

### Also corrected: "SAVED" was shown before the clip existed

The submit path emitted `clip_saved`, reloaded the grid, showed "SAVED" and
started post-processing the moment the command was *written* — before the
engine had encoded anything. On failure the UI had already lied. Now the submit
path may only show "SAVING…"; the grid entry, the `clip_saved` signal, the
upload and the success text all wait for `CLIP_SAVED`, and the file's existence
and non-zero size are checked before any of it.

### AUDIT-018 · P2 · Engine IPC correctness · **OPEN**
**The Linux engine publishes the error response before the error text**

Found while consolidating the reader; the fix belongs in C++ and was
deliberately not attempted as a side effect of a Python change.

**Exact site.** `FTHRcapture_linux/src/main.cpp`, the command loop's
`case fthr::CommandType::SAVE_CLIP:` block, lines 198–205:

```cpp
bool ok = engine.SaveClip(out_path, duration_sec, layout);
layout->engine_response = ok                       // ← published FIRST
    ? static_cast<uint32_t>(fthr::ResponseType::CLIP_SAVED)
    : static_cast<uint32_t>(fthr::ResponseType::ERROR_OCCURRED);
if (!ok)
    snprintf(layout->engine_string,                // ← payload written AFTER
             sizeof(layout->engine_string),
             "SaveClip failed: %s", out_path.c_str());
```

A consumer polling between those two statements observes `ERROR_OCCURRED` with
`engine_string` still empty.

**Desired invariant** — the same rule the UI already follows in the opposite
direction, where `save_clip()` writes the path and duration and publishes
`ui_command` last:

```text
write engine_string / result payload first
publish engine_response last
```

**Why no reproduced user-visible failure.** Three things contain it. The engine
clears `engine_string` at the start of every `SAVE_CLIP`, so a reader in the
window sees an *empty* message, never a stale one from a previous save — the
failure mode is a missing detail, not a wrong one. The two statements are
adjacent, so the window is a few instructions wide against a 50 ms poll. And
the UI-side reader now tolerates it explicitly and substitutes its own text
(`tests/test_save_state.py::test_error_with_empty_string_is_tolerated`).

**Why fix it anyway.** The tolerance is a consumer working around a producer
that is wrong, and it costs exactly the diagnostic the message exists to
provide: the one report where the detail goes missing is a race no user can
reproduce on request. The ordering is also load-bearing for anything added
later — a second payload field, or an error code — which would inherit the same
window without the empty-string mitigation.

**Related, same family, also open.** The Windows engine
(`FTHRcapture/FTHRclips/src/`) never writes `engine_string` at all: `grep`
returns no hits across its sources, while it sets `ERROR_OCCURRED` at eight
sites (`capture_engine.cpp` 848, 1179, 1191, 1320, 1330, 1401, 1589 and
`main.cpp` 301/319). Every Windows save failure therefore reaches the user as
the UI's generic fallback text, with the specific cause — no packets in the
snapshot, mux failure, encoder error — visible only in the engine's stdout.
That is a diagnosability gap, not a correctness one, and it overlaps AUDIT-007.

**Stale comment, engine-side.** The `SAVE_CLIP` block's comment still refers to
`poll_async_result()`, which Pass IV removed. Harmless, but it should be
corrected in the same change.

### Single-flight

One engine save at a time. A second hotkey press during an active save is
refused with a debug log and the same "SAVING…" flash the 1-second debounce
always gave — it never confirms a second clip. A timed-out operation is
deliberately *not* counted as active, so a wedged engine cannot lock the hotkey
out. No queue was introduced.


---

## Pass V — Engine publication contract (2026-08-07)

Tests: **250 to 267 passed**, 30 skipped. Closes AUDIT-018, opens and closes
AUDIT-019 and AUDIT-021, opens AUDIT-020.

This pass started as a two-line ordering fix and turned up a P1 data-loss
defect on the primary platform. The producer inventory that found it is below.

### Producer matrix — Linux (`FTHRcapture_linux/`)

Every response write lives in `src/main.cpp`'s command loop. `SaveClip()` is
called synchronously from that same loop, so there is exactly **one writer
thread** and no atomics or barriers are warranted. `save_clip.cpp` touches the
layout only to update `bytes_written`, a progress field with no response.

| Site | Response | Payload | Order before | Order after |
|---|---|---|---|---|
| `SAVE_CLIP` ack | `SAVE_STARTED` | none (string cleared first) | ok | ok |
| `SAVE_CLIP` result | `CLIP_SAVED` / `ERROR_OCCURRED` | `engine_string` | **response first** (bad) | payload first (fixed) |
| `GET_STATUS` | `STATUS_UPDATE` | `engine_param1` | **response first** (bad) | payload first (fixed) |
| `RECONFIGURE_ENCODER` | `STATUS_UPDATE` | `active_codec`, `nvenc_active` | ok | ok |
| `SET_RESOLUTION` / `SET_QUALITY` | `STATUS_UPDATE` | cfg fields (UI-owned) | ok | ok |
| `STOP_` / `START_RECORDING` | `RECORDING_STOPPED` / `RECORDING_STARTED` | none | ok | ok |

Two violations, not one: the `GET_STATUS` site had the same defect and was not
in the original report.

### Producer matrix — Windows (`FTHRcapture/FTHRclips/`)

**Two** writer threads, unlike Linux: the command loop (`main.cpp`) publishes
`SAVE_STARTED`, and `SaveClipThread` (`capture_engine.cpp`) publishes the
result later. They are sequenced by the save queue, not concurrent for one
save. `engine_response` is declared `volatile`, which under MSVC's default
`/volatile:ms` carries acquire/release semantics — adequate here.

| Site | Response | Payload before | Payload after |
|---|---|---|---|
| `main.cpp` START_RECORDING fail | `ERROR_OCCURRED` | **none** | message |
| `main.cpp` SAVE_CLIP queue fail | `ERROR_OCCURRED` | **none** | message |
| `SaveClipThread` completion | `CLIP_SAVED` | **published unconditionally** | only on success |
| `MuxEncodedClip` empty snapshot | `ERROR_OCCURRED` | **none** | message |
| `MuxEncodedClip` alloc output ctx | `ERROR_OCCURRED` | **none** | message |
| `MuxEncodedClip` new video stream | `ERROR_OCCURRED` | **none** | message |
| `MuxEncodedClip` `avio_open` | `ERROR_OCCURRED` | **none** | message + code |
| `MuxEncodedClip` write header | `ERROR_OCCURRED` | **none** | message + code |
| `MuxEncodedClip` packet alloc | `ERROR_OCCURRED` | **none** | message |
| `EncodeRawClip` encoder init | `ERROR_OCCURRED` | **none** | message |

### AUDIT-021 · P1 · Windows · Save pipeline · **FIXED**
**Every failed Windows save was reported to the UI as a success**

Found during the inventory. It is the most serious defect in this pass and was
not what the pass set out to fix.

`SaveClipThread` ran:

```cpp
ProcessSaveClipTask(task);                       // void — cannot report failure
if (task.shared_memory) {
    task.shared_memory->engine_response = ResponseType::CLIP_SAVED;   // always
}
```

All three save functions returned `void`. Each of the seven failure paths
inside them wrote `ERROR_OCCURRED` and returned — and this line then overwrote
it with `CLIP_SAVED` microseconds later. **The UI never saw one of those
errors.** Disk full, unwritable path, encoder unavailable, empty replay
buffer: all reported as a saved clip. The UI then showed "SAVED", refreshed
the grid, started post-processing and queued the upload for a file that did
not exist.

The three functions now return `bool`, failures publish themselves through
`SetEngineError()`, and success is published in exactly one place.

Partial mitigation already in the tree: Pass IV made the UI stat the file
before believing `CLIP_SAVED`, so the most common shapes (`avio_open` failed,
nothing written) surface as "CLIP WAS NOT SAVED" rather than a phantom grid
entry. That is a backstop, not a fix — a failure after a partial header write
leaves a non-empty file that passes the check.

### AUDIT-018 · P2 · Both · **RESOLVED**
**Payload is now published before the response, on both engines**

Both Linux sites corrected. The invariant is stated in both `shared_memory.h`
files next to the fields it governs, and enforced by
`tools/verify_engine_response_contract.py`.

Bounded string helpers added: `fthr::set_engine_string()` (Linux, 2048-byte
`char`, UTF-8) and `fthr::SetEngineString()` / `SetEngineError()` (Windows,
512 `wchar_t`, UTF-16). Both truncate at the buffer bound and always
NUL-terminate — `strncpy`/`wcsncpy` do *not* terminate on truncation, which is
the failure mode being prevented. `SetEngineError()` writes the message and
then publishes, so the order cannot be got wrong at a call site.

Also removed: `SharedMemory::WaitForResponse()` in the Windows engine, which
consumed `engine_response` by writing `NONE`. It was the client half of the
protocol, had no callers, and made the engine a second consumer of a channel
the UI owns — the same defect class as AUDIT-017, waiting to be called.

### AUDIT-019 · P2 · Windows · Engine diagnostics · **CONFIRMED and FIXED**
**The Windows engine published errors with no diagnostic payload**

Confirmed by full inventory: a `grep` for `engine_string` across
`FTHRcapture/FTHRclips/src/` returned **zero** production writes against nine
`ERROR_OCCURRED` producers. Every distinct Windows failure reached the user as
the UI's generic fallback text, with the cause visible only in the engine's
stdout — which no user sends.

Each path now carries a specific message, and the two `libav` paths carry the
return code. `avio_open` — disk full, read-only target, missing folder, the
most common real failure — reads: *"Could not open the clip file for writing
(error N). Check free disk space and folder permissions."*

### The static gate

`tools/verify_engine_response_contract.py` scans both engines and reports
`ERROR-WITHOUT-PAYLOAD`, `PAYLOAD-AFTER-RESPONSE`, `RAW-PAYLOAD-WRITE`,
`UNSAFE-STRING` and `ENGINE-CONSUMES`. It tracks brace depth rather than line
numbers, and blanks comments and string literals first, so it survives
reformatting and does not trip over the prose describing the contract.

It exists because these paths need a full disk or a dead GPU to execute; a
pytest run proves nothing about them. **It is proven able to fail**:
`tests/test_engine_contract_gate.py` feeds it the verbatim pre-fix shapes of
all three defects and asserts rejection, plus an empty-source-tree case — a
gate that silently scans nothing after a directory rename would otherwise
report success forever.

### AUDIT-020 · P2 · Both · Startup / UI responsiveness · **OPEN**
**The engine connection retry loop blocks the Qt main thread**

Found by the AUDIT-011 sweep for main-thread sleeps. Documented, not fixed —
that is a separate change and this pass is already large.

`FTHR_UI/main.py::connect_to_engine()` retries `bridge.initialize()` up to
**10 times with `time.sleep(0.5)`** between attempts, on the Qt main thread:
a **5-second** worst-case freeze. It runs when the UI attaches to the engine —
at startup, and on the reconnect path after an engine exit. During it the
window does not repaint, the status text does not update, and the app is
"not responding" to the OS.

Mitigating: the happy path returns on the first attempt, and a *separate*
threaded reconnect path (`_poll_connect`) already exists for the restart case.
The blocking loop is the startup path. Same fix shape as AUDIT-011 — a QTimer
with a retry counter instead of a loop.

### Builds

| Target | Command | Result |
|---|---|---|
| Windows engine | `MSBuild FTHRcapture/FTHRcapture.sln -p:Configuration=Release -p:Platform=x64` | **compiles and links** — `x64/Release/FTHRclips.exe` produced, no compiler warnings at `-v:minimal`. The post-build step fails (`MSB3073`) copying `third_party/ffmpeg/bin/*.dll`, which are not in a clean checkout; restore with `tools/fetch_third_party.py --ffmpeg`. |
| Linux engine (Release) | `cmake -DCMAKE_BUILD_TYPE=Release` | **NOT RUN** — refused by the AUDIT-014 licence gate: a Release build requires the pinned LGPL FFmpeg (`FTHR_FFMPEG_ROOT`), which is not fetched in this checkout. The gate behaved correctly. |
| Linux engine (Debug) | `cmake -DCMAKE_BUILD_TYPE=Debug && cmake --build` | **exit 0**, binary linked. 2 warnings, both pre-existing and in files this pass did not touch (`backend_wlr.cpp` `-Wclass-memaccess`, `encoder.cpp` `-Wunused-function`). |

Runtime verification of the new error paths is **NOT RUN** on either platform:
forcing a real encoder or disk failure needs a running engine and a human.
Both AUDIT-019 and AUDIT-021 are therefore *fixed and compiled*, not
*observed working*.


---

## Pass VI — Diagnosability (2026-08-07)

Tests: **267 to 306 passed**, 30 skipped. AUDIT-007 moves to
**PARTIALLY FIXED** — deliberately not RESOLVED; the remaining work is
quantified below rather than claimed.

### The inventory

`except: pass` was the wrong thing to count. An AST sweep of the production
tree found:

| | Count |
|---|---|
| Exception handlers total | 232 |
| Silent handlers (body does nothing observable) | 154 |
| ...of which carry a justifying comment | 62 |
| ...of which carry **no** justification | **92** |
| Bare `except:` | **0** |
| `except BaseException` | **0** |

"Silent" includes the semantically silent shapes the brief asked for, not just
`pass`: `continue`, `break`, bare `return`, and `return False` / `return None`
from a broad handler — a caller receiving those cannot tell "nothing to do"
from "it broke".

Distribution: `PASS` 81 · `OTHER-SILENT` 50 · `RETURN-None` 6 · `RETURN-BARE`
5 · `CONTINUE` 5 · `RETURN-False` 3 · `RETURN-True` 3 · `BREAK` 1.
Concentrated in `main.py` (65 handlers), `clip_viewer.py` (32),
`hotkey_manager.py` (22) and `clip_grid.py` (19).

### The structural finding

**The application has no logging.** Every diagnostic in 18k lines is a
`print()`, with stdout/stderr teed into `~/.fthr/logs/fthr.log` by `_LogTee`.
There is no level, no timestamp per line, no module, no thread name — and a
clip save crosses the Qt main thread, a mux worker and an upload worker, so an
interleaved log cannot be attributed. `logger.exception` does not appear
anywhere because there is no logger.

That is why this pass builds the floor rather than rewriting 92 handlers on
top of no floor.

### What was built — `FTHR_UI/core/diagnostics.py`

**Structure.** A rotating file handler (2 MB, 2 backups) writing
`time level [thread] module: message` into the *same* `fthr.log` testers are
told to send. Existing `print()` calls keep working and keep landing there;
new diagnostics go through the logger. Startup failure of logging is now
reported on stderr instead of being swallowed — a tester with an empty log
previously had no way to know why.

**Redaction.** `redact_secret()`, `sanitize_url_for_log()` and
`sanitize_headers_for_log()` strip bearer tokens, `Authorization` and `Cookie`
values, `key=value` secrets, URL credentials and query tokens. A
`logging.Filter` applies redaction to *every* record as a backstop, so a call
site — or a library — that logs a raw URL cannot leak. This is the floor
AUDIT-012 will build on; it exists now so AUDIT-007 does not introduce the
leak AUDIT-012 is about.

> One real defect was found by its own test: the header pattern stops at the
> first space, so `Authorization: Bearer eyJ...` was redacted to
> `Authorization: <redacted> eyJ...` — leaving the token in the log. Ordering
> the bearer rule first fixes it. This is why redaction needs tests with
> realistic tokens rather than `"secret123"`.

**Restraint.** `StateLogger` reports a condition on transition, not per poll:
first failure logged, repeats suppressed, recovery logged once with how long
it lasted and how many occurrences were suppressed. A changed *reason* is
treated as new information and logged. 200 polls of a failing engine
connection produce 1 line, not 200.

**Stack traces.** `log_unexpected()` for category E — a violated invariant, an
impossible state — logs with `exc_info` and redacted context. Expected
failures are explicitly kept out of it.

### The gate — `tools/verify_exception_handling.py`

AST-based, so a docstring or a test fixture containing the literal text
`except: pass` is not a finding. Rules: `BARE-EXCEPT`, `BASE-EXCEPTION`
(both **forbidden**, currently zero, fail the build outright),
`BROAD-SWALLOW` and `UNDOCUMENTED-PASS` (**ratcheted**).

The ratchet is set at the measured debt, `BASELINE = 92`, and may only ever go
down. A new silent handler pushes the count over the line and fails CI while
the existing debt is worked off deliberately. Mechanically rewriting 92
handlers into `logger.exception` would have been the wrong fix — several sit
inside 50 ms polls and would flood the very log this pass exists to make
readable.

Proven able to fail: `tests/test_exception_gate.py` rejects each bad shape,
accepts documented handlers and logging handlers, refuses to flag a fixture
string, and fails on an empty source tree.

### What is NOT done

AUDIT-007 is **not** RESOLVED. Remaining, in priority order:

1. **92 undocumented silent handlers** — each needs classifying (A optional /
   B degraded / C core / D cleanup / E bug) and either a log line, a narrower
   exception, or a comment. The gate prevents new ones; it does not fix these.
2. **No call site has been migrated to the logger yet.** The infrastructure is
   in place and tested; the 232 handlers still `print()` or stay silent.
3. Phases not reached this pass: the Qt-slot audit, the subprocess audit
   (timeouts/stderr capture), the settings-persistence failure paths beyond
   what already exists, the worker-failure UI propagation review, and the
   engine-payload end-to-end log assertion.

No runtime smoke test was performed: **NOT RUN**. Nothing here has been
observed writing a real `fthr.log` outside the test suite.

### Not found

The Phase 13 sweep for false-success patterns after a failed operation found
no *new* defect of the AUDIT-021 class in the Python tree. The post-processing
workers already use `try/finally` and set `clip_ready` unconditionally — which
is a deliberate "always unblock the upload" design, documented, and not a
silent failure. Whether uploading a clip whose watermark pass failed is
*correct* is a product question, not a diagnostics defect; it is noted here
rather than filed as a finding.


---

## Bottom line

The engineering around the product is now in good shape: it is in git with an
honest history, its dependencies are pinned and reproducible, its version comes
from one place and is enforced, its most fragile contract is checked
automatically on both platforms, and four release gates run in CI. Three passes
found and fixed sixteen defects, several of which — a socket any local user
could hijack or wedge, saves reported as failures, a guard whose answer depended
on unrelated Win32 calls — would have generated exactly the kind of bug report
that is impossible to act on.

What has not changed is the shape of the risk. Two licence decisions block any
download, and **the application still has not been watched working by a human on
either platform.** Everything verified so far was verified programmatically.
That is worth a great deal, and it is not the same thing as knowing the product
records your screen.

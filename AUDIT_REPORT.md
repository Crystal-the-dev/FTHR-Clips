# FTHR Clips — Alpha Release Audit

**Audit date:** 2026-08-05
**Audited tree:** `C:\Users\Tom\Desktop\FTHR_Clips_source\FTHR_Clips`
**Version under audit:** 1.0.0-alpha
**Audit environment:** Windows 10 Pro 19045, Python 3.14.3, PyQt6 6.11.0 / Qt 6.11.1

> **Scope honesty.** This pass covered inventory, build/test reproduction on
> Windows, static analysis, targeted source review of the IPC / save / hotkey /
> upload paths, a dependency-and-licence review, and four implemented fixes with
> regression tests. It did **not** include: a Linux run of any kind, GUI runtime
> verification, performance measurement, or soak testing. Everything not
> executed is marked `NOT RUN` and is listed under
> [Verification still outstanding](#verification-still-outstanding). Nothing in
> this document is inferred from a passing build.

---

## Release decision

### `NO-GO` — for public distribution in the current state

> **Updated 2026-08-05 (second pass).** AUDIT-005 (GPL FFmpeg) is now
> **RESOLVED and verified against a rebuilt Windows artifact**. It has been
> replaced as the blocker by AUDIT-013, found while doing that work.

**AUDIT-013 — PyQt6 is `GPL-3.0-only`, so the distributed build is still a
GPLv3 work.** Removing the GPL FFmpeg was necessary but not sufficient: the UI
framework's Python bindings are themselves GPL. Publishing the download as
"MIT" remains inaccurate, and this is still the one class of defect that cannot
be walked back after ~1,807 people have the file.

What changed: the licence paperwork now exists, is shipped, and is enforced by
an automated check, and no GPL FFmpeg component remains in any artifact. What
has not changed: the *overall* licence of the download.

With AUDIT-013 decided and the two Linux items verified, this becomes a
defensible `CONDITIONAL GO` for a clearly-labelled alpha. See
[Path to GO](#path-to-go).

---

## What the software is

FTHR Clips is a background instant-replay recorder — the open-source answer to
ShadowPlay/Medal. It keeps a rolling ring buffer of the screen; a global hotkey
writes the last N seconds to `~/FTHR_Clips` as an MP4.

### Architecture

Two processes, one binary contract:

```
┌──────────────────────────┐   Shared memory: FTHR_SharedMemory_v3    ┌────────────────────────────┐
│  FTHR_UI  (Python/PyQt6) │ ◄──────────────────────────────────────► │  Capture engine (C++)      │
│  ~17.3k LOC              │   fixed-layout struct, no serialization  │  Win: ~8.6k LOC (DXGI/WGC) │
│  main.py = 5.4k LOC      │                                          │  Linux: ~3.3k LOC (wlr)    │
│  settings / upload / UI  │   Unix socket (Linux hotkeys only)       │  FFmpeg + NVENC/AMF/QSV    │
└──────────────────────────┘ ◄────────────────────────────────────    └────────────────────────────┘
```

The struct in `FTHR_UI/core/capture_bridge.py` must match
`FTHRcapture/FTHRclips/include/shared_memory.h` byte-for-byte. It is
hand-maintained on both sides and is the single most fragile contract in the
project — a field-order mistake produces garbage reads, not a crash.

### Critical components

| Component | Why it is critical |
|---|---|
| `core/capture_bridge.py` | Sole channel to the engine. Single-writer contract with no locking. |
| `main.py::_save_clip` | The one path users actually care about. Fans out into three different async post-processing routes. |
| `core/hotkey_manager.py` | On Linux the socket is the *only* working hotkey path; the `keyboard` lib needs root. |
| `core/upload_manager.py` | Only component that sends user data off the machine. |
| Engine `capture_engine.cpp` (2.6k LOC) | Capture + ring buffer. Not reviewed in this pass. |

### Windows vs Linux — genuinely different, not a thin abstraction

| | Windows | Linux |
|---|---|---|
| Shared memory | `OpenFileMapping`/`MapViewOfFile`, UTF-16 (`c_wchar`) | `mmap` of `/dev/shm`, UTF-8 bytes |
| Path field size | **256 wchar** — hard cap | 1024 bytes |
| Hotkeys | `keyboard` lib, works unprivileged | needs root → **falls back to a Unix socket driven by compositor binds** |
| Compositor | n/a | Hyprland auto-configured; KDE/GNOME/X11 require manual binds |
| Capture | DXGI / Windows Graphics Capture | wlr-screencopy, X11 fallback |

The Linux hotkey story is the weakest part of the product: outside Hyprland the
user must hand-write compositor binds, and the app can only warn about it.

---

## Checks performed

| Check | Status | Result |
|---|---|---|
| Repository inventory | ✅ done | 44 Python files / 17.3k LOC; ~12k LOC C++ across two engines |
| Identify authoritative source tree | ✅ done | 3 stale copies found and excluded (see AUDIT-008) |
| Windows dependency install | ✅ done | All 7 `requirements.txt` entries resolve |
| Python byte-compile, whole tree | ✅ done | Clean |
| Test suite (Windows) | ✅ done | **81 passed, 3 skipped** (was 68/3) |
| Static analysis (ruff F,E9,B,S,RUF) | ✅ done | 185 findings, triaged below |
| Source review: IPC / save / hotkey / upload | ✅ done | 4 defects found and fixed |
| Secret scan (source tree) | ✅ done | **Clean** — no hardcoded credentials |
| Dependency licence review | ✅ done | **1 blocker** (AUDIT-005) |
| Windows release build | ❌ NOT RUN | Prebuilt `FTHRclips.exe` + `FTHRClips_Setup_Windows.exe` present but not rebuilt from clean |
| Linux build | ❌ NOT RUN | No Linux machine available in this environment |
| GUI runtime verification | ❌ NOT RUN | Would hijack the operator's screen and start recording — needs explicit consent |
| Performance measurement | ❌ NOT RUN | No measurements taken; **no performance claims are made in this report** |
| Soak / stress testing | ❌ NOT RUN | |
| Installer / uninstall test | ❌ NOT RUN | |

### Static analysis summary

`ruff check FTHR_UI tests --select=F,E9,B,S,ASYNC,RUF` → 185 findings.

| Code | Count | Assessment |
|---|---|---|
| `S110` try-except-pass | 34 | **Genuine concern.** Bulk silent-failure surface; each swallowed exception is a bug that will never reach a log. Not individually triaged — see AUDIT-007. |
| `F401` unused import | 33 | Cosmetic. |
| `S603`/`S607` subprocess | 40 | Mostly false positives (fixed argv, no user input) — but see AUDIT-006 for the real subset. |
| `RUF001-003` ambiguous unicode | 30 | Cosmetic (em-dashes in comments). |
| `RUF012` mutable class default | 12 | Latent shared-state risk, none currently exploited. |
| `S108` hardcoded temp path | 2 | Both real — `/tmp/fthr_hotkey.sock`, `/dev/shm/...`. One fixed (AUDIT-003). |
| `S324` md5 | 1 | False positive — thumbnail cache key, not security. |

---

## Findings

### AUDIT-001 · P0 · **FIXED** · Both platforms · Startup
**No single-instance guard — two instances corrupt each other**

**Cause.** `main()` created the window and engine unconditionally. Nothing
checked whether FTHR Clips was already running.

**User impact.** Two instances are actively destructive, not merely redundant:
two capture engines encode the same screen (double GPU load; on single-session
NVENC hardware the second silently fails); both map `FTHR_SharedMemory_v3`,
whose command/response fields are a single-writer contract, so each UI consumes
the other's `engine_response` and saves time out; and on Linux the second
instance calls `os.unlink()` on the hotkey socket and rebinds it, **silently
stealing every hotkey from the first instance**. A double-click on the desktop
icon is enough to trigger it.

**Reproduce.** Launch FTHR Clips twice. Before the fix both windows appeared and
both spawned engines.

**Change.** New `FTHR_UI/core/single_instance.py` — named kernel mutex
(`Local\FTHR_Clips_SingleInstance_v1`) on Windows, `flock(LOCK_EX|LOCK_NB)` on
`~/.fthr/fthr.lock` on Linux. Both primitives are kernel-owned, so a hard crash
releases them and cannot lock the user out. Wired into `main()` **before** any
engine spawn, socket bind, or window creation; a `QMessageBox` explains the
refusal. The guard deliberately **fails open** — if the primitive is
unavailable, the app starts rather than being blocked by its own safety check.

**Test evidence.** `tests/test_single_instance.py` — 7 tests, all passing,
including a real **subprocess** conflict test (`test_second_process_is_refused_
while_first_holds_lock`) and a **hard-kill recovery** test
(`test_lock_is_released_when_holder_dies`). In-process tests alone would not
have exercised this, since flock is per-file-description.

**Residual risk.** Verified on Windows only. The POSIX branch is unexercised —
see [Verification still outstanding](#verification-still-outstanding).

---

### AUDIT-002 · P1 · **FIXED** · Both platforms · Save pipeline
**A clip that failed to write reported success — perceived data loss**

**Cause.** `save_clip()` waits only for `SAVE_STARTED`; the engine encodes and
writes on its own thread afterwards. If that write then failed (disk full, path
gone, encoder error) the engine set `ERROR_OCCURRED` — and **nothing read it**.
`_update_status()` polled twice a second and never looked at `engine_response`.

**User impact.** The UI flashed `SAVED`, played the save animation, and added a
grid entry. The clip did not exist. The user discovers this later, with no error
and nothing in the log — the single worst failure mode for a clipping tool, and
the one most likely to generate "it randomly eats my clips" bug reports.

**Reproduce.** Fill the disk (or make `~/FTHR_Clips` read-only after the engine
acks) and press the save hotkey. Before the fix: `SAVED`, no file, no error.

**Change.** New `CaptureBridge.poll_async_result()` — non-blocking, consumes the
response exactly once, tolerates a garbled `engine_string`, and ignores
unrelated response codes. `_update_status()` now surfaces a real error bar with
an **Open folder** action, and on success refreshes the grid (which previously
listed the clip *before* the engine had written it).

**Test evidence.** 6 new tests in `tests/test_capture_bridge.py` covering: idle,
late error, late success, consume-once (the status poll runs 2×/s and would
otherwise spam the error bar), unrelated-response isolation, and garbled-string
resilience.

**Residual risk.** Correct handling of the *engine-side* `ERROR_OCCURRED` signal
is assumed from the struct contract; the engine was not run.

---

### AUDIT-003 · P1 · **FIXED** · Linux · Privacy
**World-writable hotkey socket let any local user capture this user's screen**

**Cause.** `/tmp/fthr_hotkey.sock` was bound under the process umask, typically
mode 0755–0777, in a directory shared by every account on the machine. The
server accepts any connection and dispatches the command with no authentication.

**User impact.** On a shared/multi-user Linux box, **any** local account could
run `echo -n "save_screenshot" | nc -U /tmp/fthr_hotkey.sock` and cause this
user's screen to be captured to this user's disk — then read it if the clips
directory is readable. Also allows trivial disk-filling by spamming `save_clip`.
Confidentiality impact, local attacker, no privileges required.

**Change.** `bind()` is now wrapped in `umask(0o177)` so the socket never exists
with permissive bits (a `chmod` after `bind()` would leave a race window),
followed by an explicit `chmod(0o600)` for filesystems that ignore umask on
AF_UNIX nodes.

**Test evidence.** ❌ **NOT RUN** — requires Linux. Windows returns early from
this function. Verification is an outstanding Linux item.

**Residual risk.** The socket remains **unauthenticated for the owning user**,
which is acceptable (same trust boundary), and predictable-path pre-creation by
another user before FTHR starts is not defended against. Acceptable for alpha;
a `$XDG_RUNTIME_DIR` path would fix both properly.

---

### AUDIT-004 · P2 · **FIXED** · Windows · Save pipeline
**Deep paths lost the clip and blamed disk space**

**Cause.** The Windows `ui_string` field is `c_wchar * 256`, so the bridge
refuses any path over 255 characters — correctly, but it returns a bare `False`,
indistinguishable from a write failure. The UI then told the user to check disk
space.

**User impact.** Clip lost, and the error actively misdirects. Reachable without
anything exotic: a long Windows profile name plus a long game name (the capture
source name becomes a folder) is enough.

**Change.** `_save_clip()` now checks the length before calling the bridge and
reports the actual cause, the actual length, and the 255 limit.

**Test evidence.** Logic verified by inspection; the 255-char guard in
`capture_bridge.save_clip` is pre-existing and unchanged. No new automated test
— asserting on UI error text would pin wording rather than behaviour.

---

### AUDIT-005 · P0 · **RESOLVED (2026-08-05)** · Both platforms · Licensing
**The distributed binary is GPLv3, shipped under an MIT notice, with no third-party licences**

**This is the finding that makes the release decision `NO-GO`.**

**Evidence.** The bundled FFmpeg DLLs in
`FTHRcapture/FTHRclips/third_party/ffmpeg/bin/` self-report:

```
libavcodec license: GPL version 3 or later
```

Build flags recovered from the binaries: `--enable-gpl`, `--enable-version3`,
`--enable-libx264`, `--enable-libx265` (80 `--enable-*` flags total;
`--enable-nonfree` is **not** set). x265 copyright banners are present in
`avcodec-62.dll`. The C++ engine links these directly via the import libraries
in `third_party/ffmpeg/lib/`, and `installer_windows.iss` ships the whole
`dist\FTHRClips\*` tree.

**Why it blocks.** MIT source code is GPL-compatible, so the *project's own
code* is not the problem — but the **distributed combined work** is subject to
GPLv3. Currently:

- `LICENSE` and `README.md` present the download as plain MIT. It is not.
- **No FFmpeg `COPYING`/`LICENSE` text is shipped at all** — no `NOTICE`, no
  `THIRD_PARTY_NOTICES`. Verified: the only licence file in the tree is the
  project's own MIT `LICENSE`.
- GPLv3 obliges a complete-corresponding-source offer for the whole distributed
  work, which is not present.

Distributing to ~1,807 users in this state is a licence violation on the first
download, and unlike a code bug it cannot be undone by shipping a patch.

**Two viable resolutions** — a project decision, not a code fix:

1. **Switch to an LGPL FFmpeg build** *(recommended)*. Drop `--enable-gpl`,
   `--enable-version3`, x264 and x265. The engine already targets NVENC/AMF/QSV,
   and it already links FFmpeg **dynamically** via DLLs, which is what LGPL
   requires. Cost: the x264/x265 software fallback path advertised in the README
   must be dropped or re-sourced. The app keeps its MIT licence.
2. **Ship as GPLv3.** Relicense the distributed work, include the GPLv3 text and
   all FFmpeg/x264/x265 notices, and publish a complete corresponding source
   offer.

Either way `THIRD_PARTY_NOTICES.md` must be created and included in both the
installer and the AppImage.

> Note: this is a factual reading of the licence markers embedded in the shipped
> binaries, not legal advice. Given ~1,807 recipients, confirm the chosen path
> before publishing.

**Second FFmpeg copy.** The Python UI muxes audio using a *different* FFmpeg —
`imageio-ffmpeg`'s bundled `ffmpeg-win-x86_64-v7.1.exe`. Its licence must be
covered in the same notices file. Two independent FFmpeg copies also ship
duplicate megabytes; consolidating is a P3 size win.

---

### AUDIT-006 · P2 · **OPEN** · Linux · Security hardening
**External tools invoked by bare name — PATH-dependent**

16 `subprocess` call sites invoke `hyprctl`, `xdotool`, `nc`, `grim`, `xdg-open`
by bare name (`focus_monitor.py`, `game_detector.py`, `hotkey_manager.py:317`,
`clip_grid.py`, `capture_card.py`, `main.py`). Arguments are fixed, so this is
**not** command injection — the risk is that a directory earlier in `PATH`
shadows the real binary. Low severity for a desktop app run as the user; worth
resolving via `shutil.which()` at startup, which also yields a much better error
than a silent feature failure when the tool is simply not installed.

### AUDIT-007 · P2 · **OPEN** · Both · Diagnosability
**34 bare `try/except: pass` blocks**

Each is a failure that can never appear in a log or a bug report. For an alpha
whose entire diagnostic strategy is "testers send `~/.fthr/logs/fthr.log`", this
is the single biggest obstacle to acting on reports. Recommendation: mechanical
pass converting each to a logged debug line. Low risk, high diagnostic payoff.

### AUDIT-008 · P1 · **OPEN** · Process · Release engineering
**The project is not under version control, and stale copies are in play**

`FTHR_Clips_source/FTHR_Clips` has **no `.git` directory**, despite shipping a
`.gitignore`, a CI workflow, GitHub issue templates, and a README pointing at
`github.com/FTHR-Community/FTHR-Clips`. Consequences: no history, no bisect when
an alpha tester reports a regression, no way to tag what was released, and the
CI workflow is dead weight.

Three additional copies exist on the Desktop and are **not** the source of truth
— confirmed stale and excluded from this audit:

| Path | State |
|---|---|
| `Desktop\clipping\` | Abandoned C++ rewrite, last commit 2026-05-10 |
| `Desktop\FTHR_CLIPS_BACKUP(1)\` | Older snapshot |
| `Desktop\FTHR_Clips\` | Empty stub (`engine/FTHRclips/nul`) |

Also: `Desktop\CLAUDE.md` documents a layout (`engine/`, `ui/`) that does not
match this tree (`FTHRcapture/`, `FTHR_UI/`) and describes stubs that have since
shipped. It will actively mislead contributors and AI assistants. Fix or delete.

### AUDIT-009 · P2 · **OPEN** · Both · Reproducibility
**Dependencies unpinned**

`requirements.txt` uses `>=` for all 7 entries. Two testers can therefore run
materially different Qt versions, which makes UI bug reports non-reproducible —
this audit ran against PyQt6 6.11.0 while the file only demands `>=6.6.0`.
Pin exact versions for the alpha and keep a separate unpinned dev file.

### AUDIT-010 · P2 · **FIXED** · Both · Diagnostics
**About screen reported the wrong version**

The About label was hardcoded `FTHR Clips v1.0.0` while every other source of
truth said `1.0.0-alpha`. Alpha bug reports quoting the version would have been
ambiguous about which build was meant. Now derived from
`QApplication.applicationVersion()`, so it cannot drift again.

### AUDIT-011 · P2 · **OPEN** · Both · UI responsiveness
**`save_clip()` blocks the Qt event loop for up to 1 second**

The `SAVE_STARTED` handshake busy-waits with `time.sleep(0.001)` on the main
thread. Against a healthy engine this is milliseconds. Against a hung one the UI
freezes for a full second on every hotkey press. AUDIT-002's crash detection
reduces exposure but does not remove it. Fix properly by moving the handshake
off the main thread. **No measurement was taken** — this is a code-structure
finding, not a profiled result.

### AUDIT-012 · P2 · **OPEN** · Both · Privacy
**Upload auth header stored in plaintext**

`upload_auth_header` (typically a bearer token) is written to
`~/.fthr/settings.json` in cleartext. Consistent with a local-first tool and
arguably acceptable for alpha, but it must be **documented** so users do not put
a high-value token there. Also: `test_server_connection()` passes a user-supplied
URL to `urllib.request.urlopen` without scheme validation, so `file://` is
accepted (ruff `S310`); restrict to http/https.

---

## Findings summary

| ID | P | Component | Status |
|---|---|---|---|
| AUDIT-001 | P0 | Startup | ✅ Fixed + 7 tests |
| AUDIT-005 | P0 | Licensing (FFmpeg) | ✅ Fixed + 12 tests + CI gate + artifact verified |
| AUDIT-013 | **P0** | **Licensing (PyQt6)** | ❌ **Open — blocks release** |
| AUDIT-002 | P1 | Save pipeline | ✅ Fixed + 6 tests |
| AUDIT-003 | P1 | Linux privacy | ✅ Fixed (unverified on Linux) |
| AUDIT-008 | P1 | Release engineering | ❌ Open |
| AUDIT-004 | P2 | Windows save | ✅ Fixed |
| AUDIT-010 | P2 | Diagnostics | ✅ Fixed |
| AUDIT-006 | P2 | Linux hardening | ❌ Open |
| AUDIT-007 | P2 | Diagnosability | ❌ Open |
| AUDIT-009 | P2 | Reproducibility | ❌ Open |
| AUDIT-011 | P2 | UI responsiveness | ❌ Open |
| AUDIT-012 | P2 | Privacy | ❌ Open |

**Fixed: 5 (2 of them P0/P1 data-integrity). Open: 7 (1 release-blocking).**

---

## Changes made

| File | Change |
|---|---|
| `FTHR_UI/core/single_instance.py` | **New.** Cross-platform single-instance guard. |
| `FTHR_UI/main.py` | Guard wired into `main()`; async save-failure surfacing in `_update_status()`; Windows long-path pre-check in `_save_clip()`; version label derived from app version. |
| `FTHR_UI/core/capture_bridge.py` | **New** `poll_async_result()` + `_read_engine_string()`. |
| `FTHR_UI/core/hotkey_manager.py` | Socket bound under `umask(0o177)` + explicit `chmod(0o600)`. |
| `tests/test_single_instance.py` | **New.** 7 tests incl. subprocess conflict + hard-kill recovery. |
| `tests/test_capture_bridge.py` | +6 tests for async save verdicts. |

**No public API was changed. No feature was removed. No test was weakened or
disabled.** All changes are additive or replace a silent failure with a reported
one.

### Possible side effects to watch

- **AUDIT-001** — if a previous FTHR process is left running invisibly (hung,
  tray-only), the next launch now *refuses* instead of starting a second copy.
  This is intended, but it converts a silent-corruption failure into a visible
  "already running" dialog that alpha testers may report as a bug. The dialog
  text tells them to end the running process.
- **AUDIT-002** — `poll_async_result()` consumes `engine_response`. It runs on
  the main thread from the status timer; `save_clip()`'s handshake blocks that
  same loop, so the two cannot interleave in the current design. **If the
  AUDIT-011 fix moves the handshake off the main thread, this becomes a real
  race and both sites will need a lock.** Flagged in the code.

---

## Test results

**Command:** `QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q`
**Environment:** Windows 10 Pro 19045, Python 3.14.3, PyQt6 6.11.0, pytest 9.1.1

| | Before | After |
|---|---|---|
| Passed | 68 | **81** |
| Skipped | 3 | 3 |
| Failed | 0 | **0** |

The 3 skips are Linux-only tests (`test_capture_backend_detection.py`), skipped
by design on Windows.

**Coverage caveat — read this before trusting the green.** 81 passing tests are
*not* evidence of a working application. The suite exercises pure logic against
fake shared-memory buffers. It does not, and in this environment cannot, cover:
the C++ engine (~12k LOC, **zero** automated tests), actual screen capture,
actual encoding, real IPC against a live engine, any GUI interaction, or
anything on Linux. Test count went up 19%; real-world confidence did not move
proportionally.

---

## Performance

**NOT RUN.** No profiling, no timing, no memory measurement was performed. This
report therefore makes **no** performance claims and reports no before/after
numbers. AUDIT-011 is a structural observation from reading the code, not a
measured bottleneck.

`docs/PERFORMANCE.txt` (referenced by `Desktop\CLAUDE.md`) does not exist in this
tree — `docs/` is empty.

---

## Verification still outstanding

Ordered by how badly the absence of the check hurts.

1. **Linux: nothing has ever been run.** No build, no launch, no test. Half the
   advertised platform is completely unverified — including the AUDIT-003 fix
   and the entire POSIX branch of the AUDIT-001 guard, both written this pass.
2. **GUI runtime verification on Windows.** The app has not been launched. Not
   done here because starting a screen recorder on the operator's machine begins
   capturing their screen — it needs explicit consent. Required checks: launch,
   engine connects, hotkey saves a clip, clip plays, second launch is now
   refused, clean exit.
3. **Clean-room rebuild on both platforms.** Existing `FTHRclips.exe` and
   `FTHRClips_Setup_Windows.exe` were **not** rebuilt from a fresh checkout.
4. **Installer lifecycle** — install, update over previous, uninstall, leftovers.
5. **Soak test** — hours of runtime, hundreds of clips, device hot-swaps.
6. **Multi-monitor / HiDPI / fractional scaling** on both platforms.
7. **Hotkeys on KDE/GNOME/X11** — the documented weak spot.
8. **C++ engine review.** ~12k LOC of buffer/thread/encoder code with no tests
   was outside this pass and is the largest unexamined risk surface in the
   project.

---

## Path to GO

### Before any public download (blocks release)

| # | Task | Discipline | Size | Depends on | Definition of done |
|---|---|---|---|---|---|
| 1 | ~~Resolve AUDIT-005~~ **DONE** — LGPL FFmpeg swapped in, verified | Release | — | — | ✅ `tools/verify_release_licenses.py` passes on tree and rebuilt Windows artifact |
| 1b | Resolve AUDIT-013: port UI to PySide6, ship as GPLv3, or buy a commercial PyQt licence | Release / legal | **L** | project decision | `README`/`LICENSE` state the true licence of the download |
| 2 | Create `THIRD_PARTY_NOTICES.md`, include in installer + AppImage | Release | S | 1 | Both artifacts contain it; covers both FFmpeg copies, x264/x265 if kept, NVIDIA SDK header, Qt |
| 3 | Put the tree under git, tag `v1.0.0-alpha` | Release | S | — | Clean history; CI green; stale Desktop copies archived or deleted |
| 4 | Verify on real Linux: build, launch, hotkey, clip, **AUDIT-001 + AUDIT-003 fixes** | QA | **L** | 3 | AppImage runs on Hyprland + one of KDE/GNOME; second launch refused; socket is mode 0600 |
| 5 | Windows runtime verification incl. second-launch refusal | QA | M | 3 | Checklist in `TESTING.md` passes on Win10 **and** Win11 |

### Before inviting all ~1,807 users (strongly recommended)

| # | Task | Discipline | Size | Definition of done |
|---|---|---|---|---|
| 6 | Pin `requirements.txt` exactly (AUDIT-009) | Build | S | Two clean installs produce identical versions |
| 7 | Replace the 34 silent `except: pass` with logged handlers (AUDIT-007) | Eng | M | `ruff --select=S110` reports 0; failures reach `fthr.log` |
| 8 | Fix or delete the stale `Desktop\CLAUDE.md` (AUDIT-008) | Docs | S | Documented layout matches reality |
| 9 | Document the plaintext auth token + restrict URL schemes (AUDIT-012) | Eng/Docs | S | Warning in upload settings UI; `file://` rejected |
| 10 | Short soak: 2 h runtime, 50+ clips, watch RAM/handles/zombies | QA | M | No growth trend, no orphaned processes |
| 11 | Staged rollout — ~50 testers before opening to the server | Release | S | Log-collection instructions published first |

### During alpha

12. `shutil.which()` for external tools (AUDIT-006) · 13. Move the save handshake
off the main thread — carefully, see the AUDIT-002 side-effect note (AUDIT-011) ·
14. First automated tests for the C++ engine · 15. Split `main.py` (5.4k LOC).

---

## Bottom line

The engineering is in better shape than a 5,400-line `main.py` suggests: the
error handling is deliberate, the comments record real debugging history, and
the two genuine data-integrity defects found here (AUDIT-001, AUDIT-002) are now
fixed with tests that would catch a regression.

What stands between this and a release is **not** primarily code quality:

- One **licence blocker** that no amount of testing will surface, and that
  cannot be corrected after 1,807 people have downloaded the file.
- One **entirely unverified platform** — Linux has never been run, including two
  fixes written during this pass.
- **No version control**, so the first regression report cannot be bisected.

Fix the licence, put it in git, and actually run it on Linux — then a clearly
labelled alpha is a defensible call.

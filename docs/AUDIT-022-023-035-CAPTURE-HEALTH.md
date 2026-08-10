# AUDIT-022 / AUDIT-023 / AUDIT-035 — Capture-health remediation

**Date:** 2026-08-10

**Product:** FTHR Clips 1.0.0-alpha

**Scope:** capture progress, Linux failure publication/recovery, conservative
content health, replay admission, native builds, runtime evidence

## 1. Existing health-signal matrix

Phase 1 was completed before capture code changed.

| Signal | Windows | Linux | Producer | Consumer | Meaning before this change |
|---|---|---|---|---|---|
| `is_initialized` | yes | yes | engine main | bridge | Engine setup/mapping is usable; not capture health. |
| `frames_captured` | `uint64_t` | `uint64_t` | capture thread | UI status poll | Windows incremented after the encode/copy path; Linux after `EncodeFrame()` was called. Process-monotonic, practically non-overflowing, but not a backend-generation counter. |
| capture `running_` | yes | yes | capture engine | engine threads | Thread-control state. Linux previously remained true after `CaptureFrame()` failure; neither value meant the process was alive. |
| process state | `Popen.poll()` | `Popen.poll()` | OS/UI | UI | Process liveness only. A live process could have a dead backend. |
| `nvenc_active` / active codec | yes | yes | encoder | UI | Encoder selection, not frame progress or usable pixels. |
| `engine_response` | one slot | one slot | command/save path | one UI poller | Save command result. It is not a continuous health channel and must remain single-consumer. |
| backend error/status | logs only | logs plus loop exit | backend | operator | Not a typed UI contract. |
| last-frame timestamp | internal timestamps only | raw-frame timestamps | backend/encoder | muxer | Not published for health interpretation. |
| replay ring after failure | retained | retained | capture engine | save path | Could expose stale pre-failure content. |

The v4 contract now adds typed health flags, a backend generation, a content
sample sequence/streak, and mean/variance metrics. Stall time remains UI-local
and uses `time.monotonic()` over the existing 500 ms status poll.

## 2. Capture state machine

The UI states are `INITIALIZING`, `HEALTHY`, `DEGRADED`, `CONTENT_SUSPECT`,
`STALLED`, `FAILED`, `RECOVERING`, and `STOPPED`. First post-generation frame
progress enters `HEALTHY`; delayed progress enters `DEGRADED` then `STALLED`;
typed recovery/failure flags take precedence; later counter progress returns to
`HEALTHY`. An intentional pause is degraded but resets replay freshness.

## 3. Stall thresholds and rationale

No progress for 3.0 seconds is `DEGRADED`; 8.0 seconds is `STALLED`. This
tolerates normal compositor jitter, low-FPS sources, and short loading pauses
without allowing the UI to claim capture indefinitely. One recovery
recommendation is emitted per incident; 30 continuously healthy seconds reset
that budget. Tests inject the monotonic clock.

## 4. Linux failure-lifecycle change

`running_` now means the capture thread/backend is intended to run. A failed
generation is cleaned up and reported as recovering. When retries exhaust, the
engine publishes `CAPTURE_HEALTH_BACKEND_FAILED`, sets `running_ = false`, and
rejects saves. The process command loop deliberately remains alive so the UI
can read the terminal typed state instead of reconnecting to a stale mapping.

## 5. Linux recovery policy

There are three retries with 250/750/1500 ms backoffs. Waits are split into
50 ms cancellable slices. A generation producing ten configured seconds of
frames starts a new incident. Backend and encoder objects are recreated, the
generation increments, content streaks reset, and the replay ring is cleared.
CTest exercises transient recovery, exhaustion, healthy reset, and cancellation
with a fake backend and injected sleeper.

## 6. Windows stall behavior found

| Condition | Previous behavior | New state/recovery |
|---|---|---|
| WGC delivers no frame | condition-variable wait; process stays alive | UI heartbeat degrades/stalls; save denied; manual restart offered |
| WGC acquisition/surface exceptions | processing exceptions logged and loop continued | acquisition is inside the same handler; 30 consecutive errors publish failed and stop capture |
| DXGI wait timeout | normal no-frame event | remains normal; heartbeat bounds a sustained absence |
| DXGI access/output loss | one reinitialization | recovering flag, ring clear, 500 ms wait, generation bump on success, failed on failure |
| other DXGI acquisition errors | unbounded retry | 100 consecutive errors then typed failure |
| focus-gated WGC alt-tab | dropped frames looked stalled | typed paused state; watchdog baseline/fresh replay reset |
| WGC resize/source removal | restart was required | exceptions/no-progress become failed or stalled; architecture otherwise unchanged |

## 7. Content-health algorithm

Once per configured second, the CPU paths read a distributed 16×9 grid (144
pixels). Windows GPU zero-copy paths issue 144 one-pixel GPU copies into a 16×9
staging texture and perform one tiny map; the earlier centre-strip experiment
was rejected after real runtime evidence showed a dark-wallpaper false positive.
Only derived luminance mean, variance, sample sequence, and suspicious streak
are published. No pixels, images, OCR, or content-derived filenames are stored
or logged.

## 8. Black-frame logic

A sample is black-like when mean luminance is at most 8 and variance at most 6.
It is uniform when variance is at most 2. Twelve consecutive one-Hz suspicious
samples are required before `CONTENT_SUSPECT`; one black frame never changes
health, and a normal sample clears the streak immediately. Content suspicion is
a warning, not backend failure.

## 9. Frozen-frame logic

No counter progress is authoritative for a backend freeze and reaches
`STALLED` regardless of sampled content. Identical hashes with an advancing
counter are retained only as a test/diagnostic signal and are not called failed,
because a static desktop is indistinguishable from a repeated non-uniform frame
without a stronger source signal. Sustained uniform fresh content becomes
`CONTENT_SUSPECT`, never `FAILED`.

## 10. False-positive protections

Temporal evidence is mandatory; content and progress states are independent;
dark high-variance scenes stay healthy; repeated normal frames stay healthy;
black/uniform content can still be saved; focus-gated pauses reset rather than
stall; backend generations discard old watchdog baselines. Tests cover black,
white, solid colour, dark varied content, static normal content, one-frame
post-recovery warm-up, low-duration pauses, and frozen counters.

## 11. Save behavior while unhealthy

The hotkey path evaluates health before publishing a command. `STALLED`,
`FAILED`, `RECOVERING`, `STOPPED`, paused, and not-yet-warm states are rejected
with an actionable message. `DEGRADED` is admitted only with at least five
seconds of observed fresh replay. Both engines also reject known failed,
recovering, or paused saves as defense in depth. The existing response slot and
single UI consumer were not changed.

## 12. Ring-buffer behavior on failure/restart

Linux clears the encoded ring on every recovered generation and failure. DXGI
clears encoded and raw replay state before reinitialization. Terminal failures
deny saves. A new process owns a new ring. WGC no-delivery stalls deny saves;
after progress resumes, UI freshness starts at zero so only post-recovery tail
duration can be requested.

## 13. Warm-up behavior after recovery

Fresh replay seconds accumulate only across status intervals in which the frame
counter advanced; wall time alone cannot warm the ring. Generation changes,
recovery, pause, disconnect, failure, and first stall reset the value. Saves are
rejected below five fresh seconds, then shortened to the available fresh duration
with a warning until the requested duration is available.

## 14. Logging/state-transition behavior

Logging occurs only on state transitions in `capture.health`, formatted as
`OLD -> NEW` with reason, counter, generation, sample sequence/streak, mean, and
variance. Pixels are never logged. There is no new thread or busy loop, and
health interpretation does no I/O or sleeping on the Qt thread.

## 15. Performance measurements

The synthetic Python parity sampler measured 86.11 µs at 1080p, 86.86 µs at
1440p, and 91.58 µs at 4K (median of seven 5,000-call batches). At the production
one-Hz cadence that is 0.0086–0.0092% of one CPU core; peak traced transient
memory was about 1.5 KiB and net retained memory after 1,000 calls was under
1 KiB. Production C++ samples 144 pixels and performs no per-sample heap
allocation. Controlled capture-thread latency and GPU readback timing at
1080p60/1440p60/4K60 were **NOT RUN**; the Windows WGC probe remained healthy
at the configured 30 FPS but is not an isolated overhead benchmark.

## 16. New tests

Added deterministic clock tests for progress/pause/stall/recovery/live-process
failure/save admission/generation warm-up; synthetic content tests for black,
recovery, dark variance, static normal, white and solid colour; v4 layout/flag
contract tests; Linux fake-backend policy/cancellation CTest; pinned FFmpeg
alias/link confinement tests; and hash-verified licence-gate alias tests.

## 17. pytest before/after

Before: 308 passed, 28 skipped. After: **330 passed, 29 skipped**. The added
Windows skip is the native GNU C++ harness; the same executable passed under
Linux CTest.

## 18. Ruff

`python -m ruff check .`: **PASS**.

## 19. All release gates

**PASS:** exception handling, shared-memory byte layout (Windows 2736 B; Linux
4272 B), engine-response publication, product version consistency, repository
hygiene, `compileall`, `git diff --check`, and the licence gate. The Linux
licence run reported 99 checks, 0 failures, and two pre-existing informational
warnings that the command-line `ffmpeg`/`ffprobe` probes did not expose an
OpenH264 fallback; all linked FFmpeg SONAMEs and bundle-relative RPATH checks
passed.

## 20. Windows build

Visual Studio 2022 MSBuild, Release x64: **PASS**. `FTHRclips.exe` linked and the
seven pinned LGPL FFmpeg DLLs were available in the Release directory.

## 21. Linux build

Ubuntu 24.04 under WSL2, GCC 13.3/CMake 3.28, Release against the pinned LGPL
FFmpeg: **PASS**. CTest: 1/1. `readelf`/`ldd` confirmed FFmpeg 62/60/11/9/6
SONAMEs resolve to the pinned tree, not system FFmpeg. NTFS clock-skew warnings
were emitted after the successful complete build.

## 22. Windows runtime results or NOT RUN

Normal desktop WGC on an RTX 4060 Ti: **PASS**. State moved
`INITIALIZING -> HEALTHY`, frames advanced 0→347, generation stayed 1, 11
content samples produced zero suspicious streak after the distributed-grid
fix, and save events were `SAVE_STARTED -> CLIP_SAVED`. FFprobe decoded H.264
1280×720 and an extracted frame showed visible desktop pixels. Forced DXGI,
fullscreen game, lock/unlock, resolution change, monitor removal, and fault
injection: **NOT RUN**. The temporary probe artifacts were deleted.

## 23. Linux runtime results or NOT RUN

**NOT RUN.** WSL/WSLg is not sufficient evidence for a real Hyprland/wlroots,
KDE, GNOME, or X11 compositor path. Native Release compilation, fake recovery,
linkage, and licence checks passed only.

## 24. AUDIT-022 status

**RESOLVED in code and deterministic tests; disruptive runtime fault matrix not
run.** Progress is bounded, visible in the UI, save-safe, non-blocking, and
recovery-aware.

## 25. AUDIT-023 status

**CONDITIONALLY RESOLVED — REAL LINUX COMPOSITOR VALIDATION NOT RUN.** Terminal
failure clears running state, publishes failure, denies stale saves, and bounded
fake-backend recovery passes.

## 26. AUDIT-035 status

**CONDITIONALLY RESOLVED.** Conservative sampling, privacy boundaries, temporal
black/uniform detection, static-content protection, performance evidence, and
visible WGC pixels pass; the full multi-resolution GPU timing and disruptive
real-world black/frozen matrix remain unrun.

## 27. New AUDIT findings

* **AUDIT-040 (P1 release blocker) — RESOLVED:** Windows extraction omitted Linux FFmpeg linker/SONAME
  aliases, allowing a Release link to mix system GPL-generation libraries.
  Fetch now materializes verified aliases; CMake uses `NO_DEFAULT_PATH`; the
  licence gate hashes aliases.
* **AUDIT-041 (P2 gate defect) — RESOLVED:** credential-shaped redaction fixtures made the
  mandatory hygiene gate fail. Fixtures now assemble test URLs at runtime; the
  scanner remains strict.
* **AUDIT-042 (P1 footage loss) — OPEN:** the healthy Windows runtime probe requested five seconds
  but produced 3.533 seconds / 91 frames after keyframe/timestamp trimming.
  This is outside the capture-health fix and needs save-duration investigation.

## 28. Commit hashes

* `2631258` — `feat: add cross-platform capture health recovery`
* `1769a2a` — `fix: keep Linux Release on pinned FFmpeg`
* `4fdc08c` — `test: keep credential fixtures hygiene-safe`
* Documentation — the commit containing this report; its hash is recorded in
  the final handoff because a commit cannot contain its own hash.

## 29. Working tree

At handoff, the only intended uncommitted change is the user's pre-existing
`.gitignore` edit. Generated native build/dependency outputs are ignored.

## 30. Confirmation that unrelated files were not committed

Confirmed. `.gitignore` was never staged or committed. Capture-health,
necessary Release-link/gate hardening, their tests, and documentation were the
only committed scopes.

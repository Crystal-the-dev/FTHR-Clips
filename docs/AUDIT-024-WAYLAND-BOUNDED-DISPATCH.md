# AUDIT-024 — Wayland Bounded Dispatch / Recovery

**Date:** 2026-08-13

**Status:** **CODE FIXED / AUTOMATED TESTED / REAL WAYLAND RUNTIME NOT VERIFIED**

The wlr-screencopy and ext-image-copy backends no longer call blocking
`wl_display_dispatch()` or `wl_display_roundtrip()`. Registry discovery,
output discovery, protocol negotiation, frame metadata, and frame completion
now share one deadline-aware event-dispatch implementation. Release builds,
native tests, sanitizers, a silent real Wayland socket, and headless recovery
pass. No real wlroots, KDE, or GNOME Wayland desktop was available.

## Original hanging paths

All Wayland objects and callbacks are owned by the Linux capture thread and use
the default event queue. No other thread dispatches the display. Before this
audit the main thread could set `running_ = false`, but the capture thread could
remain indefinitely inside any of these calls:

| Backend / phase | Original wait | Original timeout / shutdown behavior |
|---|---|---|
| wlr initialization | two `wl_display_roundtrip()` calls | none |
| wlr output discovery | `dispatch()` until `wl_output.done` | none |
| wlr resolution probe | `dispatch()` until buffer metadata | none |
| wlr resolution probe | `dispatch()` until `ready` / `failed` | none |
| wlr steady-state frame | `dispatch()` until buffer metadata | none |
| wlr steady-state frame | `dispatch()` until `ready` / `failed` | none |
| ext initialization | two `wl_display_roundtrip()` calls | none |
| ext output discovery | `dispatch()` until `wl_output.done` | none |
| ext session negotiation | `dispatch()` until buffer `done` / session stopped | none |
| ext steady-state frame | `dispatch()` until `ready` / `failed` / stopped | none |

A compositor stall therefore prevented `CaptureFrame(false)`, backend teardown,
the AUDIT-023 retry policy, terminal failure publication, and thread join.
Compositor disconnect return values were also not classified consistently.

## Bounded dispatch model

`src/wayland_dispatch.{h,cpp}` centralizes all Wayland waits and returns one of:

- `EventReceived`
- `Timeout`
- `Cancelled`
- `Disconnected`
- `Error`

Each wait follows the libwayland prepared-read protocol:

1. Dispatch already-pending default-queue events without blocking.
2. Retry `wl_display_prepare_read()` only after pending events are dispatched.
3. Flush outgoing requests; treat `EAGAIN` as a request to include `POLLOUT`.
4. Poll the display fd up to the monotonic deadline in cancellation-aware slices.
5. On `POLLIN`, call `wl_display_read_events()` and dispatch pending events.
6. On every exit without a read, cancel the prepared read through an RAII guard.

`POLLHUP` and `POLLERR`, connection-class `errno` values, a libwayland display
error, or failed event reads return `Disconnected`. `POLLNVAL` and other local
failures return `Error`. A short quiet poll slice is never itself a backend
failure; only the overall operation deadline is returned to the caller.

The old blocking roundtrips are replaced by `wl_display_sync()` callbacks
dispatched through the same helper and the same initialization deadline.

## Deadlines and cancellation

- **Initialization:** one shared 5-second monotonic budget per backend instance.
  Both registry/output sync callbacks and all protocol negotiation must complete
  inside that total budget. A broken compositor therefore cannot multiply the
  timeout for every initialization sub-step.
- **Frame request:** one shared 2-second budget per frame. For wlr this includes
  both buffer-metadata and copy-completion phases. This tolerates many display
  refresh intervals and the minimum configured capture cadence while still
  returning control well before an indefinite protocol stall.
- **Shutdown poll slice:** at most 50 ms. Poll still wakes immediately for real
  fd readiness; this is not a per-frame sleep and does not limit FPS.
- **Recovery:** unchanged AUDIT-023 policy — three reconnects after 250, 750,
  and 1500 ms cancellable backoffs, then `BACKEND_FAILED` and `running_ = false`.

The engine's atomic `running_` flag is passed read-only to both Wayland
backends. `CaptureEngine::Shutdown()` clears it before joining. A prepared read
is cancelled, the frame proxy is destroyed, and the capture thread returns.
The backend factory checks the same flag before and between wlr, ext, and X11
attempts, so shutdown cannot start a new fallback/reconnect attempt.

## Frame-request and disconnect behavior

On a frame timeout, frame failure, stopped ext session, display disconnect, or
dispatch error:

1. no synthetic frame is published;
2. any pending screencopy/image-copy frame is destroyed;
3. `CaptureFrame()` returns false;
4. the current backend and encoder are shut down and reset;
5. the replay ring is cleared;
6. health changes to `RECOVERING` and the existing bounded retry policy runs;
7. exhausted recovery publishes `BACKEND_FAILED` and clears `running_`.

Wayland callbacks still execute only on the capture thread, so teardown cannot
race an active callback. Disconnects do not cause further blind dispatches or
use of a frame proxy in the next generation.

## Capture-health interaction

Wayland event traffic does not increment `frame_count`. Only a successfully
completed capture followed by the existing encode path advances progress.
Consequently:

- an unchanged desktop that continues delivering real frames remains healthy;
- unrelated Wayland events cannot hide a real frame stall;
- a frame-request stall returns into backend recovery after its deadline;
- a display disconnect is diagnosed separately from static pixels;
- content-suspicion sampling and the existing 3/8-second UI progress states are
  unchanged.

No shared-memory field, response slot, save state, transactional-save behavior,
or Qt-thread path changed.

## Tests added and extended

The pipe-backed native `wayland_dispatch_test.cpp` uses real Linux `poll()` with
injected libwayland operations. It covers:

1. event readiness before the deadline;
2. bounded no-event timeout and non-spinning poll cadence;
3. shutdown cancellation with a prepared read;
4. display-fd HUP classification;
5. pending-event dispatch before prepare-read retry;
6. successful bounded sync callback;
7. initialization sync timeout, callback cleanup, and read cancellation.

The native recovery test additionally covers repeated generation failures,
retry exhaustion, stale-backend shutdown before replacement, healthy frame
progress resetting the incident budget, and shutdown during backoff preventing
another reconnect. Python source-contract tests prove both backends have no
blocking dispatch/roundtrip calls, share the same deadlines/helper, receive the
engine cancellation flag, clean timed-out frame requests, enter existing
recovery, and advance progress only after a completed frame. Existing
capture-health tests cover static/no-change frames and real counter stalls.

## Validation results

### Builds and native tests

- Linux Release CMake build using the pinned LGPL FFmpeg root: **PASS**, engine
  plus all native test executables built with no compiler warnings.
- Release CTest: **3 passed, 0 failed** (`capture_recovery_policy`,
  `transactional_save_contract`, `wayland_bounded_dispatch`).
- ASan + UBSan Debug build: **PASS**; CTest **3 passed, 0 failed**, with no
  sanitizer findings.
- Current Release linkage: required FFmpeg 62/60/9/6 SONAMEs resolve to the
  pinned tree and RPATH remains bundle-relative.

### Python and gates

- Focused Wayland/capture-health/save/IPC set: **97 passed, 2 skipped, 0 failed**.
  The Windows skips are native GNU C++ compiler harnesses; their corresponding
  binaries passed Linux CTest.
- Full Python suite: **357 passed, 30 skipped, 5 failed** out of 392. All five
  failures are the pre-existing unrelated dirty metadata-manager experiment,
  whose current `_load()` calls `self._.read_text()`. No AUDIT-024, capture,
  save, IPC, or transactional-save test failed.
- Engine response publication: pass, 48 source files.
- Shared memory: pass, 29 fields; Windows 2736 bytes and Linux 4272 bytes.
- Exception-handling ratchet: pass, 38 files / baseline 92.
- Ruff, compileall, version consistency, repository hygiene, and diff check: pass.
- Linux release-license gate: **101 checks, 0 failed, 0 warnings**.

### Runtime available in WSL/headless

- A real UNIX Wayland socket accepted the engine connection but intentionally
  never sent protocol data. wlr registry discovery returned `Timeout` after
  approximately five seconds instead of hanging. SIGTERM during the following
  ext registry wait returned `Cancelled`; the final Release process exited 0
  in **56 ms** after the signal.
- During five seconds on that silent socket, the process consumed **10 ms of
  combined user/system CPU time**, consistent with blocking poll rather than a
  busy loop.
- With both `DISPLAY` and `WAYLAND_DISPLAY` absent, the engine completed all
  three existing recovery attempts, reported `Recovery exhausted`, and shut
  down cleanly on SIGTERM.

These checks exercise real libwayland socket/read/poll behavior but not a real
Wayland compositor, registry, output, screencopy callback, image-copy callback,
hotplug, or compositor restart. **REAL WAYLAND RUNTIME VERIFIED remains NO.**

## Remaining limitations

- Normal capture, stop/start, save, compositor restart, output loss, and session
  change still require testing on real wlroots and ext-image-copy desktops.
- A socket that accepts but sends nothing validates deadlines/cancellation, not
  compositor-specific proxy lifetimes or protocol correctness.
- The 2-second frame and 5-second initialization constants are deliberately
  centralized per backend, but real compositor evidence may justify tuning
  them later without changing the dispatch contract.

## New audit finding

**AUDIT-044 (P1, OPEN) — X11 frame reads are not cancellation/deadline bounded.**

`backend_x11.cpp::CaptureFrame()` calls synchronous FFmpeg `av_read_frame()`;
the format context has no `AVIOInterruptCB`, deadline, or access to the engine
stop flag. An unresponsive X server/demuxer could therefore reproduce the same
shutdown/recovery hang class on the X11 fallback. This is outside AUDIT-024's
Wayland scope and was documented, not fixed. A dedicated audit must verify and
bound X11 initialization and frame reads without weakening normal x11grab
capture.

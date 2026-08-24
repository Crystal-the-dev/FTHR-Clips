# Final Performance Qualification

Date: 2026-08-24
Host: Windows 10 Pro 19045, NVIDIA GeForce RTX 4060 Ti, driver 610.88, two displays (1920×1080 and 2560×1080)

## Scope and method

Measurements used the source-built Release x64 engine, isolated UI profiles, `psutil`, NVIDIA device-wide counters, FFprobe, and full FFmpeg decode. Results under `build/performance_baseline/` are intentionally ignored build evidence. Device-wide GPU figures include the desktop and wallpaper workload. Shared Memory v4 does not expose `frames_dropped`; that value remains unmeasured rather than inferred.

The baseline was captured at cleanup commit `fc6364e`. Performance changes were retained only where a physical or deterministic result supported them. A proposed background-only prewarm deferral was reverted because it produced no measurable benefit.

## Before and after

### UI, background replay, and shutdown

| Scenario | Metric | Before | After | Result |
|---|---:|---:|---:|---|
| Background, capture active | UI process working set | 119.2 MB | 115.4 MB | -3.8 MB |
| Background, capture active | UI process private bytes | 634.3 MB | 612.8 MB | -21.5 MB |
| Background, all measured processes | Working set | 360.1 MB | 415.3 MB | Higher because capture now produces ~60 fps and the repaired notification process remains alive |
| Background, all measured processes | CPU | 6.10% | 9.62% | Higher throughput: 1,420 vs 1,686 frames during the same startup/measurement window |
| Background | UI constructed | No | No | Preserved |
| Background | Startup to event loop | 0.404 s | 0.403 s | Unchanged |
| Background | Shutdown | 2.555 s | 0.526 s | -79.4% |
| Full UI + replay | Working set | 483.7 MB | 460.8 MB | -22.9 MB |
| Full UI + replay | Private bytes | 1,292.9 MB | 1,261.2 MB | -31.7 MB |
| Full UI + replay | CPU | 10.38% | 9.37% | -1.01 percentage points |
| Full UI | UI build | 1.275 s | 1.219 s | -4.4% |
| Full UI | Shutdown | 2.375 s | 0.484 s | -79.6% |

The aggregate background memory comparison is deliberately not presented as a memory regression: the baseline aliased a 60 fps target to roughly 45–50 fps and its Windows notification child had exited. The post-change run performs more capture work and keeps that child functional. The UI process itself remains substantially smaller than the full UI (115.4 MB versus 212.2 MB working set), and a physical workflow saved a clip before the full UI was ever constructed.

### Capture cadence and replay memory

| Scenario | Before | After | Delta |
|---|---:|---:|---:|
| H.264 configured 60 fps | 46.31 fps | 60.01 fps | +29.6% |
| HEVC configured 60 fps | 44.95 fps | 60.00 fps | +33.5% |
| AV1 configured 60 fps | 45.58 fps | 60.01 fps | +31.7% |
| H.264 configured 120 fps on host display | 75.32 fps | 94.14 fps | +25.0%; host-limited |
| H.264, 30 s buffer, 60 s run, end working set | 251.9 MB | 220.7 MB | -31.2 MB |
| H.264, 30 s buffer, 120 s run, end working set | 299.4 MB | 240.2 MB | -59.2 MB |
| H.264, 30 s buffer, final-third growth | 0.52 MB/s | 0.23 MB/s | -55.1% while slot capacities finish warming |
| H.264, 300 s buffer/run, end working set | 547.1 MB | 416.2 MB | -130.8 MB |
| H.264, 300 s buffer/run, end private bytes | 910.3 MB | 777.8 MB | -132.5 MB |
| H.264, 300 s save latency | 2.070 s | 1.667 s | -19.5% |

The former ring reserved `buffer_seconds × fps × 2` slots. The new policy retains requested history, one maximum four-second GOP, and one second of publication safety. Slot reductions are 3,600→2,100 for 30 seconds, 7,200→3,900 for 60 seconds, and 36,000→18,300 for 300 seconds. Memory remains bounded; the 120-second/30-second-buffer soak ended with 25 threads and 484 handles, with no accumulating thread or handle count.

All post-change H.264, HEVC, and AV1 samples used the GPU-resident WGC→NVENC path. Every saved qualification clip fully decoded, contained two AAC streams, had no `.partial`, and respected the four-second maximum keyframe interval. CPU and GPU utilization rose in some capture comparisons because the engine now produces the requested 60 fps instead of silently producing ~45–50 fps.

### Save, background workflow, and screenshots

| Operation | Result |
|---|---:|
| H.264 30 s native save | 0.202 s |
| H.264 60 s native save | 0.353 s |
| HEVC 30 s native save | 0.403 s |
| AV1 30 s native save | 0.514 s |
| H.264 300 s native save | 1.667 s |
| Background save before UI construction | 0.752 s UI-level completion; 30.013 s; full decode; two AAC streams |
| Save after tray/UI restore | 0.752 s UI-level completion; 30.003 s; full decode; two AAC streams |
| Primary 1920×1080 screenshot | 35.5 ms capture + 402.2 ms PNG encode/publish average |
| Secondary 2560×1080 screenshot | 45.1 ms capture + 110.8 ms PNG encode/publish average |

Each monitor was captured five times through its stable Windows device identity. All ten PNGs were readable, no `.partial` remained, and thread/handle deltas were zero. The test exposed and fixed a PySide6 name mismatch (`DISPLAY1/2` versus EDID names) by matching the current Windows monitor geometry without an enumeration-index fallback.

### Audio and playback

No audio hot path was changed. The baseline production mixer decoded and mixed five seconds with one decoder worker:

| Tracks | Mix wall time | CPU time | Working set | Seek latency range |
|---:|---:|---:|---:|---:|
| 1 | 6.2 ms | 0.0 ms | 56.1 MB | 0.09–0.16 ms |
| 2 | 11.7 ms | 15.6 ms | 56.6 MB | 0.12–0.25 ms |
| 4 | 13.4 ms | 15.6 ms | 58.4 MB | 0.14–0.38 ms |
| 8 | 25.3 ms | 31.3 ms | 61.4 MB | 0.18–0.70 ms |

The final playback smoke recompiled its probe outside the repository and passed FFmpeg 1/4/8-stem mix, QMediaPlayer nine-track discovery, and QAudioSink start/stop with no device error. The existing bounded queue, synchronization model, and multi-audio architecture were unchanged.

## Retained optimizations and risks

| Change | Evidence | Regression risk |
|---|---|---|
| QPC deadline scheduler for WGC and DXGI | 90/144→60 deterministic tests; physical 60 and 120 target runs | Low; first frame is accepted and missed deadlines advance without bursts |
| Bounded encoded-ring headroom | 30/120/300-second measurements and capacity tests | Low; retains four-second GOP pre-roll plus one second safety |
| Native Windows capture-card environment and bounded close | Physical shutdown 2.38→0.48 s; Windows child remains functional | Low; Linux XWayland behavior remains capability-scoped |
| Numeric logging arguments retain their type | Physical health log plus regression test | Low; secret-shaped strings remain redacted |
| Selected-monitor screenshot geometry mapping | Ten physical captures across both connected monitors | Low; missing/ambiguous geometry still fails closed |
| Thumbnail fade timer owned by its card | Background-save/restore exposed the stale callback; lifecycle regression test added | Low; visual timing is unchanged |

No extra frame copy, CPU readback, Shared Memory change, audio queue change, or save-snapshot weakening was introduced.

## Regression and remaining qualification

- Full Python suite: pass.
- Ruff and compileall: pass.
- Windows Release x64 build: pass.
- Native Windows suite: 389 checks pass.
- Shared Memory v4: Windows 29 fields/2,736 bytes; Linux 29 fields/4,272 bytes; pass.
- Engine response, exception, version, generated asset, repository hygiene, and license gates: pass.
- License gate: 160 checks, no failures, one expected Windows-host warning because the Linux FFmpeg bundle is not present.
- Existing Windows artifact lifecycle contract: pass with release-blocking unsigned-artifact warnings. The checked `dist/` and installer are pre-change artifacts and were not represented as the final candidate package.

Not physically verified in this phase: Windows 11 per-app stems, Windows 11 border suppression, AMD/Intel hardware, Linux runtime, audio-device recovery, viewer open/close soak, and installed-package upgrade/uninstall. `frames_dropped` remains unavailable without changing frozen Shared Memory v4. These belong to final physical qualification, not to a new source architecture.

## Decision

The source tree is ready to produce a release-candidate build. It is not authorization to publish the existing unsigned/stale artifacts. The next and only recommended task is **FINAL PHYSICAL QUALIFICATION + RELEASE CANDIDATE HANDOFF**.

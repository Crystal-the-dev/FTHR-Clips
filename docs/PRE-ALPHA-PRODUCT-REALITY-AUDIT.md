# FTHR Clips Pre-Alpha Product Reality Audit

> **Temporary internal development document — 2026-08-21.** This is a source-of-truth product audit for product-owner and UI-rework decisions. It is not release marketing, legal advice, or a claim of performance parity with Medal.

The current checkout, not older prose, is authoritative. Where source, tests, and runtime evidence differ, this report uses the order **runtime evidence > current source > tests > documentation**. Status words in this document use exactly the requested classification vocabulary; automated testing and real runtime verification are reported separately.

## 1. Executive summary

FTHR is already a real local replay recorder, not a mock-up. On the physically tested Windows/NVIDIA path it starts capture automatically, keeps an encoded GPU replay ring, saves timestamp-selected H.264/HEVC/AV1 clips transactionally, includes one system-audio stream, discovers the saved file in the library, and reports capture health. Thirty- and sixty-second NVIDIA saves were fully decoded and measured at the current HEAD.

The broad product shown by the UI is not yet equally real. AMD AMF and Intel QSV support for H.264/HEVC/AV1 is code-integrated and automated-tested, but has no physical AMD/Intel or hybrid-laptop qualification. Linux has real Wayland and X11 implementations, but no current real GNOME/KDE/wlroots desktop qualification; X11 can still block shutdown inside FFmpeg/x11grab. Several controls also misstate when or whether a value reaches the running engine, including Windows codec apply, long replay durations, 360 FPS, quality changes, Linux hotkey instructions, the update page, focus pause, and per-source audio controls.

The largest architectural mismatch is audio. Normal Windows capture records the default system output as one already-mixed WASAPI loopback stream; a separately buffered microphone is mixed into that stream after the engine has already committed the MP4. Normal Linux capture currently opens the PulseAudio **default source**, which is not reliably desktop/output audio, and separately adds the Python microphone after save. Linux “Multiband Audio” reroutes applications into per-category null sinks, stores raw category PCM until save, then mixes everything into one AAC stream and deletes the temporary WAVs. Windows does not implement that multiband engine path at all. Current FTHR therefore does **not** create genuine game/mic/chat/music tracks, even though parts of the editor and settings UI say or imply that it does.

**Audio recommendation: FULL AUDIO ARCHITECTURE REWORK** if independent cross-platform tracks are part of the product promise. For a narrow alpha that promises only one mixed system-plus-microphone track, the smaller safe decision is to hide multiband and per-source track controls, correct Linux source selection, and stabilize the existing single-track path before redesigning audio later.

**Release conclusion for the broad planned Windows + Linux alpha: NO, not this exact build.** A deliberately restricted Windows/NVIDIA cohort could become credible after a small product-truth pass and end-to-end UI/installer verification. Broad AMD/Intel and Linux claims remain blocked by runtime qualification, and exposed multiband behavior is not alpha-safe.

## 2. Current build / branch / HEAD

| Item | Current evidence |
|---|---|
| Authoritative checkout | `C:\Users\Tom\Desktop\FTHR_Clips_alpha_baseline` |
| Branch | `fix/windows-alpha-hardware-paths` |
| HEAD | `fbd855163494b68f4319a8107d748eb85336108e` |
| HEAD subject | `docs: finalize AUDIT-049 qualification` |
| Initial Git status | Clean |
| Product version | `1.0.0-alpha` |
| Latest substantive work | Windows codec-neutral replay, topology-aware monitor selection, native NVIDIA H.264/HEVC/AV1, AMD AMF and Intel QSV matrices, Windows hardware qualification harness |

Validation performed during this audit:

| Check | Result | What it does and does not prove |
|---|---|---|
| `python -m pytest tests -q` | **394 passed, 30 skipped** | Current Python/contract suite passes; skips remain platform/dependency-specific. |
| `python -m ruff check .` | **PASS** | Python lint only. |
| `python -m compileall -q FTHR_UI tests tools` | **PASS** | Python sources compile; not a UI runtime test. |
| Release-licence verification | **PASS: 76 checks, 0 failed, 1 warning** | Windows assets/Qt/FFmpeg provenance passes. Warning: Linux FFmpeg was not vendored in this checkout. Technical, not legal advice. |
| Version consistency | **PASS** | Source, installer, release notes, and build definitions agree. |
| Shared-memory contract | **PASS** | Windows 2736-byte and Linux 4272-byte layouts and enums agree. |
| Engine response-publication contract | **PASS** | Response payload ordering and bounded string helpers pass. |
| Exception-handling contract | **PASS** | No bare/BaseException handlers; known silent-handler baseline unchanged. |
| Repository hygiene | **PASS** | 447 tracked/working-tree files scanned; no detected secret/user-data/large-binary issue. |
| Native Windows qualification report | **PASS on RTX 4060 Ti for NVIDIA only** | Real H.264/HEVC/AV1 at 1920x1080@60. This does not qualify AMD, Intel, hybrid adapters, installers, or the whole GUI flow. |
| Native test executable in checkout | **Not present** | Native results are represented by committed tests/reports, not re-executed in this audit. |

Primary evidence includes `docs/AUDIT-ID-MAP.md`, `docs/AUDIT-049-WINDOWS-CODEC-STAGES.md`, `build/audit-049-nvidia-final/qualification-report.json`, the current Windows/Linux engine source, `FTHR_UI/main.py`, and the UI/core modules cited below.

## 3. Platform support summary

| Platform/cohort | What actually exists | Real runtime evidence | Status |
|---|---|---|---|
| Windows 10 + NVIDIA RTX, native resolution | WGC/DXGI capture, exact monitor-to-adapter resolution, native NVENC H.264/HEVC/AV1, encoded replay, WASAPI loopback | RTX 4060 Ti: all three codecs, 1920x1080@60, 30/60-second full decode, one audio stream; whole packaged UI lifecycle remains unverified | **COMPLETE** |
| Windows + AMD | Same-adapter WGC/DXGI plus FFmpeg AMF H.264/HEVC/AV1; native-resolution restriction | Automated policy/encoder tests only; no physical AMD GPU result | **EXPERIMENTAL** |
| Windows + Intel | Same-adapter WGC/DXGI plus FFmpeg QSV H.264/HEVC/AV1; scaling code exists | Automated policy/encoder tests only; no physical Intel GPU result | **EXPERIMENTAL** |
| Windows hybrid/multi-GPU | Capture monitor resolves an owning adapter; cross-adapter/raw fallback deliberately disabled | No hybrid laptop qualification | **EXPERIMENTAL** |
| Linux Wayland/wlroots | `wlr-screencopy` backend with bounded dispatch and recovery | Automated dispatch tests; no current real wlroots session result | **EXPERIMENTAL** |
| Linux Wayland ext-image-copy | ext-image-copy backend with bounded dispatch and recovery | Automated dispatch tests; no current GNOME/KDE real-desktop result | **EXPERIMENTAL** |
| Linux X11/XWayland | FFmpeg `x11grab` backend and encoded replay; bounded cancellation is absent | WSL2/XWayland produced black frames; no representative X11 gaming desktop verification | **BROKEN** |
| Linux packaging | PyInstaller AppDir/AppImage build path | Offscreen start only; no complete desktop integration/capture lifecycle | **EXPERIMENTAL** |

The broad phrase “Windows and Linux support” must therefore be qualified. Current runtime evidence supports one Windows/NVIDIA configuration strongly; all other cohorts require explicit release scoping or further qualification.

## 4. Complete feature inventory

### Master feature table

| Feature | Exists | Actual Behavior | Platform | Tests | Runtime Verified | Status | Product Review Needed |
|---|---:|---|---|---|---|---|---:|
| Automatic replay capture | Yes | Engine launches and capture starts after UI startup; no ordinary start button is required | Both | Automated contracts | NVIDIA Windows only | **PARTIAL** | Yes — expose capture lifecycle accurately |
| Manual start/stop capture | Limited | Internal commands exist, but normal UI treats capture as always on; Windows legacy start/stop is not replay pause | Both | Limited | No | **MISLEADING** | Yes |
| Normal clip save | Yes | Timestamp-selected replay snapshot, transactional MP4 commit, then Python post-processing | Both | Extensive | NVIDIA Windows only; other platforms unverified | **PARTIAL** | Yes — “fully ready” timing and platform scope |
| Extended clip save | Yes | Same pipeline with separate duration setting/hotkey | Both | Extensive | NVIDIA 60-second save only | **PARTIAL** | Yes — supported duration cap and platform scope |
| Replay warm-up | Yes | Health layer can shorten requested save to safely available history | Both | Automated | NVIDIA warm-up | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes — user wording/expectation |
| Rapid-save admission | Yes | Debounce and single-flight save state prevent ambiguous overlapping responses | Both | Automated | NVIDIA rapid saves | **COMPLETE** | No |
| Transactional engine save | Yes | Writes `.partial`, finalizes, then atomically renames without replacing an existing clip | Both | Automated/native contracts | NVIDIA Windows | **COMPLETE** | No |
| Fully-final post-processing state | No unified state | `CLIP_SAVED` is published before mic/multiband/crop/watermark/camera completion | Both | Partial | No end-to-end timing | **PARTIAL** | Yes |
| Capture-health admission | Yes | Tracks freshness/content/generation and rejects or shortens unsafe saves | Both | Automated | Partial Windows runtime | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes — exact user feedback |
| Backend recovery | Yes | New capture generation, ring reset, bounded retry policy | Both | Automated | Partial | **EXPERIMENTAL** | Yes — retry/failure UX |
| Monitor selection | Yes | Stable Windows device path; Linux output name | Both | Automated | Real two-monitor Windows; Linux unverified | **PARTIAL** | Yes — Linux scope |
| Windows WGC capture | Yes | Preferred desktop/window path with monitor and DXGI fallbacks | Windows | Native tests | NVIDIA desktop | **COMPLETE** | No for qualified path |
| Windows DXGI fallback | Yes | Used after WGC failure and for protected-window fallback | Windows | Automated/native | Limited | **EXPERIMENTAL** | Yes — unsupported cases |
| NVIDIA H.264/HEVC/AV1 | Yes | Native NVENC, same capture adapter, encoded GPU replay | Windows | Automated/native | All three on RTX 4060 Ti | **COMPLETE** | No for qualified configuration |
| AMD H.264/HEVC/AV1 | Yes | FFmpeg AMF path; same-adapter, native resolution | Windows | Automated | No physical AMD | **EXPERIMENTAL** | Yes — whether alpha claims it |
| Intel H.264/HEVC/AV1 | Yes | FFmpeg QSV path; same-adapter | Windows | Automated | No physical Intel | **EXPERIMENTAL** | Yes |
| Unsupported codec/vendor behavior | Yes | Windows fails closed; no silent software/cross-adapter/raw fallback | Windows | Automated | Partial | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes — fail vs fallback |
| Linux Wayland capture | Yes | wlr first, ext-image-copy second, then X11 fallback | Linux | Automated | No representative desktop | **EXPERIMENTAL** | Yes — alpha platform scope |
| Linux X11 cancellation | No bounded guarantee | `avformat_open_input`, stream info, and reads can block without an interrupt callback | Linux | Contract gap known | No | **BROKEN** | Yes — ship X11 or disable |
| System audio | Yes | Windows default render mix; Linux default Pulse source, not guaranteed desktop output | Both | Unit/contracts | Windows NVIDIA report: one stream | **PARTIAL** | Yes — Linux source model |
| Microphone | Yes | Python PortAudio ring; post-save FFmpeg mix when audio is enabled | Both | Unit tests | No complete device matrix | **PARTIAL** | Yes — fallback, gain, source identity |
| Multiband/per-app audio | Linux-only engine path | Linux null sinks + category PCM + save-time one-track mix; ignored by Windows engine | UI on both | Unit tests | No | **BROKEN** | Yes — hide or redesign |
| True multi-track output | No | Normal FTHR clips contain one final audio stream, not independent app/mic tracks | Both | Editor tests are assumption-based | NVIDIA ffprobe: one | **NOT IMPLEMENTED** | Yes |
| Clip editor playback/trim/crop | Yes | QMediaPlayer preview; FFmpeg copy or software re-encode export | Both | Some automated | Not end-to-end | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes — output and failure semantics |
| Per-source editor sliders | UI exists | Assumes fixed track order that current recorder does not produce | Both | Limited | No | **MISLEADING** | Yes |
| Screenshot + crop | Yes | UI-owned screenshot, optional crop editor, PNG output | Both | Limited | No multi-monitor runtime | **PARTIAL** | Yes — selected monitor semantics |
| Clip library | Yes | Local/imported scanning, date grouping, thumbnails, playback, filters | Both | Automated | No large-library runtime | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes |
| Imported folders | Yes | Indexes external media in place; does not copy/move | Both | Limited | No | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes — ownership/delete semantics |
| Imported-file deletion | Yes | Deletes the original external file after generic confirmation | Both | No safety-specific runtime | No | **BROKEN** | Yes |
| Metadata | Yes | Absolute-path keyed JSON stores tags and description only | Both | Automated | No migration runtime | **PARTIAL** | Yes |
| Game detection/switch prompt | Yes | Visible/fullscreen-window heuristics; prompt and transient capture switch | Both | Limited | No representative game matrix | **EXPERIMENTAL** | Yes |
| Game grouping | Superficial | Parent directory name is displayed as “game”; no reliable assigned game metadata | Both | Limited | No | **MISLEADING** | Yes |
| Watermark | Yes | Post-save drawtext video re-encode | Both | Limited | No | **EXPERIMENTAL** | Yes |
| Auto-crop | Yes | Post-save cropdetect and video re-encode | Both | Limited | No | **EXPERIMENTAL** | Yes |
| Camera overlay | Yes | Continuous 90-second JPEG ring; post-save software overlay/re-encode | Both | Limited | No performance runtime | **EXPERIMENTAL** | Yes |
| Generic upload | Yes | User-defined HTTP(S) multipart endpoint, manual/immediate/interval modes | Both | Automated retry tests | No real provider | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes — security/provider/support |
| Local share/export | Yes | Writes derived MP4 to `Shared`/`Exported`; no remote provider | Both | Limited | No | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes |
| Cross-PC/library migration | No | No archive/metadata migration workflow | Both | None | No | **NOT IMPLEMENTED** | No for alpha if hidden |
| Update system | UI only | Static “latest version” text; no check/download/signature/install path | Both | None | No | **PLACEHOLDER** | Yes — hide |
| Notifications/sounds | Yes | Separate animated card process and four event sounds with volume controls | Both | Limited | Partial | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes — timing/duplicates |
| Themes | Yes | Local theme colors/icons/sounds and ZIP import/export | Both | Limited | No adversarial/runtime matrix | **EXPERIMENTAL** | Yes |
| Autostart | Yes | Windows HKCU Run registry integration | Windows | Limited | No installer lifecycle | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Yes |
| Startup splash setting | UI only | Checkbox is always checked and has no persistence/backend connection | Both | None | No | **PLACEHOLDER** | Yes — remove/hide |
| Plugins | No | No product plugin API, loader, permissions, or versioning | Both | None | No | **NOT IMPLEMENTED** | No for alpha |
| Diagnostics export | No | Logs/tools exist, but no user-facing support bundle/export | Both | Tool tests | No | **NOT IMPLEMENTED** | Yes — support strategy |
| Windows installer | Built definition/bundle | PyInstaller onedir + Inno Setup + bundled engine/FFmpeg | Windows | Release gates | Full install/update/uninstall not run | **EXPERIMENTAL** | Yes |
| Linux AppImage | Build definition | PyInstaller AppDir -> AppImage | Linux | Build/gates in prior audits | Offscreen only | **EXPERIMENTAL** | Yes |

## 5. Capture/replay behavior

### Current lifecycle

1. `FTHR_UI/main.py` loads settings, creates FTHR directories, removes only stale FTHR-named partials, starts supporting services, then launches the platform engine.
2. The engine receives FPS, replay buffer duration, target dimensions, bitrate, memory pool, source/monitor, scaling, codec/preset, multiband flag, and audio flag as process arguments.
3. The UI connects to versioned shared memory. Capture begins automatically; the normal product surface has no clean user-owned “start capture” session step.
4. The capture backend supplies timestamped frames. Encoded packets enter the replay ring. Capture health tracks generation, progress, freshness, and suspicious content.
5. F9/F10 or the UI action enters the Python save state machine. It rejects duplicate/in-flight saves, checks capture health, derives a safe duration, and publishes a single IPC command with path and duration.
6. The engine snapshots packets by presentation timestamp, writes a same-directory `.partial`, closes/trailers it, and atomically commits the final MP4. Only then does it publish `CLIP_SAVED`.
7. The UI immediately refreshes the library and shows/sounds “saved.” It then starts exactly one Python route: microphone mix, multiband mix, or finalize. Crop, watermark, and camera may follow in series.
8. Upload is gated by a `threading.Event`, but waits only 60 seconds. The file can therefore still be changing when a slow post-process exceeds that ceiling.

### Core user-flow table

| User Action | UI Handler | Backend Path | Async? | Success | Failure | User Feedback |
|---|---|---|---:|---|---|---|
| Launch FTHR | application/MainWindow startup | settings -> engine process -> shared memory -> backend -> encoder | Yes | UI connects and capture health advances | Typed startup diagnostics; engine terminated on failed connection | Splash/status/error card |
| Save normal clip | hotkey/clip action -> save state | health admission -> `SAVE_CLIP` -> ring snapshot -> transactional mux -> post-process | Yes, except small IPC admission work | Engine MP4 commits, later post-process completes | Admission reject, engine error, timeout, or post-process fallback | “Saved” currently appears at engine commit, not final readiness |
| Save extended clip | extended hotkey/action | Same path with extended duration | Yes | Same as normal | Same as normal; may be shorter than UI setting if ring/history differs | Same notification path |
| Select monitor | capture popup | persist device path/output name -> full engine restart | Yes | New generation uses resolved output/adapter | Startup/capture error; prior ring is cleared | Applying/restart status |
| Change FPS/resolution | capture settings | persist -> engine restart -> new ring generation | Yes | New engine config | Startup failure | UI indicates restart/apply |
| Change quality | capture popup | persists bitrate level only | No running-engine reconfigure | Applies on a later engine restart | No immediate failure because no immediate action | Current UI can imply it already applied |
| Change codec/preset | encoder settings | persist -> `RECONFIGURE_ENCODER` IPC | Yes | Linux restarts encoder/ring | Windows command is a stub; running engine stays unchanged | UI says applying on both platforms |
| Pause on lost focus | setting + focus monitor | foreground polling -> START/STOP command | Yes | Linux pause path can stop ring updates | Windows focus discovery/path does not implement intended replay pause | UI presents a cross-platform capability |
| Take screenshot | F11/UI | Qt/grim capture -> crop dialog -> PNG | UI dialog + local I/O | Original or cropped PNG saved | Capture/save warning | Capture card and sound after accepted edit |
| Import folder | library UI | persist external directory -> scanner indexes in place | Scanner thread | Items appear with generated thumbnail | Missing/unreadable items omitted or blank | Library refresh |
| Delete clip | viewer | detach player -> `os.remove(path)` | Mostly synchronous | Actual file and thumbnail cache removed | File-lock/I/O dialog | Generic irreversible confirmation, including external files |
| Upload clip | viewer/save manager | readiness wait -> multipart POST -> retry/history/delete policy | Worker thread | HTTP success and optional local delete | Retries, then error/history behavior | Card/error; no progress or cancel |

### Important replay details

- Default normal/extended settings are 30/60 seconds. The engine buffer duration is computed at process start from the longer configured duration plus safety margin; changing the duration in the UI does not resize the current engine ring.
- `MainWindow` restart-time validation clamps values differently from the capture popup: effective normal clip 5–300 seconds, extended 5–600 seconds, and FPS 15–240. The popup offers normal/extended up to 900 seconds and FPS up to 360. Stored UI values can therefore differ from effective runtime values.
- Save selection is timestamp-driven rather than packet-count-driven. A warming buffer can produce a shorter valid clip; it does not fabricate unavailable history.
- Static imagery is not itself treated as failure. Health uses conservative distributed content sampling plus progress/freshness/generation information to avoid rejecting legitimate static screens solely for being static.
- A monitor/backend/encoder restart creates a new capture generation and clears replay history. The UI must present this as losing the previous replay window.
- Save while recovery is active is admitted or rejected by capture health; save while shutdown is cancelled by the save state/teardown path. Shutdown does not promise completion of a newly requested save.

Evidence: `FTHR_UI/main.py`, `FTHR_UI/core/save_state.py`, `FTHR_UI/core/capture_health.py`, `FTHR_UI/core/capture_bridge.py`, `FTHRcapture/FTHRclips/src/save_clip_task.cpp`, `FTHRcapture_linux/src/save_clip.cpp`, and AUDIT-022/023/028/035/042.

## 6. Windows capture/codec behavior

### Actual execution flow

1. The persisted Windows monitor identifier is a stable display device path, not a display index.
2. `windows_monitor_resolver.cpp` resolves that path to source/target/GDI/HMONITOR/DXGI output and the adapter that owns the selected output. AUDIT-048 includes a real two-monitor result.
3. Normal desktop capture prefers WGC for the selected monitor and falls back to DXGI. Regular window capture tries WGC window, then WGC monitor, then DXGI. Known protected/anti-cheat window cases use monitor capture plus a foreground gate before DXGI fallback.
4. Replay policy selects an encoder on the **same adapter**. Cross-adapter encode, raw-frame replay fallback, and silent software fallback are deliberately disabled.
5. NVIDIA uses native NVENC. AMD and Intel use FFmpeg AMF/QSV replay encoder implementations. Requested codec and active backend are surfaced in engine startup status/diagnostics.
6. Encoded packets, not full-resolution raw frames, are retained in the Windows replay pool. Save remuxes a timestamp-selected snapshot and AAC-encodes the audio snapshot.

### Codec matrix

| Vendor | H.264 | HEVC | AV1 | Scaling | Unsupported behavior | Evidence status |
|---|---|---|---|---|---|---|
| NVIDIA | Native `h264_nvenc` | Native `hevc_nvenc` | Native `av1_nvenc` | Native path is integrated; physical report used source/native dimensions | Startup fails closed if unavailable | Automated + real RTX 4060 Ti |
| AMD | FFmpeg `h264_amf` | FFmpeg `hevc_amf` | FFmpeg `av1_amf` | Current AMF replay path rejects capture/encode dimension mismatch | Startup fails closed | Automated only |
| Intel | FFmpeg `h264_qsv` | FFmpeg `hevc_qsv` | FFmpeg `av1_qsv` | Scaling code integrated | Startup fails closed | Automated only |
| Other/unsupported | None | None | None | N/A | No raw/software fallback on Windows | Policy automated; hardware matrix incomplete |

`codec_pref=auto` maps to H.264 in the Windows engine. That differs from Linux, where Auto walks an AV1-first hardware/software candidate list. “Auto” is therefore not one product behavior across platforms.

### Physical NVIDIA evidence

The committed qualification report records Windows 10, RTX 4060 Ti, 1920x1080 at 60 FPS, 16 Mbps, same-adapter native NVENC, and no raw replay pool for all three requested codecs. Each full 30-second result was about 30.016 seconds; each full 60-second result was about 60.01 seconds; all clips fully decoded, had one audio stream, and left no `.partial`. The warm-up clips were correctly shorter because only about six seconds of history existed. Reported process CPU around 11% is a controlled observation, **not** a gaming benchmark and not evidence that FTHR outperforms Medal.

### Product limitations

- AMD, Intel, hybrid laptops, topology change during a session, WGC failure-to-DXGI transition, protected fullscreen games, and non-native scaling do not have complete current physical qualification.
- Windows `RECONFIGURE_ENCODER` is explicitly a restart-required stub in `FTHRcapture/FTHRclips/src/main.cpp`. The current UI still acts as if Apply reconfigured it.
- AMF rejects non-native output size. QSV scaling is present but not hardware-proven. The UI does not communicate the vendor-specific difference.
- Failure-closed behavior is technically coherent for avoiding hidden CPU/cross-GPU cost, but product must decide whether a user should instead receive a guided fallback.

## 7. Linux capture behavior

### Backend selection and capture

The current source order in `FTHRcapture_linux/src/capture_backend.cpp` is:

1. On Wayland, try `wlr-screencopy`.
2. Then try ext-image-copy.
3. Fall back to FFmpeg `x11grab` where available.

The Wayland backends use the shared bounded-dispatch layer: finite initialization/frame deadlines, cancellation tied to the running state, disconnect classification, and recovery through new backend generations. This is implementation plus automated evidence, not proof across GNOME, KDE, and wlroots compositors.

The X11 backend in `FTHRcapture_linux/src/backend_x11.cpp` calls `avformat_open_input`, `avformat_find_stream_info`, and `av_read_frame` without an `AVIOInterruptCB`, an operation deadline, or a subprocess boundary. A stuck X server/XCB reply can therefore keep the capture thread inside FFmpeg after shutdown sets the running flag. Joining that thread is not truly bounded. AUDIT-044 remains open. The reserved helper-worker architecture in AUDIT-045 is not implemented.

### Encoding and replay

Linux capture supplies CPU BGRA frames, throttles them from monotonic timestamps, converts through `sws_scale`, then tries FFmpeg encoders. Hardware candidates include NVIDIA, AMD, and Intel; software candidates include OpenH264 and AV1/HEVC libraries where the pinned build exposes them. Unlike Windows, Linux permits software fallback. The code recognizes hardware encoder names, but current real-desktop/hardware-device behavior is not qualified across vendors.

The encoded ring uses timestamp-selected save snapshots. Linux currently deep-copies a snapshot while holding the ring mutex; AUDIT-047 records that this may pause capture for several 60 FPS pushes in measured long-snapshot cases.

### What is and is not verified

| Claim | Status |
|---|---|
| Wayland dispatch has finite source-level deadlines/cancellation | Automated-tested |
| Actual GNOME ext-image-copy capture | **UNVERIFIED** |
| Actual KDE ext-image-copy capture | **UNVERIFIED** |
| Actual wlroots wlr-screencopy capture | **UNVERIFIED** |
| Representative native X11 capture and bounded shutdown | **UNVERIFIED / bounded shutdown known broken** |
| WSL2/XWayland video validity | Observed black output; not an acceptable Linux runtime qualification |
| Linux AppImage UI offscreen startup | Previously exercised; not a capture test |
| Linux hardware encode on NVIDIA/AMD/Intel | **UNVERIFIED** at current qualification level |

For alpha, Linux must either be explicitly experimental with a narrow supported-environment list or receive real compositor, audio, shutdown, and AppImage lifecycle qualification.

## 8. Video settings behavior

The mandatory settings table appears in section 21; this section focuses on video truth and apply timing.

| Setting | UI range/choice | Stored value | Running-engine reality | Status |
|---|---|---|---|---|
| Normal replay length | 5–900 s | `clip_length` | Restart-time code clamps effective value to 5–300 s; current ring is not resized on change | **MISLEADING** |
| Extended replay length | 30–900 s | `extended_clip_length` | Restart-time code clamps effective value to 5–600 s; current ring is not resized on change | **MISLEADING** |
| FPS | 30–360 | `framerate` | Restart-time code clamps to 15–240; apply/restart is required | **MISLEADING** above 240 |
| Resolution | 480p/720p/1080p/1440p/source | `resolution` | Consumed at engine start; vendor scaling limitations differ | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** |
| Quality | low/medium/high | `bitrate_level` | Maps to bitrate at startup; changing it does not reconfigure/restart current engine | **MISLEADING** during current session |
| Codec | auto/H.264/HEVC/AV1 | `codec_pref` | Startup consumes it. Linux reconfigure restarts encoder/ring; Windows reconfigure command is a stub | **MISLEADING** on Windows Apply |
| Encoder preset | 1–7 | `encoder_preset` | Same apply behavior as codec | **MISLEADING** on Windows Apply |
| Scaling mode | stretch/fit in config | `scaling_mode` | Engine consumes it at startup, but normal UI does not expose it | **PARTIAL** |
| Capture monitor | display selector | stable path/name | Full engine restart and new replay generation | **COMPLETE** on qualified Windows path |
| Hardware acceleration | Not a direct toggle | Implied by codec/vendor | Windows fails if same-adapter hardware path is unavailable; Linux may software-fallback | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** |

The UI redesign needs one explicit apply model: **live**, **restart capture now**, or **saved for next launch**. The current mix of those semantics is the main reason settings appear to lie.

## 9. FULL MULTI-AUDIO ARCHITECTURE REVIEW

### 9.1 Bottom-line model

**Current multi-audio model: a platform-dependent hybrid of Model C and Model E, resulting in one final track.**

- Windows: the OS output mix is already one source in the engine replay ring; the Python microphone remains separate until post-save FFmpeg `amix`.
- Linux normal mode: one PulseAudio default-source ring is encoded into the engine clip; the Python microphone is mixed after commit.
- Linux multiband mode: categories remain as separate raw PCM rings/temporary WAVs until save, then Python mixes them (and optionally mic) into one AAC track and deletes the source WAVs.
- Windows multiband: the UI/settings flag is passed as a process argument, but the Windows engine does not implement the category capture path. Enabling it changes Python post-route selection without producing category WAVs.

Normal FTHR output is therefore **not a multi-track MP4** and the fixed Windows “game/browser/music/Discord” and Linux “desktop/mic” assumptions in `FTHR_UI/ui/clip_viewer.py` do not match recorder output.

### 9.2 Detailed audio table

| Source | Captured Independently? | Stored Independently? | Mixed When? | Final Track | Volume Applied When? | Can Re-Mix Old Replay? |
|---|---|---|---|---|---|---|
| Windows system/default output | One independent OS loopback endpoint, but applications are already mixed | One raw PCM system ring | Engine AAC encode at save; mic added later if enabled | One AAC stream containing system, then optionally mic | OS/app volumes before capture; no FTHR per-app gain | **No** for app separation |
| Windows microphone | Yes, Python PortAudio mono ring | Yes until save, for about 90 s | Python FFmpeg `amix` after engine commit | Folded into the same final AAC stream | `mic_volume` applied during capture | **Partly**: segment can be selected, but historical gain/source cannot be reconstructed |
| Windows app categories | No | No | Never; category engine does not exist | None as separate sources | Category controls do not affect normal Windows capture | **No** |
| Linux normal Pulse source | One default PulseAudio source | One raw stereo PCM engine ring | AAC-encoded during engine save; mic later | One AAC stream, if source capture succeeds | No source-selection gain in FTHR engine | **No** for sub-sources |
| Linux microphone | Yes, Python PortAudio mono ring | Yes until save | Python FFmpeg `amix` after commit | Folded into final AAC | Gain during capture | **Partly**, same limitation as Windows mic |
| Linux multiband category | Yes after application is successfully moved to its null sink | Separate raw PCM ring and save-time WAV | Python `audio_mixer.py` after engine commit | All categories become one AAC stream | Category gain at save time | **Yes before that save**, then no after WAV deletion |
| Linux multiband microphone | Yes in Python | Separate until save | Added to category FFmpeg mix | Folded into one AAC stream | Gain during capture **and again** in multiband mix | **Partly**, but current double-gain behavior is incorrect |
| Multiple microphones | No | No | N/A | N/A | N/A | No |
| Multiple output devices | No product selection/model | No | N/A | N/A | N/A | No |

### 9.3 Audio sources that actually exist

- Windows system audio: one WASAPI loopback of the default eConsole render endpoint. Although `AudioCaptureConfig.device_id` exists, the UI does not populate or pass a selected output device.
- Linux normal engine audio: one PulseAudio simple-recording stream opened with a null source name, which means **default source**. That is commonly a microphone, not the selected sink monitor/desktop mix.
- Python microphone: one input selected by friendly-name matching or system default.
- Linux-only multiband categories: configurable process-name substring groups routed to one Pulse null sink per category.
- Not implemented: multiple microphones, stable selected output devices, Windows per-process loopback, portable game/chat/music source identities, or independent MP4 tracks.

### 9.4 Device enumeration and identity

**Windows system output.** The capture code can accept a WASAPI device ID, but product startup leaves it empty and opens the default eConsole render device. Device-default changes/recovery reopen the configured ID or default; because no ID is selected, behavior follows the default. There is no user-visible output-device picker or stable persisted identity.

**Microphone.** `FTHR_UI/core/mic_recorder.py` uses `sounddevice`/PortAudio enumeration. Settings persist `mic_device_name`, a friendly string, not a stable endpoint ID. If that name is absent after USB/Bluetooth reconnect or rename, the code falls back to index `None` (system default) rather than presenting a typed “saved device missing” state. Settings changes restart microphone capture, but there is no continuous device-loss recovery loop.

**Linux output/source.** Normal mode does not enumerate or persist a Pulse source/sink identity. Multiband creates FTHR-owned null sinks and moves matching sink inputs by process-binary substring. This is not stable device selection and is sensitive to session-server behavior.

### 9.5 Acquisition-to-file traces

**Windows system audio**

`default eConsole render endpoint -> WASAPI event-driven loopback -> assumed float32 frames -> AudioRingBuffer -> QPC timestamp per packet -> select PCM overlapping video presentation interval -> AAC encode during save -> MP4 audio stream 0`

The code warns when the device is not float but still treats bytes as float samples. The ring is constructed for 48 kHz stereo before the actual endpoint format is known, while capture exposes actual rate/channels. Non-float, 44.1 kHz, or multichannel devices therefore lack trustworthy qualification.

**Python microphone (both platforms)**

`PortAudio input -> 48 kHz mono float32 callback -> gain multiplication -> 90-second MicRecorder ring -> monotonic/frame-count segment selection -> temporary WAV -> FFmpeg amix with committed engine MP4 -> temporary mixed MP4 -> os.replace(final clip)`

**Linux normal engine source**

`PulseAudio default source -> blocking pa_simple_read -> 48 kHz stereo float32 chunks -> raw PCM ring -> CLOCK_MONOTONIC chunk timestamp -> trim around video interval -> AAC encode -> MP4 audio stream 0`

**Linux multiband**

`application sink input -> FTHR null sink selected by substring -> sink monitor -> one 48 kHz stereo raw ring per category -> one float WAV sidecar per category at save -> optional Python mic WAV -> audio_mixer.py volume filters + amix -> one AAC stream -> os.replace(final clip) -> delete WAVs`

### 9.6 Replay representation and memory

The replay audio is raw float32 PCM, not a reusable set of encoded per-source tracks.

| Ring | Format | Nominal bytes/second | 30 s | 60 s | Current retained window |
|---|---|---:|---:|---:|---:|
| Windows system | 48,000 x 2 ch x 4 bytes | 384,000 | 11.52 MB | 23.04 MB | Video buffer duration plus implementation margin |
| Python mic | 48,000 x 1 ch x 4 bytes | 192,000 | 5.76 MB | 11.52 MB | 90 s = 17.28 MB |
| Linux normal source | 48,000 x 2 ch x 4 bytes | 384,000 | 11.52 MB | 23.04 MB | 120 s = 46.08 MB |
| Linux multiband category | Same per category | 384,000 | 11.52 MB each | 23.04 MB each | 120 s = 46.08 MB each |
| Five default multiband categories | Five stereo rings | 1,920,000 | 57.60 MB | 115.20 MB | 120 s = 230.40 MB |

These figures exclude container/allocator overhead and the Python mic ring. Multiband’s memory cost is fixed per configured category even before considering video or camera buffers.

### 9.7 Clock domains and synchronization

- Windows video uses the capture/replay presentation timeline derived from high-resolution monotonic timing. Windows audio packets receive QPC timestamps and are selected against the saved video interval.
- Linux video PTS is derived from `CLOCK_MONOTONIC`. Normal/multiband Pulse chunks are stamped from `CLOCK_MONOTONIC` at chunk boundaries.
- Python mic timing uses `time.monotonic()` plus accumulated frame count, not a PortAudio hardware/device timestamp.
- FFmpeg resamples/encodes as required by the post-process commands, but there is no long-running cross-device drift estimator, adaptive resampling controller, or explicit correction after audio-device restart.
- Dropped video frames do not cause packet-count duration distortion because selection is timestamp-based. They can still create A/V cadence differences; no broad device-drift runtime matrix exists.
- Windows audio recovery tries up to five reopen attempts with increasing waits and then continues video-only. Linux normal `pa_simple_read` has no equivalent recovery loop and may impede bounded stop until the read returns.

### 9.8 Mixing, gain, clipping, and channel behavior

Normal microphone mixing uses FFmpeg `amix=duration=first`; multiband applies one `volume` filter per category followed by `amix`. The output is AAC at 192 kbps, while video is stream-copied for the audio-only post-process.

There is no product-level limiter, loudness normalization, automatic headroom, or documented clipping-prevention policy. Two loud inputs can sum beyond the intended level. Channel conversion/resampling is delegated to FFmpeg/PortAudio defaults rather than governed by a tested product contract.

Gain timing matters:

- Windows/Linux normal mic gain is burned into the raw mic ring at capture callback time. Changing the slider cannot retroactively change already-buffered mic samples.
- Linux multiband category volume is applied at save, so buffered category PCM can be remixed **before its first save** using current category values.
- The multiband mic path applies `mic_volume` in the capture callback and then supplies the same factor again to the save-time mixer. At 50%, for example, the effective gain is approximately 25%. This is a real defect, not a design preference.
- Editor master volume is playback-only. Editor per-source values only become truly per-track when an imported/input clip happens to contain multiple audio streams in the hardcoded assumed order.

### 9.9 Where source separation is lost

- Windows applications lose independence at the WASAPI/Windows system mix **before entering FTHR**.
- Normal Linux sub-sources lose independence at the selected Pulse source **before entering the engine**.
- Python microphone independence is lost at post-save FFmpeg `amix` and `os.replace`.
- Linux multiband categories lose independence at post-save `amix`; their WAVs are then deleted.
- Final output mux code is hardcoded around one selected/mixed audio output. Extending the container mux alone would not restore sources already mixed upstream; replay storage and IPC/config need a portable per-source contract.

### 9.10 Track count evidence

The real NVIDIA qualification report records `audio_streams: 1` for every H.264, HEVC, and AV1 clip, including warm-up, 30-second, 60-second, and rapid saves. Source inspection matches that evidence: the Windows save adds one audio stream, normal Linux save adds at most one, and Python mic/multiband processing replaces it with one mixed AAC stream. Failures can leave a video-only clip with zero audio streams.

The editor merely counts streams with FFmpeg and treats `>=2` as multitrack. It then assumes Windows means exactly game/browser/music/Discord and Linux means desktop/mic, without reading stream titles or provenance. Imported arbitrary multitrack media can therefore be assigned the wrong semantic sliders or reference nonexistent indices.

### 9.11 Save-time audio and AUDIT-042 interval

The engine first commits the base video/audio MP4. Windows extracts raw system PCM overlapping the timestamp-selected video presentation interval and AAC-encodes it. WASAPI silent packets are represented as zero samples, but the save pipeline does not advertise a general cross-platform “pad every missing source to exact video length” contract. Linux extracts raw source/category buffers around the same requested save interval; normal audio enters the MP4 while categories become WAV sidecars. The Python mic recorder extracts its own segment ending at the UI’s recorded monotonic save time and mixes it afterward.

This is not one shared master clock with sample-accurate multi-device drift correction. It is an interval-alignment model across QPC/monotonic/frame-count domains. It is plausible for short clips and has source/unit coverage, but microphone-device drift, restart, Bluetooth, 44.1 kHz, multichannel, and long-session behavior remain unverified.

### 9.12 Multiband routing defects

The Linux category implementation in `FTHRcapture_linux/src/audio_multi_capture.cpp` has product-level hazards:

- Applications moved to FTHR null sinks are not looped back to a physical output sink, so routed users may stop hearing those applications.
- Only NEW/CHANGE sink-input events are subscribed; already-running applications are not comprehensively assigned at startup.
- Matching uses case-sensitive process-binary substrings.
- The first empty-pattern category becomes the fallback. In defaults, `Game` is first and empty while `Sonstige` is also empty, so unmatched applications can land in `Game`, contrary to the apparent “Other” category.
- Category WAVs are temporary/nontransactional sidecars. Their write failure is not integrated into the engine’s transactional completion response.
- The Windows engine ignores the multiband startup value entirely.

The toggle also creates a severe current-session split because `SettingsPage._on_multiband_toggled` only persists state; it does not restart the engine:

- Enabling it while a normal Linux engine is running makes Python select multiband post-processing even though the engine produced no category WAVs; the normal mic route is skipped.
- Enabling it on Windows always creates that mismatch.
- Disabling it while a Linux multiband engine is still running makes the engine produce video plus category sidecars, while Python selects the normal mic route; the base video may have no audio and category sidecars can remain or be lost.

This is classified **BROKEN**, not merely unverified.

### 9.13 Post-processing lifecycle and failure semantics

`CLIP_SAVED` means **the engine’s transactional base MP4 is committed**. It does not mean microphone/category mixing, crop, watermark, camera overlay, upload readiness, thumbnail generation, or final media validation has completed.

After that response, `FTHR_UI/main.py` refreshes the library, sets “SAVED,” and fires the notification. A daemon worker may then run an FFmpeg operation of up to 120 seconds, replace the already-visible file, and proceed through as many as three separate video re-encodes (crop, watermark, camera). Upload waits on a readiness event for at most 60 seconds.

If mic mixing fails, the base clip is retained and later post-processing continues; it may be system-only or video-only. If multiband mixing fails, UI logging says the clip was retained with its default mix, but the engine multiband base can actually be video-only. The category WAVs are deleted regardless of mixer success, removing recovery evidence. The user has already received a success notification in either case.

This is the unresolved broader “fully ready” semantic referenced as AUDIT-027. Transactional engine commit is sound; product completion semantics are only **PARTIAL**.

### 9.14 True multi-track capability assessment

The MP4/container technology and FFmpeg can support multiple audio streams in principle, but current FTHR cannot gain true multitrack merely by changing one mux loop:

- Windows replay has only one already-mixed system source plus a separate out-of-engine mic ring.
- Linux normal replay has one source plus the mic ring.
- Linux categories exist only in a platform-specific mode with routing defects and temporary WAVs.
- Shared config/IPC does not describe portable source IDs, track metadata, per-source health, or track-selection policy.
- The editor assumes order instead of reading semantic metadata.
- Save/recovery/final-ready behavior is designed around one audio output.

A real architecture needs stable source/device identity, explicit platform adapters, one timing model, per-source replay buffers, bounded device recovery, declared channel/rate conversion, save-time track policy, MP4 stream titles/metadata, and UI built from detected capabilities rather than hardcoded platform labels. This need not change the existing UI/engine shared-memory layout immediately; it should be a separately designed audit/feature stage.

### 9.15 Audio rework decision

**CURRENT MULTI-AUDIO MODEL:** One engine system/default-source track plus a separately buffered Python mic mixed after commit; Linux has an additional experimental category mode that also collapses to one track.

**WHAT IT CAN DO:** Produce a useful one-track system-plus-mic clip on the qualified Windows/default-device path; retain Linux category PCM until save in experimental mode; apply save-time category gains; keep video when many audio post-process failures occur.

**WHAT IT CANNOT DO:** Produce reliable cross-platform independent game/mic/chat/music tracks; select multiple outputs/mics; preserve per-app sources on Windows; reliably guarantee Linux desktop audio; recover and re-edit sources after finalization; prevent mix clipping; or promise stable device reconnection/sync.

**WHERE SOURCE SEPARATION IS LOST:** Windows OS mix before capture; Linux default-source mix before capture; Python/multiband `amix` at post-save finalization.

**WHETHER OLD REPLAY CAN BE REMIXED:** **NO for the promised cross-platform source model.** Limited exception: Linux multiband category PCM can be re-gained before its first save; mic PCM can be selected before save, but its historical gain is already baked in.

**NUMBER OF AUDIO TRACKS IN FINAL CLIP:** Normally **one**; zero on some failures. Current FTHR recording does not normally produce two or more independent streams.

**BIGGEST TECHNICAL LIMITATION:** There is no portable per-source replay/timestamp/health/mux contract, and the only per-app capture path is Linux-specific, disruptive, and destroyed at save.

**PRODUCT DECISIONS NEEDED:** Decide whether alpha promises (A) one mixed system+mic track, (B) separate system and mic tracks, or (C) fully configurable app tracks; decide supported output/mic device selection; decide mix loudness policy and completion semantics.

**RECOMMENDATION: FULL AUDIO ARCHITECTURE REWORK** for option B or C as a durable cross-platform product. If alpha explicitly chooses option A, hide multiband/per-source claims and do a smaller stabilization pass rather than beginning that redesign immediately.

## 10. Hotkeys

- Windows uses the `keyboard` library for global hooks. F9/F10/F11 default to normal clip, extended clip, and screenshot. Duplicate bindings are rejected and registration failure is surfaced.
- Hotkeys persist separately in `~/.fthr/hotkeys.json`; the nested `settings.json` `hotkeys` defaults are stale/dead duplication.
- Linux uses a private per-user Unix socket, normally `$XDG_RUNTIME_DIR/fthr/hotkey.sock`, with a private directory and socket mode 0600. Hyprland configuration is generated and reloaded automatically. Other compositors require a user-defined command; direct keyboard-library capture may require privileges not suitable for a normal app.
- The main settings page still prints `/tmp/fthr_hotkey.sock` for KDE, GNOME, and generic configurations. That path was intentionally retired for security, so copying the displayed command fails against the current server. The backend’s resolved-command warning path is newer/correct, but the primary instructions are **MISLEADING**.
- F8/F7 are used internally to confirm/dismiss game-capture prompts but are not exposed in the hotkey editor.
- Duplicate save presses are handled by debounce/single-flight state. A press while a save is active is rejected rather than ambiguously attached to the existing response.
- Shutdown asks the hotkey manager to stop and joins its server thread with a finite two-second wait.

Overall status: Windows clip hotkeys **COMPLETE** on a normal supported desktop; Linux Hyprland **FUNCTIONAL BUT NEEDS PRODUCT REVIEW**; non-Hyprland setup **MISLEADING** until the displayed socket command is corrected and runtime-qualified.

## 11. Screenshots

Screenshot capture is owned by the Python UI, not the capture engine. F11 triggers `MainWindow` logic; Linux first tries `grim` and otherwise uses Qt, while Windows uses Qt screen capture. The result is a PNG named `screenshot_from_<timestamp>.png` under `~/FTHR_Clips/Screenshots`.

The implementation does not resolve the configured `capture_monitor`. Qt fallback captures the primary screen; `grim` is invoked without an explicit selected output. A user can therefore record one monitor but screenshot another. After acquisition, the screenshot editor can keep the full original, save an additional `_cropped.png` while retaining the original, or delete the original on cancel. Notification/sound occurs after the editor returns Accepted. Capture/save failures are surfaced through the UI.

Status: **PARTIAL**. The basic path is real, but monitor semantics and crop-file behavior require product approval and multi-monitor runtime verification.

## 12. Clip library

`FTHR_UI/ui/clip_grid.py` scans the FTHR root and one directory level below it, excluding `Exported` and `Shared`, plus one level in each imported folder. It recognizes MP4/MKV/AVI and PNG/JPG/JPEG, explicitly excludes partial files, sorts by modification time, groups by date, and offers newest/oldest plus all/clips/screenshots/imported filters.

Work is mostly kept off the UI thread: scanning/sorting runs in a worker, a `QFileSystemWatcher` and a 30-second refresh trigger reloads, and at most two thumbnail workers decode frames. Thumbnail cache entries in `~/.fthr/thumbnails` use a hash of path plus modification time; duration has a cache sidecar. Corrupt/unreadable media can remain as a blank thumbnail with zero duration instead of receiving a typed corrupt status.

Playback uses `QMediaPlayer`. The viewer supports trim, crop, quick crop, tags/description, local export/share, upload, reveal, copy path, and delete. There is no reliable game assignment, game search/filter, favorite, rename, or database-backed library model.

Imported files are indexed **in place**. A missing/moved external file disappears on refresh. Crucially, viewer delete calls `os.remove(self.clip_path)` after a generic irreversible confirmation, even for imported external paths. This deletes the user’s original file and is a **BROKEN ownership boundary** for alpha.

Large-library behavior has reasonable throttling/caching but no representative scale measurement. Overall library status: **FUNCTIONAL BUT NEEDS PRODUCT REVIEW**, with imported-file deletion requiring correction or disabling before alpha.

## 13. Metadata

Current clip metadata lives in `~/.fthr/clip_metadata.json`, keyed by the clip’s absolute path. It stores only `tag` and `description` and is written transactionally. It is not embedded into MP4/image files and is not colocated as a per-clip portable sidecar.

| Metadata field | Current source |
|---|---|
| Tags | User JSON metadata |
| Description | User JSON metadata |
| Game | Not metadata; library displays parent folder name |
| Creation time | Filesystem mtime/scanner behavior |
| Duration | Probed/cached thumbnail metadata, not durable clip metadata |
| Favorite | Not implemented |
| Upload state | Separate `~/.fthr/upload_history.json`, also path-keyed |
| Source application | Not implemented |
| Codec/resolution/FPS | Probed/displayed in viewer where available, not stored in metadata system |

Renaming or moving a file breaks the absolute-path association; copying it to another PC does not bring tags/description or upload history; orphaned entries are not systematically pruned. Status: **PARTIAL**. The UI developer must not design around durable game/favorite/portable metadata without an engine/data-model stage.

## 14. Game detection

**Current implementation:** a polling window heuristic, not a game database.

- Windows enumerates visible titled windows and treats borderless popups or windows exactly matching the primary-screen rectangle as game candidates.
- Linux uses fullscreen Hyprland clients where available or `xdotool`/`xprop` fullscreen information as a fallback.
- A background detector polls around every three seconds. A newly detected candidate produces a roughly 15-second prompt; F8 confirms and F7 dismisses.
- Confirmation stores a transient window handle/title, restarts capture in window mode, and creates a game-named output folder. When the game closes, capture returns to desktop and restarts.
- There is no Steam/launcher integration, executable database, log parser, durable per-game rule, reliable local game identity, or persistent manual reassignment.

The separate anti-cheat capture fallback in the Windows engine is not proof that the user-facing “pause when unfocused” control works. `FocusMonitor` implements Hyprland/xdotool discovery but no real Windows branch; its Windows START/STOP commands operate a legacy continuous-recording path rather than replay pause.

Status: game detection **EXPERIMENTAL**; the cross-platform focus/anti-cheat setting is **BROKEN** because its advertised Windows replay-pause behavior does not occur. Product must decide whether alpha auto-switches capture, merely suggests a game, or omits game detection.

## 15. Import

The user can add directories containing arbitrary supported media extensions. FTHR then stores the directory path in `imported_clip_folders`, scans media **in its original location**, and generates local thumbnail/duration cache entries.

It does not copy or move files, ingest them into FTHR ownership, validate full codec/container decodability, detect content duplicates, preserve/import rich source metadata, or create a durable source record. Original timestamps remain whatever the external filesystem exposes. If the directory/file disappears, the item disappears. If the user deletes through FTHR, the original external file is deleted.

Status: **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** for in-place indexing; deletion semantics **BROKEN**. The owner must choose link/index, copy, move, or an explicit per-import choice before the UI redesign presents “Import” as a simple ownership transfer.

## 16. Export/migration

The clip editor can export a trimmed/cropped result to `~/FTHR_Clips/Exported`, and the share workflow writes to `~/FTHR_Clips/Shared`. When no crop or multitrack filter is requested, FFmpeg stream-copy is used; crop or the editor’s detected multitrack mix re-encodes as needed. “Discord <=10 MB” estimates a target bitrate but does not perform a final file-size acceptance check. “Share” is a local derived-file/drag workflow, not a hosted provider.

Generic editor export writes directly to its final-looking `.mp4`; unlike engine clip save, it does not consistently use the `.partial` transactional contract. Failure/cancellation can therefore have weaker cleanup semantics. The dedicated share window tracks its pending output more carefully.

There is no library export, ZIP/7z/uncompressed archive, metadata bundle, path remapping, Windows-to-Windows migration, or Windows-to-Linux migration. Those are **NOT IMPLEMENTED**, not incomplete menu items. Local clip export is **FUNCTIONAL BUT NEEDS PRODUCT REVIEW**.

## 17. Upload

Uploads are a generic user-configured multipart HTTP client, not an integrated FTHR cloud or named provider. Settings include enabled state, arbitrary URL, authentication header, manual/immediate/interval mode, interval value/unit, and optional local deletion on success.

`FTHR_UI/core/upload_manager.py` uses a sequential daemon worker. It waits for clip readiness (maximum 60 seconds), rejects partial/non-final paths, and performs up to four attempts with approximately 5/15/45-second delays. Interval mode recursively scans `~/FTHR_Clips`, avoids very fresh files, and uses `~/.fthr/upload_history.json` to avoid repeating recent successes. There is no persistent failed-job queue, upload progress, cancellation, resumable transfer, provider contract, or clip-metadata upload.

Important behavior:

- URL and auth header are plaintext in `~/.fthr/settings.json`; the auth field is a normal visible line edit.
- Plain HTTP is allowed, so clips and credentials can be sent without transport encryption.
- Manual upload of a library-visible clip can race post-processing because the library is refreshed before final readiness. Automatic readiness waits only 60 seconds, while FFmpeg post-processing can run 120 seconds or more in sequence.
- Retry sleep is not shutdown-aware. “Queued for next scan” wording is only fully true for interval mode; immediate/manual failures are not durable queue entries.
- Auto-delete removes the local clip after a successful response.

Status: **FUNCTIONAL BUT NEEDS PRODUCT REVIEW**, with AUDIT-012 privacy/credential concerns still open. A public alpha should either disable upload or define a supported HTTPS-only provider/auth model and completion contract.

## 18. Privacy/network behavior

The runtime source audit found one product feature that intentionally creates outbound traffic: generic clip upload, including its test/HEAD request behavior. No source path was found for telemetry, analytics, crash reporting, remote game metadata, authentication service, remote configuration, or update checking.

| Network-capable feature | Default | Destination/data | User control | Reality |
|---|---:|---|---|---|
| Clip upload | Off | User-supplied HTTP(S) endpoint; media plus configured auth header | Settings/manual action | Real |
| Upload connection test | User action | Configured endpoint, headers | Settings action | Real |
| Update check/download | N/A | None | Static UI only | Not implemented |
| Telemetry/analytics | N/A | None found | N/A | Not implemented |
| Crash reporting | N/A | None found | N/A | Not implemented |
| Game metadata lookup | N/A | None found | N/A | Not implemented |
| Hotkey IPC | Local only | Private Unix socket on Linux | Internal | Not network egress |

“Local-first” is accurate for default operation, but not a guarantee once upload is enabled. Plaintext credential storage, visible auth text, arbitrary HTTP, and optional auto-delete need explicit product/security treatment. Logging helpers redact structured secrets, but the legacy print/tee path is not a universal redaction boundary.

## 19. Update system

There is no update system. The Versions page displays “You are on the latest version,” but no code checks a release source, compares versions, downloads a package, verifies a signature/checksum, launches an installer, handles AppImage replacement, or reports update failure.

The central version declaration in `FTHR_UI/version.py` and the build-time version-consistency gate are real. They support packaging but do not turn the static page into an updater.

Status: **PLACEHOLDER** and currently **MISLEADING**. Hide the page/claim for alpha or replace it with truthful static “Version 1.0.0-alpha; updates are manual” text. Implementing an updater is a separate post-alpha project unless distribution strategy makes it mandatory.

## 20. Notifications

The capture card is a separate lightweight Qt helper process controlled by stdin. The client can relaunch it if it exits. It supports clip, screenshot, startup, upload, and error visual states; display selection is `auto` (highest refresh) or a named screen. Four generated or themed WAV events have independent 0–100 volume controls.

The most important timing issue is that clip notification fires when the engine publishes `CLIP_SAVED`, before Python mic/multiband/crop/watermark/camera completion. A clip can therefore be visible and announced while its bytes are still going to be replaced. Some failures also appear both in the capture card and the main error bar, so duplicate error feedback is possible.

Startup sound/card runs once after engine connection. Screenshot notification occurs after the editor accepts the image. Upload success/error notification reflects HTTP handling, not a persistent queue state.

Status: **FUNCTIONAL BUT NEEDS PRODUCT REVIEW**. The UI rework needs distinct states such as “base clip saved,” “finalizing,” “ready,” and “saved without optional audio/effects,” or it must redefine the pipeline so one success boundary is authoritative.

## 21. Settings inventory

`SettingsManager` stores almost all preferences as plaintext JSON at `~/.fthr/settings.json`, writes through `.json.tmp` and `os.replace`, and renames corrupt input to `.json.corrupt`. It merges new top-level defaults and one nested dictionary level into old files. Presets live separately in `~/.fthr/presets.json`; hotkeys and themes have their own stores. There is no schema version or centralized validation layer—validation is distributed across widgets and startup code.

### Mandatory settings table

| Setting | UI | Stored | Engine Reads It | Applied When | Platform | Unsupported Behavior |
|---|---|---|---|---|---|---|
| `clip_length` (30 s) | Yes, 5–900 | `settings.json` | UI sends duration; buffer size set at engine start | Save immediately, capacity only after restart | Both | Effective startup clamp 5–300; request may exceed current ring |
| `extended_clip_length` (60 s) | Yes, 30–900 | Same | Same | Same | Both | Effective startup clamp 5–600 |
| `framerate` (60) | Yes, 30–360 | Same | Process arg | Engine restart | Both | Effective startup clamp 15–240; 360 shown but not used |
| `resolution` (`source`) | Yes | Same | Process dimensions | Engine restart | Both | AMF rejects dimension mismatch; other scaling not fully qualified |
| `bitrate_level` (`medium`) | Yes | Same | UI maps to kbps process arg | Next engine restart | Both | Changing quality does not restart/reconfigure current engine |
| `scaling_mode` (`stretch`) | No normal control | Same | Process arg | Engine restart | Both | Dead-to-user `fit` capability; no normal discoverability |
| `capture_monitor` (empty/default) | Yes | Same | Process arg/resolver | Engine restart/new generation | Both | Linux output behavior unverified; missing monitor startup/recovery UX limited |
| `codec_pref` (`auto`) | Yes | Same | Startup and reconfigure command | Linux reconfigure; Windows only next full restart | Both | Auto differs by platform; Windows Apply is a stub |
| `encoder_preset` (4) | Yes | Same | Startup/reconfigure | Same as codec | Both | Windows Apply does not reach running encoder |
| `hotkeys` F9/F10/F11 | Superseded UI exists | Stale nested `settings.json` defaults | No; HotkeyManager uses separate file | Never from this copy | Both | Duplicated/dead setting |
| Actual hotkey bindings | Yes | `~/.fthr/hotkeys.json` | Hotkey manager | Re-register on change | Both | Linux non-Hyprland instructions use wrong socket path |
| `quick_crop` | Yes/editor | `settings.json` | Python editor/export | Next quick-crop export | Both | Source-dimension mismatch requires fallback/review |
| `mic_device_name` | Yes | Friendly name in settings | Python MicRecorder | Recorder restart/change | Both | No stable ID; missing device silently falls to default |
| `mic_volume` (100, 0–200) | Yes | Same | Python callback and multiband mixer | New mic samples; multiband again at save | Both | Historical samples keep old gain; multiband double-applies gain |
| `mic_loopback` (false) | Yes | Same | Settings-page PortAudio monitor | While settings control is active | Both | Separate 44.1 kHz path; lifecycle/persistence semantics awkward |
| `audio_capture_enabled` (true) | Yes | Same | Engine process arg and Python post-route | Engine restart for system path; post-route reads live setting | Both | Mic recorder still runs when disabled; live toggle can split engine/UI state |
| `master_volume` (80) | Yes, editor | Same | QAudioOutput only | Live playback | Both | Does not alter saved clip |
| `source_volumes` | Yes, editor | Same | Editor export only | Export-time for actual multitrack input | Both | Current recorder produces one track; hardcoded semantics misleading |
| Four sound volumes (100) | Yes | Same | Notification sound player | Next event | Both | No single global mute; otherwise coherent |
| `notification_monitor` (`auto`) | Yes | Same | Capture-card process | Next card/restart path | Both | Runtime monitor changes/topology not fully qualified |
| `imported_clip_folders` | Yes | Same | Library scanner | Refresh | Both | Index-in-place ownership; delete removes external original |
| `upload_enabled` (false) | Yes | Same | Upload manager | Runtime/settings initialization | Both | Can be enabled with incomplete/unsafe HTTP config |
| Upload URL/auth | Yes | Plaintext same file | HTTP client | Test/upload | Both | HTTP allowed; auth visible/plaintext |
| Upload mode/interval | Yes | Same | Upload scheduler | Worker scheduling | Both | Only interval behaves like a recurring durable scan; no persistent job queue |
| Upload auto-delete | Yes | Same | Upload worker | After successful HTTP response | Both | Irreversible local deletion; requires explicit warning |
| `multiband_audio_enabled` (false) | Yes | Same | Linux engine at startup; Python route live; Windows engine ignores | Requires restart in reality, UI does not restart | Both UI/Linux engine | Severe current-session split; broken on Windows |
| `audio_categories` | Yes | Same | Linux engine JSON + Python mixer | Engine startup/routing; gain at save | Linux only in engine | Empty fallback ordering, no physical loopback, no Windows implementation |
| `game_detection_enabled` (false) | Yes | Same | Python detector | Live start/stop | Both | Heuristic only; no durable game DB/rules |
| `anticheat_detection_enabled` (false) | Yes | Same | Python focus logic | Live polling/commands | Both | Windows implementation does not perform advertised replay pause |
| `watermark_enabled/text` | Yes | Same | Python post-process | After engine commit | Both | Adds full video re-encode; text and font behavior require FFmpeg runtime |
| `auto_crop_enabled` | Yes | Same | Python post-process | After audio/watermark ordering stage | Both | An additional full software re-encode; detection result unverified broadly |
| `camera_enabled` | Yes | Same | Python camera recorder/post-process | Recorder startup + save | Both | High JPEG-ring/CPU/storage pressure; no performance qualification |
| Camera device/position/size | Yes | Index/string | Python camera path | Recorder restart/save overlay | Both | UI lists Camera 0–3 rather than real device identities |
| Startup splash checkbox | Yes | Not stored | No | Never | Both | **PLACEHOLDER** always checked |
| Autostart | Yes | Windows HKCU Run registry | OS shell | Next login | Windows | Separate from settings manager; installer lifecycle unverified |
| Theme/custom assets | Yes | `~/.fthr/theme` | Theme/UI/notification loaders | Import/apply/restart depending asset | Both | ZIP size/decompression/semantic asset bounds need product/security review |
| Clip directory | No | Fixed path | UI and engines use `~/FTHR_Clips` | Always | Both | Custom clip path is not implemented |

Dead/duplicated settings that must not drive a new UI: nested `settings.json` hotkeys, startup splash, hidden scaling mode without an apply surface, and Windows multiband/category controls. Settings that require explicit restart labeling: FPS, resolution, monitor, system-audio enable, scaling, codec/preset on Windows, and any multiband mode transition.

## 22. Startup

The startup sequence is:

`Python entry -> single-instance guard -> settings/version/theme/font/media setup -> create data folders -> conservative stale-partial cleanup -> UI/services -> engine process -> background shared-memory connection -> capture status`

Concrete behavior:

- The single-instance guard is acquired before competing UIs can share the one command/response slot.
- `~/FTHR_Clips/Desktop`, `Exported`, `Shared`, and `Screenshots` are created.
- Only FTHR-named `*_clip_from_*.mp4.partial` files older than 24 hours are removed. Fresh, symlinked, or unrelated partials are preserved.
- OpenCV/FFmpeg/Qt multimedia support is prepared; game detector/focus monitor/camera start when enabled.
- `MicRecorder` starts whenever available, even when `audio_capture_enabled` is false.
- The engine path comes from the source/build tree or PyInstaller `_MEIPASS`. Startup arguments carry current capture configuration.
- Connection polling runs in a background thread for roughly 20 x 150 ms with a generation guard, avoiding the older blocking `connect_to_engine` path in normal startup.
- Windows captures engine startup stdout/stderr in a temporary file and converts known failures to structured messages.
- Capture starts automatically. `is_capturing` in the UI is effectively treated as true rather than being a reliable state machine exposed to the user.

Missing engine/dependency, shared-memory connection failure, or typed encoder/backend startup failure ends the attempted engine and surfaces an error. Stale unrelated processes are not broadly killed; ownership is process-handle/single-instance based. Startup is **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** because “always capture” and microphone behavior need explicit consent/UX, and full packaged lifecycle is unverified.

## 23. Shutdown

On close, the UI marks teardown, stops timers/services, cancels active save-state ownership, waits for tracked post-processing threads, stops audio/camera/hotkeys/notifications, sends engine shutdown, detaches IPC, and terminates/kills the engine if it does not exit.

Important bounds and gaps:

- Each tracked mux/finalize thread is joined for up to about 15 seconds. Individual FFmpeg operations can have 120-second timeouts and multiple sequential stages. Threads are daemon threads, so close can continue while a worker is still replacing or writing a clip.
- The engine is given a finite graceful window (about five seconds) and then a kill window. That contains most engine failures from the Python parent’s perspective.
- Linux X11 can keep its capture thread blocked inside FFmpeg/XCB, so in-process engine shutdown is not proven bounded. Parent kill is the eventual external containment, not clean backend cancellation.
- Linux Pulse `pa_simple_read` is blocking and has no explicit read cancellation contract.
- Upload retry sleeps are not shutdown-aware and the daemon queue is not persisted.
- Hotkey and capture-card subprocess cleanup has finite joins/termination, but not a complete packaged crash matrix.

Status: **PARTIAL**. Normal shutdown paths are structured, but post-processing durability and Linux blocking calls remain release risks.

## 24. Recovery/failure behavior

| Failure | Current behavior | User outcome | Recovery status |
|---|---|---|---|
| Capture backend stops/progress stalls | Health/recovery policy creates a new backend generation, clears ring, retries with backoff | Save may reject/shorten; status/error eventually shown | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW**; runtime matrix incomplete |
| WGC failure | Falls toward WGC monitor/DXGI depending capture mode | Capture may resume with new generation | **EXPERIMENTAL** beyond qualified desktop path |
| Display disconnect/topology change | Monitor resolver/backend recovery attempts new topology | Old replay is lost; selected path may no longer resolve | Real multi-monitor selection verified, live disconnect not fully verified |
| Unsupported Windows encoder | Typed startup failure; no hidden fallback | No replay capture | Coherent fail-closed policy; UX decision required |
| Encoder fails after startup | Engine health/error and recovery/termination path; ring generation reset where supported | Save unavailable; error state | Automated/limited runtime |
| Windows audio device loss | Up to five reopen attempts with increasing delays, then video-only | Clip can have no audio; no dedicated strong UI state | **PARTIAL** |
| Linux Pulse source loss | No robust reopen loop around blocking simple API | Audio may stop/fail; shutdown can wait on read | **BROKEN** as a resilient audio path |
| Mic device loss | PortAudio callback/stream stops; no autonomous stable-ID recovery | Later clip may contain system only | **PARTIAL** |
| FFmpeg mic/multiband failure | Keep base clip; continue optional video effects; readiness eventually set | User was already told saved; audio may be missing | **MISLEADING** completion feedback |
| Crop/watermark/camera FFmpeg failure | Keep whatever prior version exists and log/notify according to stage | Feature may be absent; clip usually remains | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** |
| Disk full/write/trailer/rename failure | Transactional save returns error and removes/retains only controlled partial behavior | No false final MP4 from engine save | **COMPLETE** at engine transaction boundary |
| Python UI crash | Engine is a child but no independent durable supervisor contract; stale partial cleanup next launch | Capture/session ends; committed clips remain | **PARTIAL** |
| Engine crash | UI poll/startup diagnostics detects loss; process can be restarted by recovery/start flow where implemented | Replay history lost | **PARTIAL** |
| Corrupt settings | Original renamed `.json.corrupt`, defaults loaded | App can start with defaults | **COMPLETE** for basic recovery |
| Corrupt media in library | Often blank thumbnail/duration zero; item may remain | Ambiguous unusable card | **PARTIAL** |
| Upload/network failure | In-memory retry then error; interval may rescan later | No persistent queue/progress | **PARTIAL** |

The most important semantic distinction is “base media committed” versus “final product ready.” AUDIT-028 correctly protects the first; the UI/product still lacks a trustworthy state for the second.

## 25. Storage/file layout

| Data | Path/layout | Notes |
|---|---|---|
| Desktop clips | `~/FTHR_Clips/Desktop/desktop_clip_from_<timestamp>.mp4` | Window/game capture uses a sanitized game folder/name; Windows checks path length around 255 characters |
| Game clips | `~/FTHR_Clips/<sanitized game>/...mp4` | Folder label is later treated as game identity |
| Screenshots | `~/FTHR_Clips/Screenshots/screenshot_from_<timestamp>.png` | Cropped save may add `_cropped.png` and keep original |
| Exports | `~/FTHR_Clips/Exported` | Excluded from normal grid scan |
| Shares | `~/FTHR_Clips/Shared` | Excluded from normal grid scan |
| Engine transaction | Same-directory `*.mp4.partial` | Atomic final rename; old owned partials cleaned after 24 h |
| Multiband WAV sidecars | Beside clip using category-derived suffixes | Temporary, nontransactional, deleted after mix |
| Settings | `~/.fthr/settings.json` | Plaintext, atomic replace |
| Presets | `~/.fthr/presets.json` | Plaintext, atomic replace |
| Hotkeys | `~/.fthr/hotkeys.json` | Separate from stale settings copy |
| Metadata | `~/.fthr/clip_metadata.json` | Absolute-path keyed |
| Upload history | `~/.fthr/upload_history.json` | Absolute-path keyed, about 90-day policy |
| Themes | `~/.fthr/theme` | Imported/generated local assets/config |
| Thumbnail cache | `~/.fthr/thumbnails` | Path+mtime hashed image/duration entries |
| Logs | `~/.fthr/logs/fthr.log` plus rotations | UI-oriented; engine visibility differs by platform/startup |
| Linux runtime IPC | `$XDG_RUNTIME_DIR/fthr/hotkey.sock`, fallback private `~/.fthr/run` | Directory 0700/socket 0600 |
| Shared memory | Platform-specific versioned `FTHR_SharedMemory_v4` | Layout intentionally unchanged by this audit |
| Installer/update cache | No product update cache | Updater not implemented |

Paths use `Path.home()` consistently for user data, but metadata portability and installer cleanup are inconsistent. The Windows uninstaller removes `{userappdata}\fthr`, while actual configuration is normally `C:\Users\<user>\.fthr`; uninstall therefore does not clear the real settings directory. User clips are intentionally preserved.

## 26. Logging

The Python application writes `~/.fthr/logs/fthr.log`. A stdout/stderr tee rotates at startup when the log exceeds roughly 2 MB, while a `RotatingFileHandler` also targets the same file with a 2 MB/2-backup policy. Two rotation mechanisms writing the same target can make history/correlation less predictable.

Structured entries include timestamps, thread/logger context, and a secret-redaction filter. Legacy `print` output passes through the tee but not necessarily every structured sanitizer. Current upload paths avoid deliberately printing the auth header, but this is not a formal no-secret logging boundary.

Windows engine startup output is captured temporarily so startup failures can be parsed/sanitized. It is not exposed as a durable, user-exportable engine session log after startup. Linux engine-output persistence is likewise not presented as a coherent support artifact. The repository has diagnostic/report scripts, but the application has no “Export diagnostics” workflow, privacy preview, or correlation ID spanning UI, engine, save, and upload.

Status: **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** for developer use; **PARTIAL** for alpha supportability.

## 27. Plugins

No FTHR product plugin architecture exists. There is no plugin discovery directory, API/ABI, manifest, capability/permission model, isolation boundary, signing model, update/version negotiation, or community-loader UI. Qt platform plugins and the PulseAudio null-sink module are runtime dependencies, not FTHR plugins.

Status: **NOT IMPLEMENTED**. Mark it planned/post-alpha if it appears in product discussions; do not reserve UI surface for a capability that has no contract yet.

## 28. Packaging/release

### Windows

- `FTHR.spec` builds a PyInstaller onedir bundle in `dist/FTHRClips`, including the PySide6 UI, native engine, pinned FFmpeg runtime, assets, and licences.
- `installer_windows.iss` builds an x64 Inno Setup installer, installs the VC++ redistributable, creates Start Menu integration, and offers an optional desktop shortcut.
- The onedir folder can be launched without a single-file bootstrapper, but the supported release flow is an installed application; no explicit portable-mode contract keeps all state beside the executable.
- Release provenance gates approve 25 shipped assets and the pinned LGPL-compatible FFmpeg/Qt paperwork at this checkout.
- The installer requests administrative installation. No code-signing certificate/signing stage is configured, so Windows reputation/warning behavior is a release concern.
- The current bundle exists, but a fresh installer install/launch/capture/save/update/uninstall lifecycle was not run during this audit. Uninstall config cleanup points to the wrong settings location as described above.

### Linux

- `FTHR_linux.spec` builds a PyInstaller onedir application, then `build_linux.sh` assembles AppDir/AppImage output with desktop assets and libraries.
- The pinned Linux FFmpeg provenance is documented, but the actual vendored Linux FFmpeg payload is absent in this Windows checkout; the licence gate reports that as its one warning.
- Prior audit evidence records an AppImage built against glibc >=2.39 and only offscreen startup validation. Compatibility with older target distributions, desktop integration, permissions/portals, hotkeys, audio, and real capture is not established.
- `build_linux.sh` downloads a continuous `appimagetool` artifact without a pinned checksum, reducing build reproducibility. Windows prerequisites similarly rely on external downloads in parts of the build flow.

### Release reality

| Artifact | Built/defined | Automated gates | Installer lifecycle verified | Status |
|---|---:|---:|---:|---|
| Windows onedir bundle | Yes | Yes | No complete current run | **EXPERIMENTAL** as a release artifact |
| Windows Inno installer | Defined/previously buildable | Yes | No | **EXPERIMENTAL** |
| Linux AppImage | Defined/previous evidence | Partial | Offscreen only | **EXPERIMENTAL** |

`docs/RELEASE_CHECKLIST.md` still says not to tag/release and contains some older counts/status prose. The newer HEAD source and NVIDIA qualification report supersede those details, but the unresolved runtime/install gates remain real.

## 29. Feature dependency graph

### Performance-related behavior (not a Medal benchmark)

- Windows retains encoded GPU-produced packets and deliberately avoids a full-resolution raw replay fallback. This is the correct shape for low replay-memory bandwidth, but only the NVIDIA qualification path has physical evidence.
- Linux capture receives/copies CPU BGRA frames and converts them through `sws_scale` before encoding. Even a named hardware encoder is currently fed through this CPU-side frame path; vendor/device and gaming-impact behavior are unverified.
- Video capture is timestamp-throttled rather than busy-producing unlimited frames. Capture status polls at roughly 500 ms, game discovery around 3 s, focus around 2 s, multiband mapping around 2 s, and the library has a 30-second safety refresh. The larger scans run in workers rather than directly on the UI thread.
- Thumbnail generation is capped at two workers and cached by path/mtime. There is no representative 10k-item library measurement, so scale remains **UNVERIFIED**.
- The default five Linux multiband category rings reserve about 230.4 MB of raw audio for a 120-second window, plus microphone/overhead.
- Camera capture keeps about 90 seconds of JPEG frames at a nominal 30 FPS. At an illustrative 150–300 KB per JPEG, that is roughly 405–810 MB, plus continuous JPEG CPU work. This is an estimate from the storage model, not a measured runtime result.
- Mic/multiband audio processing can rewrite the container, and crop, watermark, and camera can then cause up to three separate full software video re-encodes. Completion latency and generational quality loss grow when features are combined.
- No idle/gaming comparison against baseline or Medal was run here. The current report must not be used to claim lower performance impact than Medal.

### Dependency graph

```text
APPLICATION START
  -> settings + single-instance ownership + paths/licences
  -> engine discovery + shared-memory compatibility
  -> platform capture backend
       -> monitor/output resolver
       -> capture health + recovery generation
  -> adapter/codec policy -> encoder -> encoded replay ring
  -> system audio ring

SAVE CLIP
  -> hotkey/UI admission
  -> capture-health verdict + available history
  -> shared-memory command/response single slot
  -> timestamp-selected video snapshot
  -> timestamp-aligned system audio snapshot
  -> transactional MP4 commit
  -> Python finalization route
       -> mic OR Linux category mix
       -> auto-crop
       -> watermark
       -> camera overlay
  -> final-ready event
       -> upload
  -> library scan -> thumbnail -> playback/editor
  -> notification (currently fires before Python finalization)

LIBRARY ITEM
  -> filesystem ownership/path
  -> thumbnail/duration cache
  -> absolute-path metadata + upload history
  -> editor/export/share/upload/delete

GAME CAPTURE
  -> window heuristic + prompt
  -> capture-source restart -> new replay generation
  -> output-folder name (currently also presented as game metadata)
```

A simple “Save Clip” button therefore depends on capture freshness, monitor topology, encoder availability, timestamp selection, audio clocks, disk transaction, post-processing, scanner timing, and notification semantics. The UI must model these as asynchronous states instead of treating a button click as one synchronous file write.

## 30. UI developer handoff

### What the UI developer must know

| UI Control / Action | Real Backend Function | Valid Values | Restart Required? | Error State | Async / Loading State | Success State |
|---|---|---|---:|---|---|---|
| App launch/capture | `MainWindow.start_engine` -> engine process/shared memory | Current persisted config | Automatic process start | Missing engine/backend/encoder/IPC | CONNECTING / INITIALIZING / WARMING / RECOVERING | HEALTHY with backend/codec/monitor |
| Start/stop capture | No coherent normal replay session API today | N/A | N/A | Do not expose legacy recording commands | N/A | Keep capture automatic or design engine API first |
| Save clip | save state -> capture health -> `SAVE_CLIP` | 5–300 effective normal at startup today | No for request; ring capacity depends on earlier restart | Busy, unhealthy, engine error, timeout, disk error | REQUESTED -> ACCEPTED -> ENGINE COMMITTED -> FINALIZING | FINAL READY, possibly “shortened during warm-up” |
| Extended save | Same with extended duration | 5–600 effective today | Same | Same | Same | Same |
| Select monitor | persist stable path/name -> restart engine | Enumerated outputs only | **Yes** | Output disappeared/unresolvable/backend failure | RESTARTING; replay history cleared | New monitor + active adapter/backend confirmed |
| FPS | process argument | Product should expose <=240 unless engine cap changes | **Yes** | Encoder/backend cannot sustain/start | RESTARTING; ring cleared | Active FPS returned/status |
| Resolution | process dimensions/scaling | source, 480/720/1080/1440 | **Yes** | AMF mismatch or encoder failure | RESTARTING | Active capture/encode dimensions, not just requested label |
| Quality/bitrate | startup bitrate mapping | low/medium/high | **Yes today** | Startup encoder failure | SAVED FOR RESTART or restart now | Show active bitrate after restart |
| Codec | startup policy / reconfigure | auto, H.264, HEVC, AV1 subject to capabilities | Windows: **yes**; Linux reconfigure restarts internally | Typed unsupported vendor/codec | CHECKING/RESTARTING; ring cleared | Show **requested and active** codec/backend |
| Encoder preset | same as codec | 1–7 | Same | Typed init failure | Same | Active preset/backend confirmation |
| Replay duration | settings + engine buffer capacity | Product cap must be chosen | Engine restart to guarantee capacity | Insufficient warm-up/current capacity | “Applies after capture restart” if changed | Show requested vs available history |
| System audio | engine audio start arg | on/off; no real output picker | **Yes** for coherent state | Device unsupported/lost -> video-only | AUDIO INITIALIZING/RECOVERING | Active device identity and channels/rate if supported |
| Microphone | Python MicRecorder | Enumerated input or default | Recorder restart, not engine | Saved name missing/device lost | STARTING/UNAVAILABLE | Active actual device; do not silently relabel fallback as saved device |
| Mic volume | Python callback gain | 0–200 currently | No | None explicit | Affects new samples only | Label “capture gain”; historical replay unchanged |
| Multiband | Linux experimental mode | Do not expose in alpha as-is | **Yes** in reality | Routing/muting/no WAV/mix failure | Engine restart + route verification | No trustworthy cross-platform success state exists today |
| Per-source editor volume | QAudioOutput/FFmpeg export | Only real when stream metadata identifies tracks | No | Missing/wrong stream index | Probe streams asynchronously | Playback master; per-track export only for verified tracks |
| Screenshot | UI Qt/grim/editor | selected output contract not implemented | No | Screen/capture/save failure | Crop dialog | PNG path after user accepts |
| Game detection | `GameDetector` + capture restart | enabled/disabled | Capture restart on selected game | False candidate/window closes | Prompt countdown/restarting | Active source label, not durable metadata |
| Pause when unfocused | No correct Windows replay implementation | Do not expose cross-platform | N/A | N/A | N/A | Engine work required first |
| Clip directory | Fixed `~/FTHR_Clips` | Not configurable | N/A | N/A | N/A | Do not show a fake picker |
| Import | persist external directory -> index in place | Existing directories/media extensions | No | Missing/unreadable folder/media | SCANNING | Clearly label “linked external folder” |
| Delete | `os.remove` actual item | Owned file only until policy is redesigned | No | Locked/I/O | Short operation | Must distinguish FTHR-owned vs external original |
| Export/share | FFmpeg worker | time range/crop/quality mode | No | FFmpeg/cancel/disk | EXPORTING with cancel | Completed derived path and validated size where promised |
| Upload | UploadManager multipart | Valid HTTPS endpoint/auth/provider policy | No | Config/network/HTTP/readiness | QUEUED/WAITING/UPLOADING/RETRYING | Uploaded; separate auto-delete confirmation |
| Update | None | None | N/A | N/A | N/A | Hide or show version/manual updates only |
| Camera/watermark/crop | Python post-process | Current device/text/position options | Camera recorder restart; save-time effects | FFmpeg/device failure | FINALIZING, possibly several passes | Only report clip ready after final replacement |

The redesigned UI should obtain capability/status from backend truth wherever possible. Persisted requested values are not proof of active values.

## 31. UI truth/mismatch findings

| UI claim/state | Source reality | Classification | Required UI action before redesign handoff |
|---|---|---|---|
| Codec/preset “Apply” changes running encoder | Windows IPC handler is a restart-required stub | **MISLEADING** | Restart engine or show “applies after restart”; display active codec |
| 900-second normal replay is selectable | Startup caps normal to 300 s; current ring does not resize live | **MISLEADING** | Expose supported cap and restart/capacity state |
| 900-second extended replay is selectable | Startup caps extended to 600 s | **MISLEADING** | Same |
| 360 FPS is selectable | Startup caps effective FPS to 240 | **MISLEADING** | Limit control or expand/test engine intentionally |
| Quality changed successfully | Only stored; running bitrate unchanged until restart | **MISLEADING** | Make restart explicit |
| Multiband records each app separately | Only Linux attempts categories; it mixes to one track and may mute routing; Windows ignores it | **BROKEN** | Hide for alpha; architecture decision first |
| Windows volume popup represents game/browser/music/Discord tracks | Recorder produces one mixed system stream | **MISLEADING** | Show master only unless stream metadata proves tracks |
| Linux volume popup represents desktop/mic tracks | Normal output is one mixed stream; default Pulse source may be mic | **MISLEADING** | Same; fix source model |
| Per-source slider affects a single-track clip meaningfully per source | No source distinction exists | **MISLEADING** | Remove/disable with truthful explanation |
| KDE/GNOME hotkey command uses `/tmp/fthr_hotkey.sock` | Server uses a private runtime path | **MISLEADING** | Generate the real resolved command |
| “You are on the latest version” | No update check exists | **PLACEHOLDER** | Remove claim |
| Startup splash checkbox | No storage or backend signal | **PLACEHOLDER** | Remove or implement later |
| Pause replay when game is unfocused | Windows focus/backend path does not pause replay | **BROKEN** | Hide on Windows; verify Linux semantics separately |
| Screenshot follows selected capture monitor | Screenshot uses primary/default screen behavior | **MISLEADING** if implied | Add explicit screenshot target or label primary screen |
| Game label is known game metadata | It is normally the parent folder name/window heuristic | **MISLEADING** | Call it folder/source label until metadata exists |
| Clip is “saved/ready” at notification | Optional audio/effects may still replace it | **MISLEADING** | Add finalizing/ready boundary |
| Failed multiband mix leaves “default audio mix” | Engine multiband base may be video-only | **MISLEADING** | Preserve sidecars/report exact audio result |
| Import implies media ownership | It indexes external originals in place | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Label linked folder and protect delete |
| Auto codec means same policy everywhere | Windows Auto = H.264; Linux prefers different candidate order/fallback | **FUNCTIONAL BUT NEEDS PRODUCT REVIEW** | Define platform-neutral promise or label behavior |
| Audio off means microphone capture off | MicRecorder still runs; only mux route/engine audio changes | **MISLEADING** | Tie consent/state to actual capture |

These are not cosmetic copy issues alone. Several controls need a backend apply/capability contract before another developer freezes their UI behavior.

## 32. Alpha feature classification

| Product area | Alpha classification | Scope guidance |
|---|---|---|
| Windows NVIDIA desktop replay/save, H.264/HEVC/AV1 | **MUST WORK FOR ALPHA** | Restrict claim to qualified configurations until broader results exist |
| Windows exact monitor selection | **MUST WORK FOR ALPHA** | Include real two-monitor and topology smoke tests |
| Transactional save and capture health | **MUST WORK FOR ALPHA** | Preserve existing contracts |
| System audio + one mic mixed track | **MUST WORK FOR ALPHA** if audio is promised | Define device/failure/final-ready behavior |
| Basic clip library/playback/delete owned clips | **MUST WORK FOR ALPHA** | Protect imported originals |
| Hotkeys | **MUST WORK FOR ALPHA** | Windows plus explicitly supported Linux environment |
| Windows AMD/Intel/hybrid codecs | **CAN BE EXPERIMENTAL** only if clearly gated | Otherwise physical qualification is mandatory before claim |
| Linux capture | **CAN BE EXPERIMENTAL** only with a named supported matrix | Broad Linux claim requires real desktop/audio/shutdown qualification |
| Multiband/per-app audio | **SAFE TO DISABLE FOR ALPHA** | Hide settings and per-source promises |
| True multitrack | **POST-ALPHA FEATURE** unless owner elevates it | Requires architecture stage |
| Camera overlay | **SAFE TO DISABLE FOR ALPHA** | Large continuous overhead and multiple re-encode cost unmeasured |
| Auto-crop/watermark | **CAN BE EXPERIMENTAL** | Clearly show finalizing and quality/performance cost |
| Game detection/auto-switch | **CAN BE EXPERIMENTAL** | Avoid claiming reliable game identity |
| Pause when unfocused | **SAFE TO DISABLE FOR ALPHA** | Broken on Windows as currently wired |
| Screenshot | **CAN BE EXPERIMENTAL** | Basic utility works; target-monitor limitation explicit |
| In-place imports | **CAN BE EXPERIMENTAL** | External delete must be protected first |
| Generic upload | **SAFE TO DISABLE FOR ALPHA** | AUDIT-012 and final-ready race otherwise need closure |
| Update page/updater | **SAFE TO DISABLE FOR ALPHA** | Manual updates are acceptable if truthfully stated |
| Themes | **CAN BE EXPERIMENTAL** | Limit/support policy needed |
| Plugins | **POST-ALPHA FEATURE** | No architecture exists |
| Cross-PC migration/archive | **POST-ALPHA FEATURE** | No implementation exists |
| Windows installer | **MUST WORK FOR PUBLIC ALPHA** | Fresh install/launch/save/uninstall and reputation/signing decision |
| Linux AppImage | **MUST WORK** only if Linux is in alpha cohort | Real target-distro lifecycle needed |

## 33. Remaining P0/P1/P2/P3 findings

Priority is tied to the intended release cohort. A finding marked “cohort gate” ceases to block if that cohort/capability is explicitly excluded and hidden.

### P0 — release-claim gates

1. **AUDIT-049 remains OPEN for the advertised full Windows hardware matrix.** AMD H.264/HEVC/AV1, Intel H.264/HEVC/AV1, and hybrid/same-adapter behavior lack physical qualification. This is a P0 if public alpha claims those paths; it is not a P0 for an explicitly NVIDIA-only cohort.
2. **No P0 engine-save defect was found on the qualified NVIDIA path.** Do not manufacture one merely because optional features are unfinished.

### P1 — reliable alpha use

1. **Exposed multiband audio is broken and cross-platform UI claims are false.** It can skip mic, produce video-only output, misroute/mute Linux applications, double mic gain, and lose source WAVs. Hide it or redesign it.
2. **Linux core audio is not reliably desktop audio.** The normal engine opens the Pulse default source, and the multiband alternative is not safe. This blocks a general Linux gaming-clips claim.
3. **Linux runtime qualification and bounded X11 shutdown are incomplete.** Wayland is automated-only; X11 can hang inside FFmpeg/XCB. This blocks broad Linux alpha support.
4. **Imported external deletion can destroy the user’s original file.** The UI does not distinguish linked external ownership.
5. **Requested/active settings are not truthful.** Windows codec Apply, bitrate changes, duration/FPS ranges, and live buffer capacity must be corrected before freezing the new UI contract.
6. **Final-ready semantics are missing.** Users/library/upload can act on a file while optional audio/effects still replace it; upload’s 60-second gate can expire early.
7. **Public package lifecycle is unverified.** A public Windows alpha needs clean install/launch/capture/save/uninstall verification and a code-signing/reputation decision. Linux needs an equivalent AppImage matrix if included.

### P2 — important but scope-containable

1. **AUDIT-012:** upload credentials are plaintext/visible, HTTP is allowed, and retry/readiness semantics are weak. Disable upload to remove it from alpha scope.
2. **AUDIT-047:** Linux long replay snapshot holds the ring mutex during deep copy and can stall capture briefly.
3. Screenshot target does not follow selected capture monitor.
4. Windows audio format/rate/channel assumptions and device-recovery UX lack a representative matrix.
5. Mic identity is friendly-name based and no continuous device recovery/drift control exists.
6. Camera’s continuous JPEG ring and sequential post-save video re-encodes have no gaming-performance qualification.
7. Editor/export and optional post-process outputs do not uniformly use the engine’s transactional final-file contract.
8. Metadata/upload history use absolute paths and are not portable or self-cleaning.
9. Diagnostics are insufficiently packaged for alpha support; engine/UI correlation and user export are missing.

### P3 — post-alpha/product completeness

1. No updater, plugin system, cross-PC migration/archive, rename, favorites, durable game assignment, or game search/filter.
2. Static startup-splash control and stale settings/hotkey copies should be removed during the later UI/source cleanup.
3. Theme import/export and notification customization need long-term support/security policy but can remain experimental or hidden.
4. Large-library and long-session performance are unmeasured; this is a benchmark/scale task, not proof of a current defect.

## 34. Product-owner decisions required

These are the highest-impact real choices exposed by the current implementation:

1. **Alpha cohort:** NVIDIA-only Windows first, all Windows vendors, or Windows plus named Linux environments?
2. **AMD/Intel exposure:** hide unqualified choices, label them experimental, or delay alpha until physical qualification?
3. **Audio product model:** one mixed system+mic track, two independent system/mic tracks, or fully configurable app tracks?
4. **Multiband:** remove/hide it for alpha or elevate a full audio redesign before alpha?
5. **Linux system audio:** explicitly select a sink monitor, use a portal/PipeWire design, or exclude Linux audio from first cohort?
6. **Microphone semantics:** should “audio off” stop all mic capture; what happens when the saved device disappears; is fallback silent or confirmed?
7. **Mix policy:** expected headroom, limiter/normalization, channel/rate support, and whether mic gain is capture-time or save-time?
8. **Final-ready boundary:** should “Clip saved” mean engine container committed or all audio/effects finished?
9. **Supported replay limits:** what normal/extended maximum and FPS should the UI promise? Keep 300/600 and 240, or intentionally expand/test them?
10. **Settings apply model:** automatically restart capture, present an Apply/Restart button, or save for next launch? Which changes clear replay history?
11. **Auto codec:** stable H.264 compatibility default everywhere or platform-specific best-codec selection? Must active codec always be visible?
12. **Unsupported hardware:** fail closed with guidance, offer software fallback, or allow same-vendor/cross-adapter fallback after explicit consent?
13. **Import ownership:** link/index, copy into library, move into library, or ask per import? What may Delete remove?
14. **Game behavior:** automatic capture switching, opt-in prompt, manual source selection, or no game detection in alpha?
15. **Focus/anti-cheat behavior:** pause replay, gate protected-window frames, or hide the user setting until one cross-platform contract exists?
16. **Optional effects:** are camera, auto-crop, and watermark alpha features despite multiple re-encodes and finalization delay, or hidden experiments?
17. **Upload scope:** disabled in alpha, generic HTTPS webhook for advanced users, or a supported provider with secure credential storage and durable queue?
18. **Update distribution:** manual download/version display for alpha or a signed/checksummed updater now?
19. **Library truth:** is parent-folder grouping sufficient for alpha, or must durable game metadata/rename/favorites precede redesign?
20. **Screenshots:** always primary display, current capture display, or an independently selected screenshot display?
21. **Packaging trust:** code-sign Windows now, what minimum Windows/Linux versions are promised, and is the installer system-wide or per-user?
22. **Diagnostics/support:** what log bundle can alpha testers export, what is redacted, and where will issues be reported?

## 35. Recommended immediate next steps

1. **Freeze the release scope in writing.** The smallest defensible cohort is Windows 10/11, physically qualified NVIDIA hardware, one selected monitor, native resolution, and one mixed system+optional-mic audio track. Any broader cohort needs its own qualification gate.
2. **Create a product-truth patch before UI redesign.** Align duration/FPS ranges, mark restart-required settings, expose requested versus active codec/backend, replace the Linux hotkey path, remove the fake update/splash claims, and hide focus pause/multiband/per-source track controls that have no trustworthy backend.
3. **Protect user-owned imported media.** Disable delete for linked external items or require an explicit external-original warning/ownership policy.
4. **Choose the audio model.** If alpha is one mixed track, fix Linux desktop source selection, microphone on/off/device behavior, gain once, and final-ready failure messaging. If independent tracks are required, open a new dedicated audio architecture finding/spec rather than incrementally extending the current multiband code.
5. **Close final-ready semantics.** Introduce a UI-level finalization state and ensure upload/manual actions cannot race file replacement. Preserve AUDIT-028’s engine transaction boundary.
6. **Run cohort hardware/runtime qualification.** Complete AMD, Intel, and hybrid scenarios only if included; otherwise capability-gate them. For Linux, run real GNOME/KDE/wlroots/X11, audio, hotkey, monitor, shutdown, recovery, and AppImage tests.
7. **Run clean-package lifecycle tests.** Windows installer install/first launch/capture/save/relaunch/uninstall; signing/reputation decision. Linux target-distro AppImage launch/portal/audio/hotkey/capture/uninstall-equivalent checks if in scope.
8. **Give the UI developer a backend contract, not screenshots.** Use section 30 as the starting state machine; require controls to render active capability and asynchronous state instead of only persisted preferences.
9. **Keep later cleanup and performance work separate.** Do not delete historical evidence or claim performance superiority. After product truth and alpha scope are fixed, run the dedicated FTHR-versus-baseline/Medal benchmark.

### Source evidence index

- UI/orchestration: `FTHR_UI/main.py`, `FTHR_UI/ui/clip_viewer.py`, `FTHR_UI/ui/clip_grid.py`, `FTHR_UI/ui/capture_settings_widget.py`.
- Settings/state: `FTHR_UI/core/settings_manager.py`, `presets_manager.py`, `hotkey_manager.py`, `linux_runtime.py`, `clip_metadata_manager.py`, `upload_manager.py`, `save_state.py`, `capture_health.py`, `capture_bridge.py`.
- Audio/post-processing: `FTHR_UI/core/mic_recorder.py`, `audio_mixer.py`, `camera_recorder.py`; Windows `audio_capture.cpp`, `audio_ring_buffer.cpp`, `audio_encoder.cpp`; Linux `audio_capture.cpp`, `audio_multi_capture.cpp`, `save_clip.cpp`.
- Windows capture/codec: `windows_monitor_resolver.cpp`, `capture_engine.cpp`, `replay_encoder_policy.cpp`, `replay_encoder_factory.cpp`, `hardware_encoder.cpp`, `ffmpeg_amf_replay_encoder.cpp`, `ffmpeg_qsv_replay_encoder.cpp`, `save_clip_task.cpp`, `main.cpp`.
- Linux capture: `capture_backend.cpp`, `backend_wlr.cpp`, `backend_ext.cpp`, `backend_x11.cpp`, `wayland_dispatch.cpp`, `capture_engine.cpp`, `encoder.cpp`, `ring_buffer.cpp`.
- Packaging/release: `FTHR.spec`, `FTHR_linux.spec`, `installer_windows.iss`, `build_linux.sh`, `tools/release_asset_manifest.json`, `tools/verify_release_licenses.py`, `docs/AUDIT-ID-MAP.md`, `docs/RELEASE_CHECKLIST.md`, and `build/audit-049-nvidia-final/qualification-report.json`.

## Product-owner summary — plain-English answers

### 1. What does FTHR actually do today?

It automatically captures a local replay buffer, saves recent timestamp-selected clips, records one main audio stream, can add a microphone after save, shows clips/screenshots in a local library, offers basic editing/export, and can optionally upload to a user-defined server. The strongest proven configuration is Windows 10 with an NVIDIA RTX GPU at native 1080p60.

### 2. Which features are genuinely complete?

On that qualified NVIDIA path: exact monitor-to-adapter selection, native NVENC H.264/HEVC/AV1 encoded replay, timestamp-correct 30/60-second saves, rapid/warm-up save handling, transactional engine commit, and the shared-memory/save-state contracts. Settings persistence and conservative partial-file cleanup are also coherent.

### 3. Which features only look complete from the UI?

Multiband/per-source audio, Windows live codec Apply, 900-second/360-FPS choices, immediate quality apply, focus pause on Windows, Linux’s displayed non-Hyprland hotkey setup, selected-monitor screenshots, reliable game metadata, startup-splash control, and the “latest version” page.

### 4. Which feature implementation surprised you most?

Multiband audio. Its UI sounds like a finished cross-platform per-app recorder, but only Linux has an engine implementation; it reroutes applications to null sinks, can make them inaudible, mixes all categories back to one stream, and deletes the independent WAVs. Windows ignores the engine flag.

### 5. How does multi-audio REALLY work right now?

Windows records one already-mixed system-output ring and later mixes a separate Python microphone into the committed clip. Normal Linux records one PulseAudio default source—which may be a mic, not desktop output—and later mixes the Python mic. Linux multiband temporarily records category PCM, then also collapses it to one AAC stream.

### 6. Does multi-audio preserve separate tracks?

**No.** Normal FTHR recordings do not preserve independent game, mic, Discord, browser, or music tracks in the final MP4.

### 7. Where is audio irreversibly mixed?

Windows applications are mixed by the OS before FTHR receives them. Normal Linux sources are already combined at the selected Pulse source. Microphone and Linux categories become irreversible at Python/FFmpeg `amix`, after which the final file replaces the base clip and category WAVs are deleted.

### 8. Can already-buffered audio be remixed before save?

**Not in the promised cross-platform sense.** Linux multiband categories are a limited exception: their raw buffered PCM can use current category gains at first save. Historical mic gain is already baked in, and Windows app sources were never separate.

### 9. How many audio tracks are in a normal clip?

Normally **one**. The real NVIDIA qualification clips all had exactly one audio stream. An audio failure can leave zero.

### 10. Does current multi-audio need no, minor, major, or complete redesign?

**Complete redesign / FULL AUDIO ARCHITECTURE REWORK** if the desired product is genuine independent tracks. If alpha deliberately wants one mixed track only, a smaller stabilization pass is enough—but all multiband/per-source claims must be hidden.

### 11. Which UI controls do not map cleanly to real backend capability?

Codec/preset Apply on Windows, long duration/FPS values, live quality apply, multiband and per-source volumes, focus pause, screenshot monitor, game identity, audio on/off versus mic capture, the update page, startup splash, and linked-folder import/delete ownership.

### 12. What MUST be fixed before handing the project to the UI developer?

Decide alpha platform/audio scope; define active-versus-requested and restart semantics; choose honest duration/FPS caps; hide or contract multiband/focus/update placeholders; protect external imported files; and define when a clip is truly final. Otherwise the new UI will encode incorrect engine assumptions.

### 13. What can the UI developer safely redesign without engine work?

Layout, visual hierarchy, accessibility, navigation, library card appearance, notification presentation, basic hotkey editing on Windows, owned-file playback/reveal, theme appearance, and state displays—provided they use the real state/apply contract in section 30 and do not invent new capability.

### 14. What should be hidden/disabled in the alpha UI?

Multiband/per-source track claims, Windows focus pause, fake updater/latest-version claim, startup-splash checkbox, unqualified AMD/Intel/Linux choices outside the selected cohort, camera by default, generic upload unless AUDIT-012 is accepted/closed, and external-file Delete until ownership is explicit.

### 15. What are the remaining genuine alpha blockers?

For broad alpha: AMD/Intel/hybrid physical qualification; real Linux desktop/audio/AppImage verification; bounded X11 shutdown; broken multiband and Linux audio model; incorrect apply/capability UI; external-original deletion; final-ready/upload races; and public installer lifecycle/signing decisions. A NVIDIA-only Windows cohort removes several, but not the UI truth, external-delete, final-ready, and package-lifecycle issues.

### 16. If this exact implementation shipped tomorrow, what are the five most likely product complaints?

1. “I changed codec/quality/duration, but the recording did not actually change.”
2. “The app says separate audio, but my clip has one track / missing mic / wrong Linux audio.”
3. “FTHR deleted the original clip from my imported folder.”
4. “The app said the clip was saved, but it was still changing, upload caught the wrong version, or an effect/audio part was missing.”
5. “My AMD/Intel/Linux/hybrid machine shows a supported-looking option but capture fails or has not been validated.”

### 17. What product decisions must the owner answer before final UI implementation?

The 22 decisions in section 34 cover cohort/vendor support, audio model and devices, mix policy, final-ready semantics, duration/FPS caps, restart behavior, Auto/fallback policy, import ownership, game/focus behavior, optional effects, uploads, updates, metadata, screenshots, packaging, and diagnostics. Those are the decisions that materially change the UI contract.

### 18. Is the current product internally coherent enough for the planned alpha?

**NO.** The qualified Windows/NVIDIA capture-and-save core is coherent, but the product surface around it is not yet: several controls promise inactive behavior, audio has incompatible platform models, final readiness is ambiguous, external import ownership is unsafe, and broad hardware/Linux claims outrun runtime evidence. A short, explicitly scoped product-truth pass can make a NVIDIA-first alpha viable; a broad Windows-and-Linux alpha cannot responsibly ship from this exact state.

## Fix-pass status update — 2026-08-21

This addendum records the later Product Truth / Broken Feature Fix Pass. It does
not rewrite the baseline findings above. Current source at `2b46cb4` supersedes
the old-behavior descriptions where the table below says resolved or hidden.

| Finding | Current status | Current behavior |
|---|---|---|
| Linked/imported original deletion | **RESOLVED** | Imported roots remain links. Normalized real-path ownership disables and hard-guards generic Delete; removing a root only unregisters it. |
| Duration/FPS truth | **RESOLVED** | Both replay durations stop at 300 seconds, FPS at 240, and the native launch ring never exceeds 300 seconds. Invalid persisted values are rejected to documented defaults rather than silently clamped. |
| Codec/quality/duration/FPS/resolution/monitor/audio Apply | **RESOLVED** | These are requested values until a full engine restart produces a matching fresh healthy generation. Failure preserves the requested value but does not label it active. |
| Windows P1–P7 preset | **HIDDEN FOR ALPHA** | Windows backends do not consume the preset argv consistently, so the selector and invented active-P4 label are disabled. |
| Clip final readiness | **RESOLVED** | Native `CLIP_SAVED` remains the atomic base-commit boundary. Application readiness gates thumbnails, playback, edit, upload and delete until optional work reaches READY, READY_WITH_WARNING or FINALIZATION_FAILED. |
| Upload 60-second race | **RESOLVED** | Every upload entry point waits on the readiness registry; an elapsed timeout/event alone cannot promote intermediate bytes. |
| Optional processing result | **RESOLVED** | A usable base survives optional failure and is published once as READY_WITH_WARNING; a missing/empty final file is FINALIZATION_FAILED. |
| Windows focus pause | **HIDDEN FOR ALPHA** | No legacy start/stop command is presented as replay focus pause. |
| Linux hotkey instructions | **RESOLVED** | Hyprland/KDE/GNOME/generic instructions use the resolved owner-only runtime socket and never `/tmp/fthr_hotkey.sock`. |
| Fake latest-version / splash setting | **RESOLVED** | Updates are described as manual; the dead startup-splash control is removed. |
| Screenshot monitor | **RESOLVED IN SOURCE** | Screenshot uses the active capture monitor. A selected-but-missing target fails explicitly rather than silently using the primary display. Representative Linux runtime remains unverified. |
| Normal Linux desktop audio | **RESOLVED IN SOURCE** | The engine resolves the default sink and opens its monitor source synchronously. It never substitutes the default microphone; open failure produces a video-only startup warning. Real desktop/audio-content qualification remains open. |
| Multiband and semantic per-source controls | **DEFERRED TO AUDIO REWORK / HIDDEN FOR ALPHA** | UI, presets, post-route and Linux native startup all force the feature off. Legacy implementation files remain isolated. The editor exposes master volume only and does not fabricate Game/Discord/Browser/Music identities. |
| Local export publication | **RESOLVED** | FFmpeg writes a unique same-directory staging file and atomically replaces the final name only after success; cancellation/failure removes the stage. |
| AUDIT-044 X11 cancellation | **STILL OPEN; MITIGATED FOR ALPHA** | `x11grab` is excluded by default. Unsupported X11/Wayland sessions fail clearly and exit after bounded recovery. The experimental opt-in is not release-eligible. No worker/subprocess or Shared Memory change was introduced. |
| AMD / Intel / hybrid qualification | **STILL OPEN** | AMD and Intel remain code-ready/automated-tested. Physical AMD, Intel and hybrid results do not exist. |
| Linux public support | **STILL OPEN** | Build/tests and bounded failure are proven; real wlroots/GNOME/KDE visible capture, audio content, hotkeys and current AppImage lifecycle are not. |
| Installer lifecycle and signing | **STILL OPEN** | The current-source installer install/launch/capture/relaunch/uninstall flow was not run. The binaries are not code-signed; signing/reputation is an owner decision. |

### Fix-pass verification actually performed

- Python: **450 passed, 34 skipped** on Windows; Ruff and `compileall` passed.
- Windows Release x64: MSBuild passed. Native suite: **82 scenarios / 149
  checks** passed, including replay policy and AMD/Intel failure/policy matrices.
- Current NVIDIA runtime: RTX 4060 Ti, same adapter, native NVENC H.264/HEVC/AV1.
  Each codec produced warm-up, measured 30.016-second, measured 60.010-second
  and two rapid saves; all 15 files fully decoded, carried one audio stream and
  left no partial file. This does not qualify AMD, Intel, hybrid, GUI settings,
  screenshot or installer lifecycle.
- Linux development build: succeeded under WSL with system FFmpeg; native CTest
  **10/10 passed**. A pure-X11 smoke opened `RDPSink.monitor`, refused x11grab,
  exhausted three recoveries and exited cleanly. This is not a representative
  compositor/audio-content test and not the pinned Release/AppImage build.
- Source licence/asset gate: **76 checks, 0 failed, 1 warning** (Linux pinned
  FFmpeg not vendored in this Windows checkout). Existing Windows artifact
  licence/Qt/asset gate: **84 checks, 0 failed**; that artifact predates this
  source fix and is not a current functional package build.
- Version, Shared Memory v4, response publication, exception baseline,
  repository hygiene and generated-asset gates passed. No ABI change occurred.

### Updated release conclusion

The product-truth correction itself is **PASS**. The code is coherent enough for
the planned UI redesign, but no public tag is justified yet: the current GUI and
installer lifecycle were not exercised, signing remains undecided, and broad
Windows/Linux qualification is incomplete. NVIDIA is the only hardware cohort
with current physical codec/save evidence.

## MULTI-AUDIO REWORK HANDOFF

- **Current platform implementations:** Windows captures the selected render
  device through event-driven WASAPI loopback into one system mix. Linux normal
  capture uses PulseAudio/pipewire-pulse `pa_simple` against the default sink's
  monitor source. Python `sounddevice` captures one optional microphone. The
  legacy Linux multiband code creates category sinks/PCM buffers but is disabled
  at every alpha boundary; Windows has no equivalent implementation.
- **Clock domains:** Windows system PCM carries WASAPI QPC positions in
  100-nanosecond units, shared with video selection. Linux video and normal
  audio segments use `CLOCK_MONOTONIC` nanoseconds. Python microphone chunks use
  `time.monotonic()` plus frame-count-derived 48 kHz positions. These domains
  are not one cross-platform clock contract.
- **Reusable current APIs:** Windows `AudioCapture`, `AudioRingBuffer` snapshot
  selection and `AudioEncoder`; Linux `AudioCapture::Start/Stop/ExtractSegment`;
  Python `MicRecorder.extract_segment`/WAV writer; the application clip-readiness
  registry and same-directory atomic replacement helpers.
- **Libraries and formats:** Windows uses WASAPI plus FFmpeg AAC. Linux uses
  libpulse/`pa_simple` for float32 stereo 48 kHz PCM and FFmpeg AAC/muxing.
  Python uses `sounddevice`/NumPy for mono float32 48 kHz microphone PCM and the
  bundled FFmpeg tools for `amix`/MP4 replacement.
- **Current mux capability:** Normal output is one MP4 audio stream. Windows and
  Linux system audio is already a mix; the optional microphone is later mixed
  into that stream. Legacy Linux category buffers can be extracted before save,
  but the old route collapses them and does not preserve semantic tracks.
- **Legacy behavior not to preserve accidentally:** application rerouting to
  null sinks, fabricated Game/Discord/Browser/Music identities, platform-only
  category semantics, destructive deletion of category evidence, double-applied
  gain, settings/native activation split, and treating one mixed AAC stream as
  independent editable tracks.

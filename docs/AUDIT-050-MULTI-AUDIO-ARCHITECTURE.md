# AUDIT-050 — Multi-Audio Architecture Validation

**Status:** IMPLEMENTATION IN PROGRESS — QUALIFICATION OPEN
**Date:** 2026-08-23
**Branch at start:** `fix/windows-alpha-hardware-paths`
**Starting HEAD:** `2e6e9cad986436428c0f092aac6d7fa5e9399f2c`

## Scope and stop rule

This document records focused validation spikes and, after the approved
architecture decision, the implementation/qualification ledger for the
clean-sheet multi-audio architecture. The existing mixed-audio alpha path,
AUDIT-022/023/035 capture-health behavior, AUDIT-024 bounded Wayland behavior,
AUDIT-028 transactional saves, and Shared Memory v4 remain unchanged.

The phase stops after the evidence below. A product owner must approve the
architecture and the resulting Windows capability model before the full
rework starts.

## Product target under test

The target is a clip-local source list containing only sources that contributed
non-trivial audio in the saved interval. Microphone remains independent;
source gain and mute are non-destructive. The proposed file shape is a default
compatibility mix followed by semantic source stems, with a versioned FTHR
manifest carrying the authoritative mapping.

## Previous stop conditions

1. Official Windows process loopback is unavailable on the current Windows 10
   release target.
2. Qt `QMediaPlayer` exposes track selection, not simultaneous independent
   live mixing.
3. One long raw PCM ring per source scales poorly at the supported replay cap.

## Spike inventory

| Spike | Isolated artifact | Purpose |
|---|---|---|
| Windows process loopback | `tools/audit050/windows_process_loopback_probe.cpp` | Compile against the SDK and report runtime capability; capture a bounded WAV when supported |
| AAC replay | `tools/audit050/aac_ring_benchmark.cpp` | One process with 1/4/8 persistent native AAC encoder contexts |
| MP4/export | `tools/audit050/run_mp4_spike.ps1` | Default Mix + 1/4/8 stems, metadata, mapping and copy-export behavior |
| Qt playback | `tools/audit050/qmediaplayer_probe.py` | Track discovery and active-track semantics |
| Publication | `tools/audit050/manifest_transaction_probe.py` | MP4/manifest staging, hash binding and failure recovery |

No artifact under `tools/audit050/` is linked into the production engine,
packager, UI, or Shared Memory contract.

## Windows support reality

The repository currently promises Windows 10 version 1903+ or Windows 11. The
official process-loopback activation contract requires the newer process
loopback API and is not available on Windows 10 client build 19045. Session
enumeration remains useful for identity and lifecycle, but it is not a PCM
capture path. The supported product alternatives are therefore:

- Windows 11: per-process/process-tree capture after runtime qualification.
- Windows 10: truthful mixed system audio plus microphone; no fabricated app
  tracks or per-app controls.
- A virtual driver, injection, or undocumented hook is outside this audit and
  is rejected as an alpha architecture.

## Windows 11 process-loopback spike

### API and method

The probe uses `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK` and
`ActivateAudioInterfaceAsync`, then initializes an `IAudioClient` capture
client for the virtual process-loopback endpoint. It accepts a target PID,
captures a bounded WAV, and reports the HRESULT and OS build when the API is
not available. It does not reroute an application, inject into a process, or
modify the production capture engine.

### Runtime result

The current qualification host is Windows 10 build 19045. The probe therefore
cannot provide a valid Windows 11 runtime qualification on this checkout. The
Windows 11 matrix remains **UNVERIFIED** until a Windows 11 build at or above
the documented API floor is run with two independent synthetic sources.

Required follow-up on Windows 11: 440 Hz and 880 Hz sources, spectral
separation, normal speaker audibility, app restart/exit, child-process
grouping, default-device change, and duplicate-capture checks.

## Session discovery and grouping

The final provider must maintain a live session list using
`IAudioSessionManager2`, including session-created and session-disconnected
notifications. A single enumerator snapshot is insufficient. Grouping must be
deterministic and evidence-based: process-tree lineage, executable identity,
package/application identity, and session grouping data are candidates. Same
names alone are not sufficient. The grouping result must remain generation
local and must not make PIDs durable identifiers.

The required icon/name fallback chain is local executable icon, packaged-app or
desktop identity, then a generic application icon. Missing or exited
processes must remain representable until their replay history expires.

## AAC replay storage

The current raw float32 stereo rate is 48 kHz. Raw storage is 384,000 bytes per
source-second. At 300 seconds this is approximately 109.86 MiB per source,
439.45 MiB for four, and 878.91 MiB for eight, before headroom.

The native benchmark uses one process and persistent `AudioEncoder` contexts,
not an FFmpeg subprocess per source. The target is AAC-LC, 48 kHz, stereo,
128 kbit/s. The final decision must include encoder count, CPU, packet count,
encoder delay, lock contention, and snapshot latency from the benchmark output.

Activity gating must be cheap and hysteretic. A discovered silent session must
not allocate and continuously encode a long replay ring; an exited source may
retain history until the replay window expires.

## MP4, metadata, and export

The tested file model is:

1. stream 0: `Default Mix`, marked default;
2. stream 1+: only active source stems;
3. `handler_name` and title provide understandable fallback labels;
4. a sidecar manifest is authoritative for UUIDs, source type, activity range,
   icon identity, and stream mapping.

The copy-preserve path must use an explicit manifest-derived `-map`; FFmpeg's
automatic stream selection can silently keep only one audio stream. Mixed
exports must map video and the generated selected mix explicitly. Legacy
video-plus-one-audio clips remain valid without a manifest and are displayed as
generic audio, never as fabricated application identities.

The manifest must not persist unnecessary full paths, command lines, usernames,
window titles, URLs, or server data. MP4 and manifest are staged together,
bound by a file hash/reference, and published only when both are valid. Failure
orphan cleanup must never present a rich-stem clip with missing or mismatched
metadata.

## Playback mixer

`QMediaPlayer` remains useful for legacy/default-mix playback and video
experiments, but it is not the final multi-track mixer. The required isolated
prototype is:

```text
FFmpeg demux/decode -> canonical float PCM -> gain/mute mixer -> QAudioSink
```

One playback clock must drive all stems and video. Seeking must flush every
decoder and discard stale buffers. The prototype must measure 1/4/8-track CPU,
30/60/300-second drift, and seek positions before any production viewer change.

## Linux boundary

The current Linux implementation is PulseAudio simple API based. A future
PipeWire provider must discover application output nodes and add a capture link
without removing or rerouting the existing speaker link. Full Linux
implementation is outside this spike. Headers, `libpipewire-0.3` runtime,
PipeWire-Pulse behavior, PulseAudio-only fallback, licensing, and AppImage
bundling remain an explicit follow-up gate.

## Results

The spikes were run on Windows 10 build 19045, MSVC 14.44, pinned FFmpeg
8.1.2-21-gce3c09c101-20260630, and PySide6 6.11.1. All generated media and
reports were written below the user TEMP directory.

### Windows capture and sessions

The native source compiled with the Windows SDK and linked against
`Mmdevapi.lib`, `Ole32.lib`, and `Avrt.lib`. Running it against the current
PowerShell PID returned:

```text
OS_BUILD=19045
PROCESS_LOOPBACK_ACTIVATE_FAILED 0x8000000E
```

The probe did not crash and wrote no partial WAV. This is a runtime capability
result, not a Windows 11 qualification. A real Windows 11 run remains required
for source isolation, 440/880 Hz spectral separation, endpoint audibility,
process restart/exit, child grouping, device change, and duplicate checks.

The separate `IAudioSessionManager2` probe succeeded on the same host:

```text
SESSION_COUNT=7
SESSION_NOTIFICATION_REGISTERED=true created_during_wait=0
```

It observed the system sound session, two Discord PIDs, Wallpaper Engine,
SignalRGB, Firefox, and Spotify. Several sessions had an empty display name but
an executable path in the session-instance identifier. This proves that session
identity/lifecycle discovery is available on Windows 10; it does not provide
independent PCM capture. No icon extraction or deterministic process-tree
grouping is claimed by this run.

### AAC replay ring

The native benchmark created one persistent `AudioEncoder` context per source
inside one process. It used AAC-LC, 48 kHz, stereo, 128 kbit/s, 1024-sample
frames and a packet vector representing the replay ring. Results:

| Sources | Duration | Encoded bytes | Encode wall | Process CPU | Snapshot copy |
|---:|---:|---:|---:|---:|---:|
| 1 | 30 s | 479,956 | 499 ms | 500 ms | 0.711 ms |
| 1 | 60 s | 959,636 | 1,014 ms | 1,016 ms | 1.232 ms |
| 1 | 300 s | 4,798,348 | 4,882 ms | 4,828 ms | 7.282 ms |
| 4 | 30 s | 1,916,963 | 2,042 ms | 2,031 ms | 2.291 ms |
| 4 | 60 s | 3,833,060 | 4,048 ms | 4,047 ms | 4.943 ms |
| 4 | 300 s | 19,164,227 | 20,013 ms | 20,000 ms | 25.587 ms |
| 8 | 30 s | 3,833,922 | 4,318 ms | 4,297 ms | 4.157 ms |
| 8 | 60 s | 7,664,581 | 8,569 ms | 8,531 ms | 7.566 ms |
| 8 | 300 s | 38,314,668 | 42,382 ms | 42,328 ms | 47.548 ms |

The first packet PTS was `-1024` samples for every case, exposing the AAC
encoder delay that the muxer must account for. This is a synthetic offline
benchmark, not a gaming benchmark: the 8-source 300-second case consumed about
42 seconds of one host CPU core while simulating 300 seconds of capture. At
real-time wall-clock cadence that is approximately 14% of one core on this
machine. It is promising but not an acceptance result for gaming capture.

No concurrent producer/snapshot contention was measured. The implementation
limit and activity-gated encoder lifetime therefore remain open.

The activity sketch used two loud blocks to activate and ten quiet blocks to
deactivate. A 0.001 RMS noise floor did not activate the source; the tone range
was retained through the hysteresis window. The thresholds are experimental
and not a final product policy.

### MP4 and export

`run_mp4_spike.ps1` generated video plus `Default Mix` plus 1, 4, and 8 AAC
stems. All files decoded successfully, durations were five seconds, the first
audio stream was marked default, and `handler_name` values survived probing.

| Stems | Audio streams in source | No-map copy streams | Explicit map-all streams | Source size |
|---:|---:|---:|---:|---:|
| 1 | 2 | 1 | 2 | 228,794 B |
| 4 | 5 | 1 | 5 | 476,926 B |
| 8 | 9 | 1 | 9 | 808,019 B |

This is a direct proof that the current automatic export shape loses stems.
Preserve-stem export must use a manifest-derived explicit map. Mixed export must
map video and the generated mix explicitly.

### Qt and native playback mixer

The PySide6 probe reported `LoadedMedia` and exposed 2, 5, and 9 audio tracks
for the 1/4/8-stem files respectively. In every case:

```text
active_audio_track = 0
```

Qt did not expose the MP4 `handler_name` as the `QMediaMetaData.Title` value in
this backend. The manifest or FFmpeg probe therefore remains necessary for
semantic source labels.

The isolated FFmpeg decoder/mixer decoded 5-second AAC stems into float32
stereo, mixed gain 1.0, and re-ran with the first stem muted:

| Stems | Decode + mix | RMS all | RMS first muted |
|---:|---:|---:|---:|
| 1 | 12.116 ms | 0.062 | 0.000 |
| 4 | 46.466 ms | 0.125 | 0.108 |
| 8 | 93.881 ms | 0.176 | 0.165 |

This proves a native decoder/mixer can independently mute and combine the
stems. It does not yet prove live seek/flush or long-duration clock behavior.

The Qt `QAudioSink` smoke test succeeded on the available output device with a
valid and supported 48 kHz float32 stereo format. It entered `IdleState` after
start and returned to `StoppedState` without an error. This is an output API
smoke test, not an end-to-end playback qualification.

### Manifest and publication

The fault-injection probe bound a version-1 manifest to the staged media SHA-256
and treated a clip as ready only when both final files existed and the hash
matched. Failures before the second rename never reported `ready=true`; a crash
after the media rename left a valid ready pair. Stage cleanup is still required
in the production reconciler. This supports a hybrid model: understandable
stream metadata in MP4 plus an authoritative, privacy-bounded sidecar.

### External players

The bundled FFplay 8.1.2 opened the generated 8-stem MP4 and selected the
default `Default Mix` stream. VLC and Windows Media Player were not installed as
automatable runtimes in this environment; no claim is made for their current
seek behavior. The default mix remains necessary for external-player
compatibility even if FTHR uses the stems internally.

### Capture synchronization and gaming benchmark

No Windows 11 process-loopback host, independent microphone clock, USB/44.1 kHz
device, device restart, or 30/60/300-second pulse capture was available. A/V
timing error, drift, encoder hot-path frame impact, audio drops, and real-game
video regression are therefore **UNVERIFIED**. The native AAC numbers are a
codec cost bound, not a product performance qualification.

### Linux PipeWire

The current checkout has the PulseAudio simple API dependency but no PipeWire
development/runtime provider. The available WSL environment exposed `pactl`
but no `pw-cli`, `pw-dump`, `pw-link`, or `pipewire` executable. Application
node discovery, non-destructive capture links, PipeWire-Pulse compatibility,
and AppImage dependency/licence qualification are **UNVERIFIED**.

## Final decision gate

The full implementation remains blocked until the following are explicitly
approved:

1. Windows 11 per-app stems with Windows 10 mixed-only capability;
2. AAC packet rings rather than per-source long raw PCM rings;
3. Default Mix plus semantic stems and a versioned manifest;
4. a custom FFmpeg/QAudioSink playback mixer;
5. a named Linux PipeWire qualification scope.

## Final spike decision

| Gate | Decision |
|---|---|
| Windows 11 per-app audio | **VIABLE in API design; runtime UNVERIFIED** |
| Windows 10 per-app audio | **NOT OFFICIALLY SUPPORTED** |
| Windows 10 mixed fallback | **VIABLE** |
| AAC replay ring | **APPROVED for the next isolated implementation spike; production approval awaits gaming benchmark** |
| Default Mix + stems | **APPROVED as the container hypothesis** |
| FTHR live mixer | **APPROVED for isolated prototype; not production-integrated** |
| Linux PipeWire | **UNVERIFIED** |
| AUDIT-050 | **DECISION REQUIRED** |

The recommended product model is Model A: Windows 11 per-app stems and Windows
10 mixed-only audio. The source upper bound is provisionally eight active stems;
it is not a release promise until the gaming hot-path benchmark and a real
Windows 11 capture run pass.

Unresolved P0/P1 risks are: Windows 11 source isolation, source-tree grouping,
device-loss behavior, A/V sync and drift, encoder CPU under a real game, MP4 +
manifest crash recovery in the production transaction boundary, playback seek
and clock integration, Windows external-player qualification, and the Linux
PipeWire/AppImage matrix.

## One proposed implementation sequence after approval

1. Approve the Model-A capability contract and reserve the Windows-10 mixed-only
   UI state.
2. Qualify the Windows 11 process/session provider with two synthetic sources,
   lifecycle/device-loss tests, and deterministic grouping.
3. Implement the common source-generation/manifest contracts and activity-gated
   AAC packet rings behind isolated native tests.
4. Implement native multi-stream muxing, explicit manifest-derived export maps,
   and atomic MP4/manifest publication with recovery tests.
5. Complete the FFmpeg decoder + `QAudioSink` mixer, then prove seek, flush,
   30/60/300-second drift, and video synchronization.
6. Run the gaming hot-path benchmark and set the final source cap from evidence.
7. Build and qualify the Linux PipeWire provider and AppImage dependency gate.
8. Only after all gates pass, wire the production viewer and remove the alpha
   feature gate; do not delete the legacy evidence before migration coverage is
   complete.

## Commit boundary

This document and isolated spike harnesses may be committed as AUDIT-050
evidence. No production implementation, UI redesign, Shared Memory v5, or
legacy-audio deletion is allowed in this phase.

## Approved implementation progress — 2026-08-23

The product owner approved Model A after the validation spikes. The following
production foundations have subsequently landed, in focused commits:

1. a generation-local common audio-source model, hysteretic activity gate,
   deterministic eight-source admission boundary, and bounded timestamped AAC
   packet ring;
2. a version-1, SHA-256-bound, privacy-bounded audio manifest contract and
   reader. A missing, stale, corrupt, or mismatched sidecar is rejected and
   must fall back to ordinary container metadata;
3. Windows session-discovery infrastructure based on `IAudioSessionManager2`
   initial enumeration plus `IAudioSessionNotification`, with build capability
   detection for the documented process-loopback API floor; and
4. the existing Windows default-mix hardware replay path now retains
   persistent in-process AAC packets rather than a long raw-PCM replay ring.
   Its MP4 stream is labelled `Default Mix` and carries default disposition.
   Preserve-mode export explicitly maps every audio stream, so FFmpeg automatic
   stream selection cannot delete stems; and
5. a native multi-track mux contract, version-1 manifest producer, and paired
   same-directory publication boundary. The manifest is SHA-256-bound to the
   temporary MP4, then published before the MP4; a failed final media publish
   removes that orphaned manifest. Startup recovery removes only old,
   FTHR-named orphan sidecars whose corresponding MP4 is absent. The viewer
   reads a validated manifest to expose real track labels for export-only
   mixing; it never infers sources from raw MP4 stream count.

These changes do **not** constitute completion of the approved architecture.
In particular, production process-loopback activation, dynamic application
source capture, FFmpeg/QAudioSink editable playback, live playback gain/mute,
and physical Windows 11/game performance qualification are still open. The
native microphone/device-identity stage is recorded separately below. The
legacy Python microphone post-save route is no longer selected for normal
Windows capture; it remains as the Linux compatibility implementation. The
disabled legacy multiband/null-sink path remains disabled.

Therefore AUDIT-050 is **not resolved**, Windows 11 per-app audio is **not
ready**, and Linux per-app audio remains **not implemented**. Shared Memory v4,
AUDIT-022/023/035 health behavior, AUDIT-024, AUDIT-028, AUDIT-042,
AUDIT-048, and AUDIT-049 are not intentionally changed by this foundation
work.

## WINDOWS 11 APP-STEM IMPLEMENTATION — 2026-08-23

### CODE IMPLEMENTED

`WindowsApplicationAudioSourceManager` now owns one
`WindowsAudioSessionRegistry` for a capture generation and joins its
deterministic, root-PID-scoped application groups to one
`WindowsProcessLoopbackAudioProvider` each. The provider uses the documented
`ActivateAudioInterfaceAsync` contract with `VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK`,
`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`, a `VT_BLOB`
`AUDIOCLIENT_ACTIVATION_PARAMS`, and
`PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE`.

The local Windows SDK declaration says this mode includes audio rendered by the
target process and its child processes. FTHR targets the registry's same-
executable process-tree root, rather than an arbitrary child session PID, so
same-executable helper/render descendants remain one source. The SDK does not
state a separate guarantee for children created after activation; that behavior
is explicitly part of the physical Windows 11 validation matrix below.

Windows build capability is checked before manager startup. On Windows 10
build 19045 (and every build below the documented 20348 floor), the manager is
not created and no process-loopback activation is attempted: Default Mix
remains the only system-audio source. A capable build is still not reported as
an active source until asynchronous activation, format setup, meaningful audio,
and AAC packet production all succeed.

Each provider reads the returned `WAVEFORMATEX` rather than assuming
float32/48 kHz/stereo. It supports float32, PCM16, and PCM32 input and converts
once with libswresample to canonical 48 kHz stereo float before activity
measurement and AAC-LC encoding. Packed 24-bit/unknown formats fail the one
source honestly instead of byte reinterpretation. A silent discovered session
allocates neither a persistent AAC encoder nor a replay packet ring.

After two meaningful blocks pass the existing hysteretic source gate, the
provider creates one in-process AAC-LC 128 kb/s encoder and one bounded
`EncodedAudioPacketRing`. It buffers only the two short gate blocks; no long
raw PCM history is stored. Its QPC 100-ns origin is the first pending
meaningful block. Save derives signed source sample bounds from the same video
presentation QPC interval. A source first audible at 15 s of a 30 s clip gets
a negative presentation boundary and therefore keeps a 15 s timestamp gap in
the MP4 rather than being shifted to t=0.

Session-created, state-change, and disconnect notifications mark the single
registry dirty. The manager refreshes only after those notifications, not by
periodic process enumeration. A disappeared runtime group stops its bounded
provider then marks the source ended; its AAC packet ring and source metadata
remain generation-local until normal replay expiry. A later process restart
gets a fresh source UUID under the existing root-PID grouping rule, avoiding an
unsafe PID-reuse/name-only merge. Failed activation, format, device, or service
recovery affects only that source. Activation waits at most five seconds;
capture waits at most 250 ms; recoverable device/service failures retry at most
three times with a stop-aware bounded delay.

The native save path preserves the existing transactional MP4-plus-manifest
pair. It appends only actual overlapping app packet snapshots after Default
Mix, retains the existing `handler_name`/title metadata and manifest UUID/type/
identity/activity mapping, and never synthesizes a stream for a quiet or failed
application. The provisional limit means Default Mix plus at most eight admitted
application stems (ten MP4 audio tracks total). Shared Memory v4, Linux providers,
the final playback mixer, and the legacy disabled multiband route were not
changed.

### AUTOMATED TESTED

The native `FTHRclips_tests` suite now covers unsupported-versus-supported
capability gating, deterministic provider candidates, root targeting,
activity hysteresis, silent-source exclusion, signed late-source timeline
mapping, exit-history retention, restart isolation, failure isolation,
per-generation isolation, deterministic source admission limits, and
manifest-safe source identities. Existing AUDIT-050 source/ring, manifest,
paired-publication, mux, export-map, and session-registry tests remain in the
same Release x64 suite.

The complete Windows Release x64 engine build and native suite compile with
the production provider. This proves API/header/link integration and the
deterministic logic only; it is not a source-isolation runtime result.

### REAL WIN11 VERIFIED

**Not verified.** The available qualification host is Windows 10 build 19045.
The existing isolated probe returned `PROCESS_LOOPBACK_ACTIVATE_FAILED
0x8000000E`, as expected for that unsupported host. No Windows 11 run has yet
proven 440/880 Hz isolation, absence of cross-contamination, silent-session
exclusion in an actual saved MP4, app exit/restart, future-child behavior,
default-device/service loss, 30/60/300-second timing drift, AAC hot-path cost,
or NVIDIA video regression with live app stems.

Until that matrix is executed on a supported Windows 11 machine, the accurate
status is **CODE READY / WIN11 RUNTIME UNVERIFIED**. AUDIT-050 remains
**IMPLEMENTATION IN PROGRESS**.

## NATIVE MICROPHONE STEM + COMMON AUDIO CLOCK — 2026-08-23

### IMPLEMENTED

Windows capture now has `AudioSourceType::Microphone` and one native,
generation-local `WindowsMicrophoneAudioProvider`. It uses shared-mode,
event-driven WASAPI capture on `eCapture`; no Python `sounddevice`/PortAudio
stream is opened for normal Windows replay capture. The provider enumerates
stable Windows endpoint IDs, friendly names, default status, and device state.
The settings model persists `mic_device_id` in addition to the legacy friendly
name. A legacy name migrates only when exactly one active native endpoint has
that name.

An empty ID means **Default microphone** and resolves the current Windows
default at the next capture generation. A nonempty ID is explicit. If that
endpoint is missing, native capture fails only the microphone source with a
diagnostic; it does not silently bind a different device. A device selection or
gain change requests an engine restart, so a running microphone source never
switches endpoint or clock domain in place. Endpoint IDs remain runtime/local
settings data and are deliberately absent from portable MP4 metadata and the
sidecar manifest.

The provider reads the WASAPI mix format rather than assuming 48 kHz, float,
mono, or stereo. Float32, PCM16, and PCM32 input are converted exactly once by
`libswresample` to canonical 48 kHz stereo float. Capture/input gain is applied
once before encoding; it is not an editor/export gain. A persistent AAC-LC
packet ring then retains the microphone at 96 kb/s. The canonical stereo layout
is intentional even for mono voice: it preserves a stable future mixer/mux
format while using less bitrate than Default Mix's 128 kb/s. A 300-second mic
ring is roughly 3.4 MiB of compressed payload, not a 110 MiB raw float ring.

`audio_timeline` is the shared QPC-to-sample mapping for Default Mix,
Microphone, and the existing Windows 11 process-loopback provider. A WASAPI
packet's QPC timestamp is used directly. A driver that omits it is anchored
with the same `QueryPerformanceCounter` clock, never a Python/wall-clock
timeline. Default Mix also fills measured idle endpoint gaps with QPC-timed
silence, so microphone-only clips still contain the required Default Mix
stream. The microphone's `AudioDriftController` compares submitted samples to
QPC, applies `swr_set_compensation` at most every 500 ms, and limits correction
to 100 ppm. A source beginning late retains its signed presentation offset;
its audio is not shifted to the start of the video interval.

The native save path snapshots both sources against the exact AUDIT-042 video
presentation interval, muxes the two AAC streams inside the existing paired
MP4/manifest transaction, and labels them `Default Mix` (default disposition)
and `Microphone`. The manifest contains a semantic `microphone` identity,
source UUID, clip-local activity interval, stream index, and canonical format;
it contains no raw endpoint GUID.

**Compatibility-model decision.** In this stage `Default Mix` is the native
render-loopback/system stream and `Microphone` is an independent native stem.
It does **not** fold microphone samples into Default Mix. This is deliberate:
the approved source model requires independent stems, and adding a second
decode/mix/re-encode export path before the planned FFmpeg/QAudioSink mixer
would add unqualified latency/level behaviour and duplicate voice in the future
stem mixer. Consequently, an external player which selects only the default
audio stream will not hear microphone audio automatically. This is a known
product limitation, not an implicit compatibility claim; completing a
native compatibility mix remains a follow-up decision.

### AUTOMATED

- Release x64 engine and native test projects build with the production
  microphone provider. The test project reports **363 native checks**, including
  six microphone/timeline checks: signed late-source preservation, 100 ppm
  bounded correction, adjustment-window throttling, drift telemetry, and
  endpoint/manifest identity separation.
- The full Python suite passed on this Windows host after adding five native
  microphone-device parser/migration tests. Targeted post-route, capture
  settings, settings-manager, and PySide UI smoke coverage also passed.
- `compileall`, Ruff, shared-memory-v4, engine-response, exception, version,
  asset, and release-licence gates passed. The licence gate reported one
  existing warning only: Linux FFmpeg is not vendored on this Windows checkout.
- Existing Windows 11 process-loopback automated tests remain green; Windows
  10 build 19045 still does not activate that provider.

### PHYSICALLY VERIFIED ON THIS HOST

Host: Windows 10 Pro build 19045, Intel i9-9900K, NVIDIA RTX 4060 Ti, 1920x1080
monitor capture. Native enumeration found the active/default endpoint
`Mikrofon (8- fifine Microphone)` (stable ID retained locally). The saved native
streams are canonical 48 kHz stereo AAC; PortAudio's WASAPI view of this
endpoint reports 48 kHz and two input channels. The release engine binary used
for these runs was built from this worktree.

- A silent-system 8-second smoke produced video plus `Default Mix` and
  `Microphone`, a paired manifest, and a successful full FFmpeg decode.
- A controlled 440 Hz system-output tone plus default FIFINE microphone made a
  valid 30-second H.264 clip: two AAC streams, correct labels/default
  disposition, manifest, atomic publication, and full decode.
- A 60-second H.264 clip had two 59.968-second AAC streams under a 60-second
  video stream. Manifest source time ranges showed a 16.6 ms initial packet
  boundary offset and the same 16.6 ms offset at the end: no additional
  measurable mic-to-default drift over the minute. AAC timestamps are
  quantized to 1024-sample packets (21.33 ms), so this is a timeline bound,
  not a speech-sync listening claim.
- Separate 30-second HEVC/NVENC and AV1/NVENC clips also contained both AAC
  streams, manifest sidecars, and fully decoded successfully.

The host delivered roughly 28.6-28.9 captured video frames/s while configured
for 60 fps in these desktop qualification runs. Therefore the codec results
prove native encoder/mux coexistence with microphone capture, **not** a 60-fps
gaming-performance pass. No claim is made that this is caused by the new audio
provider without a Default-Mix-only comparison.

### UNVERIFIED / OPEN

- 300-second drift, mic input 44.1 kHz conversion, deterministic mono-device
  qualification, real speech intelligibility/clipping, and 440 Hz versus 880
  Hz digital-isolation analysis were not run.
- Selected-device disconnect/reconnect, default-device change, rapid saves,
  CPU/working-set/save-latency comparison, and live-game 60-fps regression are
  not qualified.
- The Windows 11 process-loopback provider remains automated-only; physical
  app-stem qualification requires a supported Windows 11 host.
- Linux PipeWire and the FFmpeg/QAudioSink multi-track playback mixer remain
  outside this phase. Shared Memory stays v4.

AUDIT-050 remains **IMPLEMENTATION IN PROGRESS**. Native microphone capture is
**EXPERIMENTAL** until the remaining device/performance matrix and the Default
Mix compatibility decision are closed. The common audio clock is **QUALIFIED
for the 60-second Windows-10 observation only**, not for 300 seconds or all
devices.

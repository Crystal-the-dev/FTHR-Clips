# Recording, finalization and game-focus recovery — 16 September 2026

These changes build on the reliability work already present in the checkout.
Existing uncommitted library, playback and capture changes were preserved.

## Manual recording

The saved session `session-20260915T182426Z-90383ddc` contains repeated
`nvEncLockInputBuffer: INVALID_PARAM` errors after 12 frames. The source window
was 1280×976, while the recording profile requested 1920×1080. One recording
request arrived after capture had already failed; another started but closed
with zero video packets. This was an encoder-input problem, not an MP4 header
failure.

A synthetic NVIDIA test reproduced the same failure at frame 12 before the
fix. It also exposed a driver shutdown hang after the invalid submission;
that isolated baseline test process was terminated. No desktop capture was
needed for this reproduction.

Changes:

- Always allocate hybrid NVIDIA input buffers at the encoded resolution.
  Resize source BGRA into that buffer with the existing FFmpeg swscale
  dependency. Matching dimensions retain the direct row-copy path. FIT clears
  letterbox bars; STRETCH fills the output.
- Use the existing GPU video processor whenever NVIDIA GPU input needs a
  size change, including STRETCH. Do not assume NVENC scales source surfaces.
- Validate CPU input/pitch and propagate lock/unlock/scaling errors.
- Read the recording stream configuration from the synchronized replay ring.
  Wait at most three seconds for a codec that publishes configuration with
  its first packet, instead of immediately rejecting a quality-profile start.
- Request a keyframe when a recording starts. NVIDIA, AMD and Intel backends
  consume the request on their submission thread, so a new recording need not
  wait for the next periodic GOP. NVIDIA's explicit periodic IDR request now
  agrees with its existing one-second GOP configuration.
- Resolve recording and library folders to absolute paths after expanding
  environment variables and `~`. The logs contained a literal `%USERPROFILE%`
  recording destination. Recording and Open Recordings now use one resolver.
- After the native close acknowledgement, manual recording only probes the
  completed fragmented MP4. It no longer scans every packet and potentially
  re-encodes an entire long recording. Probe timeouts now end finalization with
  an error and retain the file, rather than leaving the UI stuck.

Primary files: `hardware_encoder.cpp/.h`, `replay_encoder.h`, the AMF/QSV
wrappers, `encoded_ring_buffer.cpp/.h`, `capture_engine.cpp`,
`FTHR_UI/core/settings_manager.py`, and `FTHR_UI/main.py`.

## Clip finalization

Native replay already stores encoded packets and muxes them at save time.
The Python finalizer nevertheless required every sample to have precisely
  the configured frame duration. Ordinary bounded capture timing variation
could therefore trigger a full video decode/re-encode after the native save.

Changes:

- Accept healthy variable-rate packet timing without rewriting video. This
  preserves source codec, quality, wall-clock duration and existing audio.
  **Constant frame rate is no longer mandatory for an otherwise healthy saved
  clip.** Explicit export processing remains available separately.
- Keep rejection of non-monotonic timestamps and large replay gaps. The
  existing physical gap threshold remains `max(250 ms, four frame intervals)`.
  Source timing still has to pass validation before optional visual processing.
  The bounded CFR repair fallback remains for inconclusive final output evidence.
- Reuse packet-probe summaries between source validation and publication when
  path, size, modification time, creation/change time and requested FPS match.
  Keep at most 32 small summaries; do not cache failed/inconclusive probes.
  Any file rewrite invalidates reuse.
- Remove redundant file-size-stabilization waits from visual and microphone
  workers. These workers run after `CLIP_SAVED`, which already follows native
  close and atomic publication. Missing/empty files still fail validation.

Measurement on this machine: a 10.167-second H.264 test clip, derived from a
10-second capture by adding one-frame timing gaps, took **2.603 s before** and
**0.171 s after** in the timing/finalization step. Before: video was re-encoded;
after: the original 6,428,106-byte file was preserved. This controlled fixture
demonstrates the avoided transcode, not a universal end-to-end save benchmark.
An already-CFR clip took approximately 0.18 s in both versions.
Enabled visual overlays/cropping still require their existing processing;
their settings and output behavior were preserved.

Primary files: `FTHR_UI/main.py`, `FTHR_UI/core/media_metadata.py`.

## Protected-game focus recovery

Protected games use monitor capture gated on game focus. Previously, tabbing
out deliberately stopped video submission, but the replay watchdog continued
its two-second stall countdown and could stop capture permanently. Simply
ignoring that countdown was insufficient: retaining both sides of the pause
would leave a timestamp hole that replay validation rejects.

Changes:

- Suspend the stall watchdog during an intentional focus pause, as during
  backend recovery. Resume its normal bounded monitoring afterward.
- Apply focus state before checking for an available texture, so an idle
  frame pool cannot prevent pause/resume state from being updated.
- Continue releasing queued frame-pool frames while paused, without encoding
  off-game desktop images. Discard queued images on the transition back too.
- On focus return, clear old replay history, advance the capture generation,
  and request a keyframe. Capture resumes from new game frames with a fresh
  timeline; there is a short replay refill period instead of a clip spanning
  the alt-tab gap. Manual recordings retain their elapsed-time timestamps.
- Keep protected-game detection and capture backend selection intact.

Primary files: `capture_engine.cpp`, `capture_focus_policy.h`.

## Validation

- Windows Release production engine build: passed. Isolated output:
  `D:\FTHR-validation\recording-20260916\engine\FTHRclips.exe`.
- Native suite: passed, reporting **7,966 checks**, plus the focus-transition
  and deferred-configuration scenarios. Includes fragmented MP4 crash recovery,
  video/AAC playback, repeated pauses/resumes, bounded configuration waiting,
  and AMD/Intel keyframe-request behavior through fake backend sessions.
- Eight physical NVIDIA scenarios: hybrid CPU input and same-device GPU
  input; upscale STRETCH/FIT, downscale, and unchanged dimensions. Each submits
  180 frames, starts recording at frame 31, observes an immediate keyframe,
  records 149 packets, closes successfully and fully decodes. Pixel checks
  also verified FIT bars and STRETCH output on both paths.
- Live monitor capture with audio: manual start/stop passed, **484 video and
  378 AAC packets**, native close **1 ms**; replay continued and its subsequent
  save also fully decoded.
- Full Python suite: **954 passed, 35 skipped, 2 failed**. Both failures are
  existing uploader bundle/hash mismatches:
  `test_built_dormant_packages_match_core_release_bindings` and
  `test_built_one_shot_packages_activate_and_run_locally`. The same failures
  occur in the earlier `build/reliability-full-tests.log`. The uploader
  packages and expected hashes were not changed for this task.
- Ruff on changed Python files; shared-memory, response-publication and
  exception-handling contracts; whitespace diff check: passed.

Repeat the synthetic recording checks without capturing the desktop:

```powershell
.venv/Scripts/python.exe tools/run_windows_recording_smoke.py --output D:/FTHR-validation/recording-check
```

Repeat the live capture/manual-recording check with the desired monitor's
stable device path:

```powershell
.venv/Scripts/python.exe tools/run_windows_capture_smoke.py --monitor '<monitor-device-path>' --output D:/FTHR-validation/live-recording-check --cycles 1 --manual
```

Logs and test media for this run are outside the repository at
`D:\FTHR-validation\recording-20260916`. They include local captured content
and are not release artifacts.

Live Valorant/Vanguard alt-tab behavior, AMD/Intel hardware recording, and a
long-duration recording soak were **not** physically verified. The focus
transitions and watchdog policy were covered by native tests. No installer
was regenerated and the installed application was not replaced.

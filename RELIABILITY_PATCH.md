# Clip library, playback and capture reliability

## Scope and evidence — 15 September 2026

This patch builds on the uncommitted library-index, capture-health and encoder
work already present in the checkout. Those changes were preserved. The work
here addresses the reported second-open library failure, slow seeking/resume,
and capture failing before a replay can be saved.

Evidence was collected on this Windows machine: an Intel-owned 1920×1080
display and NVIDIA RTX 3050 Laptop GPU, using the existing explicit hybrid
NVENC CPU-input path. Local evidence is in `D:\FTHR-validation` and the
checkout's ignored `build/reliability-*.log` files. These contain local capture
data and should not be published automatically.

## Findings and changes

### 1. Library access depended on optional thumbnail work

`ClipThumbnail` delayed opening a completed video until its thumbnail worker
finished. Cancelling background work could therefore prevent access to valid
media. Thumbnail jobs have generations and fingerprints, but their signal
senders also need to survive until queued completion is delivered.

Changes:

- Completed clips open immediately, with a placeholder if needed. Actual clip
  finalization remains the access gate. Enrichment never authorizes an
  unfinished file.
- Retain each thumbnail worker until its terminal callback releases ownership.
  A cancelled generation cannot retire or overwrite its replacement.
- Retry failed enrichment during later scans, including when the index has
  not changed. Failure markers expire after 30 seconds instead of poisoning an
  unchanged file indefinitely.
- Keep successfully probed metadata even when image decoding fails. Unknown
  duration displays as `--:--`; malformed metadata is a cache miss.
- Preserve source aspect ratio when generating thumbnails. Cache generation
  `aspect-v4` invalidates previously distorted images.

The real-thread regression test cancels a running probe, resumes the grid,
rebuilds it repeatedly, checks thumbnail/duration recovery and confirms GUI
updates arrive on the GUI thread. Opening before enrichment and remaining
closed during finalization are tested separately.

### 2. Long GOPs made seek and resume expensive

One affected 60-second H.264 clip contained only its initial keyframe. A GOP is
the sequence of dependent frames following a keyframe. Seeking near the end
required decoding almost the whole clip. The Windows Media Foundation backend
repeated much of that work when resuming. This reproduced outside FTHR in a
minimal Qt player, isolating the media layout from editor controls.

The shared Python FFmpeg encoding helpers did not specify a GOP limit, leaving
OpenH264 free to produce this layout. Native encoders had a four-second limit.

Changes:

- All shared H.264 transcode helpers now request a maximum 30-frame GOP with
  no B-frames. Native NVENC, AMF and QSV replay configuration uses one-second
  GOPs and publishes the same limit in stream configuration.
- Older SDR clips up to five minutes long receive an optional background
  preview when an eight-second packet sample proves a keyframe gap over about
  two seconds. The preview keeps source dimensions and audio tracks, uses
  16 Mbps H.264, and is validated for duration before atomic publication.
- Original playback remains available during preparation. A completed preview
  is adopted while paused, preserving the position, and reused on reopening.
  Original paths remain authoritative for exports, edits, metadata and deletion.
  This does **not** re-encode or replace the user's original media.
- Preview work is cancellable, has a 120-second deadline, and fails back to
  original playback. The disposable cache in `~/.fthr/playback` retains up to
  eight previews / 2 GiB, subject to files currently locked by another reader.
  HDR and longer imported recordings retain original playback.
- The timeline filmstrip now uses one sequential, cancellable FFmpeg decode
  instead of up to 120 independent OpenCV seeks. Seeking and playing cancel
  that optional work. Grid, filmstrip and preview preparation share one media
  subprocess runner with terminate/kill/reap cleanup.
- A Play click flushes the latest debounced seek before starting playback,
  avoiding a start immediately followed by another pipeline flush.

Measured on the affected clip, with the same Windows backend:

| Measurement | Original long GOP | Seek-friendly copy |
| --- | ---: | ---: |
| Minimal player: seek to a displayed frame | 664–2,212 ms | 11–19 ms |
| Minimal player: resume to an advancing frame | 695–2,266 ms | 42–50 ms |
| Actual patched editor, including 50 ms seek debounce | — | approximately 67 ms seek, 44–52 ms resume |

The initial background preview took approximately 24.5 seconds for this
60-second clip. Later opens reuse it. These are measured examples, not latency
guarantees for every codec, device or storage location. Media Foundation was
retained: the checkout documents resource growth in an earlier FFmpeg-backend
trial, and changing the media layout solved the reproduced delay without a
backend migration.

### 3. Idle WGC capture was mistaken for failure

The session log showed exactly two acquired/encoded frames, successful NVENC
completion and recycling, then `CAPTURE_FRAME_STALLED`. Increasing the wait
robustness and explicitly closing frames did **not** fix this alone.

A moving test source produced frames normally. When it stopped moving, capture
stopped producing images and the two-second watchdog failed the backend. WGC
image arrival was incorrectly being used as the continuous video clock.

The new WGC loop has one owner and one clock:

1. Wake at the requested frame cadence, interruptibly for shutdown/source loss.
2. Drain the two-slot pool and copy the newest image into one owned GPU texture.
3. Close every borrowed frame, including on errors or early exits.
4. Encode the owned image at the current timestamp, reusing it on unchanged
   desktop ticks. No borrowed WGC surface survives between ticks.

This removes the FrameArrived callback and coalesced Boolean notification as
timing dependencies. Encoder stalls still fail; device loss, item closure and
dimension changes still stop the generation. Focus-gated capture discards its
cached image while unfocused and requires a new image on re-entry.

`frames_captured` now counts WGC video-clock submissions, including intentional
unchanged-image repeats; it must not be interpreted as a count of distinct
desktop updates. `source_textures_received` counts actual received textures.
The WGC stop log reports repeated idle frames separately. A silent driver that
keeps returning no frames without signaling device/item loss cannot be
distinguished from an unchanged desktop by this API alone.

The pattern follows the separation of received image and rendered video used
by [OBS's WGC implementation](https://github.com/obsproject/obs-studio/blob/master/libobs-winrt/winrt-capture.cpp).
Microsoft documents [frame-pool ownership and manual polling](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture).

## Validation

- Native Windows test executable: **7,960 checks passed**, plus the new frame
  lease exit-path assertions in that executable.
- Python suite: **948 passed, 35 skipped, 2 deselected**. The excluded tests are
  the existing optional-uploader artifact binding/integrity failures:
  `test_built_dormant_packages_match_core_release_bindings` and
  `test_built_one_shot_packages_activate_and_run_locally`. A full initial run
  confirmed these failures. Optional package binaries/release bindings were
  not changed by this patch.
- Ruff, shared-memory ABI, response-publication and exception-handling gates
  passed. The exception baseline was not increased.
- Real WGC static capture stayed healthy at approximately 60 FPS and saved a
  10-second replay. Full video/audio decode succeeded. Three subsequent native
  initialize/capture/save/shutdown cycles also succeeded.
- The repeatable isolated smoke tool built from the final source, saved a clip
  and fully decoded it. It uses a local response structure and does not attach
  to the running application's shared-memory connection.
- The actual editor was exercised on the older clip using its generated preview.
- An idle → moving → idle source transition stayed healthy and saved correctly;
  sampled output contained the changing images, then settled again. The old
  60-second clip also produced all 24 filmstrip images through the new single
  sequential decode (about 19 seconds while other verification was running).
- CodeRabbit could not run: `coderabbit` was unavailable, and its installer
  could not run because `bash` was not found on PATH. No CodeRabbit review is
  claimed. Install/authenticate its CLI in a supported shell to run that step.

Remaining qualification: this is local NVIDIA/Intel-hybrid verification, not a
full hardware matrix, long soak, packaged-installer or live Bluetooth audio
latency qualification. Visual resume timings measure delivered video frames,
not when sound physically reaches headphones. Other native capture backends
were not rewritten.

## Reproduce and use

The updated development backend is built at
`FTHRcapture/x64/Release/FTHRclips.exe`. **Restart FTHR** to load the changed
Python code and newest backend; the already-running process keeps its old code.

```powershell
# Run from the repository using its existing virtual environment.
.venv\Scripts\python.exe -m pytest tests/test_library_reliability.py tests/test_playback_proxy.py
.venv\Scripts\python.exe tools/measure_playback_latency.py 'C:\path\clip.mp4' --editor --output D:\FTHR-validation\editor.json

# Explicit monitor identity avoids accidentally testing another display.
$captureSettings = Get-Content "$env:USERPROFILE\.fthr\settings.json" | ConvertFrom-Json
.venv\Scripts\python.exe tools/run_windows_capture_smoke.py --monitor $captureSettings.capture_monitor --output D:\FTHR-validation\smoke-retest --cycles 3
```

The smoke tool captures the selected monitor and system audio into local test
files. It never sends media externally. Preview cache files can be removed
while the editor is closed; they regenerate as needed.

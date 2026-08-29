# Clip editor playback performance

Profile date: 2026-08-25

## Scope and targets

FTHR Clips is a native PySide6 application. Browser/React profiling does not
apply; the measured path is `QMediaPlayer -> QVideoSink -> LiveVideoPreview`,
the timeline decoder, and the optional native FFmpeg audio mixer.

The release targets for a reasonable mid-range Windows development machine are:

- cold clip-open to playback-ready: at most 1,000 ms;
- warm clip-open to playback-ready: at most 400 ms;
- no recurring GUI-thread stall over 50 ms during playback;
- deliver every decoded source frame at the clip's timestamps;
- full-resolution 1080p color/effect processing below 12 ms median;
- exactly one active playhead UI timer, active only while playing;
- no timeline, metadata, or replacement audio decoder starting during playback;
- native handles and private memory reach a plateau during repeated open/play/close;
- image preview cache remains bounded to 16 million pixels and timeline cache to
  eight filmstrips.

The effect preview may reduce only its processing surface after three measured
effect frames exceed 14 ms. It targets 12 ms and periodically probes upward
after 60 fast frames. Export is always full resolution. This is a measured,
effect-only fallback, not a global resolution or frame-rate reduction.

## Profile fixture and machine

- Windows 11 10.0.26100
- Intel Core i7-11800H, 8 cores / 16 logical processors
- NVIDIA RTX 3050 Laptop GPU plus Intel UHD Graphics
- 32 GB RAM
- PySide6 6.11.1 / Qt 6.11
- fixture: H.264/AAC MP4, 1280x832, 143 decoded frames over 3 seconds
  (effective 47.67 fps; 60 tbr)

Camera and image overlays in recorded clips are already burned into the media,
so editor playback cost is the same as an ordinary H.264 frame. Their live
settings previews were profiled separately with five simultaneous image layers
and a camera pixmap.

## Root causes

1. The filmstrip opened a second OpenCV/FFmpeg decoder immediately and issued
   24 random seeks while `QMediaPlayer` was opening or already playing.
2. A one-track clip unnecessarily opened the native editable-audio mixer,
   doubling demux and decode work during startup.
3. Neutral frames were copied into a smoothly scaled `QImage` on every paint.
4. The NumPy float effect pipeline allocated several full-frame arrays and took
   53 ms for color or 67 ms with sharpness at 1280x720.
5. The playhead timer ran continuously, including while paused.
6. Player/audio objects lacked Qt parent ownership, teardown did not detach the
   video output, audio output, and media source, and background preparation did
   not have a shared cancellation token.
7. Image overlays were decoded again on every settings refresh and each paint
   created temporary scaled pixmaps. Re-selecting the active camera restarted
   the same native device. The settings camera-preview timer also kept
   converting frames every 100 ms after the settings page was hidden.
8. Qt 6.11's default Windows FFmpeg multimedia plugin retained about one native
   handle per source cycle in the repeated-switch profile. FTHR therefore uses
   the available Windows Media Foundation backend for this release. Qt marks
   that backend deprecated as of Qt 6.10, so moving back to FFmpeg after its
   source-cycle leak is fixed is a tracked compatibility limitation.
9. A pause scheduled the filmstrip decoder after only 300 ms and immediately
   prepared any late audio mixer. A quick resume could therefore compete with
   fresh decoder work. Media Foundation could also briefly report position zero
   during resume even though the decoded video stayed at the paused frame.

## Fix

- Play is queued until media and audio inspection are ready, with a visible
  `PREPARING` state and a one-second bounded audio-probe fallback.
- Filmstrips start after readiness only while idle, cancel cooperatively on Play
  or close, and reuse an eight-entry decoded-image LRU.
- Pause now has a 1.5-second idle grace period before deferred decoder work.
  Resume cancels that work, and a short display guard filters Media Foundation's
  transient zero-position report without seeking or changing the video clock.
- A single direct audio track uses `QAudioOutput` for source and master gain;
  the custom mixer is reserved for real multi-source composition.
- The playhead timer starts on `PlayingState` and stops on pause/stop. Paused
  seeks use `positionChanged`, so there is one playback UI loop.
- Neutral frames draw directly through `QPainter`. Effect math is one affine
  OpenCV transform plus one optional blur/weighted add.
- Multimedia objects are parented to the dialog and teardown is idempotent:
  preparation is cancelled, timers stop, mixer stops, outputs detach, the
  source is cleared, and retained frame buffers are released.
- Crop, stretch, color, trim, split, and deleted-segment edits are stored as a
  debounced per-clip JSON draft. The tiny snapshot is atomically flushed on
  close, restored on reopen, and ignored if the source file identity changed;
  no draft I/O runs from the playback or frame-processing loop. Draft storage
  is capped at the 250 most recently edited clips without pruning clip details.
- Image preview decode uses a file-versioned LRU, large images are decoded to a
  preview-bound size, and `QPainter` scales from the cached pixmap without a
  temporary allocation. Camera preview conversion is display-bounded and a
  start request for the already-running device is a no-op. The preview timer
  stops outside the visible settings page and application shutdown releases
  the recorder.

## Measurements

| Measurement | Before | After |
|---|---:|---:|
| Immediate-play first decoded frame, default backend | 444 ms | 221 ms with FFmpeg fixes; 753-835 ms cold / about 300 ms warm on production WMF |
| Largest 5 ms heartbeat interval, cold immediate play | 226-232 ms | 101 ms cold / 56 ms warm on WMF |
| Recurring heartbeat intervals over 50 ms | 3 | 0 after startup |
| Delivered frames for the 3 s fixture | 143-144 | 142-143 (all source frames; backend end-frame reporting differs by one) |
| 1280x720 color processing median | 53 ms | 1.2 ms |
| 1280x720 color + sharpness median | 67 ms | 3.1 ms |
| 1920x1080 color + sharpness median | 154 ms | 6.7 ms |
| Effect output difference versus old math | reference | mean 0.21 / 255, p99 1 / 255, max 2 / 255 |
| Repeated source handles | FFmpeg grew each cycle (521 -> 546 over 25 cycles) | WMF plateaued by cycle 9-12 |
| Repeated source private memory | FFmpeg reached 666 MB and was still growing at cycle 30 | WMF plateaued around 744 MB by cycle 12-15 |

The process CPU sample during repeated 0.9-second playback cycles was about 36%
of one core (2.3% of the 16-logical-processor machine) on the FFmpeg profile.
Reliable per-process GPU utilization was not exposed to this test harness;
frame delivery and GUI heartbeat were used as the GPU-path outcome measures.

The higher stable WMF memory baseline is a deliberate release tradeoff for
bounded resource use. The backend can be overridden with `QT_MEDIA_BACKEND`
for diagnostics. Qt's documented backend-selection and deprecation notes are at
<https://doc.qt.io/qt-6/qtmultimedia-index.html>.

## Automated coverage

`tests/test_video_playback_performance.py` covers:

- one playback UI timer and idle-only filmstrip work;
- quick-resume idle grace, transient position-reset filtering, and deferred
  audio preparation;
- debounced editor-draft persistence and reopen restoration;
- single-track decoder reuse;
- source/output/timer/callback cleanup and idempotent teardown;
- filmstrip cache reuse across clip switches;
- decode-once image overlays and five-layer composition;
- bounded large-image preview decode;
- same-device camera start idempotence and no hidden camera-preview work;
- a full 1280x720 effect-frame performance smoke budget.

Existing editor, audio mix/export, overlay burn-in, camera recorder, seeking,
timeline edit, and transactional export tests remain part of the regression run.

## Remaining limitations

- Windows Media Foundation is deprecated in Qt 6.10+ and is expected to be
  removed in Qt 7. The application must re-qualify Qt's FFmpeg backend before a
  Qt major upgrade, including the repeated source-cycle test.
- Physical camera permission denial, device removal, resolution renegotiation,
  and GPU utilization still require hardware QA; unit tests use deterministic
  fake capture devices.
- The 4K adaptive effect path is covered by the measured controller and pure
  effect benchmark, but final visual/GPU qualification should include the
  supported NVIDIA, AMD, and Intel hardware matrix.

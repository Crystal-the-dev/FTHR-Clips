# AUDIT-042 — Short-Save / Clip Duration Correctness

Date: 2026-08-14

Branch: `fix/audit-042-short-save-duration`

Baseline: `6f01075d03c53350ef5b85ae4caa9418830fcaa1`

## Status

| Claim | Status |
|---|---|
| Code fixed | **YES** |
| Automated tested | **YES** |
| Windows runtime verified | **YES — WGC + NVENC, video-only** |
| Linux runtime verified | **NO — WSL/headless synthetic only** |

**AUDIT-042 is resolved for full-history replay saves.** When valid decodable
history covers the requested interval, the visible MP4 duration now matches the
request within one nominal frame; the real Windows samples below were exact.
Partial history is reported and saved as partial media rather than confused with
a full-history short-save failure.

## Original symptom and reconstruction

The capture-health runtime documented in
`docs/AUDIT-022-023-035-CAPTURE-HEALTH.md` requested a healthy five-second
Windows clip but produced only **3.533 seconds / 91 frames**. That is 1.467
seconds short and a clear P1 failure.

The behavior was reproduced before product edits with the committed Windows
ring and mux selection rules:

```text
snapshot_packets=150
discarded_before_first_keyframe=59
written_packets=91
first_pts=280
last_pts=385
calculated_mux_duration_s=3.53333
```

The relevant behavior entered with the initial source import (`96d2d0f`). No
older test, release-checklist entry, known-issue entry, or dedicated AUDIT-042
document existed; the capture-health audit was the sole committed finding.

## Exact root cause

The Windows NVENC path combined three rules that could not satisfy the replay
contract:

1. `SaveClip` asked the encoded ring for exactly `duration * configured_fps`
   packets and the ring also discarded the newest configured second.
2. NVENC used a four-second frame-count GOP.
3. The muxer advanced to the first keyframe **at or after** the requested
   cutoff. The snapshot did not contain the preceding keyframe, so up to almost
   one GOP of requested media was thrown away.

Packet count was also not media time. In the verified Windows run the target was
60 FPS but capture delivered about 45 FPS. A 1,800-packet selection would not
represent 30 seconds.

Linux already retained a preceding keyframe, but had a separate latent duration
bug: encoder PTS was `next_pts_++`. Dropped or delayed captures therefore became
short CFR media. The wall timestamp attached to a delayed output packet was also
the newest input timestamp rather than the timestamp of that packet's input
frame.

The Windows raw/OpenH264 fallback does not share the compressed-ring keyframe
bug. It emits one CFR packet for every selected BGRA frame, so full physical
history produces the requested output duration. Its 2 GiB pool can physically
retain only a few seconds at large resolutions; that remains a separate
capacity/architecture limitation, not AUDIT-042.

## Save contract and timestamp model

`T_save` is the monotonic time at which the native engine accepts `SAVE_CLIP`.
For requested duration `D`, full history represents approximately:

```text
[T_save - D, T_save]
```

Capture timestamps, not packet count, are authoritative.

| Layer | Windows | Linux |
|---|---|---|
| UI request | integer seconds; normal/extended/custom values are passed unchanged | same |
| Capture clock | QPC from WGC/DXGI or `QueryPerformanceCounter` fallback | `CLOCK_MONOTONIC` nanoseconds from Wayland/X11 backend |
| Encoder PTS | elapsed QPC converted to `1/fps`, monotonic-clamped | elapsed monotonic nanoseconds converted to `1/fps`, monotonic-clamped |
| Packet wall time | originating frame QPC retained through async NVENC drain | originating input timestamp matched back to delayed encoder packet PTS |
| Ring retention | ordered sequence-numbered encoded slots | monotonic-time deque, configured history plus five seconds |
| Save end | engine-accepted QPC; bounded wait for publication | engine-accepted monotonic time; bounded wait for publication |
| Video selection | timestamp interval plus preceding keyframe | same shared selection contract |
| Audio selection | WASAPI QPC interval ending at the video boundary | monotonic audio interval ending at the video boundary |
| Mux | logical cutoff is PTS zero; older decoder pre-roll stays negative | same |

Windows waits at most `max(100 ms, 3 frame periods)` for an already-captured
encoder output to reach `T_save`; Linux uses the same bound. A timeout never
blocks the UI thread and falls back to the latest published packet at or before
the fixed boundary.

## New selection and keyframe behavior

The small shared helper performs one ordered timestamp scan:

1. fix the requested end and cutoff;
2. reject packets after the fixed end;
3. find the latest keyframe at or before the cutoff;
4. classify the interval as full or partial history;
5. copy from that physical decode point through the bounded end;
6. preserve the logical presentation cutoff separately.

The physical MP4 can therefore contain older H.264 packets. Their PTS/DTS are
negative relative to the visible cutoff, negative timestamps are explicitly
preserved, and the MP4 edit list presents from zero. This provides decoder
pre-roll without re-encoding or showing extra footage.

The configured GOP is still four seconds on Windows and two seconds on Linux,
but frame-count GOPs can stretch in wall time when capture drops. Windows now
forces an IDR at most every four media seconds. Linux requests a keyframe at
most every two media seconds and enables NVENC `forced-idr`; the irregular-time
encoder test verifies the bound. The final real Windows sample contained nine
keyframes and a maximum measured keyframe gap of **4.016666 seconds** (one-frame
quantization).

No replay-ring capacity was increased. The existing compressed capacities are
already sufficient for the bounded pre-roll, so the fix adds no continuous
encoded-memory cost.

## Partial history and recovery

If the replay generation does not reach `T_save - D`, the save uses all
decodable media in the current generation and marks the snapshot internally as
partial. It does not invent frames or call that outcome a full 30-second clip.

Recovery clears the ring. Tests establish that a post-recovery save contains
only the new generation and is classified partial until it warms. No IPC field,
enum, mapping name, or shared-memory layout was changed to expose this internal
classification.

## Audio behavior

Desktop audio uses the same requested presentation end as video. The obsolete
0.5-second artificial PCM trim was removed because the ring copy is already
mutex-protected. The Python microphone ring now also ends at its accepted save
instant rather than applying that obsolete shift.

Audio is intersected with the video presentation interval:

- early audio is trimmed to the video start;
- late audio retains a positive stream offset;
- short audio remains short and never deletes valid video;
- absent desktop audio produces video-only output;
- absent microphone does not affect the native desktop-audio interval;
- missing WASAPI QPC metadata falls back to available PCM instead of silently
  producing no audio.

No claim is made here that every AUDIT-034 device/sync scenario is resolved.

## Automated coverage

The ten native CTest targets cover the required 30 cases:

| Required cases | Automated evidence |
|---|---|
| 1–6 durations and 30/60/120 FPS | shared replay interval and raw fallback tests |
| 7–11 exact/adjacent keyframes, long/short GOP | shared replay interval tests |
| 12–14 startup, five-second history, recovery | shared and both platform ring tests |
| 15–18 variable timing, dropped frame, delayed output, non-zero epoch | shared ring and real Linux encoder tests |
| 19–22 arrival race, exact capacity, extra history, wrap | shared replay interval tests |
| 23–24 rapid independent/non-mutating saves | shared replay interval plus real Windows saves |
| 25–28 aligned, short, no microphone, no desktop audio | Windows PCM interval tests |
| 29 duration-correct `.partial` commit | actual synthetic MP4 test through production transactional save |
| 30 mux/writer failure never publishes final | transactional-save native test |

Native result: **10/10 passed**. Full Python result: **396 collected, 366
passed, 30 platform skips, 0 failed/errors**. The focused save-state,
transaction, response, shared-memory, and capture-health group was **85 passed,
2 skipped**.

## Actual MP4 and ffprobe evidence

The pinned LGPL FFmpeg integration test encodes 12 seconds of H.264, selects a
five-second replay with an older keyframe, writes through `.mp4.partial` and the
production transactional rename, reopens the MP4, and fully decodes it.

```text
video stream start_time = 0.000000
video stream duration   = 5.000000
format start_time       = 0.000000
format duration         = 5.000000
first physical packet   = -0.333333, key+discard
full decode             = success, no diagnostics
```

Real Windows desktop runtime used WGC at 1920x1080, scaled to 1280x720, NVIDIA
RTX 4060 Ti NVENC, GPU zero-copy, 60 FPS target, video-only, capture health
active, generation 1. Actual capture rate was approximately 45 FPS.

| Scenario | Requested | Available | Saved | Error | Result |
|---|---:|---:|---:|---:|---|
| Original AUDIT-042 reproduction | 5.000 s | >5 s | 3.533 s | -1.467 s | **old FAIL** |
| Synthetic actual MP4 | 5.000 s | 12.000 s | 5.000 s | 0.000 s | PASS |
| Windows normal A | 30.000 s | >65 s | 30.000 s | 0.000 s | PASS |
| Windows rapid second save | 30.000 s | >65 s | 30.000 s | 0.000 s | PASS |
| Windows normal B | 30.000 s | >65 s | 30.000 s | 0.000 s | PASS |
| Windows extended | 60.000 s | >65 s | 60.000 s | 0.000 s | PASS |
| Windows final forced-IDR regression | 30.000 s | >35 s | 30.000 s | 0.000 s | PASS |
| 30 s after 5 s warm-up | 30.000 s | about 5 s | about 5 s | N/A | expected partial history |
| 30 s after recovery + 5 s | 30.000 s | about 5 s | about 5 s | N/A | expected partial history |

All five real Windows MP4s reported stream and format `start_time=0`, fully
decoded with the pinned FFmpeg, and had last visible PTS within one frame of the
requested end. Physical keyframe pre-roll ranged from 0.267 to 4.483 seconds and
was marked key+discard.

**REAL LINUX CAPTURE DURATION NOT VERIFIED.** WSL/headless proves encoder,
selection, mux, transactional output, ffprobe duration and full decode, but not
a Wayland/X11 picture on a real compositor.

## Performance and lock impact

Selection is O(retained packets) for metadata and O(selected bytes) for the
required owned snapshot. No new continuous capture work exists beyond a small
timestamp/keyframe comparison.

A synthetic 16 Mbps, 60 FPS benchmark measured:

| Path/window | Copied data | Snapshot time |
|---|---:|---:|
| Windows 30 s + pre-roll | 66.0 MB | 54.0 ms |
| Windows 60 s + pre-roll | 122.0 MB | 74.3 ms |
| Linux 30 s + pre-roll | 62.0 MB | 24.9 ms |
| Linux 60 s + pre-roll | 122.0 MB | 53.7 ms |

Windows no longer holds one global ring mutex while copying; short per-slot
locks protect wraparound correctness. The real Windows saves completed command
to `CLIP_SAVED` in 42–82 ms while capture stayed active.

Linux still holds its existing ring mutex across the deep copy. The measured
54 ms 60-second case can block several 60 FPS pushes, so this is recorded as
**AUDIT-045 (P2, OPEN): Linux encoded snapshot copy can stall capture**. It is
not required for duration correctness and is intentionally not redesigned in
AUDIT-042.

## Build finding discovered during validation

The first actual-MP4 test exposed a distinct Release-build problem: CMake linked
the pinned FFmpeg 8.1 libraries but did not put their include directory on the
engine target, so Ubuntu system headers could be compiled against a different
ABI. The integration test reproduced invalid `AVCodecContext` fields and a
segmentation fault. `FTHRclips` and the FFmpeg-backed tests now use the pinned
`${FFMPEG_INCLUDE_DIRS}` as system includes.

This is **AUDIT-046 (P1, RESOLVED): Linux pinned FFmpeg header/library ABI
mismatch**. It is distinct from duration selection but was fixed before relying
on Linux MP4 evidence.

## Preserved invariants and remaining limitations

- AUDIT-011/015/017/018/019/021/028 response and transactional-save contracts
  remain green.
- AUDIT-022/023/035 health admission and generation resets remain intact.
- AUDIT-024 Wayland bounded dispatch remains green.
- No shared-memory UI/engine IPC contract or layout changed.
- Windows non-NVIDIA raw storage capacity remains a separate alpha limitation.
- Real Linux desktop duration/picture, Windows audio-device runtime, and long
  performance soak remain unverified.
- No new unresolved P0/P1 finding was introduced. AUDIT-046 was found and
  resolved; AUDIT-045 is P2 and remains open.

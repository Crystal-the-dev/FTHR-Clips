# AUDIT-028 — Transactional / Crash-Safe Clip Saves

## Status

**CODE FIXED / AUTOMATED TESTED. DESKTOP CAPTURE RUNTIME NOT FULLY VERIFIED.**

Both capture engines now write a clip to `final-name.mp4.partial` in the final
directory, completely finalize and close it, and atomically commit it without
replacing an existing destination. The Windows Release engine and Linux pinned
FFmpeg Release engine build successfully. A real Windows or Linux desktop clip
was not recorded in this environment, so this audit is not marked runtime
verified.

## Save-pipeline inspection

- `FTHR_UI/main.py::_save_clip` generates the final clip filename and sends the
  save command through the existing shared-memory bridge.
- Windows queues the command through `CaptureEngine::SaveClip`,
  `SaveClipQueue`, `SaveClipThread`, and `ProcessSaveClipTask`; the NVENC and raw
  paths open their FFmpeg output in `capture_engine.cpp` and
  `video_encoder.cpp`.
- Linux handles the command synchronously through `CaptureEngine::SaveClip`
  and `save_clip_to_file`; `save_clip.cpp` opens and muxes the output.
- Before AUDIT-028 both engines opened the final `.mp4` directly. Several
  packet-write, trailer, and close failures were ignored, so an interrupted or
  failed save could leave a corrupt final-looking clip.
- Disk discovery occurs in `ui/clip_grid.py`; saved-file post-processing starts
  in `main.py`; uploads enter through `core/upload_manager.py`; audio
  post-processing enters through `core/audio_mixer.py`. These boundaries now
  share explicit completed-file checks.

No shared-memory field, enum value, structure size, command flow, or response
consumer was changed.

## Transaction contract

The cross-platform helper in `FTHRcapture_common/transactional_save.h` owns the
filesystem transaction:

1. Reject an existing final destination or existing transaction path.
2. Derive `final-name.mp4.partial` in the same directory.
3. Invoke the platform writer with only the temporary path.
4. Require every packet write, encoder flush, trailer write, and output close to
   succeed.
5. Atomically rename the temporary path to the final path without replacement.
6. Only then allow the engine to publish `CLIP_SAVED`.

Windows commits with `MoveFileExW(..., MOVEFILE_WRITE_THROUGH)` and deliberately
omits `MOVEFILE_REPLACE_EXISTING`. Linux commits with `renameat2(...,
RENAME_NOREPLACE)`. A rename collision therefore cannot destroy another clip.
Both operations remain on the same filesystem and do not copy or double-buffer
the media.

If writing, finalization, closing, or renaming fails, the final `.mp4` is not
published. The helper attempts to remove the temporary file, keeps the original
save error as the primary diagnostic, and reports a cleanup failure separately.
The existing one-completion-response save state machine remains the sole IPC
completion path, preserving AUDIT-011/015/017/018/019/021 behavior.

The engine-success boundary remains intentionally narrow: `CLIP_SAVED` now
means the transactional media MP4 is committed. Existing Python
post-processing may still run afterward; AUDIT-027's broader "fully ready"
semantics are not redesigned here.

## Windows behavior

- NVENC and raw-frame saves are both wrapped by the shared transaction helper.
- FFmpeg is told explicitly to use the MP4 muxer because the physical output
  name ends in `.partial`.
- Packet allocation/writes, interleave flush, trailer, and `avio_closep` errors
  now fail the save.
- `VideoEncoder::Finalize()` reports encoder flush, audio drain/write, trailer,
  and close failures instead of returning implicit success.
- `ProcessSaveClipTask` returns success only after the atomic move; the worker
  publishes `CLIP_SAVED` only for that result.

## Linux behavior

- `save_clip_to_file` wraps its existing muxing implementation with the same
  transaction contract and explicitly selects the MP4 muxer.
- Video/audio packet writes, audio drain, trailer, and output close are checked.
- The detailed failure is returned through `CaptureEngine::SaveClip` and placed
  in the response payload before the single final response is published.
- The final commit uses the Linux no-replace atomic rename primitive.

## Crash recovery and Python safety

`FTHR_UI/core/clip_files.py` centralizes completed-media and partial-file
classification. On startup, before capture begins, FTHR removes only its own
`*_clip_from_*.mp4.partial` files older than 24 hours. Fresh files, symlinks,
and unrelated `.partial` names are left alone; cleanup failures are logged and
do not hide another error. Leftovers are never promoted to `.mp4`.

The library, thumbnail worker, recent-clip handling, post-processing entry
points, audio processing, manual uploads, retries, and interval uploads all
explicitly reject partial or non-final paths. Thus a partial cannot be decoded,
shown, processed, or uploaded merely because a scanner changes later.

## Tests added

- Native C++ fault injection for success ordering, writer failure, trailer
  failure, rename failure, cleanup failure, destination collision, missing
  temporary output, and real default-filesystem commits.
- Cross-platform source-contract tests proving both engines use the shared
  transaction and publish success after it completes.
- Python classification and conservative stale-cleanup tests.
- Library/thumbnail integration tests proving `.mp4.partial` is rejected before
  decoder use.
- Upload, post-processing, and audio-entry regression tests for partial paths.

## Validation performed

### Automated tests

- Focused Windows Python regression set: **171 passed, 1 skipped, 0 failed**.
  The skip is the native `g++` invocation; the identical native test was run
  separately with MSVC and through Linux CTest.
- Linux CTest: **2 passed, 0 failed** (`capture_recovery_policy` and
  `transactional_save_contract`).
- Full Python suite: **349 passed, 30 skipped, 5 failed** out of 384. All five
  failures are confined to the pre-existing, unrelated dirty
  `clip_metadata_manager.py` / `test_clip_metadata_manager.py` worktree edits;
  the current file calls `self._.read_text()`. No AUDIT-028 or save/IPC test
  failed, and those unrelated files were not changed by this audit.

### Verification gates

- Engine response contract: pass (46 files; ordering contract intact).
- Shared-memory contract: pass (29 fields; Windows 2736 bytes, Linux 4272
  bytes; enum and mapping checks intact).
- Exception-handling ratchet: pass (38 files; baseline 92).
- Ruff, version consistency, and repository hygiene: pass.
- Release-license gate under Linux: **101 checks, 0 failed, 0 warnings**.

### Builds and runtime

- Windows `Release|x64` MSBuild: success, 0 compiler errors; produced
  `FTHRcapture/x64/Release/FTHRclips.exe`.
- Linux Release CMake build using the repository's pinned FFmpeg root: success;
  engine and both native tests built.
- Linux linkage: required FFmpeg SONAMEs are present and RPATH is bundle-relative.
- Linux headless start: the engine initialized IPC/audio, detected that neither
  Wayland nor X11 capture was available, exhausted the existing bounded recovery
  attempts, and exited without hanging. WSL had no usable desktop capture
  backend, so no real Linux clip save was possible.
- No real Windows desktop clip was captured. The environment did not provide a
  controlled recording scene or an automated end-to-end capture harness, so no
  playback claim is made.

## Remaining limitations

- A hard-power-loss durability guarantee for directory metadata is
  filesystem-dependent; AUDIT-028 guarantees that no-replace commit happens
  only after the muxer is finalized and the file handle is closed.
- Linux requires a kernel/filesystem supporting `renameat2(RENAME_NOREPLACE)`;
  an unsupported atomic primitive fails the save safely instead of falling back
  to an overwrite-prone operation.
- Full real-desktop save/playback validation remains required on both release
  platforms before calling the runtime path verified.
- The unrelated dirty clip-metadata experiment prevents a fully green current
  worktree test run. It is not present in this audit's commits and is not a new
  product issue discovered by AUDIT-028, so no new audit number is assigned.

# Capture border compatibility update

Date: 2026-08-24

## Objective

Make the new build behave like the old FTHR build during capture: use WGC as
the normal backend, suppress the Windows privacy border before capture starts,
and keep the user experience free of a visible yellow outline.

## Follow-up investigation: why the border was still visible

The capture-policy source change was correct, but the app that was running did
not use the rebuilt engine. Source mode had two independent engine selectors:

- `launch_windows.bat` preferred
  `FTHRcapture/FTHRclips/x64/Release/FTHRclips.exe`; and
- `FTHR_UI/main.py` ignored that direct-project output and selected the older
  `FTHRcapture/x64/Release/FTHRClips.exe` compatibility output.

The running native process confirmed the second path was in use. Its SHA-256
was `F80B08653C63BDBECBEEA8BCE82D4B490E66AB7FB5E16A8115523E106B3CD2CB`,
the same hash embedded in the pre-change portable bundle. A binary string audit
also found the retired package/access-request policy in that executable,
including `GetCurrentPackageFullName`, `access_request_required`,
`capability_not_declared`, and `access_denied`.

That stale runtime, rather than another overlay or a second capture path, was
what kept the Windows yellow privacy border visible.

## What changed

### 1. Restored the old WGC border behavior

`CaptureEngine::ApplyCaptureBorderPolicy` now directly queries
`IGraphicsCaptureSession3` and calls:

```cpp
session3.IsBorderRequired(false);
```

This is performed for every new monitor or window WGC session, before
`StartCapture`, just as in the old implementation. The new build no longer
requires package identity, a manifest capability, or a `Borderless` access
request, so capture does not introduce a permission prompt.

### 2. Added truthful verification

After setting the property, the engine reads `IsBorderRequired()` back. WGC is
accepted only when all of these are true:

- the session interface exists;
- the property write succeeds; and
- the property reads back as `false`.

This preserves the old successful path while preventing a system border from
being knowingly started when Windows ignored or rejected the opt-out.

### 3. Kept a border-free fallback

If suppression cannot be confirmed, the engine closes the WGC session before
`StartCapture` and initializes DXGI Output Duplication instead. Since DXGI does
not create a WGC session, it does not produce the WGC privacy border.

The tradeoff is that DXGI can be less reliable for independent-flip or
anti-cheat-protected games. That fallback is therefore treated as an error
containment path, not the normal backend.

### 4. Simplified the policy contract

The border policy no longer models package identity, manifest capability, or
access-request state. It now models the runtime facts that determine whether
the old direct session opt-out actually worked:

- WGC versus non-WGC session;
- session interface availability;
- property write result; and
- property readback result.

The tests were updated to require a direct opt-out attempt for WGC, reject
failed or incomplete suppression, accept only a false readback, and ignore DXGI
sessions.

### 5. Corrected source-mode engine selection

`FTHR_UI/main.py` now checks the direct native-project Release and Debug
outputs before the older solution-level compatibility paths. This matches both
`FTHR.spec` and `launch_windows.bat`. The startup diagnostic now prints the
resolved absolute engine path, so two same-named binaries can no longer be
mistaken for one another in logs.

A Python contract test requires the direct-project Release candidate to remain
ahead of the compatibility Release candidate.

### 6. Refreshed the local portable engine

The canonical direct-project Release engine was rebuilt and copied into
`dist/FTHRClips/_internal/engine/FTHRClips.exe`. Both files now have SHA-256
`9380BF1A634852CC60B5D32A5EE77668F3E70A3317F28B18DAE3E33D508513C1`.

The already generated installer under `Output/` still predates this correction
and must not be used to verify it. A full installer rebuild was attempted, but
the release pipeline correctly stopped at unrelated asset-provenance failures
before PyInstaller or Inno Setup ran. Those assets were not re-approved as part
of this capture-border change.

## Files changed

- `FTHRcapture/FTHRclips/src/capture_engine.cpp`
- `FTHRcapture/FTHRclips/include/capture_engine.h`
- `FTHRcapture/FTHRclips/include/windows_capture_border_policy.h`
- `FTHRcapture/FTHRclips/src/windows_capture_border_policy.cpp`
- `FTHRcapture/FTHRclips_tests/tests/windows_capture_border_policy_test.cpp`
- `FTHR_UI/main.py`
- `tests/test_windows_lifecycle_contract.py`
- `docs/WINDOWS-CAPTURE-BORDER.md`
- `docs/WINDOWS-CAPTURE-BORDER-CHANGE.md`

Derived local artifact refreshed:

- `dist/FTHRClips/_internal/engine/FTHRClips.exe`

## Verification performed

- Direct `FTHRclips.vcxproj` Release x64 rebuild completed successfully and
  emitted the canonical nested engine.
- The native test executable completed 379 checks. Its six capture-border
  checks cover the WGC opt-out attempt, unavailable session interface, failed
  setter, true readback, false readback, and DXGI non-participation.
- The focused Python lifecycle and installer-contract run passed five tests,
  including the new engine-selection ordering check.
- Ruff passed for the modified Python source and contract test.
- The full Python suite completed with only the two unrelated asset gate
  failures (`test_real_asset_manifest_and_generated_outputs_are_current` and
  `test_bundled_sounds_are_generated_pcm_wav_files`). These are the same
  pre-existing provenance failures that stop the release build; no capture,
  engine-selection, or lifecycle test failed.
- `launch_windows.bat --diagnose` resolves the canonical nested Release engine.
- The local portable bundle contains the same verified engine hash as the
  canonical native output.

A full app restart is required for an already running source-mode instance:
the Python process cached its engine path at startup and the old native process
cannot switch executables in place. After restart, the runtime diagnostic is
the final physical confirmation that a real capture session reports
`effective=true reason=borderless_enabled` for WGC or falls back to DXGI before
`StartCapture`.

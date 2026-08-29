# Windows capture privacy indicator

## Product rule

FTHR must not leave the Windows Graphics Capture (WGC) privacy border visible
while capture is active. The border is a Windows privacy indicator, not an FTHR
overlay.

WGC remains the preferred capture backend because it is event-driven and is more
reliable for independent-flip and anti-cheat-protected games. Before any WGC
session starts, FTHR applies the same session-level opt-out used by the old
build:

```cpp
session.IsBorderRequired(false);
```

FTHR then reads the property back. WGC is used only when the runtime confirms
that the border is no longer required. If the interface is unavailable, the
setter fails, or the border remains required, FTHR closes that WGC session and
falls back to DXGI Output Duplication. DXGI never creates a WGC session and
therefore cannot display the WGC border.

## Capture paths

- Desktop capture creates a WGC monitor item first.
- Regular window capture creates a WGC window item first.
- Anti-cheat capture uses WGC monitor capture with the existing focus gate.
- Any WGC path that cannot prove border suppression falls back to DXGI before
  `StartCapture`.

The fallback is intentionally narrow: it changes only the capture backend for
that session. Encoding, audio, replay buffering, shared memory and clip-save
behavior are unchanged.

## Compatibility decision

The current unpackaged/Inno build does not request
`GraphicsCaptureAccessKind::Borderless` and does not require package identity or
a manifest capability. This matches the old FTHR behavior and avoids introducing
a permission prompt into the capture flow. Runtime support is determined by the
actual `IGraphicsCaptureSession3` interface and the setter/readback result.

This means:

1. On systems where the direct WGC session opt-out works, FTHR keeps the old WGC
   capture behavior without a visible border.
2. On systems where Windows refuses the opt-out, FTHR uses DXGI so capture does
   not start with a visible border.
3. DXGI may return black or stale frames for games rendering through independent
   flip, so the runtime log records when this fallback is selected.

## Runtime diagnostic

Each new WGC session emits a `CaptureBorderPolicy` line containing the target,
session-interface availability, setter result, readback state, effective result
and fallback reason. A successful WGC session must report
`effective=true`; otherwise initialization returns to the backend selector and
uses DXGI.

## Engine selection invariant

In a source checkout, the application must prefer
`FTHRcapture/FTHRclips/x64/Release/FTHRclips.exe`, the direct native-project
output, before the older `FTHRcapture/x64/Release/FTHRClips.exe`
solution-output compatibility path. The launcher, Python UI, and PyInstaller
spec use that same ordering, and the UI logs the resolved absolute engine path.

Changing the source or rebuilding only the direct-project executable does not
change an engine process that is already running. Restart the whole app after a
border-policy or engine-selection update, then confirm the selected path and
the `CaptureBorderPolicy` runtime line.

## Related change record

See [`WINDOWS-CAPTURE-BORDER-CHANGE.md`](WINDOWS-CAPTURE-BORDER-CHANGE.md) for
the implementation details and verification record for this compatibility
update.

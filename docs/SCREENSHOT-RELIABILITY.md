# Screenshot reliability

## Monitor contract

The screenshot target is the active capture configuration's monitor value.
On Windows this is the normalized DISPLAYCONFIG_TARGET_DEVICE_NAME
monitorDevicePath introduced by AUDIT-048. Every screenshot enumerates the
current Windows monitor topology, maps that persistent identity to its current
GDI/Qt screen, and captures that exact screen. It does not cache an HMONITOR,
DXGI output index, Qt list index, cursor screen, or primary screen.

If a configured target cannot be resolved, the action fails with
MONITOR_NOT_FOUND. It never silently captures a different display. A monitor
switch takes effect after the existing capture restart, because the active
capture configuration is the sole screenshot source of truth.

## Capture and save path

Windows captures the resolved QScreen with grabWindow(0). This is a side-band
operation: it does not send an engine command, restart replay, replace the
capture generation, touch audio, or reconfigure an encoder. Qt supplies the
native screenshot image; no manual BGRA/RGBA byte swapping, row-pitch handling,
scaling, or GPU upload was added.

Wayland/Linux retains the existing grim -o selected-output path when available.
It writes to the same non-library staging file first. The Qt fallback uses the
configured output and also rejects a missing explicit monitor. Linux runtime
qualification is not implied by this document.

The full image is PNG and uses the native selected-monitor dimensions. A staged
image is saved by a QThread, then the crop dialog offers the existing
full/cropped choices. Full images and cropped derivatives are published only
after complete encoding by an atomic same-directory rename. A failed encode or
write leaves no final-looking PNG.

Files are written beneath ~/FTHR_Clips/Screenshots using:

    screenshot_from_YYYYMMDD_HHMMSS_microseconds.png

An existing name receives a deterministic _01, _02, etc. suffix. The actual
temporary path is an OS-created hidden .png.partial file in that same
directory. The single-instance boundary and final-path collision guard avoid
overwriting an existing screenshot.

## Background and errors

The global screenshot hotkey remains owned by the running instance. In
tray/background mode it creates only the standalone crop dialog; it does not
build the deferred library/settings tree or restore the main window. The
success card/sound is sent only after the editor has published the final PNG.

The relevant user-visible failure codes are:

- MONITOR_NOT_FOUND
- CAPTURE_UNAVAILABLE
- OUTPUT_DIRECTORY_UNAVAILABLE
- IMAGE_ENCODE_FAILED
- OUTPUT_COLLISION
- WRITE_FAILED

## Evidence and limitations

Deterministic tests cover selected-monitor mapping, no fallback for a missing
selection, live topology remapping, dimensions/channel preservation,
transactional save publication, failure cleanup, filename collision handling,
off-UI-thread PNG encoding, crop publication, background construction and
hotkey duplicate suppression.

No physical multi-monitor screenshot run was performed in this change. In
particular, Monitor A/Monitor B content, mixed-DPI, rotation, HDR, tray-only
hotkey, rapid ten-shot capture, screenshot/replay visual agreement, PNG
latency, CPU/RAM cost, and replay-frame health remain runtime qualification
work. HDR screenshot tonemapping is not claimed; Qt/native behavior must be
observed on an HDR display.

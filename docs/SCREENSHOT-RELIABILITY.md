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

Wayland/Linux uses `grim -o selected-output` when available. The command is
never invoked without `-o`, and it runs through asynchronous `QProcess` with a
five-second deadline instead of blocking the GUI thread. Native X11 never
probes `grim`; it captures the selected QScreen directly. Both paths write to
the same non-library staging file first, and the Qt fallback rejects a missing
explicit monitor. Linux runtime qualification is not implied by this document.

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

The global screenshot hotkey remains owned by the running instance. One active
request plus nine pending requests forms a bounded sequence, so a rapid
ten-shot input is processed serially instead of silently discarded or creating
unbounded workers. In tray/background mode it creates only the standalone crop
dialog; it does not build the deferred library/settings tree or restore the
main window. The success card/sound is sent only after the editor has published
the final PNG.

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
transactional save publication, failure cleanup, ten-shot filename collision
handling, off-UI-thread PNG encoding, crop publication, background construction
and the bounded request queue.

On 2026-08-29 a physical Windows source run captured ten screenshots across two
monitors (1920x1080 primary and 2560x1080 at virtual X=-2560). Every PNG decoded
at the native selected-screen dimensions, preserved sampled pixel values, had a
unique final name and left no partial file. A second ten-shot run while replay
advanced from frame 5 to frame 18 produced ten valid 1920x1080 PNGs. The
pre-existing Windows replay engine later stalls at frame 24 even without this
screenshot flow; that separate capture issue is not treated as screenshot
qualification evidence.

Physical mixed-DPI, rotation, HDR, tray-only hotkey, editor-mediated rapid input,
PNG latency/CPU/RAM and Linux X11/Wayland screenshots remain runtime
qualification work. The Windows run used scale 100% on both monitors. HDR
screenshot tonemapping is not claimed; Qt/native behavior must be observed on
an HDR display.

# Windows tray, shutdown, status, and notification update

## What changed

FTHR now exposes **Minimize to system tray when closing FTHR** under
**Settings → General → System** on Windows. It is enabled by default.

When enabled:

- Minimize keeps the normal Windows taskbar behavior.
- X hides the main window to the system tray without restarting capture.
- A dedicated power icon appears beside the standard window controls. It is
  the explicit full-shutdown action.
- Hiding the window shows a compact top-right card with `NOW CAPTURING`, the
  active Desktop/window source, and `Running in the background`.
- The tray menu's **Exit FTHR** action uses the same full-shutdown path.

When disabled, the power icon is hidden and X performs the bounded full
shutdown. If Windows has no usable system tray, X also falls back to full
shutdown so the app cannot become unreachable.

## Reliable shutdown path

The new power control calls `request_full_exit()` directly. That path is
idempotent and visually hides the UI first, then:

1. stops save polling and rejects new save commands;
2. stops game detection and global hotkeys;
3. gives staged clip finalization one shared 1.5-second grace period;
4. stops microphone and upload services;
5. closes and reaps the notification helper process;
6. sends the Windows capture engine `SHUTDOWN`, waits two seconds, and uses
   terminate/kill escalation only if the engine does not exit;
7. removes the tray icon and quits `QApplication`.

The title-bar power control, tray **Exit FTHR**, close-with-the-option-disabled,
and the process-finalizer all converge on this one cleanup implementation.

## Status placement

The capture state is now a single compact rail immediately left of the capture
preset dropdown. It remains visible on both the library and settings screens
and owns `CONNECTING`, `CAPTURE STARTING`, `CAPTURING`, `APPLYING SETTINGS`,
save/finalization states, and health warnings.

The duplicate `Capturing` / `Applying Settings` labels were removed from the
capture and source dropdowns. Those popups now contain only their settings and
their conditional **Apply** action.

## Notification layout and typography

Informational and one-line result cards use a 320 × 76 compact layout. The
bundled Oswald face is loaded explicitly for short uppercase headlines.
Punctuation-heavy source, filename, and detail text uses Segoe UI on Windows
without artificial letter spacing. This fixes the distorted apostrophe and
mixed-case rendering seen in the previous all-Bahnschrift card.

## Sound routing

Each named event now uses the matching named WAV and its own volume setting:

| Event | Default sound |
|---|---|
| App startup | `startup.wav` |
| Clip saved | `clip_captured.wav` |
| Screenshot saved | `screenshot_saved.wav` |
| Capture failure | `error.wav` |
| Upload completed | `upload_successful.wav` |
| Upload failed | `upload_failed.wav` |

Game-detection prompts, source-switch confirmations, and the background card
are informational and no longer route through `error.wav`. The Customize page
also exposes all six event names, and the Audio page has matching volume
controls.

## Development shortcut

`tools/update_dev_shortcut.ps1` recreates the desktop shortcut so it points to
the current checkout's `FTHR_UI/main.py` through `pythonw.exe`, uses the checkout
as its working directory, and removes known legacy FTHR shortcut names.

## Verification

Automated coverage checks the separate close-to-tray/full-exit paths, the
Windows power-control wiring, the single top-level status indicator, the
graceful native engine command, and capture-card command routing. Visual QA
renders are written under `Output/` for the compact background card,
apostrophe-heavy detail text, and the Windows title bar.

For a physical Windows check:

1. Enable the setting, start capture, and press X. Confirm the compact card and
   that the tray menu can restore the same capture generation.
2. Save a clip from tray mode to confirm global hotkeys remain active.
3. Restore FTHR, press the power icon, and confirm `FTHRClips.exe`, its capture
   engine, and the capture-card helper all leave Task Manager.
4. Disable the setting and confirm X performs the same complete shutdown.
5. Trigger each save/upload result once and confirm its sound matches the table.

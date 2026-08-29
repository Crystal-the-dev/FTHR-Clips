# Windows lifecycle

## Product rule

On Windows, **Minimize to system tray when closing FTHR** controls the window
lifecycle and is enabled by default. With it enabled, X hides the main FTHR
window to the system tray when the native tray is available. Capture, replay
history, hotkeys, upload scheduling and the single capture-engine process
continue running. Normal minimize keeps its existing taskbar behaviour. The
title-bar power icon and `Exit FTHR` in the tray menu are deliberate full-exit
actions. With the setting disabled, the power icon is hidden and X performs
that same full exit.

If Windows reports no tray support, FTHR does not hide the only window. A close
instead starts the bounded full-exit path.

Hiding to tray also sends one compact top-right capture card that identifies
the active source and states that capture continues in the background. Restoring
the main window dismisses that card without restarting capture.

## Startup and single instance

The installed app's **Autostart with Windows** setting writes this per-user Run
value, with no administrator privileges:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\FTHRClips
"<installed FTHRClips.exe path>" --background
```

Only a frozen/package executable may be registered. Development launches never
register `python.exe`. The settings checkbox reads the current Registry value
and requires the exact current executable path and `--background` argument, so
an externally removed or stale value is not displayed as enabled.

`--background` starts the same core services as a regular run—engine, replay,
hotkeys, upload manager and tray—but defers the library/settings/editor widget
tree until the user restores FTHR. Opening the window attaches to the existing
engine; it does not restart the replay generation.

The named mutex remains the ownership authority. After it has refused a second
process, the second process sends one bounded `activate` request through a
local `QLocalServer` endpoint and exits. The existing process restores its
window. The endpoint accepts no capture or settings commands. If it is
temporarily unavailable, the second process exits without starting an engine.

## Shutdown sequence

The old code path made the window wait synchronously for every optional
post-processing thread for up to 15 seconds, while the Windows engine command
loop had no exit command and could only be terminated. This was the confirmed
source-level cause of the visible close delay; no pre-change physical timing
was captured in this implementation pass.

Full exit now hides the window and tray first, then records concise lifecycle
timestamps in `~/.fthr/logs/fthr.log` for these phases:

1. save commands and polling stop;
2. hotkeys stop;
3. optional clip finalization receives one shared 1.5-second grace period;
4. microphone, upload manager and capture-card client stop;
5. the Windows engine receives `SHUTDOWN`, gets two seconds for graceful exit,
   then receives terminate/kill escalation only if needed;
6. the tray icon is removed and `QApplication` quits.

The `SHUTDOWN` command adds no shared-memory fields and preserves the v4 ABI.
It lets the native main loop reach its existing `CaptureEngine::Shutdown()`
cleanup. A queued save gets a bounded graceful attempt; unfinished optional
post-processing remains staged rather than being published as a final clip.
Existing transactional partial-file recovery handles a later cleanup.

Upload worker joining remains bounded at three seconds. An in-flight network
request is not claimed to be cancellable; it cannot keep the window visible,
and no helper subprocess is retained after the application process exits.

## Physical evidence and remaining work

No interactive Windows tray, Registry, second-launch, reboot/login, background
hotkey, pre-UI replay clip, CPU/RAM, engine-exit or shutdown-timing measurement
was run in this pass. The log markers above are the required evidence points
for that qualification.

The later installer task must preserve or update the `FTHRClips` Run value when
the installed executable moves, and remove it on uninstall. It must not create
a Run entry for an unpackaged/dev build.

# Asset and game-detection changes

Date: 2026-08-24

## Asset location and replacement

The runtime asset directory is `FTHR_UI/assets`. The packaging specs and runtime
resource lookups already use that directory, so the files were moved there
instead of adding a new asset-path redirect.

All files previously in `assets` were moved into the corresponding runtime
locations:

- `assets/fthr_logo.png` -> `FTHR_UI/assets/fthr_logo.png`
- `assets/fonts/Oswald-Bold.ttf` -> `FTHR_UI/assets/fonts/Oswald-Bold.ttf`
- `assets/icons/*.png` -> `FTHR_UI/assets/icons/*.png`
- `assets/sounds/*` -> canonical runtime names in `FTHR_UI/assets/sounds/`

The imported sound names were normalised as follows:

| Source name | Runtime name |
| --- | --- |
| `Clip Captured (1).wav` / `.mp3` | `clip_captured.wav` / `.mp3` |
| `Error (1).wav` / `.mp3` | `error.wav` / `.mp3` |
| `Screenshot Saved (1).wav` / `.mp3` | `screenshot_saved.wav` / `.mp3` |
| `Startup (1).wav` / `.mp3` | `startup.wav` / `.mp3` |
| `Upload Failed (1).wav` / `.mp3` | `upload_failed.wav` / `.mp3` |
| `Upload Successful (1).wav` / `.mp3` | `upload_successful.wav` / `.mp3` |

The capture card now uses `upload_successful.wav` for successful uploads. The
startup and upload-failed variants are packaged in the runtime directory but
are not currently selected by an existing playback path.

The root `assets` directory is now retained only as an empty folder structure;
the application does not read from it.

## Windows game detection port

The current PySide6 application now contains the useful Windows behavior from
`FTHR_Clips/FTHR_UI/core/game_detection.py`, adapted to the current codebase:

- Reads the foreground window through Win32 APIs without blocking the UI.
- Collects the window title, executable path/name, process ID, class, bounds,
  borderless/fullscreen state, and Windows Game Bar known-game metadata.
- Filters launchers, browsers, utilities, shell windows, FTHR itself, and small
  non-game windows before considering a candidate.
- Recognises common Unity, Unreal, SDL, GLFW, Godot, CryEngine, Frostbite, and
  game-install-path markers.
- Requires two stable polls before reporting a game and two missing polls before
  reporting game loss, preventing transient focus changes from switching capture.
- Supports manual rules by title fragment, executable name, or executable path.
- Runs detection on a worker thread and emits Qt signals back to the main UI.

The original cross-platform enumerator remains available for non-Windows
platforms. The new Windows detector is selected automatically on Windows.

## UI integration

A `GAME DETECT` control was added to the top bar. Its popup contains:

- Enable/disable detection.
- `Auto-switch` and `Prompt first` modes.
- Optional fallback to desktop capture after a game disappears.
- Manual title/executable rules with add/remove controls.
- The configured game-detection hotkey reminder.

When a game is detected, the existing capture prompt is used. Confirming the
prompt switches the existing source selector to the detected window; dismissing
it suppresses that candidate until it leaves the foreground. The source popup
also accepts the detected window directly and stores its target metadata in the
current settings model.

New persisted settings are:

- `game_detection_mode`
- `game_detection_custom_games`
- `game_detection_fallback_desktop`
- `capture_mode`
- `target_hwnd`
- `target_window_name`

## Verification

Passed checks:

- `python -m compileall -q FTHR_UI tests`
- Main-module import smoke test
- 13 focused game-detection tests
- Exception-handling audit (`tests/test_exception_gate.py`)

The full test suite still reports the existing release asset-license gate as
failed because the supplied images/audio have different hashes and additional
files than the approved generated-asset manifest. This is intentional review
work remaining for the developer: confirm provenance/licensing for the moved
assets, then update `tools/release_asset_manifest.json` and its generation
metadata as appropriate before shipping. The imported files were preserved;
they were not deleted merely to make that gate pass.

## Development launch shortcut

`launch_windows.bat` now prefers the live source checkout whenever a Windows
capture engine and Python interpreter are available. This means source edits
are picked up immediately instead of silently launching an older
`dist/FTHRClips` bundle. The portable bundle and installed build remain
fallbacks when source mode is unavailable.

A desktop shortcut was created at:

`C:\Users\nombo\Desktop\FTHR Clips - Latest.lnk`

It points to the launcher, not directly to an executable. For a non-launching
diagnostic check, run:

```powershell
cmd /d /c launch_windows.bat --diagnose
```

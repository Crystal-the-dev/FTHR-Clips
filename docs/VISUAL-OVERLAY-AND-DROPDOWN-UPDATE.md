# Visual Overlay and Dropdown Update

Date: 2026-08-25

## Goal

Remove the unfinished keyboard and mouse visualizations while retaining camera,
multi-image overlays, and clip-saving hotkeys. The same pass makes closed
dropdowns wheel-safe without changing FTHR Clips' black-and-teal visual language.

## Visual Overlays

- Removed the keyboard-overlay toggle, presets, key picker, draggable preview
  layer, event recorder, FFmpeg renderer, geometry defaults, and active settings.
- Existing `keyboard_overlay_*` and `mouse_overlay_*` values are removed from
  older settings files during migration.
- The mouse event recorder and mouse post-processing path were removed.
- Camera controls remain available.
- Users can add, select, position, resize, fit, fade, remove, and render multiple
  image layers in a single FFmpeg composition pass.

## Clip Hotkeys

The keyboard listener used for application hotkeys is independent of overlay
recording and remains active. `HotkeyManager` still registers the normal clip,
extended clip, screenshot, and game-detection shortcuts. The normal save-clip
signal continues to dispatch to `MainWindow._on_hotkey_save_clip()` and then to
the existing clip-save workflow.

## Dropdowns and Customize Sections

- Combo-box menus now use Qt's dropdown behavior and open directly below their
  field when screen space permits. They no longer align the selected option over
  the closed control and cover the preceding settings rows.
- Scrolling over a closed combo box is ignored by the field and continues to the
  surrounding settings page, preventing accidental value changes.
- The popup keeps FTHR's existing surfaces, borders, teal selection color, and
  arrow treatment.
- Customize sections now expand and collapse immediately. The former animated
  maximum-height transition was removed because rapid collapse could leave the
  content in an inconsistent intermediate state.

## Development Shortcut

The refreshed shortcuts are:

`C:\Users\nombo\Desktop\FTHR Clips.lnk`

`C:\Users\nombo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\FTHR_Clips.lnk`

Both launch:

`C:\Users\nombo\Documents\FedarKlihps\launch_windows.bat`

The launcher prefers the live source checkout and local capture engine. If that
runtime is unavailable, it falls back to the portable bundle and then the
installed application.

## Implementation

- `FTHR_UI/main.py` — Visual Overlays UI, hotkey wiring, and post-processing.
- `FTHR_UI/core/settings_manager.py` — retired input setting cleanup and image
  layer migration.
- `FTHR_UI/core/camera_overlay.py` — multi-image layer normalization.
- `FTHR_UI/ui/camera_overlay_editor.py` — camera and multi-image preview layers.
- `FTHR_UI/ui/style.py` — standard below-field combo popup behavior.
- `FTHR_UI/ui/customize_page.py` — immediate accordion expand/collapse.

## Verification

- Overlay, dropdown, popup-layout, post-processing, exception-gate, and hotkey
  regression tests pass.
- The broader suite passes when the existing asset-license gate is excluded.
- The two remaining full-suite failures are the pre-existing asset manifest and
  sound inventory mismatches; this UI update does not modify bundled assets.

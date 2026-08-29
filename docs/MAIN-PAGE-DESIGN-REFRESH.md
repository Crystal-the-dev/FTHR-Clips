# Main Page Design Refresh

Date: 2026-08-24

## Goal

Refine the main-page popovers without replacing FTHR Clips' visual identity.
The update keeps the black surfaces, teal interaction color, squared geometry,
and condensed Oswald labels. SteelSeries GG was used as a reference for spacing,
field grouping, and information density rather than as a new visual theme.

## Hotkeys

- The Hotkeys popover now measures itself before opening and is constrained to
  the usable bounds of the screen containing its trigger.
- When there is not enough room to the right, the popover right-aligns with the
  Hotkeys button. It is also moved above the button when there is not enough
  vertical space below it.
- The realized native popup frame is checked after opening, accounting for the
  extra border Windows or Qt may add.
- This behavior is shared by all top-bar popovers, so Capture, Source, Game
  Detection, and Hotkeys are protected in fullscreen and multi-monitor layouts.
- The Hotkeys panel uses consistent outer gutters and larger vertical intervals.
  Its action, keyboard, controller, and clear columns were tightened so every
  control remains inside the panel without losing the three-column workflow.

## Game Detection

- Restored the original FTHR squared switches for Game Detection and Fall Back
  to Desktop.
- Replaced the generic mode dropdown with the original two-part `AUTO-SWITCH` /
  `PROMPT FIRST` selector.
- Applied the bundled Oswald display face to section labels and mode controls;
  readable field values and helper text continue to use the body face.
- Split the popover into clear Detection, Switch Mode, and Manual Games groups
  using consistent 18 px gutters, 12 px vertical rhythm, and restrained
  hairline dividers.
- Manual game fields now use aligned `TITLE` and `EXECUTABLE` captions, matching
  the structure used elsewhere in FTHR rather than relying on placeholders
  alone.

## Capture Bitrate

- Restored the compact original custom-bitrate control: a right-aligned editable
  value with the `kbps` suffix and integrated minus/plus buttons.
- The whole control begins on the same column as the selectors above it. The
  minus button no longer extends left into the label gutter.
- Existing behavior is unchanged: values remain editable, step in 500 kbps
  increments, are clamped to 500–200,000 kbps, and are persisted through the
  current settings manager.

## Interaction and Accessibility

- Toggle switches and segmented mode buttons retain keyboard focus behavior.
- Focus, hover, checked, pressed, disabled, minimum, and maximum states continue
  to use FTHR's existing color tokens.
- Control accessible names describe their action, including bitrate increments
  and both Game Detection switches.

## Development Shortcut

The desktop and Start menu development shortcuts now open:

`C:\Users\nombo\Documents\FedarKlihps\launch_windows.bat`

The launcher prefers the live `FTHR_UI/main.py` checkout and the local Release
capture engine, so these design changes are available immediately. It falls back
to the portable bundle and then the installed build only when source mode is not
available.

Updated shortcuts:

- `C:\Users\nombo\Desktop\FTHR Clips.lnk`
- `C:\Users\nombo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\FTHR_Clips.lnk`

## Implementation and Verification

The UI changes are in `FTHR_UI/main.py`. Layout regression coverage is in
`tests/test_main_popup_layout.py`.

Verification performed:

- Python compilation and Ruff checks pass.
- Capture-bitrate, game-detection, popup-boundary, and main-window UI tests pass.
- The complete test suite passes when the existing asset-license gate is
  excluded. That gate still reports unrelated changed asset hashes and extra
  sound files; this design update does not modify those assets.

# Custom bitrate and controller hotkeys

This update adds two controls to the capture and hotkey popovers.

## Capture quality

- **Capture → Quality** now includes **Custom** alongside Low, Medium, and High.
- Select **Custom** to enable the inline **Custom Bitrate** field. Type an exact
  value in kbps, or use **−** and **+** to reduce or increase it by **500 kbps**.
- The field accepts **500–200,000 kbps**. The selected value is persisted as
  `custom_bitrate_kbps` in `%USERPROFILE%\.fthr\settings.json` and is used when
  the capture engine restarts.
- As with the other capture settings, choose **Apply + Restart** before judging
  the new recording bitrate.

## Hotkeys

- The **Hotkeys** popover now has a binding field for both **Keyboard** and
  **Controller** for Capture Clip, Extended Clip, and Screenshot.
- Click a keyboard field and press a key or chord. Click a controller field and
  press the desired controller button or chord; releasing the controls saves it.
- **Clear** removes only that action's controller binding. Keyboard bindings are
  left unchanged.
- Controller bindings are available on Windows through XInput, with a raw-HID
  fallback for other compatible game controllers, and are saved in
  `%USERPROFILE%\.fthr\hotkeys.json`. Older nested controller bindings are kept
  when the app upgrades, rather than being discarded.

## Local test launcher

`C:\Users\nombo\Desktop\FTHR Clips.lnk` launches the live checkout through
`C:\Users\nombo\Documents\FedarKlihps\launch_windows.bat`. That launcher
prefers the source UI and the local Release capture engine, so it is the
shortcut to use when checking this change.

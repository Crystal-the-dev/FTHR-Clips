# Windows installer, update, and uninstall lifecycle

## Current distribution decision

FTHR Clips is a **machine-wide, x64, unpackaged Win32 application** on
Windows 10 and later. Inno Setup 6 installs the PyInstaller onedir output to
`{autopf}\FTHRClips`; its stable update identity is
`{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}`. That identity must not change: it is
what makes a new Setup replace the established FTHR installation rather than
creating a second Apps & Features entry.

The current build uses no MSIX/AppX package identity. This is intentional for
the alpha: a traditional signed EXE installer remains the Windows 10-compatible
path and does not claim automatic update support. The UI correctly says that
updates are manual.

## Install and update behaviour

- A fresh install writes program files under Program Files, the common Start
  Menu group, one Apps & Features entry, and an optional common desktop
  shortcut. It does not create an autostart entry.
- A normal update retains the existing program and Start Menu locations through
  Inno's `UsePreviousAppDir` / `UsePreviousGroup`, preserving the stable AppId.
  A matching version is a **repair/reinstall** of that same product rather than
  a side-by-side installation.
- Before it copies the current payload, an update removes only the four known
  legacy `_internal` paths for the previously bundled `imageio_ffmpeg` and
  PyQt6 packages (including their metadata). This prevents a historical GPL
  FFmpeg/PyQt6 residue from surviving an update. It is deliberately not a broad
  `{app}` cleanup.
- A newer installed product version blocks a downgrade unless an administrator
  explicitly starts Setup with `/ALLOWDOWNGRADE`.
- Inno Setup's Windows Restart Manager integration detects FTHR executable
  files in use and asks the interactive user to close them. It is configured
  not to force-kill or automatically restart a capture session. A silent
  deployment must use Inno's documented closing flags deliberately; the
  default release command does not add force-close behaviour.
- If the owned `HKCU\...\Run\FTHRClips` entry contains an FTHR executable and
  `--background`, an upgrade preserves that preference and rewrites its path
  to the installed executable. Uninstall removes only that exact value.

## Legacy per-user test installation

The installer detects the historical per-user entry only when both its registry
record and expected location agree:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\FTHR Clips
%LOCALAPPDATA%\Programs\FTHR Clips
```

It displays an **unchecked** opt-in action. If selected, and FTHR's named
mutex is not held, Setup removes only that verified legacy program directory,
that legacy registry key, and the two known *per-user* Start Menu links. It
does not execute the predecessor's uninstaller, force-kill processes, delete
unrecognised shortcuts, or touch user data. A missing/mismatched record is
left alone.

For an unattended maintenance run, the same action requires the explicit
`/REMOVELEGACY` Setup switch; without it, silent Setup leaves the legacy
installation untouched. This switch remains subject to the same registry/path
and no-running-mutex checks.

## User-data boundary

| Location | Meaning | Default uninstall action |
|---|---|---|
| `%USERPROFILE%\FTHR_Clips` | User clips, screenshots, exports, sidecars | **Never delete** |
| Clip folders selected in settings | User-selected source/destination media | **Never delete** |
| `%USERPROFILE%\.fthr` | FTHR settings, hotkeys, themes, logs, cache, clip metadata | Keep by default; an interactive opt-in may remove it |
| `{autopf}\FTHRClips` | Installed program payload and package paperwork | Remove through Inno's normal uninstaller |

The optional `.fthr` removal starts unchecked (No is the default), is disabled
for silent uninstall, and does not encompass `FTHR_Clips`. It is appropriate
for a deliberate clean settings reset, not for a normal uninstall.

## VC++ runtime policy

The installer bundles only `vc_redist.x64.exe`, acquired by
`tools/fetch_third_party.py --vcredist` from Microsoft's documented permalink.
On Windows the fetcher requires a valid Authenticode chain whose signer subject
contains `Microsoft Corporation`, records the exact SHA-256/file version in
the ignored `redist/vc_redist.x64.json`, and refuses a failed verification.

At install time Setup checks the documented v14 x64 runtime registry location.
It runs the bundled Microsoft installer only when the runtime is missing,
uninstalled, lacks a version, or is older than the bundled installer. The
package never downloads a prerequisite during end-user installation.

## Signing policy

No certificate, PFX file, password, timestamp credential, or publisher key is
stored in this repository. `tools/build_windows_installer.py` accepts an
operator-owned `--sign-command` / `FTHR_SIGN_COMMAND` template containing
`{file}` and applies it to the FTHR UI executable, native engine executable,
native playback-mixer DLL, and final Setup executable. `--require-signed` makes
a missing or invalid
Authenticode signature fail the release build.

Without that explicit external signing configuration, an artifact is valid for
local lifecycle qualification only and **must not be published**. The verifier
reports this as a release-blocking warning rather than pretending a signature
exists.

## Future package identity decision

Do not convert the alpha to MSIX now. The existing border policy correctly
reports no border removal for this unpackaged Inno build. When Windows 11
borderless WGC qualification is scheduled, evaluate an identity-only sparse
package (MSIX with external location) alongside this installer. It can grant
package identity while retaining the EXE location and Inno update path, but it
requires its own manifest, matching signed package identity, registration and
unregistration, and Windows 11 physical qualification. No current installer
claims that capability.

## Build and gates

```powershell
python tools/build_windows_installer.py
```

The command runs source license/version gates, verifies the Microsoft VC++
redistributable, rebuilds PyInstaller, gates the onedir payload, compiles Inno
Setup, and records SHA-256 values for the generated artifacts. Add the
operator-owned signing command plus `--require-signed` for a release candidate.

The automated contract is:

```powershell
python tools/verify_windows_installer_lifecycle.py
python -m pytest tests/test_windows_installer_contract.py
```

It does not replace the physical matrix: install, launch, autostart update,
uninstall with data retained, optional settings removal, reinstall, shortcut
check, Apps & Features check, and a signed-artifact review still require a real
Windows machine.

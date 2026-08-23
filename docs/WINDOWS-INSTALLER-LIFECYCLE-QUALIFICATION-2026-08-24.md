# Windows installer lifecycle qualification — 2026-08-24

## Scope and result

This is a **local unsigned-artifact qualification** on Windows 10 Pro build
19045. It exercised the real prior FTHR installation and the current
`FTHRClips-Setup-1.0.0-alpha-x64.exe` output. It is not a public-release
qualification: the FTHR UI executable, native engine executable and Setup
executable are unsigned.

| Artifact | SHA-256 | Result |
|---|---|---|
| UI `FTHRClips.exe` | `720d80d33d44955548e6dc3e27488eef5acd6144018ee284e32288949e221273` | Built; unsigned |
| Native engine `FTHRClips.exe` | `7f9e65ed1bac3b531560d73702e999436e52661f7e361304f331705ce1c87e28` | Built; unsigned |
| Setup executable | `57317a37ede5a56dc2c367bc30e8a04e2e28d3572726c56484978eb0901f8152` | Compiled; unsigned |
| `vc_redist.x64.exe` | `cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b` | Microsoft Authenticode valid; file/product version `14.44.35211.0` |

The source licence gate reported 76 checks, zero failures and one expected
Linux-input warning. The Windows bundle licence/asset gate reported 84 checks,
zero failures and zero warnings. The lifecycle source/artifact gate passed with
only the expected unsigned-FTHR warnings.

## Pre-test inventory

The machine had both an existing machine-wide FTHR product and a historical
per-user test product. The machine-wide entry used the stable Inno AppId and
`C:\Program Files\FTHRClips`; the per-user entry was version `0.1.0-test` at
`%LOCALAPPDATA%\Programs\FTHR Clips`. No FTHR process was running before the
lifecycle work.

Before any installer action, the retained user-data snapshots were:

| Data | Files | Bytes | Metadata digest |
|---|---:|---:|---|
| `%USERPROFILE%\FTHR_Clips` | 1 | 15,176,553 | `42ba0aa7404db73f86f1011217ce348e0f6ddfa1a83bd9e286127335174d1d0a` |
| `%USERPROFILE%\.fthr` | 4 | 23,024 | `f594eb560affe5ee6a368f67fd0f4e17bea56f390ef10a093d4dd172afa61213` |

The snapshot values were identical after every physical install, update,
uninstall, reinstall and legacy-cleanup action below.

After that physical matrix, the full Python test suite imported the UI entry
point twice and appended two startup-banner lines (148 bytes) to the existing
`%USERPROFILE%\.fthr\logs\fthr.log`. This was a test-host logging side effect,
not an installer action; no clip, setting, thumbnail or installer operation
changed it. The log was intentionally left intact rather than editing user
state to make a later snapshot appear unchanged.

## Physical lifecycle matrix

| Scenario | Result | Evidence / boundary |
|---|---|---|
| Existing machine-wide install → current Setup | PASS | The existing stable AppId was updated in place at `C:\Program Files\FTHRClips`; only one Installed Apps entry remained. |
| Autostart through update | PASS | A deliberate owned `HKCU\...\Run\FTHRClips` value was preserved and rewritten to the installed path with `--background`. |
| Default uninstall | PASS | The product uninstall key, Program Files payload, product Run value, common Start Menu group and public desktop shortcut were absent afterwards. The legacy per-user product remained, as expected. |
| Data preservation on default uninstall | PASS | Both retained-user-data snapshots matched the baseline exactly; no clip or `.fthr` state was removed. |
| Fresh reinstall after uninstall | PASS | Setup restored one machine-wide product at the default Program Files path and three common Start Menu links. It created no autostart value and no public desktop shortcut by default. |
| Same-version maintenance install | PASS | Re-running the current Setup against the current machine-wide product retained one product registration and a working installed payload while applying the explicitly requested legacy-maintenance action. |
| Legacy per-user cleanup | PASS | A real current Setup maintenance invocation using explicit `/REMOVELEGACY` removed only the verified legacy HKCU uninstall entry, its exact program directory and its two per-user Start Menu links. The legacy force-kill/delete script was not executed. |
| Package leaves obsolete excluded runtime packages behind | MITIGATED / PHYSICAL RETEST NOT RUN | The first real upgrade exposed stale `_internal\\imageio_ffmpeg` and `PyQt6` residue from the historical package. The installer now has four exact `InstallDelete` targets for those excluded packages and their metadata. The final script compiled and the source contract verifies those exact targets; a second historic-residue fixture was not reconstructed solely to claim a physical re-test. |

The current final machine state is the current machine-wide FTHR installation;
the historical per-user test installation is absent. No source checkout,
clips or `.fthr` state were removed.

## Deliberately not claimed

The following items were not exercised and remain release gates:

- installed GUI launch, tray behavior, second-launch activation and graceful
  Restart Manager closure while an active capture is saving;
- H.264 save/playback, screenshot, audio and capture smoke through the
  installed UI;
- the interactive optional `.fthr` deletion choice (it would alter real user
  state), custom install path, visual downgrade prompt, and VC++ install/no-op
  outcomes on a machine missing or carrying an older runtime;
- signed-artifact install, SmartScreen/reputation behavior, and Windows 11
  package-identity/borderless WGC qualification.

## Release decision

The installer lifecycle implementation is suitable for further local
qualification, but Windows publication is **not ready**. A release operator
must sign the UI executable, native engine executable and Setup executable with
an approved publisher identity, run the build with `--require-signed`, and
complete the installed-app GUI/capture/tray matrix without expanding the
uninstall data boundary.

# Known Issues — 1.0.0-alpha

Honest list of what is broken, unverified, or missing. Read before filing a bug.

**When reporting anything, attach `~/.fthr/logs/fthr.log`** (rotates at 2 MB).
Without it most reports are not actionable.

---

## Resolved since the audit

| Issue | Resolution |
|---|---|
| ~~**Qt binding and bundled asset licensing**~~ (AUDIT-013) | Resolved 2026-08-14. PySide6 6.11.1 uses its LGPLv3 option with source/notices and replaceable libraries. Every unprovenanced image was replaced by deterministic project-generated media, the four unprovenanced MP3s were removed in favour of generated PCM WAVs, and Oswald Bold 4.103 was byte-matched to its pinned OFL-1.1 upstream. Source and package gates enforce the per-file SHA-256 allowlist. |
| ~~**GPL FFmpeg**~~ (AUDIT-005) | Resolved 2026-08-05. The `--enable-gpl` build was replaced with the BtbN **LGPL** build `n8.1.2-21-gce3c09c101` (ABI-identical, avcodec-62). Software fallbacks moved x264 → libopenh264 and x265 → libkvazaar; NVENC/AMF/QSV untouched. `imageio-ffmpeg` (also GPL, and never actually bundled on Windows — which is why watermark, crop and export silently no-opped) was removed entirely in favour of `core/ffmpeg_tools.py`. Licence texts now ship inside the bundle; `tools/verify_release_licenses.py` gates it in CI. |
| ~~**No version control**~~ (AUDIT-008) | Resolved 2026-08-06. The authoritative tree is a git repository with `.gitignore`, `.gitattributes`, a documented source of truth (`docs/SOURCE_OF_TRUTH.md`) and release gates (`docs/RELEASE_CHECKLIST.md`). No tag exists yet because runtime release gates remain incomplete. |
| ~~**Unpinned dependencies**~~ (AUDIT-009) | Resolved 2026-08-06. `requirements-alpha.txt` pins the full transitive closure; `requirements.in` holds the direct list. Verified by two clean installs. |
| ~~**Hotkey socket in world-writable /tmp**~~ (AUDIT-003b) | Resolved 2026-08-06. AUDIT-003 fixed the socket *mode*; the *path* was still `/tmp/fthr_hotkey.sock`, which any local user could squat — and the old code then ran an unconditional `unlink()` on it, either deleting a stranger's file or (under the sticky bit) failing and leaving hotkeys dead indefinitely. The socket moved to `$XDG_RUNTIME_DIR/fthr/` and now refuses to remove anything that is not a dead socket owned by you. 16 new tests, verified on Linux. |
| ~~**No Linux AppImage could be built**~~ (AUDIT-014) | Resolved 2026-08-06 and revalidated 2026-08-14. The Linux engine is compiled against, and ships with, a pinned **LGPL** FFmpeg (BtbN `n8.1.2-34-g9b6c8969e0`, glibc 2.28 baseline) instead of the distribution's GPL build. CMake refuses a Release build without `-DFTHR_FFMPEG_ROOT`; the engine carries a `$ORIGIN` RPATH so it loads the bundled libraries; every shipped library is sha256-verified against `tools/ffmpeg_manifest_linux.json`. The final PySide6 AppImage passes **109 licence/runtime checks, 0 failed**, and is 206 MiB. |
| ~~**Bare-name external tool calls**~~ | Resolved 2026-08-06. `hyprctl`, `xdotool`, `xprop`, `grim`, `nc` and `xdg-open` were invoked by bare name, so `PATH` decided which binary ran and a missing tool surfaced as a swallowed `FileNotFoundError`. Now resolved once to an absolute path through `core/linux_tools.py`, cached, logged at startup, with required/optional classification. 13 new tests including `PATH` shadowing. |
| ~~**Short replay saves**~~ (AUDIT-042) | Resolved 2026-08-14. Timestamp-driven selection retains the preceding keyframe and uses MP4 edit-list presentation instead of discarding footage through the next keyframe. Real WGC/NVENC saves at 30 s and 60 s were exact and fully decoded; see `docs/AUDIT-042-SHORT-SAVE-DURATION.md`. |
| ~~**Linux FFmpeg header/library ABI mismatch**~~ (AUDIT-046) | Resolved 2026-08-14. Release linked pinned FFmpeg 8.1 libraries but could compile against system headers. CMake now gives the engine and FFmpeg-backed tests the pinned include directory; the actual-MP4 test exposed and verifies the fix. |
| ~~**Product settings claimed inactive values**~~ | Resolved for the alpha surface 2026-08-21. Replay/FPS limits now match both engines, restart-required settings keep requested and active values separate, and a setting becomes active only after a fresh healthy capture generation. Windows's ignored P1–P7 selector is hidden. |
| ~~**Linked imports could delete originals**~~ | Resolved 2026-08-21. Imported folders remain links, but ownership is classified with normalized real paths and generic Delete is disabled/guarded for external originals. Removing a folder from FTHR only removes the registration. |
| ~~**Base save was exposed as final bytes**~~ | Resolved 2026-08-21. Engine `CLIP_SAVED` still means atomic base commit; the application now gates playback/edit/upload/delete/thumbnail work until optional processing reaches a terminal readiness state. |
| ~~**Linux default source could be a microphone**~~ | Resolved in source 2026-08-21. Normal capture resolves the default sink's monitor source and otherwise reports video-only; real GNOME/KDE/wlroots runtime content remains unverified. |
| ~~**No-backend Linux process remained alive**~~ | Resolved 2026-08-21. Recovery exhaustion now ends the engine. Native CTest and a pure-X11 WSL smoke both terminate within the bound. |

## Unverified — treat as unknown, not as working

- **No Linux desktop capture has ever been confirmed to contain a picture.**
  The pipeline was verified end to end on Ubuntu 24.04 / WSL2 (engine → ring
  buffer → NVENC → valid decodable MP4 with AAC audio), but the captured frames
  measured **all black** (luma min 0, max 0, one distinct value). XWayland under
  WSLg has no root-window content to grab. The plumbing works; the picture is
  unproven. See `docs/SUPPORTED_PLATFORMS.md`.
- **No real Linux desktop was tested**: Hyprland, KDE Plasma, GNOME and bare
  metal are all `NOT RUN`. Neither Wayland capture backend
  (`wlr-screencopy`, `ext-image-copy-capture`) has succeeded in a representative
  desktop session. Historical WSLg/x11grab evidence does not qualify the current
  alpha build because x11grab is now disabled.
- **No Linux hotkey has ever fired.** The Hyprland auto-config path
  (`~/.config/hypr/fthr-hotkeys.conf` + `hyprctl reload`) is untested.
- **The packaged Windows GUI still needs a final installed-app walkthrough.**
  Source-mode Windows tests exercised the visible main window, tray restore,
  background replay, clip notification, screenshots, and graceful exit. The
  same complete workflow has not yet been repeated through the final installer.
- **Windows duration, audio, and background replay are physically verified only
  on the current NVIDIA qualification host.** Current clips with system audio
  and microphone fully decode and preserve duration; raw fallback,
  games/fullscreen, device-loss recovery, AMD, Intel, and Windows 11 app stems
  remain unverified where stated in the support matrix.
- **The Windows installer lifecycle is only partially qualified.** Real update,
  uninstall, reinstall, autostart preservation, and user-data preservation
  passed. Final installed GUI/capture smoke, custom-path coverage, optional
  settings removal, code signing, and SmartScreen reputation remain open. The
  Linux AppImage has not run on a representative bare-metal desktop.
- Untested on Linux: multi-monitor, monitor switching, resolution changes,
  fractional scaling, fullscreen games, lock/unlock, suspend/resume, device
  removal during capture.
- **No two-hour / 50-save release soak has been run.** Bounded 30-, 60-, and
  300-second replay measurements and shorter save/screenshot/lifecycle stress
  runs passed, but long-duration gaming and repeated viewer/device recovery
  remain release qualification work.
- **The C++ engines now have focused native tests**, but not comprehensive engine
  coverage. AUDIT-042 adds ten CTest targets around replay timing, audio,
  transactional save, recovery and actual MP4 output.

## Platform limitations

### Linux
- **Global hotkeys only work fully on Hyprland**, where binds are written
  automatically. On **KDE, GNOME and X11** you must bind keys manually. Get the
  exact command from Settings → Hotkeys; it looks like:
  ```
  echo -n "save_clip" | /usr/bin/nc -U $XDG_RUNTIME_DIR/fthr/hotkey.sock
  ```
  Direct key capture needs root and is disabled by design. The app names your
  desktop and the command to bind rather than just saying hotkeys are dead.
- The hotkey socket moved out of `/tmp` (AUDIT-003b). It now lives in
  `$XDG_RUNTIME_DIR/fthr/` (dir `0700`, socket `0600`), falling back to
  `~/.fthr/run/`. A stale `/tmp/fthr_hotkey.sock` from an older build is removed
  on startup **only** if it is a dead socket you own.
- Wayland capture needs `wlr-screencopy` (wlroots compositors such as Hyprland,
  Sway and river) or `ext-image-copy-capture`. If the running compositor exposes
  neither protocol, the alpha engine reports that no safe backend is available
  and exits after bounded recovery. X11/x11grab is deliberately disabled while
  AUDIT-044 remains open.
- Optional tools (`hyprctl`, `xdotool`, `xprop`, `grim`, `nc`, `xdg-open`) are
  resolved once through `PATH` to an absolute path and logged at startup. A
  missing tool now produces a message naming the tool, what breaks, and what to
  install — it used to fail silently.
- `sounddevice` needs the **system** PortAudio library (`libportaudio2` /
  `portaudio`). Without it the app fails at import with
  `OSError: PortAudio library not found`, and no amount of pip installing helps.

### Windows
- **Clip paths are capped at 255 characters.** A long profile name plus a long
  game name can exceed it; the save is refused with an explanation. Use a
  shorter clips folder if you hit it.

## Behaviour you may notice

- **"FTHR Clips is already running"** on launch — intended. Only one instance is
  allowed per user; a second one used to corrupt the first one's hotkeys and
  engine. If no window is visible, end the running `FTHRClips` process.
- **The UI can freeze for up to 1 second** when saving if the engine is
  unresponsive. Known (AUDIT-011), not yet fixed.
- Some optional/cleanup failures remain log-only. The exception gate tracks the
  reviewed baseline; final clip-processing failures now surface a READY WITH
  WARNING or FINALIZATION FAILED result instead of unconditional success.
- **AUDIT-047 (P2): Linux encoded snapshot copies hold the ring mutex.** A
  synthetic 60-second/16-Mbps snapshot held it for about 54 ms; a future change
  should move the deep copy outside the global producer lock.

## Privacy

- The **upload auth token is stored in plaintext** in `~/.fthr/settings.json`.
  Do not put a high-value token there during alpha.
- Upload is **off by default** and only ever targets the endpoint you configure.
  There is no telemetry and no analytics of any kind.
- On Linux the hotkey socket is now owner-only (mode 0600). Before this build,
  any local user on the same machine could trigger a screenshot of your screen.

## Not implemented

Carried over from earlier notes — verify against the current build before
relying on any of these:

- Community / Get Invite / Account buttons are visual only.
- Auto-clipping is not enabled.
- Windows 11 per-app audio controls are code-integrated but remain hidden when
  the operating system cannot provide official process-loopback sources.
  Windows 10 intentionally exposes only Master, System Audio, and Microphone.

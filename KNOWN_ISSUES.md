# Known Issues — 1.0.0-alpha

Honest list of what is broken, unverified, or missing. Read before filing a bug.

**When reporting anything, attach `~/.fthr/logs/fthr.log`** (rotates at 2 MB).
Without it most reports are not actionable.

---

## Blocking public release

| Issue | Detail |
|---|---|
| **Bundled asset provenance is unproven** (AUDIT-013) | The Qt binding has been migrated to PySide6 6.11.1 under its LGPLv3 option, and both artifact gates reject PyQt/GPL-only Qt modules. The repository still contains no durable authorship/licence evidence for its logos/icons, four MP3 sounds, or `Oswald-Bold.ttf`. The owner must document those rights or replace/remove the files before public release. |

## Resolved since the audit

| Issue | Resolution |
|---|---|
| ~~**GPL FFmpeg**~~ (AUDIT-005) | Resolved 2026-08-05. The `--enable-gpl` build was replaced with the BtbN **LGPL** build `n8.1.2-21-gce3c09c101` (ABI-identical, avcodec-62). Software fallbacks moved x264 → libopenh264 and x265 → libkvazaar; NVENC/AMF/QSV untouched. `imageio-ffmpeg` (also GPL, and never actually bundled on Windows — which is why watermark, crop and export silently no-opped) was removed entirely in favour of `core/ffmpeg_tools.py`. Licence texts now ship inside the bundle; `tools/verify_release_licenses.py` gates it in CI. |
| ~~**No version control**~~ (AUDIT-008) | Resolved 2026-08-06. The authoritative tree is a git repository with `.gitignore`, `.gitattributes`, a documented source of truth (`docs/SOURCE_OF_TRUTH.md`) and release gates (`docs/RELEASE_CHECKLIST.md`). No tag exists yet — see the blocker above. |
| ~~**Unpinned dependencies**~~ (AUDIT-009) | Resolved 2026-08-06. `requirements-alpha.txt` pins the full transitive closure; `requirements.in` holds the direct list. Verified by two clean installs. |
| ~~**Hotkey socket in world-writable /tmp**~~ (AUDIT-003b) | Resolved 2026-08-06. AUDIT-003 fixed the socket *mode*; the *path* was still `/tmp/fthr_hotkey.sock`, which any local user could squat — and the old code then ran an unconditional `unlink()` on it, either deleting a stranger's file or (under the sticky bit) failing and leaving hotkeys dead indefinitely. The socket moved to `$XDG_RUNTIME_DIR/fthr/` and now refuses to remove anything that is not a dead socket owned by you. 16 new tests, verified on Linux. |
| ~~**No Linux AppImage could be built**~~ (AUDIT-014) | Resolved 2026-08-06 and revalidated 2026-08-14. The Linux engine is compiled against, and ships with, a pinned **LGPL** FFmpeg (BtbN `n8.1.2-34-g9b6c8969e0`, glibc 2.28 baseline) instead of the distribution's GPL build. CMake refuses a Release build without `-DFTHR_FFMPEG_ROOT`; the engine carries a `$ORIGIN` RPATH so it loads the bundled libraries; every shipped library is sha256-verified against `tools/ffmpeg_manifest_linux.json`. The final PySide6 AppImage passes **109 licence/runtime checks, 0 failed**, and is 206 MiB. |
| ~~**Bare-name external tool calls**~~ | Resolved 2026-08-06. `hyprctl`, `xdotool`, `xprop`, `grim`, `nc` and `xdg-open` were invoked by bare name, so `PATH` decided which binary ran and a missing tool surfaced as a swallowed `FileNotFoundError`. Now resolved once to an absolute path through `core/linux_tools.py`, cached, logged at startup, with required/optional classification. 13 new tests including `PATH` shadowing. |
| ~~**Short replay saves**~~ (AUDIT-042) | Resolved 2026-08-14. Timestamp-driven selection retains the preceding keyframe and uses MP4 edit-list presentation instead of discarding footage through the next keyframe. Real WGC/NVENC saves at 30 s and 60 s were exact and fully decoded; see `docs/AUDIT-042-SHORT-SAVE-DURATION.md`. |
| ~~**Linux FFmpeg header/library ABI mismatch**~~ (AUDIT-046) | Resolved 2026-08-14. Release linked pinned FFmpeg 8.1 libraries but could compile against system headers. CMake now gives the engine and FFmpeg-backed tests the pinned include directory; the actual-MP4 test exposed and verifies the fix. |

## Unverified — treat as unknown, not as working

- **No Linux desktop capture has ever been confirmed to contain a picture.**
  The pipeline was verified end to end on Ubuntu 24.04 / WSL2 (engine → ring
  buffer → NVENC → valid decodable MP4 with AAC audio), but the captured frames
  measured **all black** (luma min 0, max 0, one distinct value). XWayland under
  WSLg has no root-window content to grab. The plumbing works; the picture is
  unproven. See `docs/SUPPORTED_PLATFORMS.md`.
- **The Linux engine does not exit promptly when no display backend exists.**
  With both `DISPLAY` and `WAYLAND_DISPLAY` removed it reports the missing
  backend and exhausts three recovery attempts, but the host process remains
  alive beyond the test's eight-second bound. The full Linux Python suite
  therefore has one failure; AUDIT-013 did not modify that C++ lifecycle path.
- **No real Linux desktop was tested**: Hyprland, KDE Plasma, GNOME and bare
  metal X11 are all `NOT RUN`. Neither Wayland capture backend
  (`wlr-screencopy`, `ext-image-copy-capture`) has ever succeeded — WSLg's
  Weston implements neither, so only the x11grab fallback was exercised.
- **No Linux hotkey has ever fired.** The Hyprland auto-config path
  (`~/.config/hypr/fthr-hotkeys.conf` + `hyprctl reload`) is untested.
- **No visible desktop GUI walkthrough was performed** this cycle. The real
  MainWindow widget tree and packaged capture-card entrypoint were exercised
  offscreen with PySide6 on Windows and Linux, but nobody manually inspected a
  rendered window, tray integration, dialogs, or media playback.
- **Windows duration was re-verified only for WGC/NVENC video-only saves.** Five
  real 30/60-second clips were exact and fully decoded. Audio devices, raw
  fallback, games/fullscreen, and soak remain unverified this cycle.
- **The installer** has not been tested for install / update / uninstall. A
  206 MiB PySide6 AppImage was produced and its extracted contents passed the
  licence gate, but it was not run on a bare-metal desktop.
- Untested on Linux: multi-monitor, monitor switching, resolution changes,
  fractional scaling, fullscreen games, lock/unlock, suspend/resume, device
  removal during capture.
- **Only replay snapshot/save latency was measured; no soak test was done.**
  AUDIT-042 records the scoped measurements. Broader performance remains unknown.
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
- Wayland capture needs `wlr-screencopy` (wlroots compositors: Hyprland, Sway,
  river) or `ext-image-copy-capture`. **KWin and Mutter implement neither**, so
  KDE and GNOME fall back to x11grab via XWayland. The engine reports which
  backend it chose.
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
- **Failures are often silent.** 34 code paths swallow their errors, so a
  feature can stop working with nothing in the log. Being fixed.
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
- Per-source volume sliders persist but only affect multi-track clips.

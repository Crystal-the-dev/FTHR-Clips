# Known Issues — 1.0.0-alpha

Honest list of what is broken, unverified, or missing. Read before filing a bug.

**When reporting anything, attach `~/.fthr/logs/fthr.log`** (rotates at 2 MB).
Without it most reports are not actionable.

---

## Blocking public release

| Issue | Detail |
|---|---|
| **PyQt6 is GPL-3.0-only** (AUDIT-013) | Qt itself is LGPLv3, but the *bindings* are GPLv3, so any bundle containing PyQt6 is GPLv3 **as a whole** — including this one. FTHR's own source stays MIT (`LICENSE`), but a download must never be advertised as MIT. Three ways out: port to PySide6 (LGPLv3), release deliberately as GPLv3, or buy a commercial PyQt licence. Until one is chosen, this blocks a public release. |
| **No Linux AppImage can be built** (AUDIT-014) | Distribution FFmpeg is a GPL build (`--enable-gpl --enable-libx264 --enable-libx265`). The Linux engine links against it and PyInstaller bundles it, so `build_linux.sh` correctly refuses at the licence gate: *"47 checks, 12 failed … refusing to build the AppImage"*. The Windows side solved this in AUDIT-005 by bundling an LGPL FFmpeg; Linux has no equivalent yet. Either do the same for Linux, or accept GPLv3 (which AUDIT-013 forces anyway). |

## Resolved since the audit

| Issue | Resolution |
|---|---|
| ~~**GPL FFmpeg**~~ (AUDIT-005) | Resolved 2026-08-05. The `--enable-gpl` build was replaced with the BtbN **LGPL** build `n8.1.2-21-gce3c09c101` (ABI-identical, avcodec-62). Software fallbacks moved x264 → libopenh264 and x265 → libkvazaar; NVENC/AMF/QSV untouched. `imageio-ffmpeg` (also GPL, and never actually bundled on Windows — which is why watermark, crop and export silently no-opped) was removed entirely in favour of `core/ffmpeg_tools.py`. Licence texts now ship inside the bundle; `tools/verify_release_licenses.py` gates it in CI. |
| ~~**No version control**~~ (AUDIT-008) | Resolved 2026-08-06. The authoritative tree is a git repository with `.gitignore`, `.gitattributes`, a documented source of truth (`docs/SOURCE_OF_TRUTH.md`) and release gates (`docs/RELEASE_CHECKLIST.md`). No tag exists yet — see the blocker above. |
| ~~**Unpinned dependencies**~~ (AUDIT-009) | Resolved 2026-08-06. `requirements-alpha.txt` pins the full transitive closure; `requirements.in` holds the direct list. Verified by two clean installs. |
| ~~**Hotkey socket in world-writable /tmp**~~ (AUDIT-003b) | Resolved 2026-08-06. AUDIT-003 fixed the socket *mode*; the *path* was still `/tmp/fthr_hotkey.sock`, which any local user could squat — and the old code then ran an unconditional `unlink()` on it, either deleting a stranger's file or (under the sticky bit) failing and leaving hotkeys dead indefinitely. The socket moved to `$XDG_RUNTIME_DIR/fthr/` and now refuses to remove anything that is not a dead socket owned by you. 16 new tests, verified on Linux. |
| ~~**Bare-name external tool calls**~~ | Resolved 2026-08-06. `hyprctl`, `xdotool`, `xprop`, `grim`, `nc` and `xdg-open` were invoked by bare name, so `PATH` decided which binary ran and a missing tool surfaced as a swallowed `FileNotFoundError`. Now resolved once to an absolute path through `core/linux_tools.py`, cached, logged at startup, with required/optional classification. 13 new tests including `PATH` shadowing. |

## Unverified — treat as unknown, not as working

- **No Linux desktop capture has ever been confirmed to contain a picture.**
  The pipeline was verified end to end on Ubuntu 24.04 / WSL2 (engine → ring
  buffer → NVENC → valid decodable MP4 with AAC audio), but the captured frames
  measured **all black** (luma min 0, max 0, one distinct value). XWayland under
  WSLg has no root-window content to grab. The plumbing works; the picture is
  unproven. See `docs/SUPPORTED_PLATFORMS.md`.
- **No real Linux desktop was tested**: Hyprland, KDE Plasma, GNOME and bare
  metal X11 are all `NOT RUN`. Neither Wayland capture backend
  (`wlr-screencopy`, `ext-image-copy-capture`) has ever succeeded — WSLg's
  Weston implements neither, so only the x11grab fallback was exercised.
- **No Linux hotkey has ever fired.** The Hyprland auto-config path
  (`~/.config/hypr/fthr-hotkeys.conf` + `hyprctl reload`) is untested.
- **The GUI has not been launched** on either platform this cycle. The test
  suite covers logic; nobody has seen a window.
- **The Windows engine has not been re-verified at runtime**, and no clip has
  been saved on Windows this cycle.
- **The installer** has not been tested for install / update / uninstall, and
  **no AppImage has been produced** (the licence gate blocks it — see below).
- Untested on Linux: multi-monitor, monitor switching, resolution changes,
  fractional scaling, fullscreen games, lock/unlock, suspend/resume, device
  removal during capture.
- **No performance or soak testing** was done on either platform. Any
  performance claim you read elsewhere is not backed by measurement.
- **The C++ engines (~12k LOC) have zero automated tests** and were not reviewed.

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

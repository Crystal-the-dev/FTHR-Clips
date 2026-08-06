# Known Issues — 1.0.0-alpha

Honest list of what is broken, unverified, or missing. Read before filing a bug.

**When reporting anything, attach `~/.fthr/logs/fthr.log`** (rotates at 2 MB).
Without it most reports are not actionable.

---

## Blocking public release

| Issue | Detail |
|---|---|
| **PyQt6 is GPL-3.0-only** (AUDIT-013) | Qt itself is LGPLv3, but the *bindings* are GPLv3, so any bundle containing PyQt6 is GPLv3 **as a whole** — including this one. FTHR's own source stays MIT (`LICENSE`), but a download must never be advertised as MIT. Three ways out: port to PySide6 (LGPLv3), release deliberately as GPLv3, or buy a commercial PyQt licence. Until one is chosen, this blocks a public release. |

## Resolved since the audit

| Issue | Resolution |
|---|---|
| ~~**GPL FFmpeg**~~ (AUDIT-005) | Resolved 2026-08-05. The `--enable-gpl` build was replaced with the BtbN **LGPL** build `n8.1.2-21-gce3c09c101` (ABI-identical, avcodec-62). Software fallbacks moved x264 → libopenh264 and x265 → libkvazaar; NVENC/AMF/QSV untouched. `imageio-ffmpeg` (also GPL, and never actually bundled on Windows — which is why watermark, crop and export silently no-opped) was removed entirely in favour of `core/ffmpeg_tools.py`. Licence texts now ship inside the bundle; `tools/verify_release_licenses.py` gates it in CI. |
| ~~**No version control**~~ (AUDIT-008) | Resolved 2026-08-06. The authoritative tree is a git repository with `.gitignore`, `.gitattributes`, a documented source of truth (`SOURCE_OF_TRUTH.md`) and release gates (`RELEASE_CHECKLIST.md`). No tag exists yet — see the blocker above. |
| ~~**Unpinned dependencies**~~ (AUDIT-009) | Resolved 2026-08-06. `requirements-alpha.txt` pins the full transitive closure; `requirements.in` holds the direct list. Verified by two clean installs. |

## Unverified — treat as unknown, not as working

- **Linux has never been run** in this audit cycle: no build, no launch, no
  test. This includes two fixes written for Linux (single-instance lock, hotkey
  socket permissions).
- **The GUI has not been launched** on Windows this cycle. The test suite covers
  logic only.
- **Neither engine has been rebuilt** from a clean checkout.
- **The installer** has not been tested for install / update / uninstall.
- **No performance or soak testing** was done. Any performance claim you read
  elsewhere is not backed by measurement.
- **The C++ engines (~12k LOC) have zero automated tests** and were not reviewed.

## Platform limitations

### Linux
- **Global hotkeys only work fully on Hyprland**, where binds are written
  automatically. On **KDE, GNOME and X11** you must bind keys manually:
  ```
  echo -n "save_clip" | nc -U /tmp/fthr_hotkey.sock
  ```
  Direct key capture needs root and is disabled by design. The app warns when it
  detects this situation.
- Requires `nc`, `grim`, and `xdotool` (KDE/GNOME game detection). Missing tools
  currently cause a silent feature failure rather than a clear message.
- Wayland only for capture (wlr-screencopy); X11 is a fallback path.

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

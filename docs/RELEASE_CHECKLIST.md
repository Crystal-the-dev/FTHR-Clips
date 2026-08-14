# Release Checklist — v1.0.0-alpha

A tag is a claim that survives everyone who made it. **No tag is created until
every gate below passes.** If a gate cannot be met, the honest move is to
record it as `NOT RUN` and not tag.

Legend: `PASS` verified · `FAIL` verified broken · `NOT RUN` never executed —
**never write `PASS` for something that was not actually performed.**

---

## Current status: 🚫 DO NOT TAG

**Decision required: AUDIT-013 — bundled asset provenance.**

The Qt blocker has been technically closed by selecting PySide6 6.11.1 under
its LGPLv3 option and gating both Windows and Linux artifacts. FTHR's own source
remains MIT and the downloadable bundle includes separately licensed LGPL and
permissive components.

The repository does not prove authorship or redistribution rights for the FTHR
logos/icons, four MP3 sounds, and `Oswald-Bold.ttf`. Before a public tag, the
owner must choose one of these routes:

| Option | Consequence |
|---|---|
| Document authorship and licence | Add durable provenance showing FTHR may redistribute every asset. |
| Replace/remove the files | Use newly authored or clearly licensed assets and record their terms. |

**AUDIT-014 is resolved (2026-08-06).** The Linux engine is compiled against and
ships a pinned LGPL FFmpeg; a Release build cannot fall back to the
distribution's GPL one. An AppImage now exists and passes the licence gate.

AUDIT-013's Qt migration is complete, but its status remains **DECISION
REQUIRED** until the asset evidence above exists.

### Linux release recommendation: 🟡 LINUX CONDITIONAL GO

Conditional on: (a) AUDIT-013 asset provenance resolved, and (b) at least one
bare-metal desktop session — Hyprland *and* one of KDE/GNOME — actually
recording a clip with visible content and firing a hotkey. What has been proven
is the build, the IPC, the encode and the file. What has **not** been proven is
that FTHR Clips records a Linux screen.

Everything else below is either already green or is honest, tracked work.

---

## 1. Licensing

| # | Gate | Status |
|---|---|---|
| 1.1 | AUDIT-005 — no GPL FFmpeg anywhere in the tree or the bundle | **PASS** — LGPL `n8.1.2-21-gce3c09c101`, all 10 shipped binaries verified against `tools/ffmpeg_manifest.json` sha256 |
| 1.2 | `imageio-ffmpeg` absent from the lock files, the environment and the bundle | **PASS** — excluded in `FTHR.spec`, asserted in CI, verified absent from the rebuilt bundle |
| 1.3 | `tools/verify_release_licenses.py --tree .` | **PASS in clean Linux staging; LOCAL FAIL in this worktree** — the gate correctly rejects a pre-existing ignored stale `FTHRcapture_linux/build/FTHRclips`; delete/rebuild that local artifact before tagging |
| 1.4 | Third-party licence texts ship *inside* the artifact | **PASS** — `LICENSE`, `THIRD_PARTY_NOTICES.md`, `licenses/` are bundled by both specs and copied into the AppDir |
| 1.5 | No distributable described as MIT | **PASS** — README, About dialog and `LICENSE` all state the split; CI greps for regressions |
| 1.6 | **AUDIT-013 — approved Qt binding/runtime and complete ownership evidence** | **DECISION REQUIRED** — PySide6/LGPL artifact gates pass; owner must prove or replace bundled assets |

## 2. Source control and hygiene

| # | Gate | Status |
|---|---|---|
| 2.1 | Authoritative tree is a git repository | **PASS** |
| 2.2 | Source of truth documented; stale copies cannot be confused with it | **PASS** — `SOURCE_OF_TRUTH.md`; the stale copies themselves still need the manual archive step recorded there |
| 2.3 | `.gitignore` / `.gitattributes` reviewed; no source accidentally ignored | **PASS** |
| 2.4 | No secrets, tokens, keys or user state in the tree or in history | **PASS** — `tools/scan_repo_hygiene.py` clean |
| 2.5 | No blob over 2 MB in history | **PASS** — enforced in CI |
| 2.6 | Working tree clean, everything committed | verify at tag time |

## 3. Dependencies

| # | Gate | Status |
|---|---|---|
| 3.1 | Alpha lock pins the full transitive closure, no `>=` | **PASS** — `requirements-alpha.txt` |
| 3.2 | Clean installs contain only the approved Qt binding | **PASS** — fresh Windows CPython 3.14.3 and Linux CPython 3.12.3 environments contain PySide6 6.11.1 and no PyQt package |
| 3.3 | `pip check` reports no conflicts | **PASS** |
| 3.4 | Imports succeed from a clean install | **PASS** — both venvs |
| 3.5 | Tests run from clean selected-binding environments | **PARTIAL** — Windows 378 passed / 30 skipped; Linux 406 passed / 1 skipped / 1 pre-existing no-display lifecycle failure |
| 3.6 | PyInstaller analysis succeeds from the locked environment | **PASS** — clean Windows onedir and Linux AppImage builds, exit 0 |

## 4. Version consistency

| # | Gate | Status |
|---|---|---|
| 4.1 | Single source of truth for the product version | **PASS** — `FTHR_UI/version.py` |
| 4.2 | `tools/verify_version_consistency.py` | **PASS** |
| 4.3 | Windows `.exe` reports the version in file properties | **PASS** — ProductVersion / FileVersion = `1.0.0-alpha`, verified on the rebuilt bundle (previously the resource was absent entirely) |
| 4.4 | Installer version matches | **PASS** — `installer_windows.iss`, gated |
| 4.5 | AppImage filename carries the version | **PASS** — derived in `build_linux.sh` |
| 4.6 | UI, About dialog and log banner report the version | **PASS** |
| 4.7 | Shared-memory contract changes carry a new mapping version | **PASS** — deliberately bumped to `FTHR_SharedMemory_v4` for typed capture-health fields (AUDIT-022/023/035) |

## 5. Code and contract

| # | Gate | Status |
|---|---|---|
| 5.1 | `python -m pytest tests/` green | **FAIL (Linux only)** — Windows **378 passed / 30 skipped**; Linux **406 passed / 1 skipped / 1 failed** because the no-display engine remains alive after recovery exhaustion; AUDIT-013 changed no C++/recovery code |
| 5.2 | `python -m ruff check .` clean | **PASS** |
| 5.3 | `python -m compileall FTHR_UI tests tools` clean | **PASS** |
| 5.4 | `tools/verify_shared_memory_contract.py` | **PASS** — 23 fields, 2712 B (win32) / 4248 B (linux), enums and reserved slots 4–9 intact |
| 5.5 | C++ engines have automated tests | **PASS (Linux-scoped)** — native CTest **10/10 passed**, including recovery, duration, transactional save, Wayland bounded dispatch, timestamps, and actual short-MP4 integration |

## 6. Builds

| # | Gate | Status |
|---|---|---|
| 6.1 | Windows engine builds clean (MSBuild, Release x64) | **PASS** — rebuilt 2026-08-05 with VS 2022 |
| 6.2 | Windows bundle builds clean (PyInstaller) | **PASS** — rebuilt 2026-08-14 from the locked PySide-only venv; 546,312,457-byte onedir, 60/60 artifact gates |
| 6.3 | Linux engine builds clean (CMake, Release) | **PASS** — clean Release rebuild on Ubuntu 24.04; bundle-relative pinned-FFmpeg RPATH/DT_NEEDED gate passes |
| 6.4 | Linux AppImage builds | **PASS** — 215,524,544-byte AppImage; AppDir and extracted AppImage each pass 109/109 licence/runtime gates |
| 6.5 | CI green on all jobs | **NOT RUN** — the workflow has never executed; there is no remote yet |

## 7. Runtime verification — the part no CI can do for you

| # | Gate | Status |
|---|---|---|
| 7.1 | Windows: GUI launches | **NOT RUN** |
| 7.2 | Windows: a real clip is captured via the hotkey and plays back | **NOT RUN** |
| 7.3 | Windows: installer install → launch → update → uninstall | **NOT RUN** |
| 7.4 | Windows: uninstall removes what it claims to | **NOT RUN** |
| 7.5 | Linux: engine starts and captures on a real compositor | **PARTIAL** — engine starts, maps shared memory, captures and encodes on Ubuntu 24.04/WSL2 via the **x11grab fallback** with `av1_nvenc`. **No real compositor was involved**: WSLg's Weston implements neither `wlr-screencopy` nor `ext-image-copy-capture`, and the engine says so clearly. Hyprland/KDE/GNOME remain `NOT RUN`. |
| 7.5b | Linux: a clip is saved, decodes, and contains a picture | **PARTIAL** — clips save and are valid (AV1 1280×720@30 + AAC 48 kHz stereo, both `start_time=0`, full decode 0 errors, unicode paths OK, invalid paths refused cleanly). **The frames are all black** (luma min 0, max 0, 1 distinct value) — XWayland under WSLg has no content to grab. The pipeline is proven; the picture is not. |
| 7.6 | Linux: AppImage launches and captures | **PARTIAL** — packaged offscreen `--card-process` starts/exits successfully; real X11/Wayland desktop capture remains NOT RUN |
| 7.7 | Linux: the Linux-only fixes are exercised | **PASS** — single-instance `flock` verified with real processes (acquire → second refused → SIGKILL holder → third acquires; lock `~/.fthr/fthr.lock` uid=you mode=600). Hotkey socket verified at `$XDG_RUNTIME_DIR/fthr/hotkey.sock`, dir 0700, socket 0600, 16 new tests. |
| 7.7b | Linux: hotkeys actually fire from a compositor bind | **NOT RUN** — no compositor available |
| 7.7c | Linux: audio capture | **PASS (headless)** — PulseAudio capture starts (48 kHz stereo float32) and a clip with a real AAC track was produced. Device switching, disconnection and "no microphone" remain `NOT RUN`. |
| 7.8 | Performance / soak measurement | **NOT RUN** — no 2-hour / 50-clip soak on either platform. No performance claim may be published. |
| 7.9 | Quality comparison OpenH264 vs. the old x264 fallback | **NOT RUN** |

> Gates 7.1–7.9 are the honest gap. The alpha has been *built* and its logic is
> tested, but it has not been *used* in this cycle on either platform. Do not
> describe it as working.
>
> Per project policy, anything Linux-side runs on a **test instance or VM
> only** — never against the live FTHR production host.

## 8. Documentation

| # | Gate | Status |
|---|---|---|
| 8.1 | `KNOWN_ISSUES.md` reflects reality | **PASS** — updated 2026-08-14; AUDIT-013 owner asset-provenance decision remains the licensing blocker |
| 8.2 | `RELEASE_NOTES.md` matches what actually ships | review at tag time |
| 8.3 | `THIRD_PARTY_NOTICES.md` complete and current | **PASS** |
| 8.4 | `CONTRIBUTING.md` describes the real layout and build | **PASS** |
| 8.5 | `SOURCE_OF_TRUTH.md` present | **PASS** |
| 8.6 | `BUILDING.md` matches the actual build (Python 3.14, no imageio-ffmpeg) | **PASS** |
| 8.7 | `BUILDING.md` (Linux) matches the actual build | **PASS** — written from the verified run |
| 8.8 | `TESTING.md` separates build / headless / desktop tests | **PASS** |
| 8.9 | `SUPPORTED_PLATFORMS.md` states a real, narrow support matrix | **PASS** — one environment tested, everything else `NOT RUN` |

---

## Tagging procedure — only once every gate above is PASS

```bash
# 1. Re-run every automated gate on a clean tree.
python -m pytest tests/
python -m ruff check .
python tools/verify_release_licenses.py --tree .
python tools/verify_version_consistency.py
python tools/verify_shared_memory_contract.py
python tools/scan_repo_hygiene.py

# 2. Confirm the tree is clean.
git status --porcelain     # must print nothing

# 3. Annotated tag — the message is the record of what was verified.
git tag -a v1.0.0-alpha -m "FTHR Clips 1.0.0-alpha

Verified: <fill in what was actually run, by whom, on what hardware>
Known gaps: <fill in what was NOT run>"

# 4. Push the tag. CI's tag-guard re-checks it against FTHR_UI/version.py.
git push origin v1.0.0-alpha
```

Artefacts are attached to a GitHub Release **by hand**, after 7.1–7.6 have
actually been performed. CI deliberately publishes nothing: uploading an
installer no human has installed is how an untested build becomes a release.

## If a gate fails after tagging

Do not move the tag. Tags are immutable by convention because people build
tooling on them. Fix forward and tag `v1.0.1-alpha`.

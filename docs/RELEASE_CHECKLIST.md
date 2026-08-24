# Release Checklist — v1.0.0-alpha

A tag is a claim that survives everyone who made it. **No tag is created until
every gate below passes.** If a gate cannot be met, the honest move is to
record it as `NOT RUN` and not tag.

Legend: `PASS` verified · `FAIL` verified broken · `NOT RUN` never executed —
**never write `PASS` for something that was not actually performed.**

---

## Current status: 🚫 DO NOT TAG

**AUDIT-013 resolved — Qt and bundled asset licensing.**

The Qt blocker has been technically closed by selecting PySide6 6.11.1 under
its LGPLv3 option and gating both Windows and Linux artifacts. FTHR's own source
remains MIT and the downloadable bundle includes separately licensed LGPL and
permissive components.

The predecessor media had no sufficient redistribution evidence. Every such
image was replaced with deterministic project-generated artwork, all four MP3s
were removed in favour of generated WAVs, and Oswald 4.103 was byte-matched to
its pinned OFL-1.1 upstream. The source and built artifacts use a per-file
hash/licence/origin allowlist; see `AUDIT-013-ASSET-PROVENANCE.md`.

**AUDIT-014 is resolved (2026-08-06).** The Linux engine is compiled against and
ships a pinned LGPL FFmpeg; a Release build cannot fall back to the
distribution's GPL one. A historical AppImage passed the licence gate; the
current source still requires a new Release AppImage build and runtime test.

AUDIT-013 is **RESOLVED**. The `DO NOT TAG` state above remains because real
desktop/capture and installer lifecycle gates below are still `NOT RUN` or
`PARTIAL`, not because of Qt or asset licensing.

### Linux release recommendation: 🚫 EXPERIMENTAL / DO NOT INCLUDE IN PUBLIC ALPHA

The alpha-safe build now disables X11/x11grab while AUDIT-044 remains open. A
future Linux cohort requires at least one real supported Wayland session to
record visible content and desktop audio, fire a hotkey, shut down cleanly and
pass the current AppImage lifecycle. WSL proves the build and bounded failure,
not representative desktop capture.

Everything else below is either already green or is honest, tracked work.

---

## 1. Licensing

| # | Gate | Status |
|---|---|---|
| 1.1 | AUDIT-005 — no GPL FFmpeg anywhere in the tree or the bundle | **PASS** — LGPL `n8.1.2-21-gce3c09c101`, all 10 shipped binaries verified against `tools/ffmpeg_manifest.json` sha256 |
| 1.2 | `imageio-ffmpeg` absent from the lock files, the environment and the bundle | **PASS** — excluded in `FTHR.spec`, asserted in CI, verified absent from the rebuilt bundle |
| 1.3 | `tools/verify_release_licenses.py --tree .` | **PASS — current Windows checkout: 75 checks, 0 failed, 1 warning**; the warning is that pinned Linux FFmpeg is not vendored, so this checkout is not a Linux Release input. |
| 1.4 | Third-party licence texts ship *inside* the artifact | **PASS** — Windows carries 18 and Linux 17 files under `licenses/`, plus `LICENSE` and `THIRD_PARTY_NOTICES.md` |
| 1.5 | No distributable described as MIT | **PASS** — README, About dialog and `LICENSE` all state the split; CI greps for regressions |
| 1.6 | **AUDIT-013 — approved Qt binding/runtime and complete redistribution evidence** | **PASS** — 24 source assets are hash/origin/licence gated; the current Windows bundle contains exactly 19 approved asset files. Current Linux packaging remains `NOT RUN`. |

## 2. Source control and hygiene

| # | Gate | Status |
|---|---|---|
| 2.1 | Authoritative tree is a git repository | **PASS** |
| 2.2 | Source of truth documented; stale copies cannot be confused with it | **PASS** — `SOURCE_OF_TRUTH.md`; the stale copies themselves still need the manual archive step recorded there |
| 2.3 | `.gitignore` / `.gitattributes` reviewed; no source accidentally ignored | **PASS** |
| 2.4 | No secrets, tokens, keys or user state in the tree or in history | **PASS** — `tools/scan_repo_hygiene.py` clean |
| 2.5 | No blob over 2 MB in history | **PASS** — enforced in CI |
| 2.6 | Working tree clean, everything committed | verify at tag time |
| 2.7 | Public source delivery excludes unresolved predecessor asset blobs | **PASS for `git archive HEAD` only** — do not publish/mirror the pre-replacement Git history unless it is scrubbed or rights evidence is supplied |

## 3. Dependencies

| # | Gate | Status |
|---|---|---|
| 3.1 | Alpha lock pins the full transitive closure, no `>=` | **PASS** — `requirements-alpha.txt` |
| 3.2 | Clean installs contain only the approved Qt binding | **PASS** — fresh Windows CPython 3.14.3 and Linux CPython 3.12.3 environments contain PySide6 6.11.1 and no PyQt package |
| 3.3 | `pip check` reports no conflicts | **PASS** |
| 3.4 | Imports succeed from a clean install | **PASS** — both venvs |
| 3.5 | Tests run from clean selected-binding environments | **PARTIAL** — current Windows run: 518 passed / 34 skipped. Linux Python suite was not run in a pytest-equipped Linux environment; native Linux CTest is separately green. |
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
| 5.1 | `python -m pytest tests/` green | **PARTIAL** — Windows **518 passed / 34 skipped** at the final-completion HEAD. Linux-specific runtime cases skip on Windows; a full Linux Python run remains `NOT RUN`. |
| 5.2 | `python -m ruff check .` clean | **PASS** |
| 5.3 | `python -m compileall FTHR_UI tests tools` clean | **PASS** |
| 5.4 | `tools/verify_shared_memory_contract.py` | **PASS** — current layout: 29 fields, 2736 B (Windows) / 4272 B (Linux), enums and reserved slots 4–9 intact; still Shared Memory v4. |
| 5.5 | C++ engines have automated tests | **PASS** — current Windows native suite **391 checks**; Linux native CTest **10/10 passed**, including recovery, duration, transactional save, Wayland bounded dispatch, timestamps, and actual short-MP4 integration. |

## 6. Builds

| # | Gate | Status |
|---|---|---|
| 6.1 | Windows engine builds clean (MSBuild, Release x64) | **PASS** — rebuilt at the product-truth HEAD with VS 2022 Build Tools. |
| 6.2 | Windows bundle builds clean (PyInstaller) | **PASS (2026-08-24)** — the final-completion onedir bundle passed 83 Windows licence/asset checks and the installer lifecycle artifact gate. Final hashes are reported with the handoff. |
| 6.2a | Windows installer input/lifecycle contract | **PASS (source contract)** — stable AppId, update/repair/downgrade policy, safe data boundary, autostart ownership, VC++ guard, legacy opt-in and package metadata are verified by `tools/verify_windows_installer_lifecycle.py`; this is not a physical install claim. |
| 6.2b | Windows Authenticode signing | **DECISION REQUIRED** — build support accepts an external operator-owned signing command, but no release signing identity is configured in the repository or this checkout. Unsigned artifacts are local-qualification only. |
| 6.3 | Linux engine builds clean (CMake, Release) | **PARTIAL** — current source built and passed tests in a WSL development build using system FFmpeg. The required pinned-FFmpeg Release rebuild was not run. |
| 6.4 | Linux AppImage builds | **NOT RUN for current source** — historical artifact evidence does not qualify the X11-disabled/audio-corrected build. |
| 6.5 | CI green on all jobs | **NOT RUN** — the workflow has never executed; there is no remote yet |

## 7. Runtime verification — the part no CI can do for you

| # | Gate | Status |
|---|---|---|
| 7.1 | Windows: GUI launches | **PARTIAL** — source-mode normal launch, background launch, tray restore, close-to-tray, and exit passed physically; final installed-package walkthrough remains `NOT RUN`. |
| 7.2 | Windows: a real clip is captured via the hotkey and plays back | **PARTIAL** — current qualification captured and fully decoded H.264/HEVC/AV1 files, controlled system audio, system-plus-microphone audio, rapid saves, and a 30-second pre-UI tray save. Final packaged hotkey/player listening remains `NOT RUN`. |
| 7.3 | Windows: installer install → launch → update → uninstall | **PARTIAL (2026-08-24)** — a real existing-install update, default uninstall, fresh reinstall and checked legacy cleanup ran successfully against the generated Setup executable. Installed-app identity, Start Menu, exact autostart ownership and clip/settings snapshots were checked. Installed GUI/tray/capture launch remains `NOT RUN`; see `WINDOWS-INSTALLER-LIFECYCLE-QUALIFICATION-2026-08-24.md`. |
| 7.4 | Windows: uninstall removes what it claims to | **PARTIAL (2026-08-24)** — default uninstall removed product registration, Program Files payload, common shortcuts and the exact Run value while preserving the recorded clip and `.fthr` state snapshots. The optional interactive settings/cache-removal choice and a custom install location remain `NOT RUN`. |
| 7.5 | Linux: engine starts and captures on a real compositor | **NOT RUN** — WSLg exposes neither supported Wayland protocol; current alpha source refuses x11grab and exits after bounded recovery. Hyprland/KDE/GNOME remain `NOT RUN`. |
| 7.5b | Linux: a clip is saved, decodes, and contains a picture | **NOT RUN for current source** — older black-frame x11grab clips are historical and do not qualify the alpha-safe build. |
| 7.6 | Linux: AppImage launches and captures | **NOT RUN for current source** |
| 7.7 | Linux: the Linux-only fixes are exercised | **PASS** — single-instance `flock` verified with real processes (acquire → second refused → SIGKILL holder → third acquires; lock `~/.fthr/fthr.lock` uid=you mode=600). Hotkey socket verified at `$XDG_RUNTIME_DIR/fthr/hotkey.sock`, dir 0700, socket 0600, 16 new tests. |
| 7.7b | Linux: hotkeys actually fire from a compositor bind | **NOT RUN** — no compositor available |
| 7.7c | Linux: audio capture | **PARTIAL** — current WSL smoke resolved/opened `RDPSink.monitor` at 48 kHz stereo float32 and shut down cleanly. Saving/ffprobing current audio content, PipeWire, device switching and loss remain `NOT RUN`. |
| 7.8 | Performance / soak measurement | **PARTIAL** — 30/60/300-second replay, 1/2/4/8-track playback, save, screenshot, startup, background lifecycle, and bounded memory measurements are recorded in `FINAL-PERFORMANCE-QUALIFICATION.md`; no 2-hour / 50-clip soak was run. |
| 7.9 | Quality comparison OpenH264 vs. the old x264 fallback | **NOT RUN** |

> Gates 7.1–7.9 are the honest gap. The alpha has been *built* and its logic is
> tested, and the NVIDIA engine path has been physically exercised. The current
> GUI, installer and Linux desktop lifecycle have not; do not generalize the
> NVIDIA engine result into a public package/platform claim.
>
> Per project policy, anything Linux-side runs on a **test instance or VM
> only** — never against the live FTHR production host.

## 8. Documentation

| # | Gate | Status |
|---|---|---|
| 8.1 | `KNOWN_ISSUES.md` reflects reality | **PASS** — updated 2026-08-24; remaining hardware, package, Linux-runtime, signing, and soak gaps are listed explicitly. |
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

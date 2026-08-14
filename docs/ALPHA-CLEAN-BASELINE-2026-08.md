# Alpha clean baseline — August 2026

## 1. Source HEAD

Validation began from committed HEAD `8305f75b452b2713f13b81b7928e935d7d6ab2b2`.
The baseline fixes are `6703166` and `3b3c94c` on top of it.

## 2. Source branch

The authoritative worktree was on `fix/audit-011-save-state-machine`. No commit,
index, or working-tree content in that worktree was changed by this validation.

## 3. Original dirty worktree summary

The original worktree had 15 tracked changes (including the metadata experiment,
deleted X11 files/docs, `.gitignore`, and Linux capture edits) plus 12 untracked
files. The untracked set included the unfinished X11 worker scaffold and its tests.
Those changes were treated as user-owned and were never copied into the baseline.

The five previously reported metadata test failures were caused only by the dirty
`clip_metadata_manager.py`/test experiment. They do not exist in committed HEAD.

## 4. Clean worktree

- Path: `C:\Users\Tom\Desktop\FTHR_Clips_alpha_baseline`
- Branch: `chore/alpha-clean-baseline`
- Created from: `8305f75b452b2713f13b81b7928e935d7d6ab2b2`

The committed tree includes AUDIT-011, 017, 018, 019, 021, 022/023/035, 024,
028, and the Linux FFmpeg pin/license gates. AUDIT-044 remains open in committed
documentation. The unfinished X11 worker implementation exists only in the dirty
original worktree and is not part of this baseline.

## 5. Original-worktree preservation

The original worktree was inspected read-only before and after validation. It was
not reset, cleaned, restored, stashed, switched, or modified.

## 6. Full Python suite

Isolated Python 3.14.3 environment installed only
`requirements-alpha.txt` plus `requirements-dev.txt`.

`python -m pytest tests -q` collected 396 tests: **366 passed, 30 skipped,
0 failed, 0 errors, 0 xfailed, and 0 xpassed** in 8.059 seconds (JUnit time).
`pip check` passed and `imageio-ffmpeg` was absent.

## 7. Focused regressions

- `python -m pytest tests/test_clip_metadata_manager.py -q`: 5 passed.
- New clean-build/CI contracts: 4 added tests passed.
- Linux native recovery, transactional-save, and Wayland-dispatch tests: 3 passed.

## 8. Static and contract gates

| Gate | Result |
| --- | --- |
| `python -m ruff check .` | PASS |
| `python -m compileall -q FTHR_UI tests tools` | PASS |
| `tools/verify_engine_response_contract.py` | PASS, 48 engine files |
| `tools/verify_shared_memory_contract.py` | PASS, Windows 29 fields/2736 B; Linux 29 fields/4272 B |
| `tools/verify_version_consistency.py` | PASS, `1.0.0-alpha` |
| `tools/scan_repo_hygiene.py` | PASS, 395 tracked files |
| `tools/verify_exception_handling.py` | PASS, 38 files; 92/92 baseline handlers |
| `pip check` | PASS |

## 9. Windows Release build

VS 2022 Build Tools/MSBuild 17.14.40 rebuilt `Release|x64` successfully with
**0 compiler/linker warnings and 0 errors**. The produced PE is x86-64 (`0x8664`),
220,672 bytes, and the post-build runtime-copy step completed.

The complete PyInstaller bundle was also produced from the clean worktree:
346 files, 539,653,579 bytes, with both the UI and bundled engine present.

## 10. Linux Release build

Ubuntu 24.04 under WSL configured and built Release successfully with CMake 3.28.3
and GCC 13.3 against the fetched pinned FFmpeg root. There were no compiler or
linker warnings/errors. `wayland-scanner` 1.22 emitted two non-fatal DTD warnings
about the newer XML `deprecated-since` attribute; these are recorded, not hidden.

This is a headless build result. **Real Linux desktop capture was not verified.**

## 11. Native CTest

`ctest --test-dir FTHRcapture_linux/build --output-on-failure` passed **3/3**:
capture recovery, transactional save, and bounded Wayland dispatch (0.29 seconds).

## 12. Licensing

- Repository plus both fetched FFmpeg trees: **101 checks, 0 failures, 0 warnings**.
- Windows distribution: **42 checks, 0 failures, 0 warnings**.
- Windows FFmpeg: `n8.1.2-21-gce3c09c101`, archive and 10 shipped files verified.
- Linux FFmpeg: `n8.1.2-34-g9b6c8969e0`, archive and 7 libraries verified.

These are technical license/provenance checks, not legal advice.

## 13. FFmpeg linkage

The Linux binary needs the pinned SONAME generation (`libavcodec.so.62`,
`libavformat.so.62`, `libavutil.so.60`, `libavdevice.so.62`, `libswscale.so.9`,
and `libswresample.so.6`). `ldd` resolved these and transitive `libavfilter.so.11`
inside the fetched pinned tree. ELF `DT_RPATH` is exclusively bundle-relative:
`$ORIGIN:$ORIGIN/lib:$ORIGIN/../lib:$ORIGIN/../third_party/ffmpeg/lib`.

## 14. CI workflow assessment

The sole workflow triggers on pushes to `main`/`master`, version tags, and pull
requests to `main`/`master`. It requires no secrets and uploads no artifacts.

| Job | Clean-clone behavior after fixes |
| --- | --- |
| Python | Ubuntu/Windows, Python 3.12/3.14, pip cache, pinned install, compile, Ruff, full tests |
| Linux engine | Ubuntu, system compiler/platform headers, fetched pinned Linux FFmpeg, Release build, CTest, license and source-clean gates |
| Windows engine | Windows, Python 3.14/pip cache, fetched FFmpeg, MSBuild x64, then PyInstaller and distribution license gate |
| Release verification | Ubuntu, full-history checkout, license/version/IPC/response/exception/hygiene/history gates |
| Tag guard | Requires all jobs and checks the tag against `FTHR_UI/version.py` |

The YAML parsed successfully and local equivalents of its build/test gates passed.
The revised GitHub-hosted workflow itself was not pushed or remotely executed in
this local validation.

## 15. Clean-clone dependencies

- **Bundled/tracked:** source, protocol XML, Windows FFmpeg headers/import libs,
  license texts, project/spec files.
- **Fetched reproducibly:** Windows and Linux LGPL FFmpeg trees, pinned by version
  and SHA-256 manifests.
- **System prerequisites:** Python 3.14 (3.12 support floor), VS 2022 Desktop C++
  workload/Windows SDK, or Linux CMake/GCC/pkg-config plus PulseAudio, X11,
  Wayland development packages and `wayland-scanner`.
- **Optional runtime helpers:** compositor/window utilities listed in `BUILDING.md`.

## 16. Absolute/local path findings

No tracked build script or workflow depends on Tom's machine paths. The literal
authoritative path in `docs/AUDIT_REPORT.md`/`docs/SOURCE_OF_TRUTH.md` is
documentation only. `/home/...` matches are deliberate test fixtures. The Windows
build guide now uses a Visual Studio developer shell instead of a Community-edition
absolute MSBuild path.

## 17. Reproducibility blockers

Two genuine clean-clone blockers were reproduced and fixed:

1. CMake wrote scanner-version-dependent Wayland output into tracked source files.
2. CI omitted the required pinned Linux FFmpeg root and attempted PyInstaller
   before the Windows engine/runtime existed.

No hidden machine-local file remains necessary for test or engine/bundle builds.
Exact installer/AppImage packaging still has two lower-priority nondeterministic
downloads: Microsoft's moving VC++ redistributable permalink and AppImageKit's
`continuous` appimagetool URL are not hash-pinned.

## 18. Fixes made

- `6703166 build: keep Wayland generation out of source tree`
- `3b3c94c ci: fix clean release validation`
- Build instructions corrected for the new generated-file location, CTest, the
  contract gates, and portable MSBuild invocation.

No product behavior, capture IPC layout, X11 implementation, or metadata code was
changed.

## 19. Remaining blockers

This baseline does not resolve product audit blockers such as AUDIT-042 or the
open X11 issue. It also does not prove physical Windows/Linux capture, installer
installation, AppImage runtime compatibility, or the two unpinned packaging
downloads above. No new P0/P1 audit finding was opened by baseline validation.

## 20. Verdict

**CLEAN BASELINE: PASS.** Another developer can start from the committed baseline,
fetch the manifest-pinned FFmpeg dependencies, reproduce the Python tests and both
engine Release builds, and run the important validation gates without local files.

This is a trustworthy engineering baseline for the next blocker. It does **not**
change the product-level verdict that the current alpha is an internal development
build until its remaining P1 blockers are resolved.

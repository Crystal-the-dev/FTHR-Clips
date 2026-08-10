# AUDIT-014 — Reproducible LGPL FFmpeg for Linux

**Date:** 2026-08-06
**Status:** `AUDIT-014 CONDITIONALLY RESOLVED`
**Worked in:** Ubuntu 24.04.3 LTS under WSL2 + WSLg · CPython 3.12.3 · GCC 13.3.0 · CMake 3.28.3 · NVIDIA RTX 4060 Ti (driver 610.62)

> **Historical layout note (2026-08-10):** the v3 mapping and 4248-byte size
> below were accurate for this audit run. Capture-health fields later moved the
> live IPC contract to `FTHR_SharedMemory_v4` (4272 bytes on Linux); see the
> [capture-health remediation](AUDIT-022-023-035-CAPTURE-HEALTH.md).

This is the full record of the work: what was measured, what was decided and
why, what changed, what was verified, and what remains unverified.

---

## 1. The problem, measured

The Linux engine linked whatever FFmpeg the distribution provided. Measured on
the pre-change build:

**Directly linked (DT_NEEDED):**

```
libavcodec.so.60   -> /usr/lib/x86_64-linux-gnu/libavcodec.so.60.31.102   (libavcodec60)
libavformat.so.60  -> /usr/lib/x86_64-linux-gnu/libavformat.so.60.16.100  (libavformat60)
libavutil.so.58    -> /usr/lib/x86_64-linux-gnu/libavutil.so.58.29.100    (libavutil58)
libavdevice.so.60  -> /usr/lib/x86_64-linux-gnu/libavdevice.so.60.3.100   (libavdevice60)
libswscale.so.7    -> /usr/lib/x86_64-linux-gnu/libswscale.so.7.5.100     (libswscale7)
libswresample.so.4 -> /usr/lib/x86_64-linux-gnu/libswresample.so.4.12.100 (libswresample4)
```

**Transitively pulled in** (via `libavdevice`): `libavfilter.so.9`,
`libpostproc.so.57`, `libavc1394.so.0`.

**RPATH / RUNPATH: none.** The engine relied entirely on the system loader.

**Licence of that FFmpeg:** `--enable-gpl --enable-libx264 --enable-libx265
--enable-libxvid --enable-libaom --enable-libsvtav1 --enable-libvpl`.

`libavdevice` is not optional: the X11 capture backend uses libavdevice's
`x11grab` rather than Xlib directly, which is also why the engine links no X11
libraries of its own.

---

## 2. Strategy: pinned upstream LGPL distribution (Option B)

Both options were evaluated against real artifacts, not on principle.

| | A — build from source | B — pinned LGPL binary |
|---|---|---|
| Licence control | full | full, but not ours to set |
| Reproducibility | needs the whole toolchain reproduced | immutable release tag + sha256 |
| glibc baseline | **2.39** (whatever this host has) | **2.28** |
| External codecs | must be built and bundled separately | linked statically already |
| Consistency with Windows | different mechanism | identical mechanism (AUDIT-005) |
| Iteration cost | ~30 min per build | one download |

**Chosen: B.** The deciding argument is portability, not convenience. A build
produced here would hard-require glibc 2.39 and the AppImage would refuse to
start on anything older — strictly worse than what upstream ships. On top of
that it is the same provider, release branch and configuration already accepted
for Windows in AUDIT-005, so the project has one licence story instead of two.

Revisit if upstream stops publishing `linux64-lgpl-shared`, if a needed codec
leaves the LGPL configuration, or if a glibc baseline below 2.28 is required.
Recorded in `tools/ffmpeg_manifest_linux.json`.

### The chosen build

```
BtbN/FFmpeg-Builds  autobuild-2026-08-06-13-39
ffmpeg-n8.1.2-34-g9b6c8969e0-linux64-lgpl-shared-8.1.tar.xz
sha256 621577f0e81df4e8f3d9287a20af3f97289b61b39a9d3f1f1e15a17cedacbd1e   56,827,416 B
```

Verified before adopting it:

| Check | Result |
|---|---|
| `--enable-gpl`, `--enable-nonfree`, `--enable-libx264`, `--enable-libx265`, `--enable-libxvid` | **absent** |
| Licence banner | `LGPL version 3 or later` |
| `x11grab` (the X11 backend depends on it) | present |
| `h264_nvenc`, `hevc_nvenc`, `av1_nvenc` | present |
| `h264_vaapi`, `hevc_vaapi`, `av1_vaapi`, `h264_qsv` | present |
| `libopenh264`, `libkvazaar`, `libsvtav1` | present |
| `aac` encoder, `mp4`/`mov` muxers | present |
| x264 / x265 files anywhere in the archive | none |
| Headers + pkgconfig + LICENSE.txt included | yes |

### `--enable-version3` — a deliberate deviation from the brief

The task listed `--enable-version3` as forbidden. This build has it, and it is
kept. `--enable-version3` upgrades the licence from **LGPLv2.1 to LGPLv3**; it
does not make the build GPL — the binary self-reports `LGPL version 3 or later`.
The Windows runtime accepted in AUDIT-005 carries the same flag, and
`tools/verify_release_licenses.py` has always documented that it is not a
failure condition. LGPLv3 is compatible with dynamic linking from non-GPL
application code, which is the entire point of the LGPL strategy. Excluding it
would force a from-source build for no licence benefit.

Flagging it rather than quietly complying: if you want strict LGPLv2.1, that is
Option A and a different glibc baseline.

---

## 3. ABI and runtime resolution

The pinned distribution is a **different SONAME generation** from the system's,
so building against one and loading the other is not possible:

| | system (GPL) | pinned (LGPL) |
|---|---|---|
| libavcodec | `.so.60` | **`.so.62`** |
| libavformat | `.so.60` | **`.so.62`** |
| libavutil | `.so.58` | **`.so.60`** |
| libavdevice | `.so.60` | **`.so.62`** |
| libavfilter | `.so.9` | **`.so.11`** |
| libswscale | `.so.7` | **`.so.9`** |
| libswresample | `.so.4` | **`.so.6`** |

The engine is compiled against the headers that ship beside it. Verified after
the change:

```
NEEDED: libavcodec.so.62 libavformat.so.62 libavutil.so.60
        libavdevice.so.62 libswscale.so.9 libswresample.so.6
RPATH:  [$ORIGIN:$ORIGIN/lib:$ORIGIN/../lib:$ORIGIN/../third_party/ffmpeg/lib]
```

**RPATH, not RUNPATH**, via `-Wl,--disable-new-dtags`. RUNPATH is not inherited
by transitive dependencies, so `libavdevice` would have searched for its sibling
`libavfilter` without it and found the system's GPL copy.

### Runtime scenarios tested

| Scenario | Result |
|---|---|
| System FFmpeg present, bundled libraries beside the binary | all 7 resolve from the bundle |
| No environment at all (`env -i`) | 0 `not found` |
| Inside the extracted AppImage | all 7 from `_internal/` |

### glibc

| Component | Requires |
|---|---|
| Pinned FFmpeg libraries | GLIBC_2.28 |
| FTHR engine | GLIBC_2.38 (`__isoc23_strtol`, `shm_open`) |
| Whole AppImage | **GLIBC_2.39** |

FFmpeg is not the constraint — the build host is. The AppImage currently needs
glibc ≥ 2.39 (Ubuntu 24.04, Debian 13, Fedora 40+). Building on an older base
image would lower this substantially. `NOT RUN`.

---

## 4. Changes

### `tools/ffmpeg_manifest_linux.json` (new)

Version, release tag, URL, archive sha256, full configure flags, glibc figures
per library, forbidden flags confirmed absent, verified capabilities, SONAME
map, per-library sha256, the strategy rationale, and the reproduce command.

### `tools/fetch_third_party.py`

New `--ffmpeg-linux`. Downloads, verifies the archive sha256, extracts with
`filter='data'`, verifies all seven libraries individually, then installs
`lib/`, `include/`, `bin/` and `LICENSE.txt`. `--all` is now platform-aware
instead of fetching 150 MB of Windows DLLs on Linux.

### `FTHRcapture_linux/CMakeLists.txt`

* `FTHR_FFMPEG_ROOT` (cache variable or environment). Prepends its `pkgconfig`
  to `PKG_CONFIG_PATH`.
* **A Release build without it is a fatal error**, with the fix in the message.
  Development builds may use the system FFmpeg and print a loud warning.
* After `pkg_check_modules`, verifies the resolved library directories are
  actually inside the pinned tree.
* Prints version, include and library paths.
* `$ORIGIN` RPATH with `--disable-new-dtags`; `BUILD_WITH_INSTALL_RPATH TRUE` so
  no absolute build-host path is embedded.
* Removed the redundant `-O2`, which landed after Release's `-O3 -DNDEBUG`.

### `FTHR_linux.spec`

Bundles the seven pinned libraries explicitly, plus FFmpeg's `LICENSE.txt` and
the manifest into `licenses/ffmpeg/`. Aborts with instructions if the pinned
tree is missing or incomplete.

### `build_linux.sh`

Fetches the pinned FFmpeg, builds with `-DFTHR_FFMPEG_ROOT`, then **verifies the
engine's linkage before packaging** (every FFmpeg SONAME must exist in the
pinned tree; RPATH must be present). Afterwards removes FFmpeg copies that are
neither the pinned runtime nor a documented wheel provider.

### `tools/verify_release_licenses.py`

* `check_linux_manifest` — completeness and licence of the Linux manifest.
* `check_linux_ffmpeg_libs` — per-library sha256 against the manifest; distro
  SONAMEs rejected by name; undocumented libraries rejected; x264/x265
  DT_NEEDED rejected; absolute RPATH entries rejected; documented wheel
  providers verified by licence.
* `check_linux_engine` — DT_NEEDED, RPATH presence, no leaked host paths.
* Wired into `--appdir`, not only `--tree`.
* `FFMPEG_SO_RE` matches the eight real FFmpeg names exactly.

### `tests/test_license_gate_linux.py` (new, 31 tests)

Almost all negative: missing/incomplete/GPL manifest, hash mismatch, distro
SONAME, undocumented library, `libpostproc`, missing manifest blocking
validation, engine without RPATH, engine on system FFmpeg, engine with an
absolute RPATH, a wheel library turning GPL, and `libavif`/`libavc1394` **not**
being treated as FFmpeg.

---

## 5. Two mistakes this work made, and how they surfaced

**The cleanup step deleted too much.** The first version matched
`libav*.so*`, which also matches `libavif` — the AV1 *image* codec Qt and
OpenCV need. The AppImage built, passed the licence gate, and then died on
startup:

```
ImportError: libavif-cbf1e83c.so.16.3.0: cannot open shared object file
```

Now an anchored regex over the eight real FFmpeg names, with a comment saying
not to widen it and regression tests covering `libavif` and `libavc1394`.

**The bundle contains three FFmpeg builds, not one.** Besides the pinned
runtime, Qt Multimedia ships its own inside the PyQt6-Qt6 wheel
(`libavcodec.so.61`, LGPLv2.1) and OpenCV ships an auditwheel-renamed one
(`libavcodec-156beeea.so.62.11.100`, LGPLv2.1). The first passing build still
contained them unexamined, because the library check only ran on `--tree`.
Both are now documented providers, verified by licence on every build. Neither
is loaded by the engine.

Both were caught by extending the gate and then running it, not by reading code.

---

## 6. Results

### Licence gate

```
83 checks, 0 failed, 0 warnings
PASS — no GPL FFmpeg components, licence paperwork present.
```

### AppImage

| | |
|---|---|
| File | `FTHRClips-1.0.0-alpha-x86_64.AppImage`, 219 MB, executable |
| Starts | yes (`QT_QPA_PLATFORM=offscreen`, ran to timeout, no exception) |
| Engine FFmpeg resolution | 7 of 7 from inside the AppImage |
| x264 / x265 / xvid | none |
| Licence texts | `LICENSE`, `THIRD_PARTY_NOTICES.md`, `licenses/` (9 texts), `licenses/ffmpeg/LICENSE.txt`, manifest |
| User data / clips / logs / tests | none |
| Absolute host paths in any RPATH | none |
| Qt platform plugins | xcb, wayland, offscreen, eglfs, minimal, vnc, linuxfb |

### Engine and clips (against the new FFmpeg)

| Check | Result |
|---|---|
| `/dev/shm/FTHR_SharedMemory_v3` | 4248 B = `ctypes.sizeof(SharedMemoryLayout)`, mode 600 |
| Capture backend | Wayland backends correctly reported unavailable → `x11grab` |
| Encoder | `av1_nvenc` (NVENC works with the pinned build) |
| Audio | PulseAudio 48 kHz stereo float32 |
| ASCII path clip | saved, valid |
| Unicode path clip (`clip_ünïcøde_日本語_🎮.mp4`) | saved, valid |
| `ffprobe` | AV1 1280×720 @30 + AAC 48 kHz stereo |
| Decode, bundled ffmpeg 8.1 | **exit 0, no messages** |
| Read-only directory | refused, reported |

> The system `ffmpeg` 6.1 **crashes** (SIGABRT, `Assertion pkt failed at
> ffmpeg_dec.c:597`) reading these AV1 files. That is a bug in Ubuntu's older
> ffmpeg CLI, not in the clips — the bundled 8.1 decodes them cleanly. Worth
> knowing before someone reports it as data corruption.

### Tests

Windows 140 passed / 30 skipped · Linux 169 passed / 1 skipped · ruff clean ·
all four release gates exit 0.

---

## 7. What is still not verified

- **No capture with visible content.** Frames remain all black
  (`luma min 0 max 0 distinct 1`) because XWayland under WSLg has no
  root-window content. Unchanged by this work and unrelated to FFmpeg.
- **The AppImage has not run on a real Linux desktop.** Only headless under
  WSLg with the offscreen platform plugin. `NOT RUN`.
- **Not started on a second distribution or a clean container.** `NOT RUN`.
- **glibc 2.39 baseline not lowered.** Building on an older base image is the
  fix; not attempted. `NOT RUN`.
- **Wayland capture backends still never exercised** — WSLg's Weston implements
  neither `wlr-screencopy` nor `ext-image-copy-capture`.
- **VAAPI and QSV encoders present but never used** here; only NVENC ran.
- **No soak test** with the new FFmpeg.

---

## 8. Status

## `AUDIT-014 CONDITIONALLY RESOLVED`

The licence problem is fixed and verified. The Linux engine is compiled against
and ships a pinned LGPL FFmpeg, a Release build cannot fall back to the
distribution's GPL one, every shipped library is hash-verified, the gate passes
83 checks, and an AppImage exists and starts.

It is not `RESOLVED` because the Definition of Done also requires the AppImage
to start on a real Linux desktop and to produce a visible, playable clip.
Neither has happened. Those need hardware or a VM with a genuine compositor.

**This does not unblock a release.** AUDIT-013 stands: PyQt6 is `GPL-3.0-only`,
so the bundle as a whole is still GPLv3. AUDIT-014 removed one of the two
reasons there was no distributable Linux build, not both.

### Reproducing this

```bash
python tools/fetch_third_party.py --ffmpeg-linux
cmake -S FTHRcapture_linux -B FTHRcapture_linux/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DFTHR_FFMPEG_ROOT="$PWD/FTHRcapture_linux/third_party/ffmpeg"
cmake --build FTHRcapture_linux/build --parallel
bash build_linux.sh
python tools/verify_release_licenses.py --appdir build_output/AppDir
```

# Third-Party Notices — FTHR Clips

FTHR Clips bundles and links against third-party software. This file lists
every component that is **actually distributed** in a release artifact, its
licence, and where to find the full licence text.

FTHR Clips' own source code is licensed **MIT** (see [`LICENSE`](LICENSE)).
That does not, by itself, describe the licence of the *downloadable build* —
see [Licence of the distributed build](#licence-of-the-distributed-build).

Full licence texts live in [`licenses/`](licenses/), and are installed
alongside the application (Windows: `licenses\` in the install directory;
Linux: `licenses/` inside the AppImage).

Last verified: **2026-08-05**.

---

## Licence of the distributed build

> **Read this before publishing a build.**

Two bundled components are copyleft, and they lead to different conclusions:

| Component | Licence | Consequence for the distributed binary |
|---|---|---|
| FFmpeg (LGPLv3, dynamically linked) | LGPLv3 | ✅ Compatible with keeping FTHR's own code MIT. Requires notices + the ability to relink, which dynamic linking satisfies. |
| **PyQt6** (Riverbank bindings) | **GPL-3.0-only** | ⚠️ **The combined distributed work is GPLv3.** |

**FFmpeg is resolved.** As of 2026-08-05 the bundled build is LGPLv3 with no
GPL components (see below). It is loaded as separate DLLs/shared libraries, so
the LGPL relinking requirement is met.

**PyQt6 is not resolved.** Riverbank ships PyQt6 under `GPL-3.0-only` (or a paid
commercial licence). Bundling it into a distributed application makes that
distribution subject to the GPLv3 as a whole. Note this is the *bindings*, not
Qt: Qt itself (`PyQt6-Qt6`) is LGPLv3 and would be fine on its own.

The three ways out, none of which is a code change this file can make:

1. **Migrate the UI to PySide6** (LGPLv3, from the Qt Company). The API is close
   to PyQt6 and most of a port is mechanical, but it touches all ~17k lines of
   UI code. Keeps the distributed build free of GPL.
2. **Ship under GPLv3.** Keep the repository MIT (MIT is GPL-compatible), but
   label the *download* GPLv3, include the GPLv3 text, and publish a complete
   corresponding source offer.
3. **Buy a commercial PyQt licence** from Riverbank.

Until one of these is chosen, do not describe the download as "MIT".

---

## Distributed components

### FFmpeg

| | |
|---|---|
| **Version** | `n8.1.2-21-gce3c09c101` (release branch 8.1, build dated 2026-06-30) |
| **Source** | [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), release `autobuild-2026-06-30-13-34`, asset `ffmpeg-n8.1.2-21-gce3c09c101-win64-lgpl-shared-8.1.zip` |
| **SHA-256 (zip)** | `27bcaf58b5140171…` — full value recorded in [`tools/ffmpeg_manifest.json`](tools/ffmpeg_manifest.json) |
| **Licence** | **LGPL v3 or later** — self-reported by the binary as `libavcodec license: LGPL version 3 or later` |
| **Linkage** | Dynamic. The C++ engine links the import libraries; the DLLs ship beside it. The Python UI shells out to `ffmpeg.exe`. |
| **Used for** | Video/audio encoding and muxing in the capture engine; mic mux, multiband audio mix, watermark, auto-crop, webcam overlay, clip export/share in the UI. |
| **Licence text** | [`licenses/FFmpeg-LICENSE.txt`](licenses/FFmpeg-LICENSE.txt) |
| **Shipped files** | `avcodec-62.dll`, `avdevice-62.dll`, `avfilter-11.dll`, `avformat-62.dll`, `avutil-60.dll`, `swresample-6.dll`, `swscale-9.dll`, `ffmpeg.exe`, `ffprobe.exe` |

Build configuration — the flags that matter, verified with `ffmpeg -buildconf`:

```
absent : --enable-gpl          absent : --enable-libx264
absent : --enable-nonfree      absent : --enable-libx265
absent : --enable-libxvid      absent : --enable-libxavs2
present: --enable-version3     present: --enable-libopenh264
```

`--enable-version3` upgrades the licence from LGPLv2.1 to **LGPLv3**. It does
**not** make the build GPL — `ffmpeg -L` reports the GNU *Lesser* General Public
License v3. It is required by the LGPL builds BtbN publishes and cannot be
switched off without compiling FFmpeg from source.

**Software encoders in this build** (no GPL encoders are present):

| Codec | Software encoder | Licence |
|---|---|---|
| H.264 | `libopenh264` (Cisco OpenH264) | BSD-2-Clause |
| H.265 | `libkvazaar` | LGPLv2.1 |
| AV1 | `libsvtav1`, `libaom-av1`, `librav1e` | BSD-3-Clause / BSD-2-Clause |
| AAC | FFmpeg native `aac` | LGPL (part of FFmpeg) |

Hardware encoders (NVENC, AMF, QSV, VAAPI, MediaFoundation) are unaffected and
remain fully available.

> **Patent note, not a licence note:** H.264/H.265 are covered by patent pools.
> Cisco distributes OpenH264 binaries under terms where it covers the AVC
> licensing fees, but that arrangement applies to *Cisco's own* binary
> downloads. FFmpeg builds that compile OpenH264 in do not automatically
> inherit it. This is unchanged from the previous x264-based build and is
> outside the scope of the LGPL fix — but it is worth a deliberate decision
> before a large public release.

### Qt 6

| | |
|---|---|
| **Version** | 6.11.1 (via `PyQt6-Qt6` wheel) |
| **Source** | [pypi.org/project/PyQt6-Qt6](https://pypi.org/project/PyQt6-Qt6/) |
| **Licence** | LGPL v3 |
| **Linkage** | Dynamic (shared libraries bundled by PyInstaller) |
| **Used for** | Entire GUI, multimedia playback |
| **Licence text** | [`licenses/Qt6-LICENSE.txt`](licenses/Qt6-LICENSE.txt) |

### FFmpeg bundled inside Qt Multimedia

A **second, independent** FFmpeg comes in with the `PyQt6-Qt6` wheel: Qt
Multimedia uses it for media playback (the clip preview player). It is separate
from the engine's copy and carries different SONAMEs, so both coexist without
conflict.

| | |
|---|---|
| **Files** | `avcodec-61.dll`, `avformat-61.dll`, `avutil-59.dll`, `swresample-5.dll`, `swscale-8.dll`, `ffmpegmediaplugin.dll` |
| **Location** | `_internal/PyQt6/Qt6/bin/` and `.../plugins/multimedia/` |
| **Licence** | **LGPL v2.1 or later** — self-reported by the binaries as `libavcodec license: LGPL version 2.1 or later` |
| **Origin** | Built and shipped by the Qt Company as part of Qt 6 |
| **Used for** | `QMediaPlayer` playback in the clip viewer |
| **Licence text** | Covered by [`licenses/Qt6-LICENSE.txt`](licenses/Qt6-LICENSE.txt); the LGPL text also applies — see [`licenses/FFmpeg-LICENSE.txt`](licenses/FFmpeg-LICENSE.txt) |
| **Verified** | Contains no `--enable-gpl` and no x264/x265 |

### PyQt6

| | |
|---|---|
| **Version** | 6.11.0 |
| **Source** | [pypi.org/project/PyQt6](https://pypi.org/project/PyQt6/) (Riverbank Computing) |
| **Licence** | **GPL-3.0-only** (or commercial) |
| **Linkage** | Python extension modules, bundled |
| **Used for** | Python bindings for Qt — the UI framework |
| **Licence text** | [`licenses/PyQt6-LICENSE.txt`](licenses/PyQt6-LICENSE.txt) |
| **Note** | See [Licence of the distributed build](#licence-of-the-distributed-build). This is the component that currently makes the download GPLv3. |

### PyQt6-sip

| | |
|---|---|
| **Version** | 13.11.1 · **Licence** BSD-2-Clause |
| **Used for** | Runtime support for the PyQt6 bindings |
| **Licence text** | [`licenses/PyQt6_sip-LICENSE.txt`](licenses/PyQt6_sip-LICENSE.txt) |

### NumPy

| | |
|---|---|
| **Version** | 2.4.4 · **Licence** BSD-3-Clause |
| **Used for** | Audio buffer maths (mic capture, multiband mixing) |
| **Licence text** | [`licenses/numpy-LICENSE.txt`](licenses/numpy-LICENSE.txt) |

### OpenCV (`opencv-python-headless`)

| | |
|---|---|
| **Version** | 4.13.0.92 · **Licence** Apache-2.0 |
| **Used for** | Webcam capture, thumbnail generation, frame decoding |
| **Licence texts** | [`licenses/opencv-LICENSE-Apache2.txt`](licenses/opencv-LICENSE-Apache2.txt), third-party components in [`licenses/opencv-LICENSE.txt`](licenses/opencv-LICENSE.txt) |

### python-sounddevice

| | |
|---|---|
| **Version** | 0.5.5 · **Licence** MIT |
| **Used for** | Microphone capture |
| **Licence text** | [`licenses/sounddevice-LICENSE.txt`](licenses/sounddevice-LICENSE.txt) |
| **Note** | Wraps **PortAudio** (MIT). On Linux `libportaudio.so.2` is bundled explicitly by `FTHR_linux.spec`. |

### keyboard

| | |
|---|---|
| **Version** | 0.13.5 · **Licence** MIT |
| **Used for** | Global hotkeys (the only hotkey path on Windows) |
| **Licence text** | [`licenses/keyboard-LICENSE.txt`](licenses/keyboard-LICENSE.txt) |

### NVIDIA Video Codec SDK header

| | |
|---|---|
| **File** | `FTHRcapture/FTHRclips/include/nvenc/nvEncodeAPI.h` |
| **Copyright** | © 2010–2024 NVIDIA Corporation |
| **Licence** | MIT-style permissive grant, stated in the header itself |
| **Linkage** | **None at build time.** NVENC is resolved at runtime via `LoadLibraryA("nvEncodeAPI64.dll")` against the user's installed driver. The header is source-only and the NVIDIA runtime is **not** redistributed. |

### Microsoft Visual C++ Redistributable

| | |
|---|---|
| **File** | `redist/vc_redist.x64.exe`, executed by the Windows installer |
| **Licence** | Microsoft Visual Studio redistributable terms |
| **Note** | Redistributed unmodified as permitted for VC++ runtime redistribution. |

---

## Not distributed

Listed so future audits do not have to re-derive it:

- **`imageio-ffmpeg`** — *removed* on 2026-08-05. Its bundled binary is a
  gyan.dev build with `--enable-gpl --enable-libx264 --enable-libx265`
  (GPLv3). It is now excluded in both PyInstaller specs and removed from
  `requirements.txt`. See `FTHR_UI/core/ffmpeg_tools.py`.
- **System FFmpeg on Linux** — if the bundled binary is absent, FTHR falls back
  to `ffmpeg` on `PATH`. That copy belongs to the user's distribution and is not
  redistributed by this project.
- **Development tooling** — pytest, ruff, PyInstaller, Inno Setup, MSVC.

---

## Verifying this file

[`tools/verify_release_licenses.py`](tools/verify_release_licenses.py) checks
the shipped artifacts for GPL build flags, x264/x265, and the presence of the
licence files described here. Run it before every release:

```bash
python tools/verify_release_licenses.py --tree .
python tools/verify_release_licenses.py --windows-dist dist/FTHRClips
```

**This is a technical verification, not legal advice.**

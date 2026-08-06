# Building FTHR Clips for Windows

> **Platform note:** This build produces a Windows-only installer.
> It bundles `FTHRcapture\` (Windows DXGI engine) and excludes all Linux files.
> For Linux builds see `build_linux.sh`.

## Prerequisites

| Tool | Download |
|---|---|
| Visual Studio 2022 (Desktop C++ workload) | https://visualstudio.microsoft.com |
| Python 3.14 x64 | https://python.org/downloads |
| Inno Setup 6 | https://jrsoftware.org/isinfo.php |
| VC++ Redistributable | https://aka.ms/vs/17/release/vc_redist.x64.exe → `redist\vc_redist.x64.exe` |

> **Python 3.14 is the alpha build interpreter.** The shipped `dist/` bundle
> contains `python314.dll` and `cpython-314` bytecode; building on anything
> else produces a different artifact from the one that was tested. CI also
> exercises 3.12 and 3.13, but the release build is 3.14.

## Step 1 — Install Python dependencies

Install from the pinned lock files, never a loose `pip install` — unpinned
installs were AUDIT-009 and made UI bug reports irreproducible:

```cmd
python -m pip install -r requirements-alpha.txt -r requirements-dev.txt
```

> **Do not install `imageio-ffmpeg`.** It bundles a GPLv3 FFmpeg build; having
> it in the environment risks PyInstaller pulling it into the bundle and
> placing the whole artifact under the GPL (AUDIT-005). It is excluded in
> `FTHR.spec` and absent from both lock files on purpose.

## Step 1b — Fetch the third-party binaries

The FFmpeg runtime DLLs (152 MB) and the MSVC redistributable are not tracked
in git. The FFmpeg headers and import libraries *are*, so the engine compiles
from a clean clone — but you need the runtime to link, run and package:

```cmd
python tools/fetch_third_party.py --all
```

Every FFmpeg file is verified against the sha256 recorded in
`tools/ffmpeg_manifest.json`. A mismatch aborts: it means either a corrupt
download or a different build from the one the licence paperwork describes.

## Step 2 — Build the Windows C++ capture engine

1. Open `FTHRcapture\FTHRcapture.sln` in Visual Studio 2022
2. Set configuration to **Release | x64**
3. **Build → Build Solution**
4. Confirm output: `FTHRcapture\x64\Release\FTHRClips.exe`

> **Note:** The engine links against FFmpeg DLLs (`avcodec-62.dll`, etc.) from
> `FTHRclips\third_party\ffmpeg\bin\`. A post-build step copies them next to the
> exe automatically. If you run the exe before rebuilding, copy `*.dll` from that
> `bin` folder into `x64\Release\` manually.

## Step 3 — Bundle with PyInstaller

```cmd
pyinstaller FTHR.spec --clean
```

Output: `dist\FTHRClips\` — Python app + Windows engine, ~80-120 MB.

**Post-build cleanup (run in PowerShell to further reduce size):**
```powershell
$int = "dist\FTHRClips\_internal"
# Remove unused OpenCV modules
Remove-Item "$int\libopencv_dnn*"    -ErrorAction SilentlyContinue
Remove-Item "$int\libopencv_ml*"     -ErrorAction SilentlyContinue
Remove-Item "$int\libopencv_calib3d*" -ErrorAction SilentlyContinue
Remove-Item "$int\libopencv_features2d*" -ErrorAction SilentlyContinue
Remove-Item "$int\libopencv_stitching*" -ErrorAction SilentlyContinue
# Remove Qt QML/Quick modules
Remove-Item "$int\Qt6Quick*"  -ErrorAction SilentlyContinue
Remove-Item "$int\Qt6Qml*"    -ErrorAction SilentlyContinue
Remove-Item "$int\Qt6Pdf*"    -ErrorAction SilentlyContinue
```

## Step 4 — Create the installer

1. `vc_redist.x64.exe` must be in `redist\` (Step 1b does this)
2. Open **Inno Setup Compiler** → `installer_windows.iss` → **Build → Compile**
3. Output: `Output\FTHRClips_Setup_Windows.exe`

> `installer_windows.iss` carries the product version as a literal because
> Inno Setup cannot import Python. `tools/verify_version_consistency.py`
> fails the build if it disagrees with `FTHR_UI/version.py`.

## Step 5 — Verify before releasing

```cmd
python tools/verify_release_licenses.py --tree .
python tools/verify_version_consistency.py
python tools/verify_shared_memory_contract.py
python tools/scan_repo_hygiene.py
python -m pytest tests/
```

All five must pass. See `RELEASE_CHECKLIST.md` for the full gate list —
there is an outstanding release blocker (AUDIT-013, PyQt6 is GPL-3.0-only).

## What the installer contains

- Windows DXGI capture engine (`FTHRClips.exe`)
- Python runtime + all dependencies
- Qt6 Widgets (no Wayland/QML)
- Visual C++ Redistributable
- Start Menu + optional desktop shortcut
- Uninstaller (removes `%APPDATA%\fthr` on uninstall)

## Notes

- DXGI desktop capture requires Windows 10 or later
- Global hotkeys work without extra permissions on Windows
- NVENC auto-detected at runtime, falls back to CPU x264
- The Linux engine (`FTHRcapture_linux/`) is never included in the Windows build

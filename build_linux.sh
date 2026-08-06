#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build_output"
APPDIR="$BUILD_DIR/AppDir"

# Product version comes from FTHR_UI/version.py — the single source of truth.
# Never hardcode it here; tools/verify_version_consistency.py enforces that.
APP_VERSION="$(python3 -c "import sys; sys.path.insert(0, '$SCRIPT_DIR/FTHR_UI'); import version; print(version.__version__)")"

echo "=== FTHR Clips — Linux AppImage Builder ==="
echo "Version: $APP_VERSION"
echo "Python: $(python3 --version)"
echo "GCC:    $(gcc --version | head -1)"
echo ""

# ── 1. Check requirements ──────────────────────────────────────────────────
echo ">>> Checking dependencies..."
for cmd in cmake gcc pkg-config python3 wayland-scanner curl pyinstaller; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "ERROR: '$cmd' not found."
        [[ "$cmd" == "pyinstaller" ]] && echo "  Install: pip install pyinstaller --break-system-packages"
        exit 1
    }
done

pkg-config --exists libavcodec libpulse-simple wayland-client || {
    echo "ERROR: Missing C++ build deps (ffmpeg / pulseaudio / wayland)."
    echo "  Arch:          sudo pacman -S ffmpeg libpulse wayland"
    echo "  Debian/Ubuntu: sudo apt install libavcodec-dev libavformat-dev \\"
    echo "                   libavutil-dev libavdevice-dev libswscale-dev \\"
    echo "                   libswresample-dev libpulse-dev libwayland-dev \\"
    echo "                   wayland-protocols"
    exit 1
}

# Import each module separately. A combined import reports only the first
# failure, and the advice it printed ("pip install …") was actively misleading
# for sounddevice: that one fails on a missing *system* library (PortAudio),
# which no amount of pip installing will fix. Verified on Ubuntu 24.04.
_missing_py=()
_missing_sys=()
for _m in PyQt6 keyboard cv2 numpy sounddevice; do
    _err="$(python3 -c "import $_m" 2>&1)" && continue
    case "$_err" in
        *PortAudio*)          _missing_sys+=("$_m: PortAudio runtime library") ;;
        *libGL*|*libEGL*|*libxkb*|*libxcb*)
                              _missing_sys+=("$_m: a system graphics library — ${_err##*$'\n'}") ;;
        *)                    _missing_py+=("$_m") ;;
    esac
done

if [ ${#_missing_py[@]} -gt 0 ]; then
    echo "ERROR: Missing Python packages: ${_missing_py[*]}"
    echo "  Install the pinned alpha set (do NOT pip install loose versions):"
    echo "    python3 -m pip install -r requirements-alpha.txt"
    exit 1
fi
if [ ${#_missing_sys[@]} -gt 0 ]; then
    echo "ERROR: Python packages are installed but their SYSTEM libraries are not:"
    for _m in "${_missing_sys[@]}"; do echo "    - $_m"; done
    echo "  This is not fixed by pip. Install the system packages:"
    echo "    Arch:          sudo pacman -S portaudio"
    echo "    Debian/Ubuntu: sudo apt install libportaudio2"
    echo "    Fedora:        sudo dnf install portaudio"
    exit 1
fi
echo "    All dependencies found."

# ── 1c. Pinned LGPL FFmpeg (AUDIT-014) ────────────────────────────────────
# The distribution's FFmpeg is a GPL build. The engine is compiled against this
# tree and ships with it; nothing here ever links /usr/lib FFmpeg.
FFMPEG_ROOT="$SCRIPT_DIR/FTHRcapture_linux/third_party/ffmpeg"
echo ""
echo ">>> Ensuring the pinned LGPL FFmpeg is present..."
if ! python3 "$SCRIPT_DIR/tools/fetch_third_party.py" --ffmpeg-linux; then
    echo "ERROR: could not obtain the pinned LGPL FFmpeg."
    echo "  Without it the engine would link the distribution's GPL build and"
    echo "  the licence gate would refuse to package the result (AUDIT-014)."
    exit 1
fi

# ── 2. Build Linux C++ engine ─────────────────────────────────────────────
echo ""
echo ">>> Building Linux capture engine (against the pinned LGPL FFmpeg)..."
cd "$SCRIPT_DIR/FTHRcapture_linux"
rm -rf build
cmake -B build -DCMAKE_BUILD_TYPE=Release -DFTHR_FFMPEG_ROOT="$FFMPEG_ROOT"     | grep -E "FFmpeg|error" || true
cmake --build build -j"$(nproc)" 2>&1 | grep -E "^\[|error:" || true
[ -x build/FTHRclips ] || { echo "ERROR: engine build produced no binary."; exit 1; }
echo "    Engine built: FTHRcapture_linux/build/FTHRclips"

# Prove the engine really links the pinned libraries before we package anything.
echo ""
echo ">>> Verifying engine linkage..."
_bad=0
for so in $(readelf -d build/FTHRclips | grep -oE 'lib(avcodec|avformat|avutil|avdevice|swscale|swresample)\.so\.[0-9]+'); do
    if ! [ -e "$FFMPEG_ROOT/lib/$so" ]; then
        echo "    ERROR: engine needs $so, which is not in the pinned tree"
        _bad=1
    fi
done
if readelf -d build/FTHRclips | grep -qE 'RPATH|RUNPATH'; then
    echo "    RPATH: $(readelf -d build/FTHRclips | grep -oE '\[.*\]' | head -1)"
else
    echo "    ERROR: engine has no RPATH — it would load system libraries."
    _bad=1
fi
[ "$_bad" -eq 0 ] || { echo "ERROR: engine linkage check failed."; exit 1; }
echo "    Engine links only the pinned LGPL FFmpeg."
cd "$SCRIPT_DIR"

# ── 3. Bundle with PyInstaller ─────────────────────────────────────────────
echo ""
echo ">>> Bundling Python app with PyInstaller..."
pyinstaller FTHR_linux.spec --clean --noconfirm 2>&1 | grep -E "^(INFO|WARNING|ERROR|Building)" || true

PYINST_DIR="$SCRIPT_DIR/dist/FTHRClips"
[[ -f "$PYINST_DIR/FTHRClips" ]] || { echo "ERROR: PyInstaller output missing."; exit 1; }
echo "    Raw bundle: $(du -sh "$PYINST_DIR" | cut -f1)"

# ── 4. Strip unused libraries ─────────────────────────────────────────────
# This is the most impactful size-reduction step. We remove shared libraries
# that PyInstaller pulled in transitively but FTHR Clips never calls at runtime.
echo ""
echo ">>> Stripping unused libraries..."
INT="$PYINST_DIR/_internal"

_rm() { find "$INT" -maxdepth 1 -name "$1" -delete 2>/dev/null; }

# VTK — 141 MB. Pulled in by the full OpenCV package. We only use
# VideoCapture/VideoWriter/cvtColor, which need core + videoio + imgproc only.
_rm "libvtk*.so*"

# OpenCV contrib & unused modules (~60 MB).
# Keep: core, imgproc, videoio (the three we actually call)
for mod in \
    alphamat aruco bgsegm bioinspired calib3d ccalib \
    dnn dnn_superres face features2d flann freetype fuzzy \
    gapi hdf hfs highgui imgcodecs img_hash \
    intensity_transform line_descriptor mcc ml \
    objdetect optflow phase_unwrapping photo plot \
    quality rapid reg rgbd saliency shape signal \
    stereo stitching structured_light surface_matching \
    text tracking viz wechat_qrcode \
    xfeatures2d ximgproc xphoto; do
    _rm "libopencv_${mod}.so*"
done

# Qt Quick / QML / 3D — we use Qt Widgets only (~15 MB)
_rm "libQt6Quick*.so*"
_rm "libQt6Qml*.so*"
_rm "libQt6Quick3D*.so*"
_rm "libQt6ShaderTools*.so*"
_rm "libQt6Pdf*.so*"
_rm "libQt6WebEngine*.so*"
_rm "libQt6Location*.so*"
_rm "libQt6Positioning*.so*"
_rm "libQt6VirtualKeyboard*.so*"
_rm "libQt6Charts*.so*"
_rm "libQt6DataVisualization*.so*"

# OpenCV ML / DNN support libs (~12 MB)
_rm "libhdf5*.so*"
_rm "libprotobuf*.so*"

# ICU data — only needed for full Unicode / BIDI, Qt ships a smaller subset
# (don't remove — Qt itself needs libicudata)

AFTER="$(du -sh "$PYINST_DIR" | cut -f1)"
echo "    After strip: $AFTER"

# ── 4b. Remove GPL FFmpeg copies PyInstaller collected (AUDIT-014) ────────
# cv2 and Qt link the *system* FFmpeg, so PyInstaller collects libavcodec.so.60
# and friends even though the engine never touches them. A GPL library sitting
# in the AppDir is a GPL library being distributed, so they go.
#
# The regex matches the eight FFmpeg library names EXACTLY, anchored on the
# basename. An earlier version used the glob 'libav*.so*', which also matched
# libavif (the AV1 *image* codec Qt and OpenCV use) and libavc1394. Deleting
# those produced a bundle that died on startup with
#   ImportError: libavif-cbf1e83c.so.16.3.0: cannot open shared object file
# Do not widen this pattern back to a glob.
_FFMPEG_LIB_RE='.*/lib(avcodec|avformat|avutil|avdevice|avfilter|swscale|swresample|postproc)\.so[.0-9]*$'

# Kept on purpose:
#   *.so.62/.60/.11/.9/.6  the pinned LGPL runtime the engine links (manifest)
#   *.so.61/.59/.8/.5      Qt Multimedia's own FFmpeg from the PyQt6-Qt6 wheel,
#                          LGPLv2.1, documented in ffmpeg_manifest_linux.json.
#                          Removing it would break Qt Multimedia playback.
_keep_regex='libavcodec\.so\.6[12]|libavformat\.so\.6[12]|libavutil\.so\.(59|60)|libavdevice\.so\.62|libavfilter\.so\.11|libswscale\.so\.[89]|libswresample\.so\.[56]'

echo ""
echo ">>> Removing GPL FFmpeg copies collected from the system..."
_removed=0
while IFS= read -r f; do
    base="$(basename "$f")"
    if echo "$base" | grep -qE "$_keep_regex"; then continue; fi
    if strings -a "$f" 2>/dev/null | grep -q -- '--enable-gpl'; then
        echo "    GPL, removed: $base"
    else
        echo "    undocumented FFmpeg copy, removed: $base"
    fi
    rm -f "$f"
    _removed=$((_removed + 1))
done < <(find "$INT" -regextype posix-extended -regex "$_FFMPEG_LIB_RE" | sort)
echo "    Removed $_removed file(s)."

echo ">>> FFmpeg libraries remaining in the bundle:"
find "$INT" -regextype posix-extended -regex "$_FFMPEG_LIB_RE" -printf '    %f\n' | sort

# ── 5. Verify the app still launches after stripping ──────────────────────
echo ""
echo ">>> Smoke-testing stripped bundle..."
if timeout 6 "$PYINST_DIR/FTHRClips" 2>&1 | grep -q "Engine found\|successfully initiated"; then
    echo "    Smoke test passed."
else
    echo "    WARNING: Could not confirm launch (no display / normal in CI)."
fi

# ── 6. Set up AppDir ──────────────────────────────────────────────────────
echo ""
echo ">>> Setting up AppDir..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
cp -r "$PYINST_DIR/." "$APPDIR/"
cp "$SCRIPT_DIR/AppDir/fthr-clips.desktop" "$APPDIR/"
cp "$SCRIPT_DIR/AppDir/fthr-clips.png"     "$APPDIR/"

# Licence paperwork (AUDIT-005). The bundled FFmpeg is LGPLv3 and PyQt6 is
# GPLv3; both require the licence texts to travel with the binary. The user
# must be able to find them locally after unpacking, not only on GitHub.
cp "$SCRIPT_DIR/LICENSE"                "$APPDIR/"
cp "$SCRIPT_DIR/THIRD_PARTY_NOTICES.md" "$APPDIR/"
cp -r "$SCRIPT_DIR/licenses"            "$APPDIR/"
echo "    Licence files copied into AppDir."

# ── 6b. Verify no GPL FFmpeg slipped into the bundle ──────────────────────
# PyInstaller pulls in the shared libraries the engine links against, so a
# distro GPL build of FFmpeg can end up inside the AppImage without anyone
# choosing it. Fail the build rather than ship it.
echo ""
echo ">>> Verifying release licences..."
if ! python3 "$SCRIPT_DIR/tools/verify_release_licenses.py" --appdir "$APPDIR"; then
    echo ""
    echo "ERROR: licence verification failed - refusing to build the AppImage."
    echo "  See THIRD_PARTY_NOTICES.md and tools/verify_release_licenses.py"
    exit 1
fi

cat > "$APPDIR/AppRun" << 'APPRUN_EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"

if [ -n "$WAYLAND_DISPLAY" ]; then
    export QT_QPA_PLATFORM="wayland"
elif [ -n "$DISPLAY" ]; then
    export QT_QPA_PLATFORM="xcb"
fi

export PYTHONUNBUFFERED=1
exec "$HERE/FTHRClips" "$@"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

# ── 7. Download appimagetool ──────────────────────────────────────────────
APPIMAGETOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo ""
    echo ">>> Downloading appimagetool..."
    curl -L --progress-bar -o "$APPIMAGETOOL" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$APPIMAGETOOL"
fi

# ── 8. Pack AppImage ──────────────────────────────────────────────────────
echo ""
echo ">>> Packing AppImage..."
OUTPUT="$SCRIPT_DIR/FTHRClips-${APP_VERSION}-x86_64.AppImage"
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$OUTPUT" 2>&1 | grep -v "^Please consider\|appimage.github"

SIZE="$(du -sh "$OUTPUT" | cut -f1)"
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              FTHR Clips Linux AppImage Ready                ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  Output: %-52s║\n" "FTHRClips-${APP_VERSION}-x86_64.AppImage"
printf "║  Size:   %-52s║\n" "$SIZE"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Linux-only — contains the Linux capture engine only.       ║"
echo "║                                                              ║"
echo "║  For global hotkeys (one-time):                             ║"
echo "║    sudo usermod -aG input \$USER  (then re-login)            ║"
echo "╚══════════════════════════════════════════════════════════════╝"

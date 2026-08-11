cmake_minimum_required(VERSION 3.18)
project(FTHRclips_linux CXX C)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(CTest)
if(BUILD_TESTING)
    add_executable(fthr_capture_recovery_test tests/capture_recovery_test.cpp)
    target_include_directories(fthr_capture_recovery_test PRIVATE src)
    add_test(NAME capture_recovery_policy COMMAND fthr_capture_recovery_test)

    add_executable(fthr_transactional_save_test tests/transactional_save_test.cpp)
    target_include_directories(fthr_transactional_save_test PRIVATE
        ${CMAKE_SOURCE_DIR}/../FTHRcapture_common)
    add_test(NAME transactional_save_contract COMMAND fthr_transactional_save_test)
endif()

find_package(PkgConfig REQUIRED)

# ---------------------------------------------------------------------------
# FFmpeg — AUDIT-014
#
# The distribution's FFmpeg is a GPL build on every mainstream distro
# (Ubuntu 24.04: --enable-gpl --enable-libx264 --enable-libx265 --enable-libxvid).
# Linking the engine against it and bundling the result puts the whole AppImage
# under the GPL, and build_linux.sh correctly refuses to package that.
#
# A Release build therefore MUST be pointed at the pinned LGPL distribution:
#
#     python tools/fetch_third_party.py --ffmpeg-linux
#     cmake -S FTHRcapture_linux -B build \
#           -DCMAKE_BUILD_TYPE=Release \
#           -DFTHR_FFMPEG_ROOT=<repo>/FTHRcapture_linux/third_party/ffmpeg
#
# Note this is a different SONAME generation from the system one
# (libavcodec.so.62 vs .so.60), so it is not enough to build against the system
# libraries and substitute others at runtime — the engine is compiled against
# exactly the headers that ship next to it.
# ---------------------------------------------------------------------------
set(FTHR_FFMPEG_ROOT "" CACHE PATH
    "Root of the pinned LGPL FFmpeg (contains include/ and lib/). Required for Release builds.")

if(NOT FTHR_FFMPEG_ROOT AND DEFINED ENV{FTHR_FFMPEG_ROOT})
    set(FTHR_FFMPEG_ROOT "$ENV{FTHR_FFMPEG_ROOT}")
endif()

if(FTHR_FFMPEG_ROOT)
    get_filename_component(FTHR_FFMPEG_ROOT "${FTHR_FFMPEG_ROOT}" ABSOLUTE)
    if(NOT EXISTS "${FTHR_FFMPEG_ROOT}/lib/pkgconfig")
        message(FATAL_ERROR
            "FTHR_FFMPEG_ROOT='${FTHR_FFMPEG_ROOT}' has no lib/pkgconfig.\n"
            "Fetch it with:  python tools/fetch_third_party.py --ffmpeg-linux")
    endif()
    # Prepend rather than replace: the pinned tree wins, but pkg-config can
    # still resolve anything it legitimately depends on.
    set(ENV{PKG_CONFIG_PATH} "${FTHR_FFMPEG_ROOT}/lib/pkgconfig:$ENV{PKG_CONFIG_PATH}")
    set(FTHR_FFMPEG_BUNDLED TRUE)
    message(STATUS "FFmpeg: pinned LGPL distribution at ${FTHR_FFMPEG_ROOT}")
elseif(CMAKE_BUILD_TYPE STREQUAL "Release")
    message(FATAL_ERROR
        "Release build without FTHR_FFMPEG_ROOT.\n"
        "  Refusing to silently link the distribution's FFmpeg, which is a GPL\n"
        "  build and would make the AppImage GPLv3 (AUDIT-014).\n"
        "  Fix:\n"
        "    python tools/fetch_third_party.py --ffmpeg-linux\n"
        "    cmake -S FTHRcapture_linux -B <build> -DCMAKE_BUILD_TYPE=Release \\\n"
        "          -DFTHR_FFMPEG_ROOT=<repo>/FTHRcapture_linux/third_party/ffmpeg\n"
        "  For a development build use -DCMAKE_BUILD_TYPE=Debug (or RelWithDebInfo),\n"
        "  which may use the system FFmpeg but must never be packaged.")
else()
    set(FTHR_FFMPEG_BUNDLED FALSE)
    message(WARNING
        "==================================================================\n"
        " Using the SYSTEM FFmpeg. This is a development build only.\n"
        " On most distributions the system FFmpeg is a GPL build; anything\n"
        " produced from it MUST NOT be packaged or distributed (AUDIT-014).\n"
        "==================================================================")
endif()

pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET
    libavcodec libavformat libavutil libavdevice libswscale libswresample)

message(STATUS "FFmpeg libavcodec version : ${FFMPEG_libavcodec_VERSION}")
message(STATUS "FFmpeg include dirs       : ${FFMPEG_INCLUDE_DIRS}")
message(STATUS "FFmpeg library dirs       : ${FFMPEG_LIBRARY_DIRS}")
message(STATUS "FFmpeg libraries          : ${FFMPEG_LIBRARIES}")

# A Release build that resolved to /usr anyway means PKG_CONFIG_PATH was
# overridden or the pinned tree is incomplete. Fail here rather than at the
# licence gate twenty minutes later.
if(FTHR_FFMPEG_BUNDLED)
    foreach(_dir ${FFMPEG_LIBRARY_DIRS})
        string(FIND "${_dir}" "${FTHR_FFMPEG_ROOT}" _in_pinned)
        if(_in_pinned EQUAL -1)
            message(FATAL_ERROR
                "FFmpeg resolved to '${_dir}', outside FTHR_FFMPEG_ROOT.\n"
                "The pinned LGPL distribution is not being used.")
        endif()
    endforeach()

    # pkg-config names libraries as -lavcodec/-lswscale/etc.  If a linker alias
    # is absent, the linker silently searches /usr/lib next and can mix the
    # distribution's GPL FFmpeg into this nominally pinned LGPL build.  Resolve
    # every direct engine dependency inside the verified tree and fail closed.
    set(FTHR_FFMPEG_LINK_LIBRARIES)
    foreach(_lib avdevice avformat avcodec swscale swresample avutil)
        find_library(FTHR_${_lib}_LIBRARY NAMES ${_lib}
            PATHS "${FTHR_FFMPEG_ROOT}/lib" NO_DEFAULT_PATH NO_CACHE)
        if(NOT FTHR_${_lib}_LIBRARY)
            message(FATAL_ERROR
                "Pinned FFmpeg linker alias lib${_lib}.so is missing from "
                "${FTHR_FFMPEG_ROOT}/lib. Re-run: "
                "python tools/fetch_third_party.py --ffmpeg-linux")
        endif()
        list(APPEND FTHR_FFMPEG_LINK_LIBRARIES "${FTHR_${_lib}_LIBRARY}")
    endforeach()
endif()

pkg_check_modules(WAYLAND REQUIRED IMPORTED_TARGET
    wayland-client)
pkg_check_modules(PULSE REQUIRED IMPORTED_TARGET
    libpulse-simple libpulse)

find_program(WAYLAND_SCANNER wayland-scanner REQUIRED)
set(PROTO_DIR ${CMAKE_SOURCE_DIR}/protocols)

# wlr-screencopy
add_custom_command(
    OUTPUT ${PROTO_DIR}/wlr-screencopy-client-protocol.h
           ${PROTO_DIR}/wlr-screencopy-protocol.c
    COMMAND ${WAYLAND_SCANNER} client-header
            ${PROTO_DIR}/wlr-screencopy-unstable-v1.xml
            ${PROTO_DIR}/wlr-screencopy-client-protocol.h
    COMMAND ${WAYLAND_SCANNER} private-code
            ${PROTO_DIR}/wlr-screencopy-unstable-v1.xml
            ${PROTO_DIR}/wlr-screencopy-protocol.c
    DEPENDS ${PROTO_DIR}/wlr-screencopy-unstable-v1.xml
)

# xdg-output
add_custom_command(
    OUTPUT ${PROTO_DIR}/xdg-output-client-protocol.h
           ${PROTO_DIR}/xdg-output-protocol.c
    COMMAND ${WAYLAND_SCANNER} client-header
            ${PROTO_DIR}/xdg-output-unstable-v1.xml
            ${PROTO_DIR}/xdg-output-client-protocol.h
    COMMAND ${WAYLAND_SCANNER} private-code
            ${PROTO_DIR}/xdg-output-unstable-v1.xml
            ${PROTO_DIR}/xdg-output-protocol.c
    DEPENDS ${PROTO_DIR}/xdg-output-unstable-v1.xml
)

# ext-image-copy-capture
add_custom_command(
    OUTPUT ${PROTO_DIR}/ext-image-copy-capture-client-protocol.h
           ${PROTO_DIR}/ext-image-copy-capture-protocol.c
    COMMAND ${WAYLAND_SCANNER} client-header
            ${PROTO_DIR}/ext-image-copy-capture-v1.xml
            ${PROTO_DIR}/ext-image-copy-capture-client-protocol.h
    COMMAND ${WAYLAND_SCANNER} private-code
            ${PROTO_DIR}/ext-image-copy-capture-v1.xml
            ${PROTO_DIR}/ext-image-copy-capture-protocol.c
    DEPENDS ${PROTO_DIR}/ext-image-copy-capture-v1.xml
)

# ext-image-capture-source
add_custom_command(
    OUTPUT ${PROTO_DIR}/ext-image-capture-source-client-protocol.h
           ${PROTO_DIR}/ext-image-capture-source-protocol.c
    COMMAND ${WAYLAND_SCANNER} client-header
            ${PROTO_DIR}/ext-image-capture-source-v1.xml
            ${PROTO_DIR}/ext-image-capture-source-client-protocol.h
    COMMAND ${WAYLAND_SCANNER} private-code
            ${PROTO_DIR}/ext-image-capture-source-v1.xml
            ${PROTO_DIR}/ext-image-capture-source-protocol.c
    DEPENDS ${PROTO_DIR}/ext-image-capture-source-v1.xml
)

# ext-foreign-toplevel-list (required by ext-image-capture-source)
add_custom_command(
    OUTPUT ${PROTO_DIR}/ext-foreign-toplevel-list-client-protocol.h
           ${PROTO_DIR}/ext-foreign-toplevel-list-protocol.c
    COMMAND ${WAYLAND_SCANNER} client-header
            ${PROTO_DIR}/ext-foreign-toplevel-list-v1.xml
            ${PROTO_DIR}/ext-foreign-toplevel-list-client-protocol.h
    COMMAND ${WAYLAND_SCANNER} private-code
            ${PROTO_DIR}/ext-foreign-toplevel-list-v1.xml
            ${PROTO_DIR}/ext-foreign-toplevel-list-protocol.c
    DEPENDS ${PROTO_DIR}/ext-foreign-toplevel-list-v1.xml
)

add_executable(FTHRclips
    src/main.cpp
    src/shared_memory.cpp
    src/capture_engine.cpp
    src/backend_wlr.cpp
    src/backend_ext.cpp
    src/backend_x11.cpp
    src/capture_backend.cpp
    src/audio_capture.cpp
    src/audio_multi_capture.cpp
    src/encoder.cpp
    src/save_clip.cpp
    src/ring_buffer.cpp
    ${PROTO_DIR}/wlr-screencopy-protocol.c
    ${PROTO_DIR}/xdg-output-protocol.c
    ${PROTO_DIR}/ext-image-copy-capture-protocol.c
    ${PROTO_DIR}/ext-image-capture-source-protocol.c
    ${PROTO_DIR}/ext-foreign-toplevel-list-protocol.c
)

target_include_directories(FTHRclips PRIVATE
    src/
    ${PROTO_DIR}/
    ${CMAKE_SOURCE_DIR}/../FTHRcapture_common
)

target_link_libraries(FTHRclips PRIVATE
    $<$<BOOL:${FTHR_FFMPEG_BUNDLED}>:${FTHR_FFMPEG_LINK_LIBRARIES}>
    $<$<NOT:$<BOOL:${FTHR_FFMPEG_BUNDLED}>>:PkgConfig::FFMPEG>
    PkgConfig::WAYLAND
    PkgConfig::PULSE
    pthread rt
)

target_compile_options(FTHRclips PRIVATE -Wall -Wextra)

# ---------------------------------------------------------------------------
# Runtime library search path — AUDIT-014
#
# The engine previously had no RPATH or RUNPATH at all, so it took whatever
# libavcodec.so.60 the system loader found. Inside the AppImage that is exactly
# the wrong behaviour: we ship a verified LGPL runtime and it must be the one
# that loads.
#
# $ORIGIN is resolved by the loader relative to the executable, so this stays
# correct wherever the AppImage is mounted. Two entries because PyInstaller
# places the engine in _internal/ while the libraries land next to it, and the
# raw CMake build keeps them one level up in third_party.
#
# --disable-new-dtags forces RPATH rather than RUNPATH. RUNPATH is not
# inherited by transitive dependencies, so libavdevice would fail to find its
# sibling libavfilter and the loader would fall back to the system copy — a GPL
# library, silently, at runtime.
# ---------------------------------------------------------------------------
# BUILD_WITH_INSTALL_RPATH is deliberately TRUE. With the default (FALSE) CMake
# appends the *absolute* link directory — /home/<user>/…/third_party/ffmpeg/lib —
# to the build-tree RPATH. That path is meaningless on any other machine, leaks
# the build host into a shipped binary, and would be searched before the bundled
# libraries on a machine where it happens to exist.
#
# The last entry lets the binary run straight out of the build tree during
# development without an absolute path: build/FTHRclips → ../third_party/ffmpeg/lib.
set_target_properties(FTHRclips PROPERTIES
    BUILD_WITH_INSTALL_RPATH TRUE
    INSTALL_RPATH "$ORIGIN:$ORIGIN/lib:$ORIGIN/../lib:$ORIGIN/../third_party/ffmpeg/lib"
    INSTALL_RPATH_USE_LINK_PATH FALSE
    BUILD_RPATH_USE_ORIGIN TRUE)
target_link_options(FTHRclips PRIVATE "-Wl,--disable-new-dtags")
if(FTHR_FFMPEG_BUNDLED)
    # libavdevice has transitive siblings (notably libavfilter).  Give the
    # static linker the same closed pinned search set used by the runtime RPATH.
    target_link_options(FTHRclips PRIVATE
        "-Wl,-rpath-link,${FTHR_FFMPEG_ROOT}/lib")
endif()

install(TARGETS FTHRclips RUNTIME DESTINATION bin)

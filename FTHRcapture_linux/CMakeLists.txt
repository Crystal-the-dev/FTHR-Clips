cmake_minimum_required(VERSION 3.18)
project(FTHRclips_linux CXX C)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(PkgConfig REQUIRED)
pkg_check_modules(FFMPEG REQUIRED IMPORTED_TARGET
    libavcodec libavformat libavutil libavdevice libswscale libswresample)
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
)

target_link_libraries(FTHRclips PRIVATE
    PkgConfig::FFMPEG
    PkgConfig::WAYLAND
    PkgConfig::PULSE
    pthread rt
)

target_compile_options(FTHRclips PRIVATE -O2 -Wall -Wextra)
install(TARGETS FTHRclips RUNTIME DESTINATION bin)

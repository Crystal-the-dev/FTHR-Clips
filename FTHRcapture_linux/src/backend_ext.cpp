#include "backend_ext.h"
#include <iostream>
#include <cstring>
#include <algorithm>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
extern "C" {
#include <libavutil/pixfmt.h>
}

namespace fthr {

// ── wl_output listener ──────────────────────────────────────────────────────
static void _eo_geom(void*, wl_output*, int32_t, int32_t, int32_t,
                     int32_t, int32_t, const char*, const char*, int32_t) {}
static void _eo_mode(void*, wl_output*, uint32_t, int32_t, int32_t, int32_t) {}
void ExtBackend::OutputDone(void* d, wl_output*) {
    static_cast<ExtBackend::OutputEntry*>(d)->done = true;
}
static void _eo_scale(void*, wl_output*, int32_t) {}
void ExtBackend::OutputName(void* d, wl_output*, const char* n) {
    if (n) static_cast<ExtBackend::OutputEntry*>(d)->name = n;
}
static void _eo_desc(void*, wl_output*, const char*) {}
static const wl_output_listener kExtOutListener = {
    _eo_geom, _eo_mode, ExtBackend::OutputDone,
    _eo_scale, ExtBackend::OutputName, _eo_desc,
};

// ── wl_registry listener ────────────────────────────────────────────────────
void ExtBackend::RegistryGlobal(void* d, wl_registry* reg,
                                 uint32_t name, const char* iface, uint32_t ver) {
    auto* b = static_cast<ExtBackend*>(d);
    if (strcmp(iface, wl_shm_interface.name) == 0) {
        b->shm_ = static_cast<wl_shm*>(
            wl_registry_bind(reg, name, &wl_shm_interface, 1));
    } else if (strcmp(iface, wl_output_interface.name) == 0) {
        auto* e = new OutputEntry{};
        e->handle = static_cast<wl_output*>(
            wl_registry_bind(reg, name, &wl_output_interface, std::min(ver, 4u)));
        b->all_outputs_.push_back(e);
        wl_output_add_listener(e->handle, &kExtOutListener, e);
    } else if (strcmp(iface, ext_image_copy_capture_manager_v1_interface.name) == 0) {
        b->mgr_ = static_cast<ext_image_copy_capture_manager_v1*>(
            wl_registry_bind(reg, name, &ext_image_copy_capture_manager_v1_interface, 1));
    } else if (strcmp(iface, ext_output_image_capture_source_manager_v1_interface.name) == 0) {
        b->src_mgr_ = static_cast<ext_output_image_capture_source_manager_v1*>(
            wl_registry_bind(reg, name, &ext_output_image_capture_source_manager_v1_interface, 1));
    }
}
void ExtBackend::RegistryRemove(void*, wl_registry*, uint32_t) {}

// ── session listener ────────────────────────────────────────────────────────
void ExtBackend::SessionBufferSize(void* d,
        ext_image_copy_capture_session_v1*, uint32_t w, uint32_t h) {
    auto* b = static_cast<ExtBackend*>(d);
    b->buf_width_ = w; b->buf_height_ = h;
    b->native_w_  = w; b->native_h_   = h;
}
void ExtBackend::SessionShmFormat(void* d,
        ext_image_copy_capture_session_v1*, uint32_t fmt) {
    auto* b = static_cast<ExtBackend*>(d);
    if (b->shm_format_ == 0) b->shm_format_ = fmt;
}
void ExtBackend::SessionDmabufDevice(void*, ext_image_copy_capture_session_v1*,
        struct wl_array*) {}
void ExtBackend::SessionDmabufFormat(void*, ext_image_copy_capture_session_v1*,
        uint32_t, struct wl_array*) {}
void ExtBackend::SessionDone(void* d, ext_image_copy_capture_session_v1*) {
    static_cast<ExtBackend*>(d)->buf_done_ = true;
}
void ExtBackend::SessionStopped(void* d, ext_image_copy_capture_session_v1*) {
    static_cast<ExtBackend*>(d)->session_stopped_ = true;
}
static const ext_image_copy_capture_session_v1_listener kSessionListener = {
    ExtBackend::SessionBufferSize,
    ExtBackend::SessionShmFormat,
    ExtBackend::SessionDmabufDevice,
    ExtBackend::SessionDmabufFormat,
    ExtBackend::SessionDone,
    ExtBackend::SessionStopped,
};

// ── frame listener ──────────────────────────────────────────────────────────
void ExtBackend::FrameTransform(void*, ext_image_copy_capture_frame_v1*, uint32_t) {}
void ExtBackend::FrameDamage(void*, ext_image_copy_capture_frame_v1*,
                              int32_t, int32_t, int32_t, int32_t) {}
void ExtBackend::FramePresentationTime(void*, ext_image_copy_capture_frame_v1*,
                                       uint32_t, uint32_t, uint32_t) {}
void ExtBackend::FrameReady(void* d, ext_image_copy_capture_frame_v1*) {
    static_cast<ExtBackend*>(d)->frame_ready_ = true;
}
void ExtBackend::FrameFailed(void* d, ext_image_copy_capture_frame_v1*, uint32_t) {
    static_cast<ExtBackend*>(d)->frame_failed_ = true;
}
static const ext_image_copy_capture_frame_v1_listener kFrameListener = {
    ExtBackend::FrameTransform,
    ExtBackend::FrameDamage,
    ExtBackend::FramePresentationTime,
    ExtBackend::FrameReady,
    ExtBackend::FrameFailed,
};

// ── buffer management ───────────────────────────────────────────────────────
bool ExtBackend::AllocShmBuffer() {
    shm_size_ = static_cast<size_t>(buf_width_) * buf_height_ * 4;
    shm_fd_ = memfd_create("fthr_ext_frame", MFD_CLOEXEC);
    if (shm_fd_ < 0) return false;
    if (ftruncate(shm_fd_, static_cast<off_t>(shm_size_)) < 0) {
        close(shm_fd_); shm_fd_ = -1; return false;
    }
    shm_data_ = mmap(nullptr, shm_size_, PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd_, 0);
    if (shm_data_ == MAP_FAILED) {
        close(shm_fd_); shm_fd_ = -1; shm_data_ = nullptr; return false;
    }
    shm_pool_ = wl_shm_create_pool(shm_, shm_fd_, static_cast<int32_t>(shm_size_));
    wl_buf_ = wl_shm_pool_create_buffer(shm_pool_, 0,
        static_cast<int32_t>(buf_width_), static_cast<int32_t>(buf_height_),
        static_cast<int32_t>(buf_width_ * 4), shm_format_);
    return wl_buf_ != nullptr;
}

void ExtBackend::FreeShmBuffer() {
    if (wl_buf_)   { wl_buffer_destroy(wl_buf_);     wl_buf_   = nullptr; }
    if (shm_pool_) { wl_shm_pool_destroy(shm_pool_); shm_pool_ = nullptr; }
    if (shm_data_ && shm_data_ != MAP_FAILED) { munmap(shm_data_, shm_size_); shm_data_ = nullptr; }
    if (shm_fd_ >= 0) { close(shm_fd_); shm_fd_ = -1; }
}

void ExtBackend::DestroyWayland() {
    FreeShmBuffer();
    if (session_) { ext_image_copy_capture_session_v1_destroy(session_); session_ = nullptr; }
    if (source_)  { ext_image_capture_source_v1_destroy(source_);        source_  = nullptr; }
    if (mgr_)     { ext_image_copy_capture_manager_v1_destroy(mgr_);     mgr_     = nullptr; }
    if (src_mgr_) { ext_output_image_capture_source_manager_v1_destroy(src_mgr_); src_mgr_ = nullptr; }
    for (auto* e : all_outputs_) { wl_output_destroy(e->handle); delete e; }
    all_outputs_.clear();
    if (shm_)     { wl_shm_destroy(shm_);           shm_      = nullptr; }
    if (registry_){ wl_registry_destroy(registry_);  registry_ = nullptr; }
    if (display_) { wl_display_disconnect(display_); display_  = nullptr; }
}

// ── ICaptureBackend impl ────────────────────────────────────────────────────
bool ExtBackend::Initialize(const CaptureConfig& cfg) {
    target_output_ = cfg.target_output;
    display_ = wl_display_connect(nullptr);
    if (!display_) return false;

    static const wl_registry_listener kRegListener = { RegistryGlobal, RegistryRemove };
    registry_ = wl_display_get_registry(display_);
    wl_registry_add_listener(registry_, &kRegListener, this);
    wl_display_roundtrip(display_);
    wl_display_roundtrip(display_);

    if (!mgr_ || !src_mgr_) {
        std::cerr << "[ExtBackend] ext-image-copy-capture not available" << std::endl;
        DestroyWayland(); return false;
    }

    OutputEntry* sel = nullptr;
    if (!target_output_.empty())
        for (auto* e : all_outputs_)
            if (e->name == target_output_) { sel = e; break; }
    if (!sel && !all_outputs_.empty()) sel = all_outputs_[0];
    if (!sel) { std::cerr << "[ExtBackend] No wl_output\n"; DestroyWayland(); return false; }
    output_ = sel->handle;
    while (!sel->done) wl_display_dispatch(display_);
    std::cerr << "[ExtBackend] Using output: "
              << (sel->name.empty() ? "(unnamed)" : sel->name) << std::endl;

    source_  = ext_output_image_capture_source_manager_v1_create_source(src_mgr_, output_);
    session_ = ext_image_copy_capture_manager_v1_create_session(mgr_, source_, 0);
    ext_image_copy_capture_session_v1_add_listener(session_, &kSessionListener, this);

    while (!buf_done_ && !session_stopped_) wl_display_dispatch(display_);
    if (session_stopped_ || buf_width_ == 0 || shm_format_ == 0) {
        std::cerr << "[ExtBackend] Session failed to negotiate buffer\n";
        DestroyWayland(); return false;
    }
    if (!AllocShmBuffer()) {
        std::cerr << "[ExtBackend] Failed to alloc shm buffer\n";
        DestroyWayland(); return false;
    }
    std::cerr << "[ExtBackend] Ready: " << native_w_ << "x" << native_h_ << std::endl;
    return true;
}

bool ExtBackend::CaptureFrame(RawFrame& out) {
    if (session_stopped_) return false;
    frame_ready_ = frame_failed_ = false;

    auto* frame = ext_image_copy_capture_session_v1_create_frame(session_);
    ext_image_copy_capture_frame_v1_add_listener(frame, &kFrameListener, this);
    ext_image_copy_capture_frame_v1_attach_buffer(frame, wl_buf_);
    ext_image_copy_capture_frame_v1_damage_buffer(frame, 0, 0,
        static_cast<int32_t>(buf_width_), static_cast<int32_t>(buf_height_));
    ext_image_copy_capture_frame_v1_capture(frame);
    wl_display_flush(display_);

    while (!frame_ready_ && !frame_failed_ && !session_stopped_)
        wl_display_dispatch(display_);
    ext_image_copy_capture_frame_v1_destroy(frame);

    if (frame_failed_ || session_stopped_) return false;

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    out.data         = static_cast<const uint8_t*>(shm_data_);
    out.stride       = buf_width_ * 4;
    out.width        = buf_width_;
    out.height       = buf_height_;
    out.av_pix_fmt   = AV_PIX_FMT_BGR0;
    out.timestamp_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + ts.tv_nsec;
    return true;
}

void ExtBackend::Shutdown() { DestroyWayland(); }

} // namespace fthr

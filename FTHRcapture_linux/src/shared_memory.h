#pragma once
#include <cstdint>
#include <cstring>
#include <string>

namespace fthr {

// Must match Python's SharedMemoryLayout in capture_bridge.py (Linux branch)
struct SharedMemoryLayout {
    uint32_t ui_command;
    uint32_t ui_param1;
    uint32_t ui_param2;
    uint32_t ui_param3;
    char     ui_string[1024];
    uint32_t engine_response;
    uint32_t engine_param1;
    uint32_t engine_param2;
    float    engine_param3;
    char     engine_string[2048];
    bool     is_recording;
    bool     is_initialized;
    uint64_t frames_captured;
    uint64_t bytes_written;
    uint32_t cfg_bitrate_kbps;
    uint32_t cfg_target_width;
    uint32_t cfg_target_height;
    bool     nvenc_active;

    // Encoder config — written by UI before RECONFIGURE_ENCODER command.
    // active_codec is written by the engine after Open() succeeds.
    uint32_t cfg_codec_pref;     // 0=auto 1=h264 2=hevc 3=av1
    uint32_t cfg_preset;         // 1–7
    char     active_codec[64];   // e.g. "hevc_nvenc\0"

    // v3 fields
    bool     multiband_enabled;
    char     active_audio_mappings[1024];

    // v4 fields: typed capture state plus privacy-safe derived content metrics.
    uint32_t capture_health_flags;
    uint32_t capture_generation;
    uint32_t content_sample_sequence;
    uint32_t content_suspicious_streak;
    float    content_luma_mean;
    float    content_luma_variance;
};

enum CaptureHealthFlags : uint32_t {
    CAPTURE_HEALTH_NONE            = 0,
    CAPTURE_HEALTH_ACTIVE          = 1u << 0,
    CAPTURE_HEALTH_RECOVERING      = 1u << 1,
    CAPTURE_HEALTH_BACKEND_FAILED  = 1u << 2,
    CAPTURE_HEALTH_CONTENT_SUSPECT = 1u << 3,
    CAPTURE_HEALTH_PAUSED          = 1u << 4,
};

// Publish engine_string/engine_param* before engine_response. The UI may
// read the payload as soon as it observes the response, matching the
// payload-first order used for ui_command. Only the UI clears a consumed
// response to NONE.

// : Copy at most 2047 bytes plus a NUL terminator into engine_string.
// : Route error payloads through this helper, including user-supplied paths.
inline void set_engine_string(SharedMemoryLayout* layout, const std::string& text) {
    if (!layout) return;
    const size_t cap = sizeof(layout->engine_string);      // 2048
    const size_t n   = text.size() < cap - 1 ? text.size() : cap - 1;
    // memcpy + explicit terminator rather than strncpy: strncpy does not
    // terminate when it truncates, which is the whole failure mode here.
    if (n) std::memcpy(layout->engine_string, text.data(), n);
    layout->engine_string[n] = '\0';
}

enum class CommandType : uint32_t {
    NONE=0, START_RECORDING=1, STOP_RECORDING=2, SAVE_CLIP=3,
    SET_RESOLUTION=4, SET_QUALITY=5, SET_FRAMERATE=6, SET_HOTKEY=7,
    SET_TARGET_WINDOW=8, GET_STATUS=9, RECONFIGURE_ENCODER=10,
    // Kept in lockstep with the Windows command enum; Linux does not issue it.
    SHUTDOWN=11
};

enum class ResponseType : uint32_t {
    NONE=0, RECORDING_STARTED=1, RECORDING_STOPPED=2, CLIP_SAVED=3,
    STATUS_UPDATE=4, ERROR_OCCURRED=5, SAVE_STARTED=6,
    MANUAL_RECORDING_ERROR=7
};

class SharedMemory {
public:
    SharedMemory() = default;
    ~SharedMemory() { Shutdown(); }

    bool Initialize(const std::string& name);
    void Shutdown();
    SharedMemoryLayout* GetLayout() { return layout_; }

private:
    int   shm_fd_  = -1;
    void* mapping_ = nullptr;
    std::string name_;
    SharedMemoryLayout* layout_ = nullptr;
};

} // namespace fthr

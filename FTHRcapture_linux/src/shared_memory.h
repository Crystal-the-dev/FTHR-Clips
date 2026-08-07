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
};

// ---------------------------------------------------------------------------
// The producer contract (engine -> UI)                          [AUDIT-018]
//
//   1. write engine_string / engine_param* — the payload
//   2. publish engine_response LAST
//
// engine_response is the field the UI polls. The moment it changes, the UI is
// entitled to read every other response field, so they must already be
// settled. This mirrors the UI's own contract in the other direction, where
// save_clip() writes ui_string and ui_param* and publishes ui_command last.
//
// Getting it backwards does not crash anything — it produces an error report
// with no message, which is the one diagnostic the message existed to provide,
// in exactly the race no user can reproduce on request.
//
// Consumption is the UI's job alone: only the UI writes
// engine_response = NONE. The engine must not clear a response it has already
// published.
// ---------------------------------------------------------------------------

//: Write `text` into engine_string, always NUL-terminated, always within the
//: buffer. Use this instead of strcpy/sprintf on the field — an error message
//: built from a user-supplied path can be arbitrarily long, and engine_string
//: is a fixed 2048-byte array.
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
    SET_TARGET_WINDOW=8, GET_STATUS=9, RECONFIGURE_ENCODER=10
};

enum class ResponseType : uint32_t {
    NONE=0, RECORDING_STARTED=1, RECORDING_STOPPED=2, CLIP_SAVED=3,
    STATUS_UPDATE=4, ERROR_OCCURRED=5, SAVE_STARTED=6
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

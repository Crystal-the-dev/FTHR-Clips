// shared_memory.h
// FTHR Capture Engine - Shared memory IPC channel
//
// Responsibilities:
//   - Define the SharedMemoryLayout struct that Python and C++ both map
//   - Define the CommandType and ResponseType enums used over the channel
//   - Provide SharedMemory: the C++-side owner that creates the mapping
//
// Layout contract:
//   Python (capture_bridge.py) opens the mapping read/write.
//   C++ (main.cpp command loop) polls ui_command and writes engine_response.
//   Both sides treat the layout as a single shared struct.
//
// Threading:
//   The layout is accessed from main.cpp's command loop (main thread) and
//   read by Python's polling loop. The 10ms Sleep() in the command loop
//   provides sufficient visibility without explicit synchronisation for
//   the use patterns here (one writer per field at a time).
//
// What does NOT live here:
//   - Encoding configuration   ->  video_encoder.h (EncoderConfig)
//   - Capture configuration    ->  capture_engine.h (CaptureConfig)
//   - Python bridge logic      ->  capture_bridge.py

#pragma once
#ifndef FTHR_SHARED_MEMORY_H
#define FTHR_SHARED_MEMORY_H

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <cstdint>


namespace fthr {


    // ---------------------------------------------------------------------------
    // CommandType
    //
    // Written by Python (ui_command field), read by C++ command loop.
    // Values are explicit to prevent silent renumbering if new commands
    // are inserted between existing ones.
    //
    // Deprecated commands (SET_*) remain in the enum for ABI stability.
    // They are accepted by the command loop and silently ignored.
    // The new settings pipeline passes all configuration via argv at
    // engine startup rather than through these runtime commands.
    // ---------------------------------------------------------------------------
    enum class CommandType : uint32_t {
        NONE = 0,
        START_RECORDING = 1,
        STOP_RECORDING = 2,
        SAVE_CLIP = 3,

        // Deprecated: settings now passed via command-line args at startup.
        // Kept for ABI compatibility - command loop ignores them gracefully.
        SET_RESOLUTION = 4,
        SET_QUALITY = 5,
        SET_FRAMERATE = 6,
        SET_HOTKEY = 7,
        SET_TARGET_WINDOW = 8,

        GET_STATUS = 9,

        // Phase 6: runtime encoder reconfiguration without process restart.
        // When sent, the engine reads cfg_bitrate_kbps / cfg_target_width /
        // cfg_target_height from SharedMemoryLayout and applies them to the
        // next SaveClip call. Currently stubbed in the command loop.
        RECONFIGURE_ENCODER = 10,
    };


    // ---------------------------------------------------------------------------
    // ResponseType
    //
    // Written by C++ command loop (engine_response field), read by Python.
    //
    // Phase 3 addition: SAVE_STARTED
    //   Sent immediately when main thread queues a clip save task.
    //   Python no longer needs to wait for encoding to complete.
    //   CLIP_SAVED is sent later by SaveClipThread when encoding finishes.
    // ---------------------------------------------------------------------------
    enum class ResponseType : uint32_t {
        NONE = 0,
        RECORDING_STARTED = 1,
        RECORDING_STOPPED = 2,
        CLIP_SAVED = 3,
        STATUS_UPDATE = 4,
        ERROR_OCCURRED = 5,
        SAVE_STARTED = 6,   // Phase 3: async SaveClip queued successfully
    };

    // v4 capture-health status bits.  These are continuous status, not command
    // responses: publishing them must never consume or overwrite the save
    // response slot owned by the UI.
    enum CaptureHealthFlags : uint32_t {
        CAPTURE_HEALTH_NONE            = 0,
        CAPTURE_HEALTH_ACTIVE          = 1u << 0,
        CAPTURE_HEALTH_RECOVERING      = 1u << 1,
        CAPTURE_HEALTH_BACKEND_FAILED  = 1u << 2,
        CAPTURE_HEALTH_CONTENT_SUSPECT = 1u << 3,
        CAPTURE_HEALTH_PAUSED          = 1u << 4,
    };


    // ---------------------------------------------------------------------------
    // SharedMemoryLayout
    //
    // The exact binary layout that both Python and C++ map into memory.
    // Both sides must agree on this struct. Any change here requires a
    // matching change in capture_bridge.py's SharedMemoryLayout ctypes struct.
    //
    // Field ownership:
    //   UI fields (ui_*)    - written by Python, read by C++
    //   Engine fields (engine_*) - written by C++, read by Python
    //   Status fields       - written by C++, read by Python
    //   Config fields (cfg_*) - written by Python, read by C++ on RECONFIGURE_ENCODER
    // ---------------------------------------------------------------------------
    struct SharedMemoryLayout {

        // ------------------------------------------------------------------
        // Command channel (Python -> C++)
        // ------------------------------------------------------------------
        volatile CommandType ui_command;
        volatile uint32_t    ui_param1;       // General purpose param (e.g. duration_seconds)
        volatile uint32_t    ui_param2;
        volatile uint32_t    ui_param3;
        wchar_t              ui_string[256];  // General purpose string (e.g. output path)

        // ------------------------------------------------------------------
        // Response channel (C++ -> Python)
        // ------------------------------------------------------------------
        // The producer contract (engine -> UI)                  [AUDIT-018]
        //
        //   1. write engine_string / engine_param* — the payload
        //   2. publish engine_response LAST
        //
        // engine_response is the field the UI polls. The moment it changes,
        // the UI is entitled to read every other response field, so they must
        // already be settled. This mirrors the UI's own contract in the other
        // direction: save_clip() writes ui_string and ui_param* and publishes
        // ui_command last.
        //
        // Consumption is the UI's job alone: only the UI writes
        // engine_response = NONE. The engine must not clear a response it has
        // already published.
        //
        // Use SetEngineError() / SetEngineString() below rather than touching
        // engine_string directly — they enforce both the bound and the order.
        volatile ResponseType engine_response;
        volatile uint32_t     engine_param1;
        volatile uint32_t     engine_param2;
        volatile float        engine_param3;
        wchar_t               engine_string[512];

        // ------------------------------------------------------------------
        // Status fields (C++ -> Python, updated every command loop tick)
        // ------------------------------------------------------------------
        volatile bool     is_recording;
        volatile bool     is_initialized;
        volatile uint64_t frames_captured;
        volatile uint64_t bytes_written;

        // ------------------------------------------------------------------
        // Phase 6: reconfiguration fields (Python -> C++)
        //
        // Set by Python before sending RECONFIGURE_ENCODER.
        // Zero means "no change" for that field.
        // C++ command loop reads these when handling RECONFIGURE_ENCODER.
        // ------------------------------------------------------------------
        volatile uint32_t cfg_bitrate_kbps;   // New encoder bitrate in kbps (0 = no change)
        volatile uint32_t cfg_target_width;   // New output width  in pixels (0 = no change)
        volatile uint32_t cfg_target_height;  // New output height in pixels (0 = no change)

        // ------------------------------------------------------------------
        // Hardware encoding status (C++ -> Python, set once at init)
        // ------------------------------------------------------------------
        volatile bool     nvenc_active;       // true if NVENC initialized successfully

        // ------------------------------------------------------------------
        // v2 fields: encoder reconfiguration (Python -> C++)
        // ------------------------------------------------------------------
        volatile uint32_t cfg_codec_pref;     // 0=auto 1=h264 2=hevc 3=av1
        volatile uint32_t cfg_preset;         // 1-7
        char              active_codec[64];   // null-terminated UTF-8 codec name (C++ -> Python)

        // ------------------------------------------------------------------
        // v3 fields: multiband audio (C++ -> Python, set at init)
        // ------------------------------------------------------------------
        volatile bool     multiband_enabled;
        char              active_audio_mappings[1024]; // JSON blob: {app: category}

        // ------------------------------------------------------------------
        // v4 fields: capture/content health (C++ -> Python)
        // ------------------------------------------------------------------
        volatile uint32_t capture_health_flags;
        volatile uint32_t capture_generation;       // increments after backend reset
        volatile uint32_t content_sample_sequence;  // ~1 Hz; wraps harmlessly
        volatile uint32_t content_suspicious_streak;
        volatile float    content_luma_mean;        // derived metric only, no pixels
        volatile float    content_luma_variance;    // derived metric only, no pixels
    };


    // ---------------------------------------------------------------------------
    // Response payload helpers                                       [AUDIT-018]
    //
    // engine_string is wchar_t[512] — 511 usable characters plus a terminator.
    // Error text is routinely built from a user-supplied output path, which can
    // be longer than that, so every write goes through here.
    // ---------------------------------------------------------------------------

    //: Write `text` into engine_string: always NUL-terminated, always inside
    //: the buffer, truncated if necessary. Does NOT publish a response.
    inline void SetEngineString(SharedMemoryLayout* layout, const wchar_t* text) {
        if (!layout) return;
        const size_t cap = sizeof(layout->engine_string) / sizeof(wchar_t); // 512
        if (!text) { layout->engine_string[0] = L'\0'; return; }
        size_t n = 0;
        while (n < cap - 1 && text[n] != L'\0') {
            layout->engine_string[n] = text[n];
            ++n;
        }
        // wcsncpy would not terminate on truncation — that is the failure mode
        // this exists to prevent, so the terminator is written explicitly.
        layout->engine_string[n] = L'\0';
    }

    //: Publish a failure: message FIRST, response LAST. This is the only
    //: sanctioned way for the engine to report ERROR_OCCURRED — writing
    //: engine_response directly on an error path skips the diagnostic and
    //: breaks the publication order the UI relies on.
    inline void SetEngineError(SharedMemoryLayout* layout, const wchar_t* message) {
        if (!layout) return;
        SetEngineString(layout, message);
        layout->engine_response = ResponseType::ERROR_OCCURRED;
    }

    // ---------------------------------------------------------------------------
    // SharedMemory
    //
    // C++-side owner of the shared memory mapping.
    // Created by the C++ engine at startup. Python opens it read/write.
    // ---------------------------------------------------------------------------
    class SharedMemory {
    public:
        SharedMemory();
        ~SharedMemory();

        // Creates the named file mapping and maps it into the process.
        // Must be called before GetLayout().
        bool Initialize(const wchar_t* name);

        // Unmaps the view and closes the file mapping handle.
        void Shutdown();

        // Returns a pointer to the mapped layout. Nullptr if not initialized.
        SharedMemoryLayout* GetLayout() { return layout_; }

        // NOTE: SendCommand()/WaitForResponse() used to live here. They were
        // the *client* half of the protocol — never called by the engine — and
        // WaitForResponse() consumed engine_response by writing NONE. That made
        // the engine a second consumer of a channel the UI owns, which is the
        // defect class AUDIT-017 was. Removed rather than left as a trap.

    private:
        HANDLE              file_mapping_;
        SharedMemoryLayout* layout_;
    };


} // namespace fthr


#endif // FTHR_SHARED_MEMORY_H

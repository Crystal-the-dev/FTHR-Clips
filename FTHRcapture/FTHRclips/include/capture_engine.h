// capture_engine.h
// FTHR Capture Engine - Core capture loop and ring buffer management
//
// Capture backend (selected at Initialize() time, WGC is the default; DXGI
// is a last-resort fallback for Windows 10 < build 1903):
//
//   WGC path (wgc_active_ = true) — DEFAULT for every capture mode:
//     - Windows Graphics Capture API — event-driven, lower overhead than DXGI.
//     - Hooks at the DWM/compositor level. Survives independent flip mode,
//       so it captures kernel-anti-cheat games (Valorant/Vanguard, EAC/BE
//       titles) that DXGI cannot see. Same path Xbox Game Bar uses.
//     - On Optimus laptops, WGC can deliver frames directly into NVIDIA VRAM
//       (unlike DXGI which forces Intel GPU involvement). No CPU copy.
//     - FrameArrived callback signals CaptureThread; no polling loop.
//     - Sets nvidia_device_ = true on desktop NVIDIA (single GPU). On Optimus,
//       nvidia_device_ is false and nvenc_device_ holds the separate NVIDIA device.
//     - Variants: CreateForMonitor (desktop + AC games), CreateForWindow
//       (regular window mode).
//
//   DXGI path (wgc_active_ = false) — FALLBACK ONLY:
//     - IDXGIOutputDuplication — polling loop in CaptureThread.
//     - Reached only when WGC init fails (rare, pre-1903 Windows 10).
//     - Returns black/stale frames for AC games in independent flip mode.
//
// Encode backend (selected after capture backend):
//
//   Compressed hardware path (legacy nvenc_active_ flag = true):
//     - Native NVENC or FFmpeg AMF encodes every frame from CaptureThread
//     - Encoded AVCC packets pushed into EncodedRingBuffer via callback
//     - Raw FramePool NOT allocated (~8GB saved at 1080p/60fps/30s)
//     - SaveClip: TakeSnapshot() -> MuxEncodedClip() (no re-encoding)
//
//   x264 fallback (legacy nvenc_active_ flag = false):
//     - Existing raw BGRA FramePool path, unchanged
//     - SaveClip: existing VideoEncoder encode loop
//
// Threading model:
//   CaptureThread  - grabs frames (WGC or DXGI), routes to NVENC or FramePool
//   SaveClipThread - muxes encoded snapshots OR encodes raw frames
//   EncodeThread   - continuous recording (StartRecording, x264 only for now)

#pragma once
#ifndef FTHR_CAPTURE_ENGINE_H
#define FTHR_CAPTURE_ENGINE_H

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <thread>
#include <atomic>
#include <vector>
#include <mutex>
#include <string>
#include <cstdint>
#include <memory>

#include "save_clip_task.h"      // SaveClipTask + SaveClipQueue
#include "replay_encoder.h"      // codec-neutral Windows replay encoder seam
#include "encoded_ring_buffer.h" // EncodedRingBuffer
#include "audio_capture.h"       // AudioCapture (WASAPI loopback -> PCM ring)
#include "audio_ring_buffer.h"   // AudioRingBuffer (raw float32 PCM)
#include "shared_memory.h"       // typed v4 capture-health flags
#include "windows_monitor_resolver.h"


namespace fthr {


    // Forward declaration
    struct SharedMemoryLayout;


    // ---------------------------------------------------------------------------
    // CaptureConfig
    // ---------------------------------------------------------------------------
    struct CaptureConfig {
        uint32_t framerate = 60;
        uint32_t buffer_seconds = 30;
        uint32_t target_width = 0;
        uint32_t target_height = 0;
        uint32_t bitrate_kbps = 16000;
        uint32_t max_buffer_mb = 512;
        // argv[11]: Auto and explicit H.264 both resolve to H.264 for this
        // NVIDIA stage; explicit HEVC/AV1 must never silently become H.264.
        VideoCodec video_codec = VideoCodec::H264;

        // Capture mode — set at startup, requires engine restart to change.
        enum class CaptureModeEnum : uint32_t { DESKTOP = 0, WINDOW = 1 };
        CaptureModeEnum capture_mode = CaptureModeEnum::DESKTOP;
        uintptr_t       target_hwnd  = 0;  // non-zero only when capture_mode == WINDOW

        // How to fit non-native target resolutions in the encoded clip.
        //   STRETCH = scale to target dims, may distort if aspect differs (legacy default).
        //   FIT     = preserve aspect, pad with black bars (letterbox/pillarbox).
        // Only consulted when target_width/target_height are set (non-zero) and the
        // source aspect ratio differs from the target.
        enum class ScalingModeEnum : uint32_t { STRETCH = 0, FIT = 1 };
        ScalingModeEnum scaling_mode = ScalingModeEnum::STRETCH;

        // argv[14]: 0 = disable WASAPI loopback audio capture entirely.
        // Useful when the user turns off audio in settings (no system audio in clips).
        bool audio_enabled = true;

        // Stable Windows monitor device interface path from the UI. Resolved
        // into transient HMONITOR/LUID/DXGI objects for each capture generation.
        std::wstring monitor_device_path;
    };


    // ---------------------------------------------------------------------------
    // FramePool - used only on x264 fallback path
    // ---------------------------------------------------------------------------
    class FramePool {
    public:
        FramePool() = default;

        void     Allocate(size_t frame_count, size_t bytes_per_frame);
        uint8_t* GetSlot(size_t index) noexcept;

        size_t BytesPerFrame() const noexcept { return bytes_per_frame_; }
        size_t FrameCount()    const noexcept { return frame_count_; }
        size_t TotalBytes()    const noexcept { return storage_.size(); }

    private:
        std::vector<uint8_t> storage_;
        size_t               bytes_per_frame_ = 0;
        size_t               frame_count_ = 0;
    };


    // ---------------------------------------------------------------------------
    // CaptureEngine
    // ---------------------------------------------------------------------------
    class CaptureEngine {
    public:
        CaptureEngine();
        ~CaptureEngine();

        bool Initialize(const CaptureConfig& config);
        void Shutdown();

        bool StartRecording(const wchar_t* path);
        void StopRecording();

        bool SaveClip(const wchar_t* path, uint32_t duration_seconds,
            SharedMemoryLayout* shared_memory);

        bool     IsRecording()   const;
        uint64_t GetFrameCount() const;
        bool     IsNvencActive() const { return nvenc_active_; }
        std::string GetActiveEncoderName() const {
            return nvenc_active_ && replay_encoder_
                ? replay_encoder_->GetActiveEncoderInfo().name
                : "libopenh264";
        }
        VideoCodec GetActiveVideoCodec() const {
            return nvenc_active_ && replay_encoder_
                ? replay_encoder_->GetActiveEncoderInfo().codec
                : VideoCodec::H264;
        }
        uint32_t GetCaptureHealthFlags() const { return capture_health_flags_.load(); }
        uint32_t GetCaptureGeneration() const { return capture_generation_.load(); }
        uint32_t GetContentSampleSequence() const { return content_sample_sequence_.load(); }
        uint32_t GetContentSuspiciousStreak() const { return content_suspicious_streak_.load(); }
        float GetContentLumaMean() const { return content_luma_mean_.load(); }
        float GetContentLumaVariance() const { return content_luma_variance_.load(); }

        struct Stats {
            uint64_t frames_captured;
            uint64_t frames_dropped;
            float    capture_fps;
            size_t   pool_memory_mb;
            size_t   ring_used_frames;
            size_t   ring_max_frames;
            float    effective_buffer_seconds;
        };
        Stats GetStats() const;


    private:
        // -----------------------------------------------------------------------
        // Thread entry points
        // -----------------------------------------------------------------------
        void CaptureThread();       // dispatches to WGC or DXGI
        void CaptureThreadWGC();    // WGC event-driven loop
        void EncodeThread();
        void SaveClipThread();

        // -----------------------------------------------------------------------
        // SaveClip worker implementations
        // -----------------------------------------------------------------------
        // These return false when the clip was NOT written, and publish the
        // reason via SetEngineError() before returning. SaveClipThread used to
        // ignore the outcome and report CLIP_SAVED unconditionally, so every
        // Windows save failure reached the user as success (AUDIT-021).
        bool ProcessSaveClipTask(const SaveClipTask& task); // transactional wrapper
        bool MuxEncodedClip(const SaveClipTask& task,
            const std::wstring& output_path);               // NVENC path: mux only
        bool EncodeRawClip(const SaveClipTask& task,
            const std::wstring& output_path);               // x264 path: encode + mux

        // -----------------------------------------------------------------------
        // D3D11 / DXGI / WGC helpers
        // -----------------------------------------------------------------------
        bool InitializeWGC();             // WGC capture of the resolved desktop monitor
        bool InitializeWindowCapture();   // WGC window/game capture
        void ShutdownWGC();
        bool InitializeD3D11();           // DXGI fallback for the resolved adapter/output
        void ShutdownD3D11();
        void SampleContentBGRA(const uint8_t* data, uint32_t stride,
            uint32_t width, uint32_t height, uint64_t produced_frame);
        void SampleContentTexture(ID3D11Texture2D* texture, uint64_t produced_frame);
        void PublishContentMetrics(uint64_t sum, uint64_t sum_sq, uint32_t count);
        void ClearReplayForRecovery();
        bool ResolveSelectedMonitor(const char* backend_name);
        bool InitializeMonitorCaptureDevice(
            const char* backend_name, IDXGIOutput** selected_output);
        bool EnsureStagingTexture();
        bool EncodeGpuReplayTexture(
            ID3D11Texture2D* source,
            int64_t present_qpc,
            uint64_t produced_frame);
        void FailReplayEncoder(const char* operation);

        // -----------------------------------------------------------------------
        // D3D11 state (shared by WGC and DXGI paths)
        // -----------------------------------------------------------------------
        ID3D11Device*           device_;           // D3D11 device (NVIDIA when WGC active)
        ID3D11DeviceContext*    context_;
        IDXGIOutputDuplication* duplication_;      // null when WGC is active
        ID3D11Texture2D*        staging_texture_;  // null on WGC+NVENC path
        ID3D11Texture2D*        health_staging_texture_; // 16x9 grid, sampled ~1 Hz

        // Optimus state: capture on Intel adapter, NVENC on separate NVIDIA device.
        // Used by both DXGI and WGC paths on Optimus laptops.
        ID3D11Device*        nvenc_device_;
        ID3D11DeviceContext* nvenc_context_;

        // -----------------------------------------------------------------------
        // WGC state (PIMPL — WinRT types confined to capture_engine.cpp)
        // -----------------------------------------------------------------------
        bool              wgc_active_;
        struct WGCState;                        // defined in capture_engine.cpp
        std::unique_ptr<WGCState> wgc_state_;
        std::mutex              wgc_frame_mutex_;
        std::condition_variable wgc_frame_cv_;
        bool                    wgc_frame_ready_;
        std::atomic<bool>       monitor_source_invalidated_{false};

        // -----------------------------------------------------------------------
        // Thread handles
        // -----------------------------------------------------------------------
        std::thread* capture_thread_;
        std::thread* encode_thread_;
        std::thread* save_clip_thread_;
        std::atomic<bool> running_;
        std::atomic<bool> is_recording_;

        // -----------------------------------------------------------------------
        // Capture dimensions (set by whichever backend initializes first)
        // -----------------------------------------------------------------------
        uint32_t  width_;
        uint32_t  height_;
        uintptr_t target_hwnd_;  // 0 = desktop mode; non-zero = window capture mode

        // Focus gate: when true, CaptureThreadWGC drops every frame where
        // GetForegroundWindow() != target_hwnd_. Used for anti-cheat games
        // captured via WGC monitor — we still capture the whole monitor (the
        // path Vanguard allows), but only encode while the protected game is
        // foreground so we don't bake the user's desktop into clips.
        bool focus_gated_;

        // -----------------------------------------------------------------------
        // Config
        // -----------------------------------------------------------------------
        uint32_t fps_;
        uint32_t buffer_seconds_;
        uint32_t target_width_;
        uint32_t target_height_;
        uint32_t bitrate_kbps_;
        uint32_t scaling_mode_;   // 0 = stretch, 1 = fit/letterbox
        std::wstring monitor_device_path_;
        monitor::WindowsMonitorTopologySource monitor_topology_source_;
        monitor::MonitorResolver monitor_resolver_;
        monitor::MonitorTopologyEntry resolved_monitor_;

        // -----------------------------------------------------------------------
        // Compressed replay path. nvenc_active_ retains its legacy name because
        // it is mirrored into the frozen shared-memory v4 contract; it now means
        // "a hardware replay encoder is active" for native NVENC or FFmpeg AMF.
        // -----------------------------------------------------------------------
        bool                              nvenc_active_;
        bool                              nvidia_device_; // true when D3D11 device is on the NVIDIA adapter (GPU zero-copy enabled)
        bool                              replay_encoder_cpu_input_;
        EncoderVendor                     capture_adapter_vendor_;
        std::unique_ptr<IReplayEncoder>    replay_encoder_;
        std::unique_ptr<EncodedRingBuffer> encoded_ring_;

        // -----------------------------------------------------------------------
        // x264 fallback path
        // -----------------------------------------------------------------------
        FramePool            frame_pool_;
        size_t               max_frames_;
        std::atomic<size_t>  ring_head_{ 0 };
        std::atomic<size_t>  ring_count_{ 0 };
        mutable std::mutex   ring_mutex_;

        // -----------------------------------------------------------------------
        // Async SaveClip
        // -----------------------------------------------------------------------
        SaveClipQueue          save_clip_queue_;
        std::atomic<uint32_t>  next_task_id_{ 1 };

        // -----------------------------------------------------------------------
        // Audio capture pipeline
        //
        // AudioCapture (WASAPI) pushes raw float32 PCM into AudioRingBuffer.
        // No encoding during gameplay - zero CPU overhead on hot path.
        // MuxEncodedClip() encodes PCM -> AAC once on SaveClipThread at save time.
        //
        // audio_active_ is true only when all components initialized successfully.
        //
        // A/V sync epoch:
        //   audio_sync_epoch_frames_ is the audio ring's head_ value at the moment
        //   audio_capture_.Start() returns. Video PTS starts from 0 on the first
        //   EncodeFrame call, which happens AFTER audio has already accumulated
        //   frames during NVENC init. Subtracting this offset from the audio
        //   frame count aligns both clocks to the same t=0.
        // -----------------------------------------------------------------------
        bool                             audio_active_;
        AudioCapture                     audio_capture_;
        std::unique_ptr<AudioRingBuffer> audio_ring_;
        uint64_t                         audio_sync_epoch_frames_; // audio frames already in ring when video t=0 begins

        // -----------------------------------------------------------------------
        // Continuous recording state
        // -----------------------------------------------------------------------
        std::wstring record_path_;
        std::mutex   record_mutex_;

        // -----------------------------------------------------------------------
        // Stats
        // -----------------------------------------------------------------------
        std::atomic<uint64_t> frames_captured_;
        std::atomic<uint64_t> frames_dropped_;
        std::atomic<uint32_t> capture_health_flags_{CAPTURE_HEALTH_NONE};
        std::atomic<uint32_t> capture_generation_{0};
        std::atomic<uint32_t> content_sample_sequence_{0};
        std::atomic<uint32_t> content_suspicious_streak_{0};
        std::atomic<float> content_luma_mean_{0.0f};
        std::atomic<float> content_luma_variance_{0.0f};
    };


} // namespace fthr


#endif // FTHR_CAPTURE_ENGINE_H

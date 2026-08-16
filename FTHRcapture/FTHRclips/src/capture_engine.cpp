// capture_engine.cpp
// FTHR Capture Engine - Implementation

// Must be defined before ANY include that pulls in windows.h (including winrt/base.h)
// to prevent the min/max macros from being defined and stomping std::min/std::max.
#ifndef NOMINMAX
#define NOMINMAX
#endif
//
// Capture backends (WGC is the default for every mode, DXGI is a last-resort
// fallback for systems where WGC is unavailable — Windows 10 < build 1903):
//
//   Desktop mode                  -> InitializeWGC() (CreateForMonitor)
//   Window mode (regular)         -> InitializeWindowCapture() (CreateForWindow)
//   Window mode (anti-cheat exe)  -> InitializeWGC() + focus_gated_=true
//
// Why WGC is preferred even for desktop: kernel anti-cheats like Vanguard
// force the protected game into independent flip mode (frames go GPU->display
// directly, bypassing DWM). DXGI OutputDuplication captures at the DWM level,
// so the protected game shows up as black/stale frames. WGC hooks deeper at
// the compositor level — it's the same path Xbox Game Bar uses, and the AC
// vendors allow it.
//
//   WGC  path: InitializeWGC()/InitializeWindowCapture() -> CaptureThreadWGC()
//   DXGI path: InitializeD3D11()                          -> CaptureThread()
//
// Encode backends:
//   NVENC path: CaptureThread -> HardwareEncoder -> EncodedRingBuffer
//               SaveClip -> TakeSnapshot -> MuxEncodedClip (no re-encoding)
//               FramePool NOT allocated
//   x264 path:  CaptureThread -> FramePool (raw BGRA ring buffer)
//               SaveClip -> EncodeRawClip (VideoEncoder encode loop)
//
// Audio:
//   AudioCapture (WASAPI loopback) -> raw float32 PCM -> AudioRingBuffer
//   SaveClip snapshots AudioRingBuffer, encodes PCM->AAC on SaveClipThread.

// ---------------------------------------------------------------------------
// Windows Graphics Capture (WGC) includes
// Must come before other Windows headers to avoid redefinition conflicts.
// Requires C++17 (/std:c++17) and windowsapp.lib.
// ---------------------------------------------------------------------------
#pragma comment(lib, "windowsapp")

#include <winrt/base.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <Windows.Graphics.Capture.Interop.h>

#include "capture_engine.h"
#include "hardware_encoder.h"
#include "encoded_video_config_ffmpeg.h"
#include "video_encoder.h"
#include "save_clip_task.h"
#include "shared_memory.h"
#include "audio_capture.h"
#include "audio_ring_buffer.h"
#include "audio_encoder.h"
#include "transactional_save.h"
#include "replay_interval.h"
#include <iostream>
#include <chrono>
#include <cstring>
#include <cwctype>
#include <algorithm>

extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/opt.h>
#include <libavutil/imgutils.h>
}


namespace fthr {


    // ===========================================================================
    // WGCState — WinRT types confined here so the header stays WinRT-free
    // ===========================================================================

    struct CaptureEngine::WGCState {
        winrt::Windows::Graphics::Capture::GraphicsCaptureItem          item{ nullptr };
        winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool   frame_pool{ nullptr };
        winrt::Windows::Graphics::Capture::GraphicsCaptureSession        session{ nullptr };
        winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice  winrt_device{ nullptr };
        winrt::event_token                                                frame_arrived_token{};
        winrt::event_token                                                item_closed_token{};
        bool                                                              item_closed_registered = false;
        bool                                                              monitor_item = false;
    };


    // ===========================================================================
    // IsAntiCheatProtected — heuristic: known kernel-AC games by exe name
    // ===========================================================================
    //
    // Returns true when the given window belongs to a process whose anti-cheat
    // is known to interfere with normal capture paths (DXGI duplication, WGC
    // CreateForWindow). For these games we route through WGC CreateForMonitor
    // — the same path Xbox Game Bar uses, which the anti-cheats allow.
    //
    // Detection is by executable name. We avoid module enumeration because
    // OpenProcess with PROCESS_VM_READ is denied against Vanguard-protected
    // processes. PROCESS_QUERY_LIMITED_INFORMATION is enough for the exe path
    // and is allowed by every kernel AC we care about.
    //
    // Add new entries here as they're confirmed in the field. Match is
    // case-insensitive on the executable basename.
    // ---------------------------------------------------------------------------
    static bool IsAntiCheatProtected(HWND hwnd) {
        if (!hwnd) return false;

        DWORD pid = 0;
        GetWindowThreadProcessId(hwnd, &pid);
        if (!pid) return false;

        HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
        if (!h) return false;

        wchar_t path[MAX_PATH] = {};
        DWORD len = MAX_PATH;
        BOOL ok = QueryFullProcessImageNameW(h, 0, path, &len);
        CloseHandle(h);
        if (!ok) return false;

        // Extract basename and lowercase it.
        const wchar_t* slash = wcsrchr(path, L'\\');
        std::wstring exe = slash ? slash + 1 : path;
        std::transform(exe.begin(), exe.end(), exe.begin(),
                       [](wchar_t c) { return static_cast<wchar_t>(towlower(c)); });

        // Known kernel-AC titles. All are matched lowercase.
        static const wchar_t* kProtected[] = {
            L"valorant.exe",                       // Vanguard
            L"valorant-win64-shipping.exe",        // Vanguard (UE shipping name)
            L"r5apex.exe",                         // Apex Legends — EAC
            L"fortniteclient-win64-shipping.exe",  // Fortnite — EAC
            L"rainbowsix.exe",                     // R6 Siege — BattlEye
            L"rainbowsix_be.exe",                  // R6 Siege — BattlEye launcher
            L"escapefromtarkov.exe",               // EFT — BattlEye
            L"destiny2.exe",                       // Destiny 2 — BattlEye
            L"tslgame.exe",                        // PUBG — BattlEye
            L"thefinals.exe",                      // The Finals — EAC
            L"deltaforceclient-win64-shipping.exe",// Delta Force — AC
        };
        for (const wchar_t* name : kProtected) {
            if (exe == name) return true;
        }
        return false;
    }


    // ===========================================================================
    // FramePool
    // ===========================================================================

    void FramePool::Allocate(size_t frame_count, size_t bytes_per_frame) {
        frame_count_ = frame_count;
        bytes_per_frame_ = bytes_per_frame;
        storage_.resize(frame_count * bytes_per_frame);
        std::cout << "[FramePool] Allocated " << frame_count << " slots x "
            << bytes_per_frame << " bytes = "
            << (storage_.size() / 1024 / 1024) << " MB" << std::endl;
    }

    uint8_t* FramePool::GetSlot(size_t index) noexcept {
        return storage_.data() + (index * bytes_per_frame_);
    }


    // ===========================================================================
    // Constructor / Destructor
    // ===========================================================================

    CaptureEngine::CaptureEngine()
        : capture_thread_(nullptr)
        , encode_thread_(nullptr)
        , save_clip_thread_(nullptr)
        , running_(false)
        , is_recording_(false)
        , device_(nullptr)
        , context_(nullptr)
        , duplication_(nullptr)
        , staging_texture_(nullptr)
        , health_staging_texture_(nullptr)
        , nvenc_device_(nullptr)
        , nvenc_context_(nullptr)
        , wgc_active_(false)
        , wgc_frame_ready_(false)
        , width_(0)
        , height_(0)
        , target_hwnd_(0)
        , focus_gated_(false)
        , fps_(60)
        , buffer_seconds_(30)
        , target_width_(0)
        , target_height_(0)
        , bitrate_kbps_(16000)
        , scaling_mode_(0)
        , monitor_resolver_(monitor_topology_source_)
        , nvenc_active_(false)
        , nvidia_device_(false)
        , max_frames_(0)
        , frames_captured_(0)
        , frames_dropped_(0)
        , audio_active_(false)
    {
    }

    CaptureEngine::~CaptureEngine() {
        Shutdown();
    }


    // ===========================================================================
    // Initialize
    //
    // 1. Init D3D11 / DXGI (always)
    // 2. Try NVENC - if succeeds: EncodedRingBuffer, skip FramePool
    // 3. If NVENC fails: fall through to x264 FramePool path
    // 4. Start threads
    // ===========================================================================

    bool CaptureEngine::Initialize(const CaptureConfig& config) {
        fps_ = config.framerate;
        buffer_seconds_ = config.buffer_seconds;
        target_width_ = config.target_width;
        target_height_ = config.target_height;
        bitrate_kbps_ = config.bitrate_kbps;
        scaling_mode_ = (config.scaling_mode == CaptureConfig::ScalingModeEnum::FIT) ? 1u : 0u;
        monitor_device_path_ = monitor::NormalizeMonitorDevicePath(
            config.monitor_device_path);

        std::cout << "[CaptureEngine] Initializing..." << std::endl;
        std::cout << "  FPS        : " << fps_ << std::endl;
        std::cout << "  Buffer     : " << buffer_seconds_ << "s" << std::endl;
        std::cout << "  Target res : ";
        if (target_width_ == 0 || target_height_ == 0)
            std::cout << "Native" << std::endl;
        else
            std::cout << target_width_ << "x" << target_height_ << std::endl;
        std::cout << "  Bitrate    : " << bitrate_kbps_ << " kbps" << std::endl;

        // ------------------------------------------------------------------
        // Select capture backend based on config.capture_mode.
        //
        //   DESKTOP                     — WGC CreateForMonitor (preferred) → DXGI fallback
        //   WINDOW (anti-cheat title)   — WGC CreateForMonitor + focus gate → DXGI fallback
        //   WINDOW (regular)            — WGC CreateForWindow → WGC monitor → DXGI fallback
        //
        // WGC is preferred for ALL modes because it hooks at the DWM/compositor
        // level and survives independent flip mode, where DXGI OutputDuplication
        // returns black/stale frames (the game renders direct to display
        // hardware, bypassing DWM entirely). This is the failure mode that hides
        // Valorant from Snipping Tool and from DXGI-based capture.
        //
        // For known kernel-anti-cheat games picked in WINDOW mode, we escalate
        // to monitor capture (the path Xbox Game Bar uses, which the AC allows)
        // and turn on the focus gate so we only encode while that game is
        // actually foregrounded.
        //
        // DXGI OutputDuplication is kept only as a last-resort fallback for
        // systems where WGC is unavailable (Windows 10 builds before 1903).
        // ------------------------------------------------------------------
        target_hwnd_ = config.target_hwnd;
        focus_gated_ = false;

        if (config.capture_mode == CaptureConfig::CaptureModeEnum::WINDOW
            && target_hwnd_ != 0
            && IsAntiCheatProtected(reinterpret_cast<HWND>(target_hwnd_)))
        {
            std::cout << "[CaptureEngine] Anti-cheat protected game detected — "
                      << "using WGC monitor capture (focus-gated)" << std::endl;
            wgc_active_ = true;
            focus_gated_ = true;
            if (!InitializeWGC()) {
                std::cerr << "[CaptureEngine] WGC unavailable — "
                          << "falling back to DXGI desktop capture" << std::endl;
                wgc_active_ = false;
                focus_gated_ = false;
                if (!InitializeD3D11()) {
                    std::cerr << "[CaptureEngine] D3D11 initialization failed" << std::endl;
                    return false;
                }
            }
        }
        else if (config.capture_mode == CaptureConfig::CaptureModeEnum::WINDOW
            && target_hwnd_ != 0)
        {
            std::cout << "[CaptureEngine] Mode: Window Capture  HWND=0x"
                      << std::hex << target_hwnd_ << std::dec << std::endl;
            wgc_active_ = true;
            if (!InitializeWindowCapture()) {
                std::cerr << "[CaptureEngine] Window capture init failed — "
                          << "falling back to WGC monitor capture" << std::endl;
                target_hwnd_ = 0;
                if (!InitializeWGC()) {
                    std::cerr << "[CaptureEngine] WGC unavailable — falling back to DXGI" << std::endl;
                    wgc_active_ = false;
                    if (!InitializeD3D11()) {
                        std::cerr << "[CaptureEngine] D3D11 initialization failed" << std::endl;
                        return false;
                    }
                }
            }
        }
        else {
            std::cout << "[CaptureEngine] Mode: Desktop Capture (WGC monitor)" << std::endl;
            wgc_active_ = true;
            if (!InitializeWGC()) {
                std::cerr << "[CaptureEngine] WGC unavailable — falling back to DXGI" << std::endl;
                wgc_active_ = false;
                if (!InitializeD3D11()) {
                    std::cerr << "[CaptureEngine] D3D11 initialization failed" << std::endl;
                    return false;
                }
            }
        }

        // ------------------------------------------------------------------
        // Attempt NVENC initialization
        // ------------------------------------------------------------------
        std::cout << "[CaptureEngine] Attempting NVENC initialization..." << std::endl;

        EncoderConfig hw_cfg;
        hw_cfg.src_width = width_;
        hw_cfg.src_height = height_;
        hw_cfg.enc_width = target_width_;
        hw_cfg.enc_height = target_height_;
        hw_cfg.fps = fps_;
        hw_cfg.bitrate_kbps = bitrate_kbps_;

        // Stage 2 keeps the production selection intentionally fixed to the
        // current native NVIDIA H.264 implementation. HEVC/AV1 are modeled for
        // ring/mux work but the factory refuses to construct them.
        replay_encoder_ = CreateProductionReplayEncoder(VideoCodec::H264);

        // Select NVENC path based on D3D11 adapter situation:
        //   nvidia_device_ = true  → DXGI and NVENC share the same NVIDIA device (GPU zero-copy)
        //   nvenc_device_  != null → Optimus: DXGI on Intel, NVENC on separate NVIDIA device
        //                            Uses CPU-side NVENC input buffers (still hardware H.264)
        //   neither              → No NVIDIA GPU or NVENC unavailable; fall back to x264
        auto nvenc_callback = [this](const uint8_t* data, uint32_t size, int64_t pts, bool is_keyframe, int64_t wall_qpc) {
            encoded_ring_->Push(data, size, pts, is_keyframe, wall_qpc);
        };

        if (nvidia_device_) {
            // GPU zero-copy path: DXGI and NVENC on the same NVIDIA adapter.
            nvenc_active_ = replay_encoder_ && replay_encoder_->Initialize(
                hw_cfg, device_, context_, nvenc_callback,
                /*cpu_input_mode=*/false);
        }
        else if (nvenc_device_ != nullptr) {
            // Optimus path: DXGI on Intel, encode on NVIDIA via CPU memcpy.
            std::cout << "[CaptureEngine] Optimus detected - using NVENC with CPU-input path." << std::endl;
            nvenc_active_ = replay_encoder_ && replay_encoder_->Initialize(
                hw_cfg, nvenc_device_, nvenc_context_, nvenc_callback,
                /*cpu_input_mode=*/true);
            if (!nvenc_active_) {
                std::cout << "[CaptureEngine] NVENC init failed on Optimus - falling back to x264." << std::endl;
            }
        }
        else {
            std::cout << "[CaptureEngine] No NVIDIA device available - using x264 fallback." << std::endl;
            nvenc_active_ = false;
        }

        if (nvenc_active_) {
            // Capacity: 2x time-based frame count gives comfortable headroom.
            // At 1080p/60fps/30s: 3600 slots × ~33KB avg = ~120MB.
            // (vs ~14GB raw BGRA at same settings)
            const size_t capacity = static_cast<size_t>(buffer_seconds_)
                * static_cast<size_t>(fps_) * 2;

            LARGE_INTEGER qpc_freq;
            QueryPerformanceFrequency(&qpc_freq);
            encoded_ring_ = std::make_unique<EncodedRingBuffer>(capacity, fps_, qpc_freq.QuadPart);

            // Publish codec, geometry, timing, packet format and decoder
            // configuration as one immutable stream description.
            const auto video_config = replay_encoder_->GetVideoConfig();
            encoded_ring_->SetVideoConfig(video_config);
            if (video_config.codec_extradata.empty()) {
                std::cerr << "[CaptureEngine] WARNING: encoded video config "
                             "has no codec extradata - MP4 files may not play "
                             "in all players" << std::endl;
            }

            // max_frames_ used for stats - set to time-based count.
            // ring_head_ / ring_count_ not used on NVENC path.
            max_frames_ = static_cast<size_t>(buffer_seconds_) * fps_;

            std::cout << "[CaptureEngine] NVENC active. Encoded ring: "
                << capacity << " slots. FramePool: skipped." << std::endl;
        }
        else {
            // ------------------------------------------------------------------
            // x264 fallback: allocate raw BGRA FramePool
            // ------------------------------------------------------------------
            std::cout << "[CaptureEngine] NVENC unavailable - using x264 fallback." << std::endl;

            const size_t bytes_per_frame = static_cast<size_t>(width_)
                * static_cast<size_t>(height_) * 4;
            const size_t time_frames = static_cast<size_t>(buffer_seconds_) * fps_;
            const size_t budget_bytes = static_cast<size_t>(config.max_buffer_mb) * 1024ULL * 1024ULL;
            const size_t budget_frames = (bytes_per_frame > 0)
                ? (budget_bytes / bytes_per_frame)
                : time_frames;

            max_frames_ = std::min(time_frames, budget_frames);
            if (max_frames_ == 0) {
                std::cerr << "[CaptureEngine] Memory budget too small - clamping to 1 frame" << std::endl;
                max_frames_ = 1;
            }

            const float effective_s = static_cast<float>(max_frames_) / static_cast<float>(fps_);
            if (max_frames_ < time_frames) {
                std::cerr << "[CaptureEngine] WARNING: memory cap limits buffer to "
                    << effective_s << "s (requested " << buffer_seconds_ << "s)" << std::endl;
            }
            else {
                std::cout << "[CaptureEngine] FramePool: " << effective_s << "s ("
                    << max_frames_ << " frames, "
                    << (max_frames_ * bytes_per_frame / 1024 / 1024) << " MB)" << std::endl;
            }

            frame_pool_.Allocate(max_frames_, bytes_per_frame);
            ring_head_.store(0, std::memory_order_relaxed);
            ring_count_.store(0, std::memory_order_relaxed);
        }

        // ------------------------------------------------------------------
        // Initialize audio capture pipeline BEFORE starting video thread.
        //
        // PCM-first design (Shadowplay approach):
        //   AudioCapture (WASAPI) pushes raw float32 PCM into AudioRingBuffer.
        //   No encoding during gameplay = zero CPU overhead on hot path.
        //   MuxEncodedClip() runs AAC encoding once on SaveClipThread at save time.
        //
        // Audio is optional - failure falls through to video-only mode.
        // Skipped entirely when config.audio_enabled = false (user disabled in Settings).
        // ------------------------------------------------------------------
        if (!config.audio_enabled) {
            std::cout << "[CaptureEngine] Audio capture disabled by user settings." << std::endl;
        }
        else do {
            // 32s + 4s headroom of PCM at 48kHz stereo = ~13.8 MB
            const uint32_t audio_capacity =
                static_cast<uint32_t>((buffer_seconds_ + 4) * 48000);

            audio_ring_ = std::make_unique<AudioRingBuffer>(
                audio_capacity, 48000, 2, 0);

            AudioCaptureConfig audio_cfg;
            audio_cfg.bitrate_kbps = 128;

            if (!audio_capture_.Initialize(audio_ring_.get(), audio_cfg)) {
                std::cerr << "[CaptureEngine] AudioCapture init failed - "
                    << "audio disabled" << std::endl;
                audio_ring_.reset();
                break;
            }

            if (!audio_capture_.Start()) {
                std::cerr << "[CaptureEngine] AudioCapture start failed - "
                    << "audio disabled" << std::endl;
                audio_capture_.Shutdown();
                audio_ring_.reset();
                break;
            }

            audio_active_ = true;
            std::cout << "[CaptureEngine] Audio capture active ("
                << audio_capture_.GetSampleRate() << "Hz, "
                << audio_capture_.GetChannels() << "ch, "
                << "PCM ring buffer, AAC encoding deferred to save time)"
                << std::endl;

        } while (false);

        if (!audio_active_) {
            std::cout << "[CaptureEngine] Running in video-only mode." << std::endl;
        }

        // Start video capture and save-clip threads.
        // Audio is already running so both clocks start together.
        running_.store(true);
        capture_generation_.fetch_add(1);
        capture_health_flags_.store(CAPTURE_HEALTH_ACTIVE);

        // Select capture thread function: WGC event-driven or DXGI polling loop.
        if (wgc_active_) {
            capture_thread_ = new std::thread(&CaptureEngine::CaptureThreadWGC, this);
        } else {
            capture_thread_ = new std::thread(&CaptureEngine::CaptureThread, this);
        }
        // NORMAL priority keeps us off-contention with game render threads.
        SetThreadPriority(capture_thread_->native_handle(), THREAD_PRIORITY_NORMAL);

        save_clip_thread_ = new std::thread(&CaptureEngine::SaveClipThread, this);

        std::cout << "[CaptureEngine] Running." << std::endl;
        return true;
    }


    // ===========================================================================
    // Shutdown
    // ===========================================================================

    void CaptureEngine::Shutdown() {
        if (!running_.load()) return;

        std::cout << "[CaptureEngine] Shutting down..." << std::endl;

        if (is_recording_.load())
            StopRecording();

        running_.store(false);

        // If CaptureThread is blocked waiting for a WGC frame, wake it so it exits.
        wgc_frame_cv_.notify_all();

        save_clip_queue_.Shutdown();

        if (capture_thread_) {
            capture_thread_->join();
            delete capture_thread_;
            capture_thread_ = nullptr;
        }

        if (encode_thread_) {
            encode_thread_->join();
            delete encode_thread_;
            encode_thread_ = nullptr;
        }

        if (save_clip_thread_) {
            save_clip_thread_->join();
            delete save_clip_thread_;
            save_clip_thread_ = nullptr;
        }

        // Finalize NVENC encoder after CaptureThread has exited
        // (guarantees no EncodeFrame call is in flight)
        if (nvenc_active_) {
            replay_encoder_->Shutdown();
        }

        // Stop audio pipeline. Order matters:
        //   1. Stop WASAPI thread (no more EncodeSamples calls after this)
        //   2. Finalize encoder (flushes partial AAC frame)
        //   3. audio_ring_ destroyed with the unique_ptr
        if (audio_active_) {
            audio_capture_.Stop();
            audio_capture_.Shutdown();
            audio_ring_.reset();
            audio_active_ = false;
            std::cout << "[CaptureEngine] Audio pipeline stopped." << std::endl;
        }

        ring_head_.store(0, std::memory_order_relaxed);
        ring_count_.store(0, std::memory_order_relaxed);

        // WGC session must be closed before releasing the D3D11 device it references.
        if (wgc_active_) {
            ShutdownWGC();
        }

        ShutdownD3D11();

        // Release the Optimus NVENC device after D3D11 and NVENC are both shut down.
        if (nvenc_context_) { nvenc_context_->Release(); nvenc_context_ = nullptr; }
        if (nvenc_device_)  { nvenc_device_->Release();  nvenc_device_ = nullptr; }

        std::cout << "[CaptureEngine] Shutdown complete. Frames captured: "
            << frames_captured_.load() << std::endl;
    }


    // ===========================================================================
    // StartRecording / StopRecording (x264 continuous recording - unchanged)
    // ===========================================================================

    bool CaptureEngine::StartRecording(const wchar_t* path) {
        if (is_recording_.load()) return false;
        {
            std::lock_guard<std::mutex> lock(record_mutex_);
            record_path_ = path;
        }
        is_recording_.store(true);
        encode_thread_ = new std::thread(&CaptureEngine::EncodeThread, this);
        std::cout << "[CaptureEngine] Continuous recording started." << std::endl;
        return true;
    }

    void CaptureEngine::StopRecording() {
        if (!is_recording_.load()) return;
        is_recording_.store(false);
        if (encode_thread_) {
            encode_thread_->join();
            delete encode_thread_;
            encode_thread_ = nullptr;
        }
        std::cout << "[CaptureEngine] Continuous recording stopped." << std::endl;
    }

    bool     CaptureEngine::IsRecording()   const { return is_recording_.load(std::memory_order_relaxed); }
    uint64_t CaptureEngine::GetFrameCount() const { return frames_captured_.load(std::memory_order_relaxed); }


    // ===========================================================================
    // EncodeThread (x264 continuous recording - unchanged)
    // ===========================================================================

    void CaptureEngine::EncodeThread() {
        std::wstring path;
        {
            std::lock_guard<std::mutex> lock(record_mutex_);
            path = record_path_;
        }

        EncoderConfig enc_cfg;
        enc_cfg.src_width = width_;
        enc_cfg.src_height = height_;
        enc_cfg.enc_width = target_width_;
        enc_cfg.enc_height = target_height_;
        enc_cfg.fps = fps_;
        enc_cfg.bitrate_kbps = bitrate_kbps_;
        enc_cfg.preset = "superfast";
        enc_cfg.tune = nullptr;
        enc_cfg.scaling_mode = scaling_mode_;

        VideoEncoder encoder;
        if (!encoder.Initialize(path.c_str(), enc_cfg)) {
            std::cerr << "[EncodeThread] Encoder initialization failed" << std::endl;
            is_recording_.store(false);
            return;
        }

        size_t read_pos = ring_head_.load(std::memory_order_acquire);

        while (is_recording_.load(std::memory_order_relaxed)) {
            size_t current_head = ring_head_.load(std::memory_order_acquire);
            size_t current_count = ring_count_.load(std::memory_order_acquire);
            (void)current_count;

            if (current_head > read_pos + max_frames_) {
                size_t skipped = (current_head - max_frames_) - read_pos;
                std::cerr << "[EncodeThread] Fell behind - skipping " << skipped << " frames" << std::endl;
                read_pos = current_head - max_frames_;
            }

            if (read_pos < current_head) {
                size_t slot_idx = read_pos % max_frames_;
                encoder.EncodeFrame(frame_pool_.GetSlot(slot_idx));
                read_pos++;
            }
            else {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
        }

        if (!encoder.Finalize()) {
            std::cerr << "[EncodeThread] Encoder finalization failed" << std::endl;
        }
        std::cout << "[EncodeThread] Done." << std::endl;
    }


    // ===========================================================================
    // SaveClip
    //
    // NVENC path:  TakeSnapshot from encoded ring -> queue encoded task
    // x264 path:   Snapshot raw ring indices -> queue raw task (unchanged)
    // ===========================================================================

    bool CaptureEngine::SaveClip(const wchar_t* path, uint32_t duration_seconds,
        SharedMemoryLayout* shared_memory) {

        const uint32_t health = capture_health_flags_.load();
        if (!running_.load() ||
            (health & (CAPTURE_HEALTH_BACKEND_FAILED |
                       CAPTURE_HEALTH_RECOVERING |
                       CAPTURE_HEALTH_PAUSED))) {
            SetEngineError(shared_memory,
                L"Capture is not receiving new frames. Restart capture before saving.");
            return false;
        }
        std::cout << "[CaptureEngine] SaveClip: " << duration_seconds << "s" << std::endl;

        LARGE_INTEGER save_qpc{};
        LARGE_INTEGER save_qpc_frequency{};
        QueryPerformanceCounter(&save_qpc);
        QueryPerformanceFrequency(&save_qpc_frequency);
        const double save_qpc_s = save_qpc_frequency.QuadPart > 0
            ? static_cast<double>(save_qpc.QuadPart)
                / static_cast<double>(save_qpc_frequency.QuadPart)
            : 0.0;

        // ------------------------------------------------------------------
        // NVENC path - mux only, no encoding
        // ------------------------------------------------------------------
        if (nvenc_active_) {
            const auto publish_timeout = std::chrono::milliseconds(
                std::max<uint32_t>(100, 3000 / std::max<uint32_t>(fps_, 1)));
            if (!encoded_ring_->WaitUntilPublished(
                    save_qpc.QuadPart, publish_timeout)) {
                std::cerr << "[SaveClip] Encoder publication did not reach the "
                             "save boundary within "
                          << publish_timeout.count() << "ms; using the latest "
                             "published packet" << std::endl;
            }
            EncodedRingSnapshot snapshot = encoded_ring_->TakeSnapshotByTime(
                duration_seconds, save_qpc.QuadPart);

            if (snapshot.packets.empty()) {
                std::cerr << "[SaveClip] Encoded ring buffer empty - nothing to save" << std::endl;
                return false;
            }

            SaveClipTask task;
            task.output_path = path;
            task.duration_seconds = duration_seconds;
            task.use_encoded_path = true;
            task.encoded_snapshot = std::move(snapshot);
            task.enc_width = (target_width_ > 0) ? target_width_ : width_;
            task.enc_height = (target_height_ > 0) ? target_height_ : height_;
            task.fps = fps_;
            task.shared_memory = shared_memory;
            task.task_id = next_task_id_.fetch_add(1);

            // Populate the QPC epoch so MuxEncodedClip can convert video PTS
            // to wall-clock seconds and align audio to it exactly.
            replay_encoder_->GetEncodeEpoch(
                task.video_qpc_epoch, task.video_qpc_freq);

            // Snapshot the PCM ring buffer.
            // Raw PCM is copied here; AAC encoding happens on SaveClipThread.
            // Request 2s of audio headroom for timestamp alignment between
            // the video packet and WASAPI sample clocks.
            // capture clocks. MuxEncodedClip intersects the result with the
            // requested video presentation window using QPC alignment.
            if (audio_active_ && audio_ring_ && !audio_capture_.IsDeviceLost()) {
                task.audio_snapshot = audio_ring_->TakeSnapshot(
                    static_cast<double>(duration_seconds) + 2.0,
                    task.encoded_snapshot.presentation_end_qpc_s);
                task.has_audio = task.audio_snapshot.valid
                    && !task.audio_snapshot.samples.empty();
                task.audio_bitrate_kbps = 128;

                if (task.has_audio) {
                    std::cout << "[SaveClip] Audio PCM snapshot: "
                        << task.audio_snapshot.samples.size() / 2
                        << " frames, QPC window "
                        << task.audio_snapshot.qpc_start_s << "s - "
                        << task.audio_snapshot.qpc_end_s   << "s" << std::endl;
                }
            }
            else if (audio_active_ && audio_capture_.IsDeviceLost()) {
                std::cerr << "[SaveClip] Audio device lost - saving clip without audio" << std::endl;
            }

            save_clip_queue_.Push(std::move(task));
            std::wcout << L"[SaveClip] Encoded task queued: " << path << std::endl;
            return true;
        }

        // ------------------------------------------------------------------
        // x264 fallback path - snapshot raw ring indices (unchanged)
        // ------------------------------------------------------------------
        const size_t needed = static_cast<size_t>(duration_seconds) * fps_;

        size_t snap_head = 0;
        size_t snap_count = 0;
        {
            std::lock_guard<std::mutex> lock(ring_mutex_);
            snap_head = ring_head_.load(std::memory_order_relaxed);
            snap_count = ring_count_.load(std::memory_order_relaxed);
        }

        if (snap_count == 0) {
            std::cerr << "[SaveClip] Ring buffer is empty" << std::endl;
            return false;
        }

        const size_t safety_frames = fps_;
        const auto raw_selection = replay_interval::SelectFixedRateFrames(
            snap_head, snap_count, needed, safety_frames);
        const size_t frames_to_encode = raw_selection.frame_count;
        const size_t start_pos = static_cast<size_t>(raw_selection.decode_start);

        if (frames_to_encode < needed) {
            std::cerr << "[SaveClip] Only " << frames_to_encode << " frames available "
                << "(requested " << needed << ")" << std::endl;
        }

        SaveClipTask task;
        task.output_path = path;
        task.duration_seconds = duration_seconds;
        task.use_encoded_path = false;
        task.start_frame_idx = start_pos % max_frames_;
        task.frame_count = frames_to_encode;
        task.src_width = width_;
        task.src_height = height_;
        task.enc_width = target_width_;
        task.enc_height = target_height_;
        task.fps = fps_;
        task.bitrate_kbps = bitrate_kbps_;
        task.scaling_mode = scaling_mode_;
        task.shared_memory = shared_memory;
        task.task_id = next_task_id_.fetch_add(1);

        // Audio snapshot - same logic as NVENC path (2s extra for alignment headroom)
        if (audio_active_ && audio_ring_ && !audio_capture_.IsDeviceLost()) {
            // The raw ring keeps its existing one-second overwrite guard.
            // Align audio to that same safe end instead of the live audio head.
            task.audio_snapshot = audio_ring_->TakeSnapshot(
                static_cast<double>(duration_seconds) + 2.0,
                std::max(0.0, save_qpc_s - 1.0));
            task.has_audio = task.audio_snapshot.valid
                && !task.audio_snapshot.samples.empty();
            task.audio_bitrate_kbps = 128;
        }
        else if (audio_active_ && audio_capture_.IsDeviceLost()) {
            std::cerr << "[SaveClip] Audio device lost - saving clip without audio" << std::endl;
        }

        save_clip_queue_.Push(std::move(task));
        std::wcout << L"[SaveClip] Raw task queued: " << path << std::endl;
        return true;
    }


    // ===========================================================================
    // SaveClipThread - unchanged structure, ProcessSaveClipTask branches internally
    // ===========================================================================

    void CaptureEngine::SaveClipThread() {
        std::cout << "[SaveClipThread] Started." << std::endl;

        while (true) {
            SaveClipTask task;
            if (!save_clip_queue_.Pop(task)) break;

            std::wcout << L"[SaveClipThread] Processing task " << task.task_id
                << L": " << task.output_path << std::endl;

            const bool ok = ProcessSaveClipTask(task);

            // AUDIT-021: this used to publish CLIP_SAVED unconditionally. Every
            // failure path inside ProcessSaveClipTask had already written
            // ERROR_OCCURRED — and this line overwrote it a moment later, so
            // the UI was told every failed save had succeeded.
            //
            // Failures publish themselves via SetEngineError(), payload first.
            // Success is published here, and only here.
            if (ok && task.shared_memory) {
                // Clear the message channel before announcing success so a clip
                // that follows a failed one cannot carry the old error text.
                SetEngineString(task.shared_memory, L"");
                task.shared_memory->engine_response = ResponseType::CLIP_SAVED;
            }
        }

        std::cout << "[SaveClipThread] Exiting." << std::endl;
    }


    // ===========================================================================
    // ProcessSaveClipTask - transactional wrapper around both media writers
    // ===========================================================================

    bool CaptureEngine::ProcessSaveClipTask(const SaveClipTask& task) {
        const auto result = transactional_save::Run(
            task.output_path,
            [this, &task](const std::filesystem::path& temporary_path,
                          std::string&) {
                const std::wstring output_path = temporary_path.wstring();
                return task.use_encoded_path
                    ? MuxEncodedClip(task, output_path)
                    : EncodeRawClip(task, output_path);
            });

        if (result.cleanup_error) {
            std::wcerr << L"[SaveClip] Secondary cleanup failure for "
                << result.temporary_path.wstring() << L": "
                << result.cleanup_error.value() << std::endl;
        }

        if (result.success) return true;

        // Normal writer failures already published their precise FFmpeg/encoder
        // diagnostic. Preflight/rename failures happen outside the writer and
        // therefore need a message here. A thrown writer exception also carries
        // text in writer_error and is published here.
        if (result.failure != transactional_save::Failure::Writer
                || !result.writer_error.empty()) {
            const std::string detail = transactional_save::DescribeFailure(result);
            const int required = MultiByteToWideChar(
                CP_UTF8, 0, detail.c_str(), -1, nullptr, 0);
            std::wstring wide_detail;
            if (required > 0) {
                wide_detail.resize(static_cast<size_t>(required));
                MultiByteToWideChar(
                    CP_UTF8, 0, detail.c_str(), -1,
                    wide_detail.data(), required);
            }
            SetEngineError(
                task.shared_memory,
                wide_detail.empty()
                    ? L"The temporary clip could not be finalized."
                    : wide_detail.c_str());
        }
        return false;
    }


    // ===========================================================================
    // MuxEncodedClip (NVENC path)
    //
    // No encoding. Packets and their codec-neutral stream configuration are
    // copied from the replay ring and wrapped directly in MP4.
    //
    // Steps:
    //   1. Open FFmpeg format context + video stream
    //   2. Set codec ID and decoder configuration from EncodedVideoConfig
    //   3. Open file + write header
    //   4. Write each packet (rescale PTS to stream timebase)
    //   5. Write trailer + close
    // ===========================================================================

    bool CaptureEngine::MuxEncodedClip(
        const SaveClipTask& task, const std::wstring& output_path) {
        const auto& snap = task.encoded_snapshot;
        const auto& video_config = snap.video_config;

        if (!IsValidEncodedVideoConfig(video_config)
            || ToAvCodecId(video_config.codec) == AV_CODEC_ID_NONE) {
            std::cerr << "[MuxEncodedClip] Invalid encoded video config" << std::endl;
            SetEngineError(task.shared_memory,
                L"The replay stream configuration is invalid; the clip was not written.");
            return false;
        }
        const double video_tick_seconds =
            static_cast<double>(video_config.time_base.numerator)
            / static_cast<double>(video_config.time_base.denominator);
        const double video_fps =
            static_cast<double>(video_config.frame_rate.numerator)
            / static_cast<double>(video_config.frame_rate.denominator);
        const double video_frame_seconds = 1.0 / video_fps;

        if (snap.packets.empty()) {
            std::cerr << "[MuxEncodedClip] No packets in snapshot" << std::endl;
            SetEngineError(task.shared_memory,
                L"Nothing to save: the replay buffer held no video frames. "
                L"Let the engine capture for a few seconds before saving.");
            return false;
        }

        // ------------------------------------------------------------------
        // Step A: Find the first keyframe in the snapshot.
        //
        // Clips MUST physically start on a keyframe (IDR) for decoders to
        // produce correct output. Timestamp snapshots normally guarantee this;
        // the scan remains as defensive validation for legacy snapshots.
        // ------------------------------------------------------------------
        size_t keyframe_start = snap.packets.size();  // sentinel = not found
        for (size_t i = 0; i < snap.packets.size(); i++) {
            if (!snap.packets[i].data.empty() && snap.packets[i].is_keyframe) {
                keyframe_start = i;
                break;
            }
        }

        if (keyframe_start == snap.packets.size()) {
            std::cerr << "[MuxEncodedClip] WARNING: no keyframe found - "
                << "writing all packets (clip may not decode correctly)" << std::endl;
            keyframe_start = 0;
        }

        // ------------------------------------------------------------------
        // Step B: Trim to requested duration using PTS span (not frame count).
        //
        // The ring buffer stores N encoded frames regardless of the wall-clock
        // time they span. With QPC-based timestamps, the actual capture frame
        // rate matters:
        //
        //   60fps capture: 3600 ring slots = 60 seconds  (expected)
        //   26fps capture: 3600 ring slots = 138 seconds (bug: too long)
        //
        // We limit the clip to the last (duration_seconds * fps) PTS ticks
        // of footage. At 60fps with delta=1 per frame this equals exactly
        // duration_seconds. At lower frame rates the PTS delta is larger,
        // so we discard older frames to keep within the time budget.
        //
        // The new keyframe_start is the EARLIEST keyframe whose PTS puts
        // the remaining clip within the requested duration.
        // ------------------------------------------------------------------
        // Timestamp snapshots already carry the last keyframe at/before the
        // requested start. The legacy trim is retained only for an old-style
        // snapshot without a presentation boundary; normal saves must never
        // advance to the keyframe after the requested start.
        if (!snap.packets.empty() && snap.presentation_start_qpc_s <= 0.0) {
            const int64_t max_pts_span = DurationInVideoTicks(
                video_config, task.duration_seconds);
            const int64_t newest_pts = snap.packets.back().pts;
            const int64_t cutoff_pts = newest_pts - max_pts_span;

            if (snap.packets[keyframe_start].pts < cutoff_pts) {
                // Current start is outside the time budget. Advance to the
                // earliest keyframe at or after cutoff_pts.
                size_t trimmed = snap.packets.size();  // sentinel
                for (size_t i = keyframe_start + 1; i < snap.packets.size(); i++) {
                    if (snap.packets[i].pts >= cutoff_pts && snap.packets[i].is_keyframe) {
                        trimmed = i;
                        break;
                    }
                }
                if (trimmed < snap.packets.size()) {
                    const size_t frames_discarded = trimmed - keyframe_start;
                    keyframe_start = trimmed;
                    std::cout << "[MuxEncodedClip] PTS trim: discarded "
                        << frames_discarded << " leading frames, "
                        << "keeping last " << task.duration_seconds
                        << "s of footage" << std::endl;
                }
            }
        }

        // ------------------------------------------------------------------
        // Step B2: Trim video start to audio ring coverage.
        //
        // The video ring stores 2x the frame count (generous capacity), so at
        // low actual capture rates the video snapshot can cover more wall time
        // than the audio ring. Example:
        //
        //   buffer=32s, configured fps=60, actual fps=49.
        //   Video ring: 32*60*2=3840 slots. At 49fps, these span ~78s.
        //   Audio ring: (32+4)*48000=1,728,000 samples = 36s.
        //
        //   After 60s of recording, video has 60s of footage (PTS 0-3573).
        //   Audio ring has wrapped: only 35.5s available, starting at +24s.
        //
        // Without this correction, both streams start at "sample 0" but
        // represent different wall-clock moments → 24s A/V desync.
        //
        // Fix: advance keyframe_start to the oldest video frame covered by
        // the audio ring. Uses the same QPC clock as the audio timestamps
        // (WASAPI pu64QPCPosition = same domain as QueryPerformanceCounter).
        // ------------------------------------------------------------------
        // Timestamp-aware snapshots never shorten video to match an incomplete
        // audio ring. Missing audio remains a shorter/late audio stream instead
        // of deleting valid requested footage. This block is a legacy fallback.
        if (snap.presentation_start_qpc_s <= 0.0
            && task.audio_snapshot.valid
            && task.audio_snapshot.qpc_start_s > 0.0
            && keyframe_start < snap.packets.size()
            && (snap.qpc_start_s > 0.0 || (task.video_qpc_epoch != 0 && task.video_qpc_freq != 0))) {

            const double T_epoch_s = (task.video_qpc_freq != 0)
                ? static_cast<double>(task.video_qpc_epoch)
                  / static_cast<double>(task.video_qpc_freq)
                : 0.0;

            // Wall-clock time of the current video start (keyframe_start).
            // Prefer per-packet QPC if available, fall back to epoch + PTS.
            double video_start_wall_s = 0.0;
            if (snap.packets[keyframe_start].wall_qpc > 0 && task.video_qpc_freq > 0) {
                video_start_wall_s = static_cast<double>(snap.packets[keyframe_start].wall_qpc)
                    / static_cast<double>(task.video_qpc_freq);
            } else {
                video_start_wall_s = T_epoch_s
                    + static_cast<double>(snap.packets[keyframe_start].pts)
                    * video_tick_seconds;
            }

            if (video_start_wall_s < task.audio_snapshot.qpc_start_s) {
                // Video reaches further back than audio. Advance keyframe_start
                // to the first keyframe whose wall-clock time >= audio start.
                const double audio_start_wall_s = task.audio_snapshot.qpc_start_s;
                const int64_t trim_pts = static_cast<int64_t>(
                    (audio_start_wall_s - T_epoch_s)
                    / video_tick_seconds + 0.5);

                size_t new_kf = snap.packets.size(); // sentinel = not found
                for (size_t i = keyframe_start; i < snap.packets.size(); i++) {
                    if (snap.packets[i].pts >= trim_pts && snap.packets[i].is_keyframe) {
                        new_kf = i;
                        break;
                    }
                }
                if (new_kf < snap.packets.size()) {
                    const size_t trimmed_frames = new_kf - keyframe_start;
                    keyframe_start = new_kf;
                    std::cout << "[MuxEncodedClip] Audio-range trim: discarded "
                        << trimmed_frames << " leading video frames ("
                        << (static_cast<double>(trimmed_frames) / video_fps) << "s) "
                        << "— audio ring only covers from "
                        << (audio_start_wall_s - T_epoch_s) << "s" << std::endl;
                }
                else {
                    std::cerr << "[MuxEncodedClip] Audio-range trim: no keyframe found "
                        "after audio start (" << (audio_start_wall_s - T_epoch_s)
                        << "s into recording) — clip may have audio gap at start"
                        << std::endl;
                }
            }
        }

        const size_t usable_count = snap.packets.size() - keyframe_start;

        // ------------------------------------------------------------------
        // Step C: Compute PTS normalization offset.
        //
        // The physical decode start can precede the requested presentation
        // start. Subtract the logical boundary so pre-roll packets retain
        // negative PTS and the MP4 edit list hides them without re-encoding.
        // ------------------------------------------------------------------

        // Declare write_audio early - used in Step D (alignment) and Step 2b (stream).
        const bool write_audio = task.has_audio
            && task.audio_snapshot.valid
            && !task.audio_snapshot.samples.empty();

        const int64_t pts_offset = snap.presentation_start_pts;

        // Trimmed video clip duration in seconds.
        // Uses the PTS span of the packets that will actually be written
        // (visible start = pts_offset, newest = back().pts), divided by fps to
        // convert from PTS ticks to seconds. This is used by both the
        // duration-fallback alignment path and the diagnostic output.
        const int64_t newest_video_pts     = snap.packets.back().pts;
        const int64_t video_clip_pts_span  = newest_video_pts - pts_offset;
        const double video_clip_duration_s =
            static_cast<double>(video_clip_pts_span + 1) * video_tick_seconds;

        // ------------------------------------------------------------------
        // Step D: Align audio to video using per-packet QPC timestamps.
        //
        // Both snapshots carry wall-clock QPC boundaries (qpc_start_s / qpc_end_s)
        // from the same QueryPerformanceCounter clock. We find the wall-clock
        // time of the first video frame being written (after keyframe/duration
        // trimming), then locate the matching audio sample in the snapshot.
        //
        // SaveClip requests 2s of extra audio beyond the clip duration, then
        // this step intersects it with the exact video presentation interval.
        //
        // Fallback: if QPC data is unavailable (shouldn't happen on Win10+),
        // align from the audio end, taking video_duration of audio.
        // ------------------------------------------------------------------

        int64_t audio_aligned_start_sample = 0;
        int64_t audio_output_pts_offset = 0;

        if (write_audio) {
            const int64_t total_snap_frames = static_cast<int64_t>(
                task.audio_snapshot.samples.size())
                / static_cast<int64_t>(task.audio_snapshot.channels);

            bool qpc_aligned = false;

            // --- Primary path: per-packet QPC overlap alignment ---
            //
            // The video snapshot now carries qpc_start_s / qpc_end_s from the
            // actual wall-clock timestamps stored with each encoded packet.
            // We compute the wall-clock time of the first video packet after
            // keyframe trimming, then find the corresponding audio sample.
            //
            // Both clocks are the same QPC domain:
            //   Video: raw QPC ticks / qpc_freq → seconds
            //   Audio: WASAPI pu64QPCPosition (100ns units) / 10_000_000 → seconds

            // Presentation starts at the requested cutoff, not at the older
            // keyframe retained solely for decoder pre-roll.
            double video_start_wall_s = 0.0;
            bool have_video_wall_time = false;

            if (snap.presentation_start_qpc_s > 0.0) {
                video_start_wall_s = snap.presentation_start_qpc_s;
                have_video_wall_time = true;
            }
            // Legacy fallback: use per-packet QPC from the physical first packet.
            else if (snap.qpc_start_s > 0.0) {
                // snap.qpc_start_s is from the FULL snapshot. We need the QPC of
                // the first packet after keyframe_start trimming.
                for (size_t i = keyframe_start; i < snap.packets.size(); i++) {
                    if (!snap.packets[i].data.empty() && snap.packets[i].wall_qpc > 0) {
                        // Convert raw QPC ticks to seconds using the same freq
                        // that was used in the snapshot (stored in qpc_start_s derivation)
                        if (task.video_qpc_freq > 0) {
                            video_start_wall_s = static_cast<double>(snap.packets[i].wall_qpc)
                                / static_cast<double>(task.video_qpc_freq);
                        } else {
                            // qpc_freq not available from encoder, derive from snapshot ratio
                            // snap.qpc_start_s was computed by EncodedRingBuffer using its qpc_freq_
                            // Use the first packet's wall_qpc with the snapshot's known conversion
                            video_start_wall_s = snap.qpc_start_s;
                        }
                        have_video_wall_time = true;
                        break;
                    }
                }
            }

            // Method 2: Fall back to epoch + PTS derivation
            if (!have_video_wall_time
                && task.video_qpc_epoch != 0 && task.video_qpc_freq != 0) {
                video_start_wall_s =
                    static_cast<double>(task.video_qpc_epoch)
                        / static_cast<double>(task.video_qpc_freq)
                    + static_cast<double>(pts_offset)
                        * video_tick_seconds;
                have_video_wall_time = true;
            }

            // Compute video end wall time (for overlap calculation)
            double video_end_wall_s = 0.0;
            if (have_video_wall_time) {
                video_end_wall_s = video_start_wall_s + video_clip_duration_s;
            }

            if (have_video_wall_time && task.audio_snapshot.qpc_start_s > 0.0) {
                const double overlap_start_s = std::max(
                    video_start_wall_s, task.audio_snapshot.qpc_start_s);
                const double overlap_end_s = std::min(
                    video_end_wall_s, task.audio_snapshot.qpc_end_s);

                if (overlap_start_s < overlap_end_s) {
                    audio_aligned_start_sample = static_cast<int64_t>(
                        (overlap_start_s - task.audio_snapshot.qpc_start_s)
                        * static_cast<double>(task.audio_snapshot.sample_rate) + 0.5);
                    audio_output_pts_offset = static_cast<int64_t>(
                        (overlap_start_s - video_start_wall_s)
                        * static_cast<double>(task.audio_snapshot.sample_rate) + 0.5);
                    audio_aligned_start_sample = std::clamp<int64_t>(
                        audio_aligned_start_sample, 0, total_snap_frames);
                    qpc_aligned = true;

                    std::cout << "[MuxEncodedClip] A/V sync (QPC overlap):" << std::endl;
                    std::cout << "  Video start wall time    : " << video_start_wall_s << "s" << std::endl;
                    std::cout << "  Video end wall time      : " << video_end_wall_s << "s" << std::endl;
                    std::cout << "  Audio snap QPC start     : " << task.audio_snapshot.qpc_start_s << "s" << std::endl;
                    std::cout << "  Audio snap QPC end       : " << task.audio_snapshot.qpc_end_s << "s" << std::endl;
                    std::cout << "  Audio overlap start      : " << overlap_start_s << "s" << std::endl;
                    std::cout << "  Audio snap frames        : " << total_snap_frames << std::endl;
                    std::cout << "  Audio start sample       : " << audio_aligned_start_sample
                        << " (skip " << (static_cast<double>(audio_aligned_start_sample)
                            / task.audio_snapshot.sample_rate) << "s)" << std::endl;
                    std::cout << "  Audio timeline offset    : " << audio_output_pts_offset
                        << " samples" << std::endl;
                }
                else {
                    audio_aligned_start_sample = total_snap_frames;
                    qpc_aligned = true;
                    std::cerr << "[MuxEncodedClip] Audio has no overlap with the "
                        "requested video interval; writing video-only" << std::endl;
                }
            }

            // --- Fallback: end-aligned duration-based ---
            // Used only when QPC data is completely unavailable.
            // Takes video_duration worth of audio from the end of the snapshot.
            if (!qpc_aligned) {
                const int64_t frames_needed = static_cast<int64_t>(
                    video_clip_duration_s
                    * static_cast<double>(task.audio_snapshot.sample_rate) + 0.5);

                audio_aligned_start_sample = total_snap_frames - frames_needed;
                if (audio_aligned_start_sample < 0)
                    audio_aligned_start_sample = 0;
                if (audio_aligned_start_sample > total_snap_frames)
                    audio_aligned_start_sample = total_snap_frames;

                std::cout << "[MuxEncodedClip] A/V sync (end-aligned fallback):" << std::endl;
                std::cout << "  Video clip duration      : " << video_clip_duration_s << "s" << std::endl;
                std::cout << "  Audio snap frames        : " << total_snap_frames << std::endl;
                std::cout << "  Audio start frame        : " << audio_aligned_start_sample
                    << " (skip " << (static_cast<double>(audio_aligned_start_sample)
                        / task.audio_snapshot.sample_rate) << "s)" << std::endl;
            }
        }

        // Convert output path to UTF-8.
        // UTF-16 -> UTF-8 worst-case expansion is 3x; MAX_PATH is 260 chars but
        // long-path-aware builds can exceed that. 1024 bytes covers typical paths.
        char output_utf8[1024] = {};
        WideCharToMultiByte(CP_UTF8, 0, output_path.c_str(), -1,
            output_utf8, sizeof(output_utf8) - 1, nullptr, nullptr);

        // ------------------------------------------------------------------
        // Step 1: Allocate format context
        // ------------------------------------------------------------------
        AVFormatContext* fmt_ctx = nullptr;
        avformat_alloc_output_context2(&fmt_ctx, nullptr, "mp4", output_utf8);
        if (!fmt_ctx) {
            std::cerr << "[MuxEncodedClip] avformat_alloc_output_context2 failed" << std::endl;
            SetEngineError(task.shared_memory,
                L"Failed to create the output file container. The clip path may "
                L"be invalid or on an unsupported filesystem.");
            return false;
        }
        fmt_ctx->avoid_negative_ts = AVFMT_AVOID_NEG_TS_DISABLED;

        // ------------------------------------------------------------------
        // Step 2: Create video stream
        // ------------------------------------------------------------------
        AVStream* video_stream = avformat_new_stream(fmt_ctx, nullptr);
        if (!video_stream) {
            std::cerr << "[MuxEncodedClip] avformat_new_stream (video) failed" << std::endl;
            avformat_free_context(fmt_ctx);
            SetEngineError(task.shared_memory,
                L"Failed to create the video stream in the output file.");
            return false;
        }

        video_stream->codecpar->codec_type = AVMEDIA_TYPE_VIDEO;
        video_stream->codecpar->codec_id = ToAvCodecId(video_config.codec);
        video_stream->codecpar->width = static_cast<int>(video_config.width);
        video_stream->codecpar->height = static_cast<int>(video_config.height);
        video_stream->codecpar->format = AV_PIX_FMT_YUV420P;

        // High-resolution MP4 stream timebase; packet timestamps are rescaled
        // from the encoder-provided time base below.
        video_stream->time_base = AVRational{ 1, 90000 };
        video_stream->avg_frame_rate = AVRational{
            video_config.frame_rate.numerator,
            video_config.frame_rate.denominator};
        video_stream->r_frame_rate = video_stream->avg_frame_rate;

        // Copy the codec's decoder configuration record (avcC/hvcC/av1C).
        if (!video_config.codec_extradata.empty()) {
            video_stream->codecpar->extradata = static_cast<uint8_t*>(
                av_malloc(video_config.codec_extradata.size()
                    + AV_INPUT_BUFFER_PADDING_SIZE));
            memcpy(video_stream->codecpar->extradata,
                video_config.codec_extradata.data(),
                video_config.codec_extradata.size());
            memset(video_stream->codecpar->extradata
                    + video_config.codec_extradata.size(),
                0, AV_INPUT_BUFFER_PADDING_SIZE);
            video_stream->codecpar->extradata_size =
                static_cast<int>(video_config.codec_extradata.size());
        }
        else {
            std::cerr << "[MuxEncodedClip] WARNING: no video extradata - "
                << "file may not play everywhere" << std::endl;
        }

        // ------------------------------------------------------------------
        // Step 2b: Create audio stream + encode PCM -> AAC
        //
        // PCM-first design: raw float32 PCM is stored in the ring buffer
        // during gameplay (zero encoding overhead). We encode to AAC here
        // on SaveClipThread, once, only when the user actually saves a clip.
        //
        // The audio stream must be added before avformat_write_header().
        // We initialize AudioEncoder here, encode the aligned PCM window,
        // collect the resulting AAC packets, then set codecpar->extradata
        // from the encoder's ASC before calling avformat_write_header().
        // ------------------------------------------------------------------
        AVStream* audio_stream = nullptr;
        std::vector<std::vector<uint8_t>> aac_packets;
        std::vector<int64_t>              aac_pts_list;

        if (write_audio) {
            const uint32_t sr = task.audio_snapshot.sample_rate;
            const uint32_t ch = task.audio_snapshot.channels;

            // Encode the aligned PCM window to AAC
            AudioEncoder aac_enc;
            bool enc_ok = aac_enc.Initialize(
                sr, ch, task.audio_bitrate_kbps,
                [&](const uint8_t* data, uint32_t size, int64_t pts) {
                    aac_packets.push_back(std::vector<uint8_t>(data, data + size));
                    aac_pts_list.push_back(pts);
                });

            if (enc_ok) {
                // Feed the aligned PCM window sample-by-sample in one call.
                // audio_aligned_start_sample is the frame index in the snapshot.
                const int64_t start_frame = audio_aligned_start_sample;
                const int64_t total_frames = static_cast<int64_t>(
                    task.audio_snapshot.samples.size()) / static_cast<int64_t>(ch);
                const int64_t frames_to_encode = total_frames - start_frame;

                if (frames_to_encode > 0) {
                    const float* pcm_start =
                        task.audio_snapshot.samples.data()
                        + static_cast<size_t>(start_frame) * ch;
                    const uint32_t num_samples =
                        static_cast<uint32_t>(frames_to_encode) * ch;
                    aac_enc.EncodeSamples(pcm_start, num_samples);
                }
                aac_enc.Finalize();

                std::cout << "[MuxEncodedClip] AAC encoded on SaveClipThread: "
                    << aac_packets.size() << " packets from "
                    << frames_to_encode << " PCM frames" << std::endl;
            }
            else {
                std::cerr << "[MuxEncodedClip] AudioEncoder init failed - "
                    << "writing video-only" << std::endl;
            }

            if (!aac_packets.empty()) {
                audio_stream = avformat_new_stream(fmt_ctx, nullptr);
                if (!audio_stream) {
                    std::cerr << "[MuxEncodedClip] avformat_new_stream (audio) failed"
                        << std::endl;
                    aac_packets.clear();
                    aac_pts_list.clear();
                }
                else {
                    audio_stream->codecpar->codec_type = AVMEDIA_TYPE_AUDIO;
                    audio_stream->codecpar->codec_id = AV_CODEC_ID_AAC;
                    audio_stream->codecpar->sample_rate = static_cast<int>(sr);
                    audio_stream->codecpar->ch_layout.nb_channels = static_cast<int>(ch);
                    audio_stream->codecpar->ch_layout.order = AV_CHANNEL_ORDER_UNSPEC;
                    audio_stream->codecpar->frame_size = 1024;
                    audio_stream->codecpar->format = AV_SAMPLE_FMT_FLTP;
                    audio_stream->time_base = AVRational{ 1, static_cast<int>(sr) };

                    // Set ASC extradata from the encoder we just ran
                    auto extradata = aac_enc.GetExtradata();
                    if (!extradata.empty()) {
                        audio_stream->codecpar->extradata = static_cast<uint8_t*>(
                            av_malloc(extradata.size() + AV_INPUT_BUFFER_PADDING_SIZE));
                        memcpy(audio_stream->codecpar->extradata,
                            extradata.data(), extradata.size());
                        memset(audio_stream->codecpar->extradata + extradata.size(),
                            0, AV_INPUT_BUFFER_PADDING_SIZE);
                        audio_stream->codecpar->extradata_size =
                            static_cast<int>(extradata.size());
                    }
                }
            }
        }

        // ------------------------------------------------------------------
        // Step 3: Open file + write header
        // ------------------------------------------------------------------
        int ret = avio_open(&fmt_ctx->pb, output_utf8, AVIO_FLAG_WRITE);
        if (ret < 0) {
            std::cerr << "[MuxEncodedClip] avio_open failed: " << ret << std::endl;
            avformat_free_context(fmt_ctx);
            {
                // avio_open is the disk-full / read-only / path-missing case —
                // the single most common real failure. Carry the errno-style
                // code so a bug report can name it.
                wchar_t msg[512];
                _snwprintf_s(msg, _TRUNCATE,
                    L"Could not open the clip file for writing (error %d). "
                    L"Check free disk space and folder permissions.", ret);
                SetEngineError(task.shared_memory, msg);
            }
            return false;
        }

        ret = avformat_write_header(fmt_ctx, nullptr);
        if (ret < 0) {
            std::cerr << "[MuxEncodedClip] avformat_write_header failed: " << ret << std::endl;
            avio_closep(&fmt_ctx->pb);
            avformat_free_context(fmt_ctx);
            {
                wchar_t msg[512];
                _snwprintf_s(msg, _TRUNCATE,
                    L"Failed to write the clip file header (error %d).", ret);
                SetEngineError(task.shared_memory, msg);
            }
            return false;
        }

        // ------------------------------------------------------------------
        // Step 4: Write video packets
        // ------------------------------------------------------------------
        const AVRational encode_tb = {
            video_config.time_base.numerator,
            video_config.time_base.denominator};

        std::cout << "\n========================================" << std::endl;
        std::cout << "[MuxEncodedClip] DIAGNOSTIC INFO" << std::endl;
        std::cout << "========================================" << std::endl;
        std::cout << "Total packets in snapshot : " << snap.packets.size() << std::endl;
        std::cout << "Keyframe start index      : " << keyframe_start << std::endl;
        std::cout << "Usable packets            : " << usable_count << std::endl;
        std::cout << "Presentation PTS boundary : " << pts_offset
            << " (" << (static_cast<double>(pts_offset) * video_tick_seconds)
            << "s absolute)" << std::endl;
        std::cout << "History classification   : "
            << (snap.full_history ? "full" : "partial") << std::endl;
        std::cout << "Video codec               : "
            << VideoCodecName(video_config.codec) << std::endl;
        std::cout << "Task FPS                  : " << video_fps << std::endl;
        std::cout << "Task duration             : " << task.duration_seconds << " seconds" << std::endl;
        std::cout << "Expected frame count      : "
            << (static_cast<double>(task.duration_seconds) * video_fps)
            << std::endl;
        std::cout << "Audio stream              : "
            << (audio_stream ? "YES" : "NO") << std::endl;

        // Compute average PTS delta to verify actual capture rate.
        {
            int64_t prev_raw_pts = -1;
            int64_t total_delta = 0;
            int64_t delta_count = 0;
            for (size_t i = keyframe_start; i < snap.packets.size(); i++) {
                if (snap.packets[i].data.empty()) continue;
                if (prev_raw_pts >= 0) {
                    total_delta += snap.packets[i].pts - prev_raw_pts;
                    ++delta_count;
                }
                prev_raw_pts = snap.packets[i].pts;
            }
            if (delta_count > 0) {
                double avg_delta = static_cast<double>(total_delta) / delta_count;
                double actual_fps = (avg_delta > 0.0)
                    ? (1.0 / (avg_delta * video_tick_seconds))
                    : 0.0;
                std::cout << "Avg PTS delta             : " << avg_delta
                    << " (actual capture rate ~" << actual_fps << " fps)" << std::endl;
            }
        }
        std::cout << "\nTimebase configuration:" << std::endl;
        std::cout << "  Encode timebase : " << encode_tb.num << "/" << encode_tb.den
            << " (each tick = " << (1.0 / encode_tb.den) << " sec)" << std::endl;
        std::cout << "  Stream timebase : " << video_stream->time_base.num
            << "/" << video_stream->time_base.den
            << " (each tick = " << (1.0 / video_stream->time_base.den) << " sec)" << std::endl;
        std::cout << "  avg_frame_rate  : " << video_stream->avg_frame_rate.num << "/"
            << video_stream->avg_frame_rate.den << " = "
            << (static_cast<float>(video_stream->avg_frame_rate.num)
                / video_stream->avg_frame_rate.den)
            << " fps" << std::endl;
        std::cout << "========================================\n" << std::endl;

        int     video_packet_count = 0;
        int64_t last_video_pts = -1;

        // Allocate one AVPacket and reuse it across the entire video loop.
        // av_interleaved_write_frame takes ownership of the packet's buffer
        // each iteration, so av_new_packet on the next pass allocates a
        // fresh buffer. The savings here are the AVPacket struct alloc/free
        // (~600/save at 60fps over 10s) which keeps the muxer hot path
        // closer to one heap allocation per frame instead of three.
        AVPacket* av_pkt = av_packet_alloc();
        if (!av_pkt) {
            std::cerr << "[MuxEncodedClip] av_packet_alloc failed" << std::endl;
            avio_closep(&fmt_ctx->pb);
            avformat_free_context(fmt_ctx);
            SetEngineError(task.shared_memory,
                L"Out of memory while preparing the clip for writing.");
            return false;
        }

        auto fail_media_write = [&](const wchar_t* operation, int error_code) {
            av_packet_free(&av_pkt);
            const int close_error = avio_closep(&fmt_ctx->pb);
            if (close_error < 0) {
                std::cerr << "[MuxEncodedClip] Secondary close failure: "
                    << close_error << std::endl;
            }
            avformat_free_context(fmt_ctx);
            wchar_t message[512];
            _snwprintf_s(
                message,
                _TRUNCATE,
                L"Failed while %ls the temporary clip (error %d). "
                L"The incomplete file was not published.",
                operation,
                error_code);
            SetEngineError(task.shared_memory, message);
            return false;
        };

        for (size_t i = keyframe_start; i < snap.packets.size(); i++) {
            const auto& pkt = snap.packets[i];
            if (pkt.data.empty()) continue;

            ret = av_new_packet(av_pkt, static_cast<int>(pkt.data.size()));
            if (ret < 0)
                return fail_media_write(L"allocating a video packet for", ret);

            memcpy(av_pkt->data, pkt.data.data(), pkt.data.size());

            // Keep decoder pre-roll negative; MP4 presents from the logical
            // replay cutoff at t=0 via an edit list.
            av_pkt->pts = pkt.pts - pts_offset;
            av_pkt->dts = pkt.pts - pts_offset;
            av_pkt->duration = 1;
            av_pkt->stream_index = video_stream->index;
            av_pkt->flags = pkt.is_keyframe ? AV_PKT_FLAG_KEY : 0;

            // Log first 3 packets before rescaling
            if (video_packet_count < 3) {
                std::cout << "[VPkt #" << video_packet_count << " BEFORE rescale] "
                    << "PTS=" << av_pkt->pts
                    << " DTS=" << av_pkt->dts
                    << " duration=" << av_pkt->duration
                    << (pkt.is_keyframe ? " [KEYFRAME]" : "")
                    << std::endl;
            }

            // Rescale from 1/fps to video stream timebase (1/90000)
            av_packet_rescale_ts(av_pkt, encode_tb, video_stream->time_base);

            if (video_packet_count < 3) {
                std::cout << "[VPkt #" << video_packet_count << " AFTER rescale]  "
                    << "PTS=" << av_pkt->pts
                    << " DTS=" << av_pkt->dts
                    << " duration=" << av_pkt->duration
                    << " (time="
                    << (static_cast<double>(av_pkt->pts) / video_stream->time_base.den)
                    << "s)" << std::endl;
            }

            last_video_pts = av_pkt->pts;
            ret = av_interleaved_write_frame(fmt_ctx, av_pkt);
            // av_interleaved_write_frame transfers buffer ownership to the
            // muxer. av_packet_unref clears any residual state on av_pkt so
            // the next iteration can call av_new_packet on a clean struct.
            av_packet_unref(av_pkt);
            if (ret < 0)
                return fail_media_write(L"writing video data to", ret);
            video_packet_count++;
        }

        if (video_packet_count == 0)
            return fail_media_write(L"writing video data to", -1);

        // ------------------------------------------------------------------
        // Step 4b: Write pre-encoded AAC packets
        //
        // aac_packets were encoded in Step 2b from the aligned PCM window.
        // PTS starts at 0 in AudioEncoder. Add the wall-clock overlap offset
        // when audio began after the requested video presentation boundary.
        // audio_stream->time_base = {1, sample_rate} so no rescaling needed.
        // Reuses the same AVPacket struct as the video loop above.
        // ------------------------------------------------------------------
        int audio_packet_count = 0;

        if (audio_stream && !aac_packets.empty()) {
            for (size_t i = 0; i < aac_packets.size(); i++) {
                const auto& pkt_data = aac_packets[i];
                if (pkt_data.empty()) continue;

                ret = av_new_packet(av_pkt, static_cast<int>(pkt_data.size()));
                if (ret < 0)
                    return fail_media_write(L"allocating an audio packet for", ret);

                memcpy(av_pkt->data, pkt_data.data(), pkt_data.size());
                av_pkt->pts = aac_pts_list[i] + audio_output_pts_offset;
                av_pkt->dts = aac_pts_list[i] + audio_output_pts_offset;
                av_pkt->duration = 1024;
                av_pkt->stream_index = audio_stream->index;
                av_pkt->flags = 0;

                ret = av_interleaved_write_frame(fmt_ctx, av_pkt);
                av_packet_unref(av_pkt);
                if (ret < 0)
                    return fail_media_write(L"writing audio data to", ret);
                audio_packet_count++;
            }

            const double audio_written_s =
                audio_packet_count * 1024.0
                / task.audio_snapshot.sample_rate;
            std::cout << "[MuxEncodedClip] Audio packets written: "
                << audio_packet_count
                << " (" << audio_written_s << "s)" << std::endl;
        }

        av_packet_free(&av_pkt);

        // Flush muxer's internal interleave buffer
        ret = av_interleaved_write_frame(fmt_ctx, nullptr);
        if (ret < 0)
            return fail_media_write(L"flushing interleaved data for", ret);

        const double actual_duration_s = (last_video_pts >= 0)
            ? (static_cast<double>(last_video_pts) / 90000.0)
                + video_frame_seconds
            : 0.0;

        std::cout << "\n[MuxEncodedClip] Summary:" << std::endl;
        std::cout << "  Video packets written   : " << video_packet_count << std::endl;
        std::cout << "  Audio packets written   : " << audio_packet_count << std::endl;
        std::cout << "  Last video PTS          : " << last_video_pts
            << " (" << (static_cast<double>(last_video_pts)
                / video_stream->time_base.den) << "s)" << std::endl;
        std::cout << "  Calculated clip duration: " << actual_duration_s << "s" << std::endl;
        std::cout << "  Requested duration      : " << task.duration_seconds << "s" << std::endl;
        if (!snap.full_history)
            std::cout << "  NOTE: partial history after startup/recovery; saved "
                "all decodable media currently available." << std::endl;
        std::cout << "========================================\n" << std::endl;

        // ------------------------------------------------------------------
        // Step 5: Write trailer + close
        // ------------------------------------------------------------------
        ret = av_write_trailer(fmt_ctx);
        if (ret < 0)
            return fail_media_write(L"finalizing the container for", ret);

        ret = avio_closep(&fmt_ctx->pb);
        if (ret < 0) {
            avformat_free_context(fmt_ctx);
            wchar_t message[512];
            _snwprintf_s(
                message,
                _TRUNCATE,
                L"Failed while closing the temporary clip (error %d). "
                L"The incomplete file was not published.",
                ret);
            SetEngineError(task.shared_memory, message);
            return false;
        }
        avformat_free_context(fmt_ctx);

        std::wcout << L"[MuxEncodedClip] Done: " << output_path
            << L" (" << video_packet_count << L" video, "
            << audio_packet_count << L" audio, "
            << actual_duration_s << L"s)" << std::endl;
        return true;
    }


    // ===========================================================================
    // EncodeRawClip (x264 fallback path - formerly ProcessSaveClipTask)
    // ===========================================================================

    bool CaptureEngine::EncodeRawClip(
        const SaveClipTask& task, const std::wstring& output_path) {
        EncoderConfig enc_cfg;
        enc_cfg.src_width = task.src_width;
        enc_cfg.src_height = task.src_height;
        enc_cfg.enc_width = task.enc_width;
        enc_cfg.enc_height = task.enc_height;
        enc_cfg.fps = task.fps;
        enc_cfg.bitrate_kbps = task.bitrate_kbps;
        enc_cfg.preset = "superfast";
        enc_cfg.tune = nullptr;
        enc_cfg.scaling_mode = task.scaling_mode;

        VideoEncoder encoder;

        // Feed audio snapshot so it gets muxed alongside the video.
        // SetAudioData must be called before Initialize() so the audio stream
        // and its AAC extradata can be registered before avformat_write_header.
        if (task.has_audio && task.audio_snapshot.valid
            && !task.audio_snapshot.samples.empty())
        {
            // Use the tail of the snapshot matching the actual clip duration.
            // x264 frames have no per-frame QPC timestamps, so we take the last
            // (frame_count / fps) seconds of audio, aligned to the clip end.
            const uint32_t sr = task.audio_snapshot.sample_rate;
            const uint32_t ch = task.audio_snapshot.channels;
            const double   clip_duration_s =
                (task.fps > 0)
                ? static_cast<double>(task.frame_count) / static_cast<double>(task.fps)
                : static_cast<double>(task.duration_seconds);
            const int64_t total_frames =
                static_cast<int64_t>(task.audio_snapshot.samples.size())
                / static_cast<int64_t>(ch);
            const int64_t frames_needed =
                static_cast<int64_t>(clip_duration_s * static_cast<double>(sr) + 0.5);
            const int64_t start_frame =
                std::max<int64_t>(0, total_frames - frames_needed);
            const size_t  skip_samples =
                static_cast<size_t>(start_frame) * static_cast<size_t>(ch);

            std::vector<float> aligned_pcm(
                task.audio_snapshot.samples.begin() + static_cast<ptrdiff_t>(skip_samples),
                task.audio_snapshot.samples.end());

            if (!aligned_pcm.empty()) {
                encoder.SetAudioData(std::move(aligned_pcm), sr, ch, task.audio_bitrate_kbps);
                std::cout << "[EncodeRawClip] Audio: "
                          << frames_needed << " frames from snapshot (clip="
                          << clip_duration_s << "s)" << std::endl;
            }
        }

        if (!encoder.Initialize(output_path.c_str(), enc_cfg)) {
            std::cerr << "[EncodeRawClip] Encoder initialization failed" << std::endl;
            SetEngineError(task.shared_memory,
                L"Failed to initialise the video encoder. The selected codec "
                L"may be unavailable on this GPU.");
            return false;
        }

        bool encode_ok = true;
        for (size_t i = 0; i < task.frame_count; i++) {
            size_t slot_idx = (task.start_frame_idx + i) % max_frames_;
            if (!encoder.EncodeFrame(frame_pool_.GetSlot(slot_idx))) {
                encode_ok = false;
                break;
            }
        }

        const bool finalize_ok = encoder.Finalize();
        if (!encode_ok) {
            SetEngineError(task.shared_memory,
                L"The software encoder failed while writing video frames. "
                L"The incomplete clip was not published.");
            return false;
        }
        if (!finalize_ok) {
            SetEngineError(task.shared_memory,
                L"The software encoder could not finalize or close the clip. "
                L"The incomplete clip was not published.");
            return false;
        }
        std::wcout << L"[EncodeRawClip] Done: " << output_path << std::endl;
        return true;
    }


    // ===========================================================================
    // GetStats
    // ===========================================================================

    CaptureEngine::Stats CaptureEngine::GetStats() const {
        Stats s{};
        s.frames_captured = frames_captured_.load(std::memory_order_relaxed);
        s.frames_dropped = frames_dropped_.load(std::memory_order_relaxed);
        s.capture_fps = static_cast<float>(fps_);

        if (nvenc_active_ && encoded_ring_) {
            s.ring_used_frames = encoded_ring_->GetCount();
            s.ring_max_frames = encoded_ring_->GetCapacity();
            s.pool_memory_mb = 0;  // no raw frame pool
            s.effective_buffer_seconds = (fps_ > 0)
                ? static_cast<float>(s.ring_used_frames) / static_cast<float>(fps_)
                : 0.0f;
        }
        else {
            s.ring_used_frames = ring_count_.load(std::memory_order_relaxed);
            s.ring_max_frames = max_frames_;
            s.pool_memory_mb = frame_pool_.TotalBytes() / (1024 * 1024);
            s.effective_buffer_seconds = (fps_ > 0)
                ? static_cast<float>(max_frames_) / static_cast<float>(fps_)
                : 0.0f;
        }

        return s;
    }

    void CaptureEngine::PublishContentMetrics(
        uint64_t sum, uint64_t sum_sq, uint32_t count) {
        if (count == 0) return;
        const float mean = static_cast<float>(sum) / count;
        const float variance = std::max(
            0.0f, static_cast<float>(sum_sq) / count - mean * mean);
        const bool suspicious_sample =
            (mean <= 8.0f && variance <= 6.0f) || variance <= 2.0f;
        const uint32_t streak = suspicious_sample
            ? content_suspicious_streak_.fetch_add(1) + 1
            : 0;
        if (!suspicious_sample) content_suspicious_streak_.store(0);
        content_luma_mean_.store(mean);
        content_luma_variance_.store(variance);
        content_sample_sequence_.fetch_add(1);
        if (streak >= 12)
            capture_health_flags_.fetch_or(CAPTURE_HEALTH_CONTENT_SUSPECT);
        else
            capture_health_flags_.fetch_and(~CAPTURE_HEALTH_CONTENT_SUSPECT);
    }

    void CaptureEngine::SampleContentBGRA(
        const uint8_t* data, uint32_t stride, uint32_t width,
        uint32_t height, uint64_t produced_frame) {
        if (!data || width == 0 || height == 0 || stride < width * 4 ||
            produced_frame % std::max<uint32_t>(1, fps_) != 0)
            return;
        constexpr uint32_t kColumns = 16;
        constexpr uint32_t kRows = 9;
        uint64_t sum = 0;
        uint64_t sum_sq = 0;
        for (uint32_t row = 0; row < kRows; ++row) {
            const uint32_t y = std::min(
                height - 1, ((2 * row + 1) * height) / (2 * kRows));
            for (uint32_t column = 0; column < kColumns; ++column) {
                const uint32_t x = std::min(
                    width - 1, ((2 * column + 1) * width) / (2 * kColumns));
                const uint8_t* pixel = data + static_cast<size_t>(y) * stride + x * 4;
                const uint32_t luma =
                    (19u * pixel[0] + 183u * pixel[1] + 54u * pixel[2]) >> 8;
                sum += luma;
                sum_sq += luma * luma;
            }
        }
        PublishContentMetrics(sum, sum_sq, kColumns * kRows);
    }

    void CaptureEngine::SampleContentTexture(
        ID3D11Texture2D* texture, uint64_t produced_frame) {
        if (!texture || produced_frame % std::max<uint32_t>(1, fps_) != 0)
            return;
        D3D11_TEXTURE2D_DESC source{};
        texture->GetDesc(&source);
        if (source.Width == 0 || source.Height == 0) return;

        constexpr UINT kColumns = 16;
        constexpr UINT kRows = 9;

        if (!health_staging_texture_) {
            D3D11_TEXTURE2D_DESC desc{};
            desc.Width = kColumns;
            desc.Height = kRows;
            desc.MipLevels = 1;
            desc.ArraySize = 1;
            desc.Format = source.Format;
            desc.SampleDesc.Count = 1;
            desc.Usage = D3D11_USAGE_STAGING;
            desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            if (FAILED(device_->CreateTexture2D(&desc, nullptr,
                                                &health_staging_texture_)))
                return;
        }

        // Copy 144 distributed pixels into one tiny staging texture, then map
        // once. A single centre strip falsely classified dark wallpapers and
        // letterboxed scenes as uniform; the full sparse grid matches the CPU
        // path without a full-frame GPU readback.
        for (UINT row = 0; row < kRows; ++row) {
            const UINT y = std::min(
                source.Height - 1, ((2 * row + 1) * source.Height) / (2 * kRows));
            for (UINT column = 0; column < kColumns; ++column) {
                const UINT x = std::min(
                    source.Width - 1,
                    ((2 * column + 1) * source.Width) / (2 * kColumns));
                D3D11_BOX box{x, y, 0, x + 1, y + 1, 1};
                context_->CopySubresourceRegion(
                    health_staging_texture_, 0, column, row, 0, texture, 0, &box);
            }
        }
        D3D11_MAPPED_SUBRESOURCE mapped{};
        if (FAILED(context_->Map(
                health_staging_texture_, 0, D3D11_MAP_READ, 0, &mapped)))
            return;
        uint64_t sum = 0;
        uint64_t sum_sq = 0;
        const uint8_t* data = static_cast<const uint8_t*>(mapped.pData);
        for (UINT row = 0; row < kRows; ++row) {
            const uint8_t* pixels = data + static_cast<size_t>(row) * mapped.RowPitch;
            for (UINT column = 0; column < kColumns; ++column) {
                const uint8_t* pixel = pixels + column * 4;
                const uint32_t luma =
                    (19u * pixel[0] + 183u * pixel[1] + 54u * pixel[2]) >> 8;
                sum += luma;
                sum_sq += luma * luma;
            }
        }
        context_->Unmap(health_staging_texture_, 0);
        PublishContentMetrics(sum, sum_sq, kColumns * kRows);
    }

    void CaptureEngine::ClearReplayForRecovery() {
        if (encoded_ring_) encoded_ring_->Clear();
        ring_head_.store(0, std::memory_order_release);
        ring_count_.store(0, std::memory_order_release);
        content_suspicious_streak_.store(0);
        capture_health_flags_.fetch_and(~CAPTURE_HEALTH_CONTENT_SUSPECT);
    }


    // ===========================================================================
    // CaptureThread
    //
    // Hot path. After acquiring and mapping a DXGI frame:
    //
    //   NVENC path: pass BGRA to replay_encoder_->EncodeFrame()
    //               Callback fires -> EncodedRingBuffer::Push()
    //               ring_head_ / ring_count_ NOT updated (encoded ring manages itself)
    //
    //   x264 path:  memcpy into frame_pool_ slot, advance ring_head_ / ring_count_
    //               (unchanged from Phase 3)
    //
    // Frame rate limiting (QPC) is identical on both paths.
    // ===========================================================================

    void CaptureEngine::CaptureThread() {
        // WGC disabled — DXGI-only path. No WinRT apartment needed.

        std::cout << "[CaptureThread] Started (DXGI path)." << std::endl;

        LARGE_INTEGER qpc_freq;
        QueryPerformanceFrequency(&qpc_freq);

        const double  target_ms = 1000.0 / static_cast<double>(fps_);
        const int64_t target_qpc = static_cast<int64_t>(
            (target_ms / 1000.0) * static_cast<double>(qpc_freq.QuadPart));

        LARGE_INTEGER last_capture_time;
        QueryPerformanceCounter(&last_capture_time);

        std::cout << "[CaptureThread] " << fps_ << " fps ("
            << target_ms << " ms/frame)  "
            << (nvenc_active_ ? "NVENC" : "software") << " path" << std::endl;

        uint32_t consecutive_acquire_errors = 0;

        while (running_.load(std::memory_order_relaxed)) {

            DXGI_OUTDUPL_FRAME_INFO info{};
            IDXGIResource* resource = nullptr;

            HRESULT hr = duplication_->AcquireNextFrame(33, &info, &resource);

            if (hr == DXGI_ERROR_WAIT_TIMEOUT) continue;

            if (hr == DXGI_ERROR_ACCESS_LOST) {
                // A new D3D11 device cannot be substituted under the live native
                // NVENC session: its registered textures belong to the old device.
                // Fail this generation and let the existing UI recovery policy
                // restart the process, which re-resolves the same persistent path
                // and initializes capture + encoder atomically.
                ClearReplayForRecovery();
                const bool same_mapping = monitor_resolver_.IsCurrent(
                    resolved_monitor_);
                std::cerr << "[CaptureThread] "
                          << monitor::ToString(same_mapping
                              ? monitor::MonitorResolveError::OutputResolutionFailed
                              : monitor::MonitorResolveError::MonitorTopologyChanged)
                          << ": DXGI access lost; a fresh capture generation is required"
                          << std::endl;
                capture_health_flags_.store(CAPTURE_HEALTH_BACKEND_FAILED);
                running_.store(false);
                break;
            }

            if (FAILED(hr)) {
                std::cerr << "[CaptureThread] AcquireNextFrame failed: 0x"
                    << std::hex << hr << std::dec << std::endl;
                if (++consecutive_acquire_errors >= 100) {
                    std::cerr << "[CaptureThread] Too many consecutive acquisition errors"
                              << std::endl;
                    capture_health_flags_.store(CAPTURE_HEALTH_BACKEND_FAILED);
                    running_.store(false);
                    break;
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }
            consecutive_acquire_errors = 0;

            // Frame rate limiting BEFORE QueryInterface.
            // At high game FPS (e.g. 300fps, 60fps target) most frames are dropped.
            // QueryInterface is a COM call with real overhead; paying it on every
            // dropped frame wastes ~240 roundtrips/sec. Checking QPC first means
            // dropped frames cost only resource->Release() + ReleaseFrame().
            LARGE_INTEGER now;
            QueryPerformanceCounter(&now);
            if (now.QuadPart - last_capture_time.QuadPart < target_qpc) {
                resource->Release();
                duplication_->ReleaseFrame();
                frames_dropped_.fetch_add(1, std::memory_order_relaxed);
                continue;
            }
            last_capture_time = now;

            ID3D11Texture2D* tex = nullptr;
            hr = resource->QueryInterface(__uuidof(ID3D11Texture2D),
                reinterpret_cast<void**>(&tex));
            resource->Release();

            if (FAILED(hr)) {
                duplication_->ReleaseFrame();
                continue;
            }

            if (nvenc_active_ && nvidia_device_) {
                // ----------------------------------------------------------
                // NVENC GPU zero-copy path.
                // DXGI and NVENC share the same NVIDIA device.
                // CopyResource is a pure GPU op — no CPU read, no stall.
                // ----------------------------------------------------------
                SampleContentTexture(
                    tex, frames_captured_.load(std::memory_order_relaxed) + 1);
                ID3D11Texture2D* input_tex =
                    replay_encoder_->GetCurrentInputTexture();
                context_->CopyResource(input_tex, tex);
                tex->Release();
                duplication_->ReleaseFrame();

                replay_encoder_->EncodeFrame(info.LastPresentTime.QuadPart);
                // Callback inside EncodeFrame fires -> EncodedRingBuffer::Push()
            }
            else if (nvenc_active_) {
                // ----------------------------------------------------------
                // NVENC CPU-input path (Optimus).
                // DXGI on Intel adapter; NVENC on separate NVIDIA device.
                // Map the staging texture on the Intel device and memcpy into
                // the NVENC system-memory input buffer on the NVIDIA device.
                // Still hardware H.264 — only the pixel copy touches the CPU.
                //
                // Blocking Map: the Intel GPU copy typically takes 1-2ms,
                // well within the 16.7ms frame budget at 60fps. DO_NOT_WAIT
                // was dropping 100% of frames because the copy never finished
                // in the microseconds between CopyResource and Map.
                // ----------------------------------------------------------
                context_->CopyResource(staging_texture_, tex);
                tex->Release();
                duplication_->ReleaseFrame();

                D3D11_MAPPED_SUBRESOURCE mapped{};
                hr = context_->Map(staging_texture_, 0, D3D11_MAP_READ, 0, &mapped);
                if (FAILED(hr)) {
                    std::cerr << "[CaptureThread] Texture map failed (Optimus): 0x"
                        << std::hex << hr << std::dec << std::endl;
                    continue;
                }

                SampleContentBGRA(
                    static_cast<const uint8_t*>(mapped.pData), mapped.RowPitch,
                    width_, height_, frames_captured_.load(std::memory_order_relaxed) + 1);

                replay_encoder_->EncodeFrameCPU(
                    static_cast<const uint8_t*>(mapped.pData),
                    mapped.RowPitch,
                    info.LastPresentTime.QuadPart);
                // Callback inside EncodeFrameCPU fires -> EncodedRingBuffer::Push()

                context_->Unmap(staging_texture_, 0);
            }
            else {
                // ----------------------------------------------------------
                // x264 fallback path: CopyResource -> staging -> Map -> memcpy
                // ----------------------------------------------------------
                context_->CopyResource(staging_texture_, tex);
                tex->Release();
                duplication_->ReleaseFrame();

                D3D11_MAPPED_SUBRESOURCE mapped{};
                hr = context_->Map(staging_texture_, 0, D3D11_MAP_READ, 0, &mapped);
                if (FAILED(hr)) {
                    std::cerr << "[CaptureThread] Texture map failed: 0x"
                        << std::hex << hr << std::dec << std::endl;
                    continue;
                }

                const uint8_t* src     = static_cast<const uint8_t*>(mapped.pData);
                const size_t   row     = static_cast<size_t>(width_) * 4;
                const bool     pitched = (mapped.RowPitch != static_cast<UINT>(row));

                const size_t write_pos = ring_head_.load(std::memory_order_relaxed);
                const size_t slot_idx  = write_pos % max_frames_;
                uint8_t*     dst       = frame_pool_.GetSlot(slot_idx);

                SampleContentBGRA(
                    src, mapped.RowPitch, width_, height_,
                    frames_captured_.load(std::memory_order_relaxed) + 1);

                if (!pitched) {
                    std::memcpy(dst, src, row * height_);
                }
                else {
                    for (uint32_t y = 0; y < height_; y++) {
                        std::memcpy(dst + y * row, src + y * mapped.RowPitch, row);
                    }
                }

                context_->Unmap(staging_texture_, 0);

                ring_head_.fetch_add(1, std::memory_order_release);
                size_t prev = ring_count_.load(std::memory_order_relaxed);
                if (prev < max_frames_)
                    ring_count_.fetch_add(1, std::memory_order_relaxed);
            }

            uint64_t fc = frames_captured_.fetch_add(1, std::memory_order_relaxed) + 1;
            if (fc == 1 || fc == 10 || fc == 100 || (fc % 500 == 0)) {
                std::cout << "[CaptureThread] Frames captured: " << fc << std::endl;
            }
        }

        std::cout << "[CaptureThread] Stopped. Total frames: "
                  << frames_captured_.load() << std::endl;
        if (capture_health_flags_.load() != CAPTURE_HEALTH_BACKEND_FAILED)
            capture_health_flags_.store(CAPTURE_HEALTH_NONE);
    }


    bool CaptureEngine::ResolveSelectedMonitor(const char* backend_name) {
        const auto result = monitor_resolver_.Resolve(monitor_device_path_);
        if (!result.ok()) {
            std::cerr << '[' << backend_name << "] "
                      << monitor::ToString(result.error) << ": "
                      << result.diagnostic << std::endl;
            return false;
        }

        if (!resolved_monitor_.monitor_device_path.empty()
            && !(resolved_monitor_ == result.monitor)) {
            std::cerr << '[' << backend_name << "] "
                      << monitor::ToString(
                             monitor::MonitorResolveError::MonitorTopologyChanged)
                      << ": selected monitor transient mapping changed; "
                         "starting a fresh capture generation" << std::endl;
        }
        resolved_monitor_ = result.monitor;
        std::cout << '[' << backend_name << "] Monitor resolved: ";
        std::wcout << resolved_monitor_.friendly_name << L"  "
                   << resolved_monitor_.source_gdi_name << L"  "
                   << resolved_monitor_.monitor_device_path << std::endl;
        std::cout << '[' << backend_name << "] Topology: LUID="
                  << resolved_monitor_.adapter_luid.high_part << ':'
                  << resolved_monitor_.adapter_luid.low_part
                  << " source=" << resolved_monitor_.source_id
                  << " target=" << resolved_monitor_.target_id
                  << " generation=" << resolved_monitor_.topology_generation
                  << std::endl;
        return true;
    }

    bool CaptureEngine::InitializeMonitorCaptureDevice(
        const char* backend_name, IDXGIOutput** selected_output) {
        if (!selected_output) return false;
        *selected_output = nullptr;
        if (!ResolveSelectedMonitor(backend_name)) return false;

        IDXGIFactory1* factory = nullptr;
        HRESULT hr = CreateDXGIFactory1(
            __uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory));
        if (FAILED(hr)) {
            std::cerr << '[' << backend_name
                      << "] CreateDXGIFactory1 failed: 0x"
                      << std::hex << hr << std::dec << std::endl;
            return false;
        }

        IDXGIAdapter1* capture_adapter = nullptr;
        std::string output_diagnostic;
        if (!monitor::OpenSelectedDxgiOutput(
                factory, resolved_monitor_, &capture_adapter, selected_output,
                output_diagnostic)) {
            std::cerr << '[' << backend_name << "] "
                      << monitor::ToString(
                             monitor::MonitorResolveError::OutputResolutionFailed)
                      << ": " << output_diagnostic << std::endl;
            factory->Release();
            return false;
        }

        DXGI_ADAPTER_DESC1 capture_desc{};
        capture_adapter->GetDesc1(&capture_desc);
        D3D_FEATURE_LEVEL feature_level{};
        hr = D3D11CreateDevice(
            capture_adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
            nullptr, 0, D3D11_SDK_VERSION,
            &device_, &feature_level, &context_);
        if (FAILED(hr)) {
            std::cerr << '[' << backend_name
                      << "] D3D11CreateDevice on selected monitor adapter failed: 0x"
                      << std::hex << hr << std::dec << std::endl;
            (*selected_output)->Release();
            *selected_output = nullptr;
            capture_adapter->Release();
            factory->Release();
            return false;
        }

        nvidia_device_ = capture_desc.VendorId == 0x10DE;
        if (!nvidia_device_ && !nvenc_device_) {
            // Preserve the existing hybrid-GPU behavior: capture on the selected
            // display-owning adapter, with a separate NVIDIA NVENC device and the
            // already-existing CPU-input path. No new readback path is introduced.
            IDXGIAdapter1* encoder_adapter = nullptr;
            for (UINT index = 0;
                 factory->EnumAdapters1(index, &encoder_adapter) != DXGI_ERROR_NOT_FOUND;
                 ++index) {
                DXGI_ADAPTER_DESC1 encoder_desc{};
                encoder_adapter->GetDesc1(&encoder_desc);
                if (encoder_desc.VendorId == 0x10DE) {
                    hr = D3D11CreateDevice(
                        encoder_adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
                        nullptr, 0, D3D11_SDK_VERSION,
                        &nvenc_device_, &feature_level, &nvenc_context_);
                    encoder_adapter->Release();
                    encoder_adapter = nullptr;
                    if (SUCCEEDED(hr)) {
                        std::cout << '[' << backend_name
                                  << "] Separate NVIDIA NVENC device ready "
                                     "(existing CPU-input path)" << std::endl;
                    }
                    break;
                }
                encoder_adapter->Release();
                encoder_adapter = nullptr;
            }
        }

        DXGI_OUTPUT_DESC output_desc{};
        (*selected_output)->GetDesc(&output_desc);
        width_ = static_cast<uint32_t>(
            output_desc.DesktopCoordinates.right
            - output_desc.DesktopCoordinates.left);
        height_ = static_cast<uint32_t>(
            output_desc.DesktopCoordinates.bottom
            - output_desc.DesktopCoordinates.top);

        capture_adapter->Release();
        factory->Release();
        std::cout << '[' << backend_name << "] Selected output ready: "
                  << width_ << 'x' << height_
                  << (nvidia_device_
                      ? " [NVIDIA same-adapter GPU path]"
                      : " [display-owning adapter]")
                  << std::endl;
        return true;
    }


    // ===========================================================================
    // InitializeWGC
    //
    // Sets up Windows Graphics Capture for the exact persistent monitor selection.
    //
    // Key advantage over DXGI OutputDuplication:
    //   - On Optimus, we create the D3D11 device on the NVIDIA adapter.
    //     WGC internally copies the composited frame (Intel) into our NVIDIA
    //     device VRAM. NVENC then reads from that same NVIDIA device → GPU
    //     zero-copy. No CPU involvement, no Intel GPU in the hot path.
    //   - Event-driven (FrameArrived) — no 300 iteration/sec polling loop.
    //
    // Sets device_, context_, nvidia_device_, width_, height_.
    // Does NOT create staging_texture_ or duplication_ (not needed on this path).
    // ===========================================================================

    bool CaptureEngine::InitializeWGC() {
        // WGC requires Windows 10 1903+ (build 18362)
        try {
            if (!winrt::Windows::Graphics::Capture::GraphicsCaptureSession::IsSupported()) {
                std::cerr << "[WGC] GraphicsCaptureSession not supported on this system" << std::endl;
                return false;
            }
        } catch (...) {
            std::cerr << "[WGC] IsSupported() threw — WGC unavailable" << std::endl;
            return false;
        }

        // Resolve the persistent monitor device path into the current topology,
        // then create D3D11 on the adapter that actually owns that monitor.
        IDXGIOutput* selected_output = nullptr;
        if (!InitializeMonitorCaptureDevice("WGC", &selected_output)) {
            return false;
        }
        selected_output->Release();
        selected_output = nullptr;
        const HMONITOR hmonitor = reinterpret_cast<HMONITOR>(
            resolved_monitor_.hmonitor);
        HRESULT hr = S_OK;

        // --- Step 3b: Staging texture for Optimus CPU-readback path ---
        // On Optimus the WGC device is Intel; NVENC is on NVIDIA.
        // CaptureThreadWGC needs to Map the frame to CPU memory for EncodeFrameCPU.
        if (!nvidia_device_ && nvenc_device_) {
            D3D11_TEXTURE2D_DESC desc{};
            desc.Width            = width_;
            desc.Height           = height_;
            desc.MipLevels        = 1;
            desc.ArraySize        = 1;
            desc.Format           = DXGI_FORMAT_B8G8R8A8_UNORM;
            desc.SampleDesc.Count = 1;
            desc.Usage            = D3D11_USAGE_STAGING;
            desc.CPUAccessFlags   = D3D11_CPU_ACCESS_READ;
            hr = device_->CreateTexture2D(&desc, nullptr, &staging_texture_);
            if (FAILED(hr)) {
                std::cerr << "[WGC] Staging texture creation failed: 0x"
                          << std::hex << hr << std::dec << std::endl;
                // Non-fatal: will fall back to x264 (staging_texture_ stays null)
            } else {
                std::cout << "[WGC] Staging texture created for Optimus CPU-readback" << std::endl;
            }
        }

        // --- Step 4-8: WinRT session setup (exception-safe) ---
        wgc_state_ = std::make_unique<WGCState>();
        wgc_state_->monitor_item = true;
        monitor_source_invalidated_.store(false, std::memory_order_release);
        try {
            // 4a. Wrap ID3D11Device as WinRT IDirect3DDevice
            winrt::com_ptr<IDXGIDevice> dxgi_dev;
            winrt::check_hresult(device_->QueryInterface(dxgi_dev.put()));

            winrt::com_ptr<IInspectable> insp;
            winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi_dev.get(), insp.put()));

            wgc_state_->winrt_device =
                insp.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();

            // 4b. Create a capture item for the exact resolved monitor.
            auto item_interop = winrt::get_activation_factory<
                winrt::Windows::Graphics::Capture::GraphicsCaptureItem,
                IGraphicsCaptureItemInterop>();

            winrt::check_hresult(item_interop->CreateForMonitor(
                hmonitor,
                winrt::guid_of<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>(),
                winrt::put_abi(wgc_state_->item)));

            wgc_state_->item_closed_token = wgc_state_->item.Closed(
                [this](auto&, auto&) {
                    monitor_source_invalidated_.store(
                        true, std::memory_order_release);
                    wgc_frame_cv_.notify_one();
                });
            wgc_state_->item_closed_registered = true;

            // 5. Frame pool: 2 slots, free-threaded.
            //
            // IMPORTANT: Use CreateFreeThreaded, NOT Create.
            //
            // Direct3D11CaptureFramePool::Create delivers FrameArrived events via
            // the calling thread's DispatcherQueue.  Initialize() runs on the C++
            // main thread which has NO message pump (no DispatchMessage loop), so
            // FrameArrived events are queued but never dispatched.  Result: the
            // condition variable in CaptureThreadWGC waits forever → 0 frames.
            //
            // CreateFreeThreaded fires FrameArrived directly on the WinRT thread
            // pool, bypassing the DispatcherQueue entirely.  This is the standard
            // pattern for WGC capture on a dedicated background thread.
            auto sz = wgc_state_->item.Size();
            wgc_state_->frame_pool =
                winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::CreateFreeThreaded(
                    wgc_state_->winrt_device,
                    winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
                    2,
                    sz);

            // 6. Capture session + disable yellow border (Windows 11)
            wgc_state_->session =
                wgc_state_->frame_pool.CreateCaptureSession(wgc_state_->item);

            try { wgc_state_->session.IsBorderRequired(false); }
            catch (...) { /* Windows 10 doesn't support this — ignore */ }

            // 7. Subscribe FrameArrived: only wakes CaptureThread, no encode work here.
            wgc_state_->frame_arrived_token =
                wgc_state_->frame_pool.FrameArrived([this](auto&, auto&) {
                    {
                        std::lock_guard<std::mutex> lk(wgc_frame_mutex_);
                        wgc_frame_ready_ = true;
                    }
                    wgc_frame_cv_.notify_one();
                });

            // 8. Start
            wgc_state_->session.StartCapture();

        } catch (winrt::hresult_error const& e) {
            std::cerr << "[WGC] "
                      << monitor::ToString(
                             monitor::MonitorResolveError::CaptureItemCreationFailed)
                      << ": WinRT setup error 0x"
                      << std::hex << e.code().value << std::dec
                      << " " << winrt::to_string(e.message()) << std::endl;
            wgc_state_.reset();
            if (context_) { context_->Release(); context_ = nullptr; }
            if (device_)  { device_->Release();  device_  = nullptr; }
            nvidia_device_ = false;
            return false;
        }

        std::cout << "[WGC] Ready. Capture started ("
                  << width_ << "x" << height_ << ", "
                  << (nvidia_device_ ? "NVIDIA adapter — GPU zero-copy"
                      : (nvenc_device_ ? "default adapter — NVENC CPU-input" : "default adapter"))
                  << ")" << std::endl;
        return true;
    }


    // ===========================================================================
    // ShutdownWGC
    // ===========================================================================

    void CaptureEngine::ShutdownWGC() {
        if (!wgc_state_) return;
        try {
            if (wgc_state_->item && wgc_state_->item_closed_registered) {
                wgc_state_->item.Closed(wgc_state_->item_closed_token);
                wgc_state_->item_closed_registered = false;
            }
            if (wgc_state_->session)    wgc_state_->session.Close();
            if (wgc_state_->frame_pool) {
                wgc_state_->frame_pool.FrameArrived(wgc_state_->frame_arrived_token);
                wgc_state_->frame_pool.Close();
            }
        } catch (...) {}
        wgc_state_.reset();
        std::cout << "[WGC] Shutdown complete." << std::endl;
    }


    // ===========================================================================
    // CaptureThreadWGC
    //
    // Replaces the DXGI polling loop on the WGC path.
    // Waits on wgc_frame_cv_ (signalled by FrameArrived callback), applies the
    // QPC frame rate limiter, extracts the ID3D11Texture2D from the WGC surface,
    // and routes it into NVENC (GPU zero-copy) or the x264 frame pool.
    //
    // On desktop NVIDIA (single GPU):
    //   Frame arrives in NVIDIA VRAM (same device as NVENC).
    //   CopyResource is GPU-to-GPU — no CPU stall. (GPU zero-copy path)
    //
    // On Optimus + WGC + NVENC:
    //   WGC frame pool uses default (Intel) adapter — NVIDIA can't capture the display.
    //   CopyResource into staging texture on Intel, Map to CPU, EncodeFrameCPU into NVENC.
    //   Still hardware H.264, only the pixel copy touches the CPU.
    // ===========================================================================

    void CaptureEngine::CaptureThreadWGC() {
        // WinRT calls (TryGetNextFrame, surface access) require the thread to be
        // in a COM apartment. Multi-threaded apartment is correct here — we have
        // no message pump and don't need the STA marshaling overhead.
        winrt::init_apartment(winrt::apartment_type::multi_threaded);

        std::cout << "[CaptureThread/WGC] Started ("
                  << fps_ << " fps, "
                  << (nvenc_active_ ? "NVENC" : "software") << " path, "
                  << (nvidia_device_ ? "GPU zero-copy"
                      : (nvenc_active_ ? "Optimus CPU-input" : "default adapter"))
                  << ")" << std::endl;

        LARGE_INTEGER qpc_freq;
        QueryPerformanceFrequency(&qpc_freq);

        const double  target_ms  = 1000.0 / static_cast<double>(fps_);
        const int64_t target_qpc = static_cast<int64_t>(
            (target_ms / 1000.0) * static_cast<double>(qpc_freq.QuadPart));

        LARGE_INTEGER last_capture;
        QueryPerformanceCounter(&last_capture);
        uint32_t consecutive_frame_errors = 0;

        while (running_.load(std::memory_order_relaxed)) {

            // Wait for FrameArrived signal. All encode work happens here on
            // CaptureThread — the callback only sets wgc_frame_ready_.
            {
                std::unique_lock<std::mutex> lk(wgc_frame_mutex_);
                wgc_frame_cv_.wait(lk, [this] {
                    return wgc_frame_ready_ ||
                           monitor_source_invalidated_.load(
                               std::memory_order_acquire) ||
                           !running_.load(std::memory_order_relaxed);
                });
                wgc_frame_ready_ = false;
            }

            if (!running_.load(std::memory_order_relaxed)) break;
            if (monitor_source_invalidated_.load(std::memory_order_acquire)) {
                std::cerr << "[CaptureThread/WGC] "
                          << monitor::ToString(
                                 monitor::MonitorResolveError::MonitorDisconnected)
                          << ": selected monitor capture item closed" << std::endl;
                capture_health_flags_.store(CAPTURE_HEALTH_BACKEND_FAILED);
                ClearReplayForRecovery();
                running_.store(false);
                break;
            }

            try {
                // Consume the frame FIRST to return the buffer slot to the pool.
                // WGC's Direct3D11CaptureFramePool has only 2 slots — if we
                // skip TryGetNextFrame (e.g. via rate limiter 'continue'), the
                // pool fills up and FrameArrived stops firing → 0 frames captured.
                // Acquisition itself can throw after a device/source loss, so it
                // belongs inside the same bounded error path as surface access.
                auto frame = wgc_state_->frame_pool.TryGetNextFrame();
                if (!frame) continue;

                if (wgc_state_->monitor_item) {
                    const auto content_size = frame.ContentSize();
                    if (content_size.Width != static_cast<int32_t>(width_)
                        || content_size.Height != static_cast<int32_t>(height_)) {
                        std::cerr << "[CaptureThread/WGC] "
                                  << monitor::ToString(
                                         monitor::MonitorResolveError::MonitorTopologyChanged)
                                  << ": selected monitor dimensions changed" << std::endl;
                        capture_health_flags_.store(CAPTURE_HEALTH_BACKEND_FAILED);
                        ClearReplayForRecovery();
                        running_.store(false);
                        break;
                    }
                }

                // QPC frame rate limiter — frame already consumed above, so the
                // pool slot is freed even when we skip processing this frame.
                LARGE_INTEGER now;
                QueryPerformanceCounter(&now);
                if (now.QuadPart - last_capture.QuadPart < target_qpc) {
                    frames_dropped_.fetch_add(1, std::memory_order_relaxed);
                    continue;  // frame destructor returns buffer to pool
                }
                last_capture = now;

            // Focus gate: when capturing for an anti-cheat game via monitor
            // capture, only encode frames while that game is in the foreground.
            // The frame has already been consumed above so the 2-slot pool is
            // freed regardless. Without this we'd bake the user's desktop into
            // saved clips whenever they alt-tab away mid-recording.
            if (focus_gated_ && target_hwnd_ != 0) {
                HWND fg = GetForegroundWindow();
                if (fg != reinterpret_cast<HWND>(target_hwnd_)) {
                    const uint32_t content = capture_health_flags_.load() &
                        CAPTURE_HEALTH_CONTENT_SUSPECT;
                    capture_health_flags_.store(
                        CAPTURE_HEALTH_ACTIVE | CAPTURE_HEALTH_PAUSED | content);
                    frames_dropped_.fetch_add(1, std::memory_order_relaxed);
                    continue;
                }
            }
            capture_health_flags_.fetch_and(~CAPTURE_HEALTH_PAUSED);
            capture_health_flags_.fetch_or(CAPTURE_HEALTH_ACTIVE);

                // Extract ID3D11Texture2D from the WGC surface
                auto surface = frame.Surface();

                // IDirect3DDxgiInterfaceAccess is a COM interface in
                // Windows::Graphics::DirectX::Direct3D11 — not a WinRT-projected
                // type, so winrt::as<>() cannot be used. QueryInterface directly.
                using DxgiAccess = Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess;
                winrt::com_ptr<DxgiAccess> interop;
                winrt::check_hresult(
                    reinterpret_cast<IUnknown*>(winrt::get_abi(surface))->QueryInterface(
                        __uuidof(DxgiAccess), reinterpret_cast<void**>(interop.put())));

                winrt::com_ptr<ID3D11Texture2D> tex;
                winrt::check_hresult(interop->GetInterface(IID_PPV_ARGS(tex.put())));

                if (nvenc_active_ && nvidia_device_) {
                    // ----------------------------------------------------------
                    // NVENC GPU zero-copy path (desktop NVIDIA).
                    // Frame is in NVIDIA VRAM, NVENC reads from same device.
                    // CopyResource is a pure GPU operation — no CPU stall.
                    // ----------------------------------------------------------
                    SampleContentTexture(
                        tex.get(), frames_captured_.load(std::memory_order_relaxed) + 1);
                    ID3D11Texture2D* input_tex =
                        replay_encoder_->GetCurrentInputTexture();
                    context_->CopyResource(input_tex, tex.get());
                    replay_encoder_->EncodeFrame(now.QuadPart); // use rate-limiter QPC as frame timestamp
                }
                else if (nvenc_active_ && staging_texture_) {
                    // ----------------------------------------------------------
                    // NVENC CPU-input path (Optimus).
                    // WGC on Intel adapter; NVENC on separate NVIDIA device.
                    // Map the staging texture on the Intel device and memcpy
                    // into the NVENC system-memory input buffer.
                    // ----------------------------------------------------------
                    context_->CopyResource(staging_texture_, tex.get());

                    D3D11_MAPPED_SUBRESOURCE mapped{};
                    HRESULT hr = context_->Map(staging_texture_, 0, D3D11_MAP_READ, 0, &mapped);
                    if (FAILED(hr)) {
                        std::cerr << "[CaptureThread/WGC] Texture map failed (Optimus): 0x"
                                  << std::hex << hr << std::dec << std::endl;
                        continue;
                    }

                    SampleContentBGRA(
                        static_cast<const uint8_t*>(mapped.pData), mapped.RowPitch,
                        width_, height_, frames_captured_.load(std::memory_order_relaxed) + 1);

                    replay_encoder_->EncodeFrameCPU(
                        static_cast<const uint8_t*>(mapped.pData),
                        mapped.RowPitch, now.QuadPart);
                    // Callback inside EncodeFrameCPU fires -> EncodedRingBuffer::Push()

                    context_->Unmap(staging_texture_, 0);
                }
                else {
                    // ----------------------------------------------------------
                    // x264 fallback: CPU read path
                    // ----------------------------------------------------------
                    context_->CopyResource(staging_texture_, tex.get());

                    D3D11_MAPPED_SUBRESOURCE mapped{};
                    HRESULT hr = context_->Map(staging_texture_, 0, D3D11_MAP_READ, 0, &mapped);
                    if (FAILED(hr)) {
                        std::cerr << "[CaptureThread/WGC] Texture map failed: 0x"
                                  << std::hex << hr << std::dec << std::endl;
                        continue;
                    }

                    const uint8_t* src = static_cast<const uint8_t*>(mapped.pData);
                    const size_t   row = static_cast<size_t>(width_) * 4;
                    const size_t   write_pos = ring_head_.load(std::memory_order_relaxed);
                    const size_t   slot_idx  = write_pos % max_frames_;
                    uint8_t*       dst = frame_pool_.GetSlot(slot_idx);

                    SampleContentBGRA(
                        src, mapped.RowPitch, width_, height_,
                        frames_captured_.load(std::memory_order_relaxed) + 1);

                    if (mapped.RowPitch == static_cast<UINT>(row)) {
                        std::memcpy(dst, src, row * height_);
                    } else {
                        for (uint32_t y = 0; y < height_; y++) {
                            std::memcpy(dst + y * row, src + y * mapped.RowPitch, row);
                        }
                    }

                    context_->Unmap(staging_texture_, 0);

                    ring_head_.fetch_add(1, std::memory_order_release);
                    size_t prev = ring_count_.load(std::memory_order_relaxed);
                    if (prev < max_frames_)
                        ring_count_.fetch_add(1, std::memory_order_relaxed);
                }

                frames_captured_.fetch_add(1, std::memory_order_relaxed);
                consecutive_frame_errors = 0;

            } catch (winrt::hresult_error const& e) {
                std::cerr << "[CaptureThread/WGC] Frame error: 0x"
                          << std::hex << e.code().value << std::dec << std::endl;
                if (++consecutive_frame_errors >= 30) {
                    std::cerr << "[CaptureThread/WGC] Too many consecutive frame errors"
                              << std::endl;
                    capture_health_flags_.store(CAPTURE_HEALTH_BACKEND_FAILED);
                    running_.store(false);
                }
            }
        }

        std::cout << "[CaptureThread/WGC] Stopped. Frames captured: "
                  << frames_captured_.load() << std::endl;
        if (capture_health_flags_.load() != CAPTURE_HEALTH_BACKEND_FAILED)
            capture_health_flags_.store(CAPTURE_HEALTH_NONE);

        winrt::uninit_apartment();
    }


    // ===========================================================================
    // InitializeWindowCapture
    //
    // WGC capture targeting a specific window (HWND stored in target_hwnd_).
    //
    // Architecture is identical to InitializeWGC (desktop) with two differences:
    //   1. Capture item is created via CreateForWindow(hwnd) instead of
    //      CreateForMonitor — captures the specific app even if partially occluded.
    //   2. width_/height_ come from item.Size() (the window's content area) rather
    //      than the monitor dimensions.
    //
    // Known limitation: if the window is resized after the engine starts, the
    // captured frames will mismatch the NVENC texture dimensions. Restart required.
    // ===========================================================================

    bool CaptureEngine::InitializeWindowCapture() {
        try {
            if (!winrt::Windows::Graphics::Capture::GraphicsCaptureSession::IsSupported()) {
                std::cerr << "[WinCapture] GraphicsCaptureSession not supported" << std::endl;
                return false;
            }
        } catch (...) {
            std::cerr << "[WinCapture] IsSupported() threw — WGC unavailable" << std::endl;
            return false;
        }

        HWND hwnd = reinterpret_cast<HWND>(target_hwnd_);
        if (!IsWindow(hwnd)) {
            std::cerr << "[WinCapture] HWND 0x" << std::hex << target_hwnd_
                      << std::dec << " is not a valid window" << std::endl;
            return false;
        }

        // --- Step 1: Enumerate adapters, detect Optimus (same as InitializeWGC) ---
        IDXGIFactory1* factory = nullptr;
        HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory);
        if (FAILED(hr)) {
            std::cerr << "[WinCapture] CreateDXGIFactory1 failed: 0x"
                      << std::hex << hr << std::dec << std::endl;
            return false;
        }

        IDXGIAdapter1* nvidia_adapter = nullptr;
        bool has_intel = false;
        {
            IDXGIAdapter1* a = nullptr;
            for (UINT i = 0; factory->EnumAdapters1(i, &a) != DXGI_ERROR_NOT_FOUND; ++i) {
                DXGI_ADAPTER_DESC1 desc; a->GetDesc1(&desc);
                char name[256] = {};
                WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1, name, sizeof(name)-1, nullptr, nullptr);
                std::cout << "[WinCapture] Adapter " << i << ": " << name << std::endl;
                if (desc.VendorId == 0x10DE && !nvidia_adapter) {
                    nvidia_adapter = a; a->AddRef();
                }
                if (desc.VendorId == 0x8086) has_intel = true;
                a->Release();
            }
        }
        factory->Release();

        bool is_optimus = nvidia_adapter && has_intel;

        // --- Step 2: Create D3D11 device ---
        //
        // WGC delivers frames on the device the frame pool is created on.
        // On Optimus: the display is driven by Intel, so WGC must use the Intel
        // (default) adapter for its device — using NVIDIA here causes 0 frames.
        // Keep a separate NVIDIA device for NVENC (CPU-input path).
        //
        // On desktop NVIDIA (single GPU): default adapter IS NVIDIA, GPU zero-copy.
        D3D_FEATURE_LEVEL feature_level;

        if (nvidia_adapter && !is_optimus) {
            hr = D3D11CreateDevice(nvidia_adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
                nullptr, 0, D3D11_SDK_VERSION, &device_, &feature_level, &context_);
            nvidia_adapter->Release();
            nvidia_adapter = nullptr;
            if (SUCCEEDED(hr)) {
                nvidia_device_ = true;
                std::cout << "[WinCapture] D3D11 on NVIDIA adapter (GPU zero-copy)" << std::endl;
            } else {
                std::cerr << "[WinCapture] NVIDIA device creation failed (0x"
                          << std::hex << hr << std::dec << ") — trying default" << std::endl;
            }
        }
        else if (is_optimus) {
            std::cout << "[WinCapture] Optimus — WGC device on default (Intel) adapter, "
                         "NVENC on separate NVIDIA device" << std::endl;
            hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
                nullptr, 0, D3D11_SDK_VERSION, &device_, &feature_level, &context_);
            if (FAILED(hr)) {
                std::cerr << "[WinCapture] Default adapter D3D11 failed: 0x"
                          << std::hex << hr << std::dec << std::endl;
                nvidia_adapter->Release();
                return false;
            }
            hr = D3D11CreateDevice(nvidia_adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
                nullptr, 0, D3D11_SDK_VERSION, &nvenc_device_, &feature_level, &nvenc_context_);
            nvidia_adapter->Release();
            nvidia_adapter = nullptr;
            if (FAILED(hr)) {
                std::cerr << "[WinCapture] NVIDIA device for NVENC failed — will use x264" << std::endl;
            }
        }

        if (nvidia_adapter) { nvidia_adapter->Release(); nvidia_adapter = nullptr; }

        if (!device_) {
            hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
                nullptr, 0, D3D11_SDK_VERSION, &device_, &feature_level, &context_);
            if (FAILED(hr)) {
                std::cerr << "[WinCapture] D3D11CreateDevice failed: 0x"
                          << std::hex << hr << std::dec << std::endl;
                return false;
            }
        }

        // --- Steps 3-8: WinRT capture session (exception-safe) ---
        wgc_state_ = std::make_unique<WGCState>();
        try {
            // 3. Wrap ID3D11Device as WinRT IDirect3DDevice
            winrt::com_ptr<IDXGIDevice> dxgi_dev;
            winrt::check_hresult(device_->QueryInterface(dxgi_dev.put()));
            winrt::com_ptr<IInspectable> insp;
            winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi_dev.get(), insp.put()));
            wgc_state_->winrt_device =
                insp.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();

            // 4. Create capture item from the target window
            auto item_interop = winrt::get_activation_factory<
                winrt::Windows::Graphics::Capture::GraphicsCaptureItem,
                IGraphicsCaptureItemInterop>();
            winrt::check_hresult(item_interop->CreateForWindow(
                hwnd,
                winrt::guid_of<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>(),
                winrt::put_abi(wgc_state_->item)));

            // 5. Get captured dimensions from the item (= window content size)
            auto sz = wgc_state_->item.Size();
            width_  = static_cast<uint32_t>(sz.Width);
            height_ = static_cast<uint32_t>(sz.Height);
            std::cout << "[WinCapture] Window content size: " << width_ << "x" << height_ << std::endl;

            // 6. Staging texture for CPU-readback paths (Optimus or x264 fallback).
            //    Created on the WGC device; used in CaptureThreadWGC() Map/Unmap.
            //    Not needed on desktop NVIDIA (GPU zero-copy — no CPU read).
            if (!nvidia_device_) {
                D3D11_TEXTURE2D_DESC tdesc{};
                tdesc.Width            = width_;
                tdesc.Height           = height_;
                tdesc.MipLevels        = 1;
                tdesc.ArraySize        = 1;
                tdesc.Format           = DXGI_FORMAT_B8G8R8A8_UNORM;
                tdesc.SampleDesc.Count = 1;
                tdesc.Usage            = D3D11_USAGE_STAGING;
                tdesc.CPUAccessFlags   = D3D11_CPU_ACCESS_READ;
                hr = device_->CreateTexture2D(&tdesc, nullptr, &staging_texture_);
                if (FAILED(hr)) {
                    std::cerr << "[WinCapture] Staging texture creation failed: 0x"
                              << std::hex << hr << std::dec << std::endl;
                    // Non-fatal: x264 fallback will fail at Map(), but NVENC Optimus needs it.
                    // Leave staging_texture_ null and proceed — NVENC check happens after.
                } else {
                    std::cout << "[WinCapture] Staging texture created for CPU-readback" << std::endl;
                }
            }

            // 7. Frame pool: 2 slots, free-threaded.
            //    See InitializeWGC() comment on why CreateFreeThreaded is required.
            wgc_state_->frame_pool =
                winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::CreateFreeThreaded(
                    wgc_state_->winrt_device,
                    winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
                    2,
                    sz);

            // 8. Capture session + suppress yellow border (Windows 10 20H2+)
            wgc_state_->session =
                wgc_state_->frame_pool.CreateCaptureSession(wgc_state_->item);
            try { wgc_state_->session.IsBorderRequired(false); }
            catch (...) { /* not supported on older Windows — silently ignore */ }

            // 9. FrameArrived: only wakes CaptureThreadWGC, no encoding work in callback
            wgc_state_->frame_arrived_token =
                wgc_state_->frame_pool.FrameArrived([this](auto&, auto&) {
                    {
                        std::lock_guard<std::mutex> lk(wgc_frame_mutex_);
                        wgc_frame_ready_ = true;
                    }
                    wgc_frame_cv_.notify_one();
                });

            // 10. Start
            wgc_state_->session.StartCapture();

        } catch (winrt::hresult_error const& e) {
            std::cerr << "[WinCapture] WinRT error during setup: 0x"
                      << std::hex << e.code().value << std::dec
                      << " " << winrt::to_string(e.message()) << std::endl;
            wgc_state_.reset();
            if (context_) { context_->Release(); context_ = nullptr; }
            if (device_)  { device_->Release();  device_  = nullptr; }
            nvidia_device_ = false;
            return false;
        }

        std::cout << "[WinCapture] Ready  "
                  << width_ << "x" << height_ << "  "
                  << (nvidia_device_ ? "NVIDIA adapter — GPU zero-copy"
                      : (nvenc_device_ ? "default adapter — NVENC CPU-input"
                                       : "default adapter — x264"))
                  << std::endl;
        return true;
    }


    // ===========================================================================
    // InitializeD3D11 - exact selected adapter/output fallback
    // ===========================================================================

    bool CaptureEngine::InitializeD3D11() {
        nvidia_device_ = false;
        IDXGIOutput* selected_output = nullptr;
        if (!InitializeMonitorCaptureDevice("DXGI", &selected_output)) {
            return false;
        }

        IDXGIOutput1* output1 = nullptr;
        HRESULT hr = selected_output->QueryInterface(
            __uuidof(IDXGIOutput1), reinterpret_cast<void**>(&output1));
        if (SUCCEEDED(hr)) {
            hr = output1->DuplicateOutput(device_, &duplication_);
            output1->Release();
        }
        selected_output->Release();
        if (FAILED(hr) || !duplication_) {
            std::cerr << "[D3D11] "
                      << monitor::ToString(
                             monitor::MonitorResolveError::OutputResolutionFailed)
                      << ": DuplicateOutput failed for selected output: 0x"
                      << std::hex << hr << std::dec << std::endl;
            if (context_) { context_->Release(); context_ = nullptr; }
            if (device_) { device_->Release(); device_ = nullptr; }
            return false;
        }

        // Staging texture (software path and the pre-existing hybrid NVENC path).
        {
            D3D11_TEXTURE2D_DESC desc{};
            desc.Width            = width_;
            desc.Height           = height_;
            desc.MipLevels        = 1;
            desc.ArraySize        = 1;
            desc.Format           = DXGI_FORMAT_B8G8R8A8_UNORM;
            desc.SampleDesc.Count = 1;
            desc.Usage            = D3D11_USAGE_STAGING;
            desc.CPUAccessFlags   = D3D11_CPU_ACCESS_READ;
            if (FAILED(device_->CreateTexture2D(&desc, nullptr, &staging_texture_))) {
                std::cerr << "[D3D11] Staging texture creation failed" << std::endl;
                return false;
            }
        }

        std::cout << "[D3D11] Ready - " << width_ << "x" << height_
                  << (nvidia_device_
                      ? "  [selected NVIDIA adapter - GPU zero-copy enabled]"
                      : "  [selected display-owning adapter]")
                  << std::endl;
        std::cout << "[D3D11] Duplication: " << (duplication_ ? "OK" : "NULL") << std::endl;
        std::cout << "[D3D11] Staging tex: " << (staging_texture_ ? "OK" : "NULL") << std::endl;
        return true;
    }


    // ===========================================================================
    // ShutdownD3D11 - unchanged
    // ===========================================================================

    void CaptureEngine::ShutdownD3D11() {
        if (health_staging_texture_) {
            health_staging_texture_->Release();
            health_staging_texture_ = nullptr;
        }
        if (staging_texture_) { staging_texture_->Release(); staging_texture_ = nullptr; }
        if (duplication_)     { duplication_->Release();     duplication_ = nullptr; }
        if (context_)         { context_->Release();         context_ = nullptr; }
        if (device_)          { device_->Release();          device_ = nullptr; }
        // nvenc_device_ / nvenc_context_ are intentionally NOT released here.
        // They must outlive the NVENC encoder session (which is finalized in Shutdown()
        // before this is called). On ACCESS_LOST reinit they stay valid.
    }


} // namespace fthr

// capture_engine.cpp
// FTHR Capture Engine - Implementation

// Must be defined before ANY include that pulls in windows.h (including winrt/base.h)
// to prevent the min/max macros from being defined and stomping std::min/std::max.
#ifndef NOMINMAX
#define NOMINMAX
#endif
//
// Capture backends (WGC is preferred, matching the old FTHR build; DXGI is the
// fallback only when the live WGC session cannot suppress its indicator):
//
//   Desktop mode                  -> InitializeWGC() -> InitializeD3D11()
//   Window mode (regular)         -> InitializeWindowCapture() (CreateForWindow)
//   Window mode (anti-cheat exe)  -> InitializeWGC() + focus_gated_=true
//
// Why WGC is preferred when it is borderless: kernel anti-cheats like Vanguard
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
//   Raw replay code remains for non-production/legacy use, but public-alpha
//   startup never selects it as an automatic fallback.
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
#include "clip_audio_manifest.h"
#include "transactional_save.h"
#include "windows_capture_border_policy.h"
#include "windows_native_error.h"
#include "replay_interval.h"
#include "frame_rate_scheduler.h"
#include <iostream>
#include <chrono>
#include <cstring>
#include <cwctype>
#include <algorithm>
#include <cmath>
#include <numeric>
#include <sstream>
#include <filesystem>

extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/dict.h>
#include <libavutil/mathematics.h>
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

    namespace {

        std::string AdapterLuidJson(const monitor::AdapterLuid& luid) {
            std::ostringstream value;
            value << "{\"high_part\":" << luid.high_part
                  << ",\"low_part\":" << luid.low_part << '}';
            return value.str();
        }

        bool QueryD3D11DeviceAdapterLuid(
            ID3D11Device* device,
            monitor::AdapterLuid& luid,
            std::string& diagnostic) {
            if (!device) {
                diagnostic = "D3D11 device is null";
                return false;
            }
            IDXGIDevice* dxgi_device = nullptr;
            HRESULT hr = device->QueryInterface(
                __uuidof(IDXGIDevice),
                reinterpret_cast<void**>(&dxgi_device));
            if (FAILED(hr) || !dxgi_device) {
                diagnostic = diagnostics::FormatHResultFailure(
                    "ID3D11Device::QueryInterface(IDXGIDevice)", hr);
                return false;
            }
            IDXGIAdapter* adapter = nullptr;
            hr = dxgi_device->GetAdapter(&adapter);
            dxgi_device->Release();
            if (FAILED(hr) || !adapter) {
                diagnostic = diagnostics::FormatHResultFailure(
                    "IDXGIDevice::GetAdapter", hr);
                return false;
            }
            DXGI_ADAPTER_DESC description{};
            hr = adapter->GetDesc(&description);
            adapter->Release();
            if (FAILED(hr)) {
                diagnostic = diagnostics::FormatHResultFailure(
                    "IDXGIAdapter::GetDesc", hr);
                return false;
            }
            luid = {description.AdapterLuid.LowPart,
                    description.AdapterLuid.HighPart};
            return true;
        }

        bool CreateD3D11DeviceForVendor(
            EncoderVendor vendor,
            ID3D11Device** device,
            ID3D11DeviceContext** context,
            std::string& diagnostic) {
            if (!device || !context) return false;
            *device = nullptr;
            *context = nullptr;

            IDXGIFactory1* factory = nullptr;
            const HRESULT factory_status = CreateDXGIFactory1(
                __uuidof(IDXGIFactory1),
                reinterpret_cast<void**>(&factory));
            if (FAILED(factory_status) || !factory) {
                diagnostic = diagnostics::FormatHResultFailure(
                    "CreateDXGIFactory1(encoder adapter search)",
                    factory_status);
                return false;
            }

            bool created = false;
            for (UINT index = 0; !created; ++index) {
                IDXGIAdapter1* adapter = nullptr;
                const HRESULT enumerated = factory->EnumAdapters1(
                    index, &adapter);
                if (enumerated == DXGI_ERROR_NOT_FOUND) {
                    break;
                }
                if (FAILED(enumerated) || !adapter) {
                    diagnostic = diagnostics::FormatHResultFailure(
                        "IDXGIFactory1::EnumAdapters1(encoder adapter search)",
                        enumerated);
                    break;
                }
                DXGI_ADAPTER_DESC1 description{};
                const HRESULT description_status = adapter->GetDesc1(
                    &description);
                if (FAILED(description_status)) {
                    diagnostic = diagnostics::FormatHResultFailure(
                        "IDXGIAdapter1::GetDesc1(encoder adapter search)",
                        description_status);
                    adapter->Release();
                    break;
                }
                if (!(description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)
                    && EncoderVendorFromPciVendorId(description.VendorId)
                        == vendor) {
                    D3D_FEATURE_LEVEL feature_level{};
                    const HRESULT device_status = D3D11CreateDevice(
                        adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
                        nullptr, 0, D3D11_SDK_VERSION,
                        device, &feature_level, context);
                    created = SUCCEEDED(device_status);
                    if (!created) {
                        diagnostic = diagnostics::FormatHResultFailure(
                            "D3D11CreateDevice(encoder adapter)",
                            device_status);
                    }
                }
                adapter->Release();
            }
            factory->Release();
            if (!created && diagnostic.empty()) {
                diagnostic = "No matching hardware encoder adapter was found";
            }
            return created;
        }

    }  // namespace


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
        , stall_watchdog_thread_(nullptr)
        , save_clip_thread_(nullptr)
        , running_(false)
        , is_recording_(false)
        , device_(nullptr)
        , context_(nullptr)
        , duplication_(nullptr)
        , staging_texture_(nullptr)
        , crop_texture_(nullptr)
        , health_staging_texture_(nullptr)
        , nvenc_device_(nullptr)
        , nvenc_context_(nullptr)
        , wgc_active_(false)
        , wgc_frame_ready_(false)
        , width_(0)
        , height_(0)
        , crop_enabled_(false)
        , crop_x_(0)
        , crop_y_(0)
        , crop_width_(0)
        , crop_height_(0)
        , target_hwnd_(0)
        , focus_gated_(false)
        , fps_(60)
        , buffer_seconds_(30)
        , target_width_(0)
        , target_height_(0)
        , bitrate_kbps_(16000)
        , scaling_mode_(0)
        , separate_audio_enabled_(false)
        , monitor_resolver_(monitor_topology_source_)
        , nvenc_active_(false)
        , nvidia_device_(false)
        , replay_encoder_cpu_input_(false)
        , capture_adapter_vendor_(EncoderVendor::Software)
        , max_frames_(0)
        , frames_captured_(0)
        , frames_dropped_(0)
        , audio_active_(false)
    {
    }

    CaptureEngine::~CaptureEngine() {
        Shutdown();
    }

    void CaptureEngine::SetCaptureFailure(std::string detail) {
        last_capture_failure_detail_ = std::move(detail);
    }

    std::string CaptureEngine::BuildStartupDiagnosticContext() const {
        std::string selected_monitor = diagnostics::WideToUtf8(
            monitor_device_path_);
        if (selected_monitor.empty()) {
            selected_monitor = "unavailable:not_configured";
        }
        std::ostringstream context;
        context << "startup_context={\"selected_monitor_id\":\""
                << diagnostics::JsonEscape(selected_monitor)
                << "\",\"dxgi_output\":";
        if (resolved_dxgi_output_.output_index != UINT32_MAX) {
            context << "{\"index\":" << resolved_dxgi_output_.output_index
                    << ",\"source_gdi_name\":\""
                    << diagnostics::JsonEscape(diagnostics::WideToUtf8(
                           resolved_dxgi_output_.source_gdi_name))
                    << "\"}";
        } else {
            context << "\"unavailable:not_resolved\"";
        }
        context << ",\"owning_adapter_luid\":";
        if (!resolved_monitor_.monitor_device_path.empty()) {
            context << AdapterLuidJson(resolved_monitor_.adapter_luid);
        } else {
            context << "\"unavailable:not_resolved\"";
        }
        context << ",\"capture_device_adapter_luid\":";
        if (capture_device_adapter_luid_available_) {
            context << AdapterLuidJson(capture_device_adapter_luid_);
        } else {
            context << "\"unavailable:not_reached_or_resolved\"";
        }
        context << ",\"encoder_adapter_luid\":";
        if (encoder_adapter_luid_available_) {
            context << AdapterLuidJson(encoder_adapter_luid_);
        } else {
            context << "\"unavailable:not_reached_or_resolved\"";
        }
        context << ",\"capture_backend\":\""
                << diagnostics::JsonEscape(startup_capture_backend_)
                << "\",\"encoder_backend\":\""
                << diagnostics::JsonEscape(startup_encoder_backend_)
                << "\",\"codec\":\""
                << diagnostics::JsonEscape(startup_codec_) << "\"}";
        return context.str();
    }

    bool CaptureEngine::FailStartup(
        ReplayStartupError code, std::string detail) {
        if (!last_capture_failure_detail_.empty()
            && detail.find(last_capture_failure_detail_) == std::string::npos) {
            detail += " ";
            detail += last_capture_failure_detail_;
        }
        detail += " ";
        detail += BuildStartupDiagnosticContext();
        last_startup_error_code_ = code;
        last_startup_error_ = std::move(detail);
        std::cerr << "FTHR_STARTUP_ERROR: "
                  << ReplayStartupErrorName(code) << ": "
                  << last_startup_error_ << std::endl;
        return false;
    }


    // ===========================================================================
    // Initialize
    //
    // 1. Init D3D11 / DXGI (always)
    // 2. Select the hardware encoder on the capture adapter
    // 3. Refuse startup if that exact codec/backend cannot initialize
    // 4. Start threads with a compressed replay ring
    // ===========================================================================

    bool CaptureEngine::Initialize(const CaptureConfig& config) {
        last_startup_error_code_ = ReplayStartupError::None;
        last_startup_error_.clear();
        last_capture_failure_detail_.clear();
        active_replay_capability_ = {};
        resolved_monitor_ = {};
        resolved_dxgi_output_ = {};
        capture_device_adapter_luid_ = {};
        encoder_adapter_luid_ = {};
        capture_device_adapter_luid_available_ = false;
        encoder_adapter_luid_available_ = false;
        startup_capture_backend_ = "unavailable:not_reached";
        startup_encoder_backend_ = "unavailable:not_reached";
        startup_codec_ = VideoCodecName(config.video_codec);
        fps_ = config.framerate;
        buffer_seconds_ = config.buffer_seconds;
        target_width_ = config.target_width;
        target_height_ = config.target_height;
        bitrate_kbps_ = config.bitrate_kbps;
        scaling_mode_ = (config.scaling_mode == CaptureConfig::ScalingModeEnum::FIT) ? 1u : 0u;
        separate_audio_enabled_ = config.separate_audio_enabled;
        monitor_device_path_ = monitor::NormalizeMonitorDevicePath(
            config.monitor_device_path);
        capture_loop_iterations_.store(0);
        capture_acquire_attempts_.store(0);
        capture_acquire_successes_.store(0);
        capture_timeouts_.store(0);
        capture_frames_released_.store(0);
        source_textures_received_.store(0);
        conversion_submissions_.store(0);
        conversion_completions_.store(0);
        video_packets_produced_.store(0);
        video_ring_insertions_.store(0);
        capture_thread_stage_.store(0);
        last_capture_hresult_.store(0);

        std::cout << "[CaptureEngine] Initializing..." << std::endl;
        std::cout << "  FPS        : " << fps_ << std::endl;
        std::cout << "  Buffer     : " << buffer_seconds_ << "s" << std::endl;
        std::cout << "  Target res : ";
        if (target_width_ == 0 || target_height_ == 0)
            std::cout << "Native" << std::endl;
        else
            std::cout << target_width_ << "x" << target_height_ << std::endl;
        std::cout << "  Bitrate    : " << bitrate_kbps_ << " kbps" << std::endl;
        std::cout << "  Codec      : " << VideoCodecName(config.video_codec)
                  << std::endl;

        // ------------------------------------------------------------------
        // Select capture backend based on config.capture_mode.
        //
        //   DESKTOP                     — WGC CreateForMonitor when borderless → DXGI
        //   WINDOW (anti-cheat title)   — WGC CreateForMonitor + focus gate → DXGI fallback
        //   WINDOW (regular)            — WGC CreateForWindow → WGC monitor → DXGI fallback
        //
        // WGC is preferred when it is borderless because it hooks at the
        // DWM/compositor level and survives independent flip mode, where DXGI
        // OutputDuplication returns black/stale frames (the game renders
        // direct to display hardware, bypassing DWM entirely). This is the
        // failure mode that hides Valorant from Snipping Tool and from
        // DXGI-based capture. If WGC would show its privacy border, the engine
        // intentionally accepts that tradeoff and uses the border-free DXGI
        // path instead.
        //
        // For known kernel-anti-cheat games picked in WINDOW mode, we escalate
        // to monitor capture (the path Xbox Game Bar uses, which the AC allows)
        // and turn on the focus gate so we only encode while that game is
        // actually foregrounded.
        //
        // DXGI OutputDuplication is also the intentional fallback when the
        // WGC privacy border cannot be disabled. That keeps a successful
        // capture visually quiet on unpackaged Windows 10 builds.
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
                    return FailStartup(
                        ReplayStartupError::CaptureAdapterUnsupported,
                        "The selected monitor could not be opened on its exact "
                        "display adapter.");
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
                        return FailStartup(
                            ReplayStartupError::CaptureAdapterUnsupported,
                            "The selected capture source could not be opened on "
                            "a supported display adapter.");
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
                    return FailStartup(
                        ReplayStartupError::CaptureAdapterUnsupported,
                        "The selected monitor could not be opened on its exact "
                        "display adapter.");
                }
            }
        }

        // ------------------------------------------------------------------
        // Select one compressed replay backend from the adapter that owns the
        // capture source. Intel now stays on its selected D3D11 adapter for
        // QSV. Hybrid-GPU policy remains a separate qualification task.
        // ------------------------------------------------------------------
        // Capture opened successfully. Do not attach a superseded WGC fallback
        // error to a later encoder failure.
        last_capture_failure_detail_.clear();
        ConfigureCrop(config);
        std::cout << "[CaptureEngine] Attempting hardware replay initialization "
                  << "for selected " << EncoderVendorName(capture_adapter_vendor_)
                  << " adapter..." << std::endl;

        EncoderConfig hw_cfg;
        hw_cfg.src_width = crop_width_;
        hw_cfg.src_height = crop_height_;
        hw_cfg.enc_width = target_width_;
        hw_cfg.enc_height = target_height_;
        hw_cfg.fps = fps_;
        hw_cfg.bitrate_kbps = bitrate_kbps_;
        hw_cfg.hardware_preset = config.encoder_preset;

        const auto selection = SelectWindowsReplayPolicy(
            capture_adapter_vendor_, config.encoder_preference,
            config.video_codec);
        startup_encoder_backend_ = ReplayEncoderBackendName(selection.backend);
        if (!selection.allowed) {
            return FailStartup(selection.error,
                "The requested encoder is unavailable for the selected capture "
                "source and codec. Cross-adapter AMD/Intel and raw replay "
                "fallbacks are disabled.");
        }

        std::cout << "[ReplayCapability] requested="
                  << VideoCodecName(config.video_codec)
                  << " capture_adapter="
                  << EncoderVendorName(selection.capture_vendor)
                  << " encoder_adapter="
                  << EncoderVendorName(selection.encoder_vendor)
                  << " backend="
                  << ReplayEncoderBackendName(selection.backend)
                  << " adapter_policy="
                  << (selection.same_adapter ? "same-adapter"
                                             : "explicit-cross-adapter")
                  << std::endl;

        replay_encoder_ = CreateProductionReplayEncoder(
            selection.encoder_vendor, config.video_codec);
        if (!replay_encoder_) {
            return FailStartup(
                ReplayStartupError::HardwareEncoderUnavailable,
                "The approved same-adapter hardware encoder backend is not "
                "available in this build.");
        }

        replay_config_publish_failed_.store(false);
        auto packet_callback = [this](const uint8_t* data, uint32_t size,
                                      int64_t pts, bool is_keyframe,
                                      int64_t wall_qpc) {
            if (!encoded_ring_ || !replay_encoder_) {
                replay_config_publish_failed_.store(true);
                return;
            }
            if (!encoded_ring_->HasVideoConfig()) {
                const auto config = replay_encoder_->GetVideoConfig();
                if (!replay_encoder_->IsVideoConfigReady()
                    || !encoded_ring_->SetVideoConfig(config)) {
                    replay_config_publish_failed_.store(true);
                    return;
                }
            }
            encoded_ring_->Push(data, size, pts, is_keyframe, wall_qpc);
            video_packets_produced_.fetch_add(1, std::memory_order_relaxed);
            video_ring_insertions_.fetch_add(1, std::memory_order_relaxed);

            std::shared_ptr<ContinuousRecordingWriter> writer;
            {
                std::lock_guard<std::mutex> lock(record_writer_mutex_);
                writer = record_writer_;
            }
            if (writer && !writer->PushVideo(data, size, pts, is_keyframe)) {
                is_recording_.store(false, std::memory_order_release);
            }
        };

        ID3D11Device* encoder_device = device_;
        ID3D11DeviceContext* encoder_context = context_;
        replay_encoder_cpu_input_ = !selection.same_adapter;
        if (replay_encoder_cpu_input_) {
            std::string encoder_device_diagnostic;
            if (!CreateD3D11DeviceForVendor(
                    EncoderVendor::Nvidia,
                    &nvenc_device_, &nvenc_context_,
                    encoder_device_diagnostic)) {
                return FailStartup(
                    ReplayStartupError::HardwareEncoderUnavailable,
                    "NVIDIA was selected, but a usable NVIDIA D3D11 device "
                    "could not be created. " + encoder_device_diagnostic);
            }
            if (!EnsureStagingTexture()) {
                return FailStartup(
                    ReplayStartupError::CrossAdapterPathUnavailable,
                    "NVIDIA was selected across adapters, but the capture "
                    "readback texture could not be created.");
            }
            encoder_device = nvenc_device_;
            encoder_context = nvenc_context_;
            std::cout << "[ReplayCapability] Explicit hybrid NVIDIA path: "
                      << "capture readback -> NVENC CPU input" << std::endl;
        }
        std::string encoder_luid_diagnostic;
        encoder_adapter_luid_available_ = QueryD3D11DeviceAdapterLuid(
            encoder_device, encoder_adapter_luid_, encoder_luid_diagnostic);
        if (!encoder_adapter_luid_available_) {
            std::cerr << "[ReplayCapability] Could not resolve encoder adapter LUID: "
                      << encoder_luid_diagnostic << std::endl;
        }
        nvenc_active_ = replay_encoder_->Initialize(
            hw_cfg, encoder_device, encoder_context, packet_callback,
            replay_encoder_cpu_input_);
        if (!nvenc_active_) {
            std::cerr << "[CaptureEngine] "
                      << EncoderVendorName(selection.encoder_vendor) << ' '
                      << VideoCodecName(config.video_codec)
                      << " hardware initialization failed";
            const std::string detail = replay_encoder_->GetLastError();
            if (!detail.empty()) std::cerr << ": " << detail;
            std::cerr << std::endl;
            const auto raw_capacity = CalculateRawReplayCapacity(
                crop_width_, crop_height_, 4, fps_, buffer_seconds_,
                config.max_buffer_mb);
            std::ostringstream reason;
            if (!detail.empty()) reason << detail << ". ";
            reason << "The requested " << VideoCodecName(config.video_codec)
                   << " encoder on the requested "
                   << EncoderVendorName(selection.encoder_vendor)
                   << " adapter did not initialize. Automatic codec, "
                      "cross-adapter, and raw replay fallbacks are disabled";
            if (!raw_capacity.meets_requested_duration) {
                reason << "; the legacy raw budget would hold only "
                       << raw_capacity.capacity_milliseconds << " ms of the "
                       << (static_cast<uint64_t>(buffer_seconds_) * 1000ULL)
                       << " ms requested";
            }
            reason << '.';
            return FailStartup(
                ClassifyReplayInitializationFailure(detail), reason.str());
        }

        active_replay_capability_ = EvaluateActiveReplayCapability(
            selection, replay_encoder_->GetActiveEncoderInfo(), true,
            static_cast<uint64_t>(capture_generation_.load()) + 1ULL);
        if (!active_replay_capability_.initialized) {
            return FailStartup(active_replay_capability_.error,
                "The initialized encoder did not match the requested codec, "
                "backend, or selected capture adapter.");
        }

        {
            // Retain requested history plus the encoder's maximum four-second
            // GOP pre-roll and one second for asynchronous publication jitter.
            // Unlike the former 2x policy, memory no longer scales with a
            // second complete copy of long (up to 300-second) replay history.
            const size_t capacity = CalculateEncodedReplaySlotCapacity(
                buffer_seconds_, fps_);

            LARGE_INTEGER qpc_freq;
            QueryPerformanceFrequency(&qpc_freq);
            encoded_ring_ = std::make_unique<EncodedRingBuffer>(capacity, fps_, qpc_freq.QuadPart);

            // Publish codec, geometry, timing, packet format and decoder
            // configuration as one immutable stream description.
            const auto video_config = replay_encoder_->GetVideoConfig();
            if (replay_encoder_->IsVideoConfigReady()) {
                if (!encoded_ring_->SetVideoConfig(video_config)) {
                    std::cerr << "[CaptureEngine] Could not publish immutable encoded "
                                 "stream configuration" << std::endl;
                    return false;
                }
            } else {
                std::cout << "[CaptureEngine] Decoder configuration will be "
                             "published with the first encoded packet" << std::endl;
            }

            // max_frames_ used for stats - set to time-based count.
            // ring_head_ / ring_count_ not used on NVENC path.
            max_frames_ = static_cast<size_t>(buffer_seconds_) * fps_;

            const auto active = replay_encoder_->GetActiveEncoderInfo();
            std::cout << "[CaptureEngine] " << active.name
                << " active. Encoded ring: " << capacity
                << " slots. Raw FramePool: skipped." << std::endl;
        }

        // ------------------------------------------------------------------
        // Initialize audio capture pipeline BEFORE starting video thread.
        //
        // Persistent AAC replay design:
        //   AudioCapture (WASAPI) -> AudioEncoder -> EncodedAudioPacketRing.
        //   The bounded compressed ring protects 300-second replay memory.
        //   Save snapshots packets directly; it never rebuilds a long raw-PCM
        //   window on the save thread.
        //
        // Audio is optional - failure falls through to video-only mode.
        // Skipped entirely when config.audio_enabled = false (user disabled in Settings).
        // ------------------------------------------------------------------
        if (!config.audio_enabled) {
            std::cout << "[CaptureEngine] Audio capture disabled by user settings." << std::endl;
        }
        else do {
            AudioCaptureConfig audio_cfg;
            audio_cfg.bitrate_kbps = 128;

            if (!audio_capture_.Initialize(nullptr, audio_cfg)) {
                std::cerr << "[CaptureEngine] AudioCapture init failed - "
                    << "audio disabled" << std::endl;
                break;
            }

            std::string default_mix_uuid;
            std::string default_mix_uuid_error;
            if (!CreateAudioManifestTransactionId(
                    &default_mix_uuid, &default_mix_uuid_error)) {
                std::cerr << "[CaptureEngine] Could not create Default Mix source UUID: "
                    << default_mix_uuid_error << std::endl;
                audio_capture_.Shutdown();
                break;
            }
            AudioSourceId default_mix_id{default_mix_uuid};
            default_mix_audio_source_ = {};
            default_mix_audio_source_.identity.id = default_mix_id;
            default_mix_audio_source_.identity.type = AudioSourceType::System;
            default_mix_audio_source_.identity.persistent_identity = "default-mix";
            default_mix_audio_source_.identity.display_name = "Default Mix";
            default_mix_audio_source_.identity.icon_reference = "system-audio";
            default_mix_audio_source_.format = {
                audio_capture_.GetSampleRate(), audio_capture_.GetChannels(), "fltp"};
            default_mix_audio_source_.state.health = AudioSourceHealth::Active;
            default_mix_audio_source_.state.admitted = true;
            default_mix_audio_source_.state.active_in_generation = true;
            default_mix_audio_ring_ = std::make_unique<EncodedAudioPacketRing>(
                default_mix_id,
                capture_generation_.load(std::memory_order_relaxed) + 1,
                AudioSourceFormat{audio_capture_.GetSampleRate(),
                                  audio_capture_.GetChannels(), "fltp"},
                buffer_seconds_);
            if (!default_mix_audio_encoder_.Initialize(
                    audio_capture_.GetSampleRate(), audio_capture_.GetChannels(),
                    audio_cfg.bitrate_kbps,
                    [this](const uint8_t* data, uint32_t size, int64_t pts) {
                        if (default_mix_audio_ring_) {
                            default_mix_audio_ring_->Push(
                                {std::vector<uint8_t>(data, data + size), pts, 1024});
                        }

                        std::shared_ptr<ContinuousRecordingWriter> writer;
                        {
                            std::lock_guard<std::mutex> lock(record_writer_mutex_);
                            writer = record_writer_;
                        }
                        if (writer && !writer->PushAudio(data, size, pts, 1024)) {
                            is_recording_.store(false, std::memory_order_release);
                        }
                    })) {
                std::cerr << "[CaptureEngine] Persistent AAC encoder init failed - "
                    << "audio disabled" << std::endl;
                audio_capture_.Shutdown();
                default_mix_audio_ring_.reset();
                break;
            }
            default_mix_audio_ring_->SetCodecExtradata(
                default_mix_audio_encoder_.GetExtradata());
            audio_capture_.SetEncoder(&default_mix_audio_encoder_);

            if (!audio_capture_.Start()) {
                std::cerr << "[CaptureEngine] AudioCapture start failed - "
                    << "audio disabled" << std::endl;
                audio_capture_.Shutdown();
                default_mix_audio_encoder_.Finalize();
                default_mix_audio_ring_.reset();
                break;
            }

            audio_active_ = true;
            std::cout << "[CaptureEngine] Audio capture active ("
                << audio_capture_.GetSampleRate() << "Hz, "
                << audio_capture_.GetChannels() << "ch, "
                << "persistent AAC packet replay)"
                << std::endl;

            std::string microphone_uuid;
            std::string microphone_uuid_error;
            if (!CreateAudioManifestTransactionId(&microphone_uuid, &microphone_uuid_error)) {
                std::cerr << "[CaptureEngine] Could not create Microphone source UUID: "
                    << microphone_uuid_error << std::endl;
            } else {
                microphone_audio_metadata_ = {};
                microphone_audio_metadata_.identity.id = AudioSourceId{microphone_uuid};
                microphone_audio_metadata_.identity.type = AudioSourceType::Microphone;
                microphone_audio_metadata_.identity.persistent_identity = "microphone";
                microphone_audio_metadata_.identity.display_name = "Microphone";
                microphone_audio_metadata_.identity.icon_reference = "microphone";
                microphone_audio_metadata_.format = {
                    kCanonicalAudioSampleRate, kCanonicalAudioChannels, "fltp"};
                microphone_audio_metadata_.state.health = AudioSourceHealth::Discovered;

                WindowsMicrophoneAudioProviderConfig microphone_config;
                microphone_config.generation = capture_generation_.load(
                    std::memory_order_relaxed) + 1;
                microphone_config.endpoint_id = config.microphone_endpoint_id;
                microphone_config.use_default_endpoint = config.microphone_endpoint_id.empty();
                microphone_config.retention_seconds = buffer_seconds_;
                microphone_config.bitrate_kbps = 96;
                microphone_config.input_gain = std::clamp(
                    static_cast<float>(config.microphone_gain_percent) / 100.0f,
                    0.0f, 2.0f);
                microphone_config.source = microphone_audio_metadata_;
                microphone_audio_source_ = std::make_unique<WindowsMicrophoneAudioProvider>(
                    std::move(microphone_config));
                if (!microphone_audio_source_->Start()) {
                    std::cerr << "[CaptureEngine] Native microphone provider did not start: "
                        << microphone_audio_source_->last_error() << std::endl;
                    microphone_audio_source_.reset();
                } else {
                    std::cout << "[CaptureEngine] Native microphone provider starting ("
                        << (config.microphone_endpoint_id.empty() ? "Default microphone"
                                                                   : "explicit endpoint")
                        << ")." << std::endl;
                }
            }

            // Per-application stems are retired. The capture contract is one
            // system loopback stream plus one microphone stream.
            std::cout << "[CaptureEngine] Audio routing: system mix + microphone."
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
        stall_watchdog_thread_ = new std::thread(
            &CaptureEngine::ReplayStallWatchdogThread, this);

        std::cout << "[CaptureEngine] Running." << std::endl;
        return true;
    }


    // ===========================================================================
    // Shutdown
    // ===========================================================================

    void CaptureEngine::Shutdown() {
        const bool has_resources = capture_thread_ || stall_watchdog_thread_
            || save_clip_thread_
            || device_ || context_ || wgc_state_
            || replay_encoder_ || audio_active_ || nvenc_device_
            || nvenc_context_ || record_writer_;
        if (!has_resources) return;

        std::cout << "[CaptureEngine] Shutting down..." << std::endl;

        bool has_recording_writer = false;
        {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            has_recording_writer = static_cast<bool>(record_writer_);
        }
        if (is_recording_.load() || has_recording_writer) StopRecording();

        running_.store(false);

        // If CaptureThread is blocked waiting for a WGC frame, wake it so it exits.
        wgc_frame_cv_.notify_all();

        save_clip_queue_.Shutdown();

        if (stall_watchdog_thread_) {
            stall_watchdog_thread_->join();
            delete stall_watchdog_thread_;
            stall_watchdog_thread_ = nullptr;
        }

        if (capture_thread_) {
            capture_thread_->join();
            delete capture_thread_;
            capture_thread_ = nullptr;
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
        nvenc_active_ = false;
        replay_encoder_cpu_input_ = false;

        // Stop audio pipeline. Order matters:
        //   1. Stop WASAPI thread (no more EncodeSamples calls after this)
        //   2. Finalize encoder (flushes partial AAC frame)
        //   3. compressed packet ring is released with the generation
        if (audio_active_) {
            microphone_audio_source_.reset();
            audio_capture_.Stop();
            default_mix_audio_encoder_.Finalize();
            audio_capture_.Shutdown();
            default_mix_audio_ring_.reset();
            default_mix_audio_source_ = {};
            microphone_audio_metadata_ = {};
            audio_active_ = false;
            std::cout << "[CaptureEngine] Audio pipeline stopped." << std::endl;
        }
        else {
            microphone_audio_source_.reset();
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
        replay_encoder_.reset();
        encoded_ring_.reset();

        std::cout << "[CaptureEngine] Shutdown complete. Frames captured: "
            << frames_captured_.load() << std::endl;
    }

    void CaptureEngine::ReplayStallWatchdogThread() {
        using clock = std::chrono::steady_clock;
        uint64_t previous_packets = 0;
        uint64_t previous_acquire_attempts = 0;
        auto last_progress = clock::now();
        auto last_acquire_progress = clock::now();
        bool snapshot_emitted = false;

        while (running_.load(std::memory_order_relaxed)) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            const uint64_t packets = video_packets_produced_.load(
                std::memory_order_relaxed);
            const uint64_t acquire_attempts = capture_acquire_attempts_.load(
                std::memory_order_relaxed);
            const auto now = clock::now();
            if (acquire_attempts != previous_acquire_attempts) {
                previous_acquire_attempts = acquire_attempts;
                last_acquire_progress = now;
            }
            if (packets != previous_packets) {
                previous_packets = packets;
                last_progress = now;
                snapshot_emitted = false;
                continue;
            }
            if (packets == 0 || snapshot_emitted
                || now - last_progress <= std::chrono::seconds(2)) {
                continue;
            }

            const auto encoder = replay_encoder_
                ? replay_encoder_->GetDiagnostics()
                : ReplayEncoderDiagnostics{};
            const bool dxgi_is_alive_but_desktop_is_static =
                static_cast<HRESULT>(last_capture_hresult_.load(
                    std::memory_order_relaxed)) == DXGI_ERROR_WAIT_TIMEOUT
                && now - last_acquire_progress < std::chrono::seconds(1)
                && encoder.pending_resources == 0
                && encoder.queued_outputs == 0
                && encoder.submit_stage == 0
                && encoder.drain_stage == 0;
            if (dxgi_is_alive_but_desktop_is_static) {
                // Desktop Duplication reports only changed frames. Repeated
                // WAIT_TIMEOUT with a live acquire loop and an empty encoder
                // is expected and must not be diagnosed as a replay stall.
                last_progress = now;
                continue;
            }
            const HRESULT removed_reason = device_
                ? device_->GetDeviceRemovedReason()
                : E_POINTER;
            std::cerr
                << "[ReplayStall] no encoded packet for >2s"
                << " capture_stage=" << capture_thread_stage_.load()
                << " loops=" << capture_loop_iterations_.load()
                << " acquire_attempts=" << capture_acquire_attempts_.load()
                << " acquired=" << capture_acquire_successes_.load()
                << " timeouts=" << capture_timeouts_.load()
                << " released=" << capture_frames_released_.load()
                << " owned=" << (capture_acquire_successes_.load()
                    - capture_frames_released_.load())
                << " textures=" << source_textures_received_.load()
                << " conversion_submit=" << conversion_submissions_.load()
                << " conversion_complete=" << conversion_completions_.load()
                << " frames=" << frames_captured_.load()
                << " packets=" << video_packets_produced_.load()
                << " ring_push=" << video_ring_insertions_.load()
                << " last_hr=0x" << std::hex
                << static_cast<uint32_t>(last_capture_hresult_.load())
                << " removed_reason=0x"
                << static_cast<uint32_t>(removed_reason) << std::dec
                << " encoder_submit_stage=" << encoder.submit_stage
                << " encoder_drain_stage=" << encoder.drain_stage
                << " slots_acquired=" << encoder.input_slots_acquired
                << " map=" << encoder.maps_succeeded << '/'
                << encoder.map_attempts
                << " mapped_now=" << encoder.mapped_resources
                << " encode_return=" << encoder.encode_returns << '/'
                << encoder.encode_attempts
                << " encode_ok=" << encoder.encode_successes
                << " drain=" << encoder.drain_dequeues
                << " completion=" << encoder.completion_events
                << " lock=" << encoder.bitstream_locks << '/'
                << encoder.bitstream_lock_attempts
                << " locked_now=" << encoder.locked_bitstreams
                << " unlock=" << encoder.bitstream_unlocks
                << " unmap=" << encoder.resources_unmapped
                << " recycled=" << encoder.slots_recycled
                << " pending=" << encoder.pending_resources
                << " queue=" << encoder.queued_outputs
                << " pool=" << encoder.pool_capacity
                << " registered=" << encoder.registered_resources
                << " nvenc_status=" << encoder.last_nvenc_status
                << std::endl;
            snapshot_emitted = true;
        }
    }


    // ===========================================================================
    // StartRecording / StopRecording
    // ===========================================================================

    bool CaptureEngine::StartRecording(const wchar_t* path) {
        if (is_recording_.load(std::memory_order_acquire) || !running_.load()
            || !path || !*path || !replay_encoder_ || !encoded_ring_) {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            last_recording_error_ =
                "Replay capture is not ready for a manual recording.";
            return false;
        }
        {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            if (record_writer_) {
                last_recording_error_ = record_writer_->HasFailed()
                    ? record_writer_->LastError()
                    : "A manual recording is already active.";
                return false;
            }
        }

        const EncodedVideoConfig video_config = replay_encoder_->GetVideoConfig();
        if (!replay_encoder_->IsVideoConfigReady()
            || !IsValidEncodedVideoConfig(video_config)
            || video_config.codec_extradata.empty()) {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            last_recording_error_ =
                "The hardware video stream is still starting. Try again in a moment.";
            return false;
        }

        ContinuousRecordingAudioConfig audio_config;
        if (audio_active_ && default_mix_audio_encoder_.IsInitialized()) {
            audio_config.sample_rate = default_mix_audio_encoder_.GetSampleRate();
            audio_config.channels = default_mix_audio_encoder_.GetChannels();
            audio_config.codec_extradata =
                default_mix_audio_encoder_.GetExtradata();
        }

        auto writer = std::make_shared<ContinuousRecordingWriter>();
        if (!writer->Start(std::filesystem::path(path), video_config, audio_config)) {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            last_recording_error_ = writer->LastError();
            return false;
        }
        {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            record_writer_ = std::move(writer);
            last_recording_error_.clear();
        }
        is_recording_.store(true, std::memory_order_release);
        std::cout << "[CaptureEngine] Packet-stream recording started." << std::endl;
        return true;
    }

    bool CaptureEngine::StopRecording() {
        is_recording_.store(false, std::memory_order_release);
        std::shared_ptr<ContinuousRecordingWriter> writer;
        {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            writer = std::move(record_writer_);
        }
        if (!writer) return true;

        const bool saved = writer->Stop();
        {
            std::lock_guard<std::mutex> lock(record_writer_mutex_);
            last_recording_error_ = saved ? std::string{} : writer->LastError();
            if (!saved && last_recording_error_.empty()) {
                last_recording_error_ =
                    "The recording contained no complete video fragment.";
            }
        }
        std::cout << "[CaptureEngine] Packet-stream recording "
                  << (saved ? "stopped." : "closed with a recoverable error.")
                  << std::endl;
        return saved;
    }

    bool CaptureEngine::IsRecording() const {
        if (!is_recording_.load(std::memory_order_acquire)) return false;
        std::lock_guard<std::mutex> lock(record_writer_mutex_);
        return record_writer_ && record_writer_->IsRunning();
    }

    std::string CaptureEngine::GetLastRecordingError() const {
        std::lock_guard<std::mutex> lock(record_writer_mutex_);
        if (record_writer_ && record_writer_->HasFailed()) {
            const std::string writer_error = record_writer_->LastError();
            if (!writer_error.empty()) return writer_error;
        }
        return last_recording_error_;
    }

    uint64_t CaptureEngine::GetFrameCount() const {
        return frames_captured_.load(std::memory_order_relaxed);
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
            task.enc_width = (target_width_ > 0) ? target_width_ : crop_width_;
            task.enc_height = (target_height_ > 0) ? target_height_ : crop_height_;
            task.fps = fps_;
            task.separate_audio_enabled = separate_audio_enabled_;
            task.shared_memory = shared_memory;
            task.task_id = next_task_id_.fetch_add(1);

            // Populate the QPC epoch so MuxEncodedClip can convert video PTS
            // to wall-clock seconds and align audio to it exactly.
            replay_encoder_->GetEncodeEpoch(
                task.video_qpc_epoch, task.video_qpc_freq);

            // Snapshot the persistent AAC packet ring against the exact video
            // presentation interval. Audio PTS are sample positions relative
            // to the first timestamped WASAPI packet in this generation.
            if (audio_active_ && default_mix_audio_ring_
                    && !audio_capture_.IsDeviceLost()) {
                const uint64_t origin_qpc = audio_capture_.GetTimelineOriginQpc100ns();
                const double start_qpc_s = task.encoded_snapshot.presentation_start_qpc_s;
                const double end_qpc_s = task.encoded_snapshot.presentation_end_qpc_s;
                const uint32_t sample_rate = audio_capture_.GetSampleRate();
                if (origin_qpc > 0 && start_qpc_s > 0.0 && end_qpc_s > start_qpc_s
                        && sample_rate > 0) {
                    const auto range = MapAudioSourcePresentationRange(
                        start_qpc_s, end_qpc_s, origin_qpc, sample_rate);
                    const int64_t start_pts = std::max<int64_t>(0, range.start_pts_samples);
                    const int64_t end_pts = std::max(start_pts + 1, range.end_pts_samples);
                    task.encoded_audio_snapshot = default_mix_audio_ring_->TakeSnapshot(
                        start_pts, end_pts);
                    task.audio_presentation_start_pts_samples = start_pts;
                    task.has_encoded_audio = task.encoded_audio_snapshot.valid();
                    if (task.has_encoded_audio) {
                        EncodedAudioTrack default_mix_track;
                        default_mix_track.source = default_mix_audio_source_;
                        default_mix_track.snapshot = task.encoded_audio_snapshot;
                        default_mix_track.presentation_start_pts_samples = start_pts;
                        // The source interval reflects the packet timeline that
                        // actually survived in this clip, not a guessed requested
                        // duration.  Default Mix is a real aggregate loopback
                        // stream and therefore remains present even for silence.
                        default_mix_track.source.state.first_active_100ns =
                            AudioSamplePositionToTimeline100ns(
                                origin_qpc,
                                default_mix_track.snapshot.first_pts_samples,
                                sample_rate);
                        default_mix_track.source.state.last_active_100ns =
                            AudioSamplePositionToTimeline100ns(
                                origin_qpc,
                                default_mix_track.snapshot.last_pts_samples,
                                sample_rate);
                        task.encoded_audio_tracks.push_back(std::move(default_mix_track));
                        if (microphone_audio_source_) {
                            if (const auto microphone_track =
                                    microphone_audio_source_->TakeTrackForInterval(
                                        start_qpc_s, end_qpc_s, microphone_audio_metadata_)) {
                                task.encoded_audio_tracks.push_back(*microphone_track);
                            } else if (!microphone_audio_source_->last_error().empty()) {
                                std::cerr << "[SaveClip] Native microphone unavailable: "
                                    << microphone_audio_source_->last_error() << std::endl;
                            }
                        }
                        std::cout << "[SaveClip] Persistent AAC snapshot: "
                            << task.encoded_audio_snapshot.packets.size()
                            << " packets, PTS " << start_pts << " - " << end_pts
                            << std::endl;
                    } else {
                        std::cerr << "[SaveClip] Default Mix AAC ring had no packets for "
                            << "presentation PTS " << start_pts << " - " << end_pts
                            << std::endl;
                    }
                } else {
                    std::cerr << "[SaveClip] Default Mix timeline is not ready; origin="
                        << origin_qpc << ", video interval=" << start_qpc_s << " - "
                        << end_qpc_s << ", sample_rate=" << sample_rate << std::endl;
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
        task.src_width = crop_width_;
        task.src_height = crop_height_;
        task.enc_width = target_width_;
        task.enc_height = target_height_;
        task.fps = fps_;
        task.bitrate_kbps = bitrate_kbps_;
        task.scaling_mode = scaling_mode_;
        task.separate_audio_enabled = separate_audio_enabled_;
        task.shared_memory = shared_memory;
        task.task_id = next_task_id_.fetch_add(1);

        // The public alpha does not automatically select the raw-video path.
        // Preserve its video-only behavior instead of reintroducing a long
        // raw PCM ring solely for a non-production fallback.
        if (audio_active_ && audio_capture_.IsDeviceLost()) {
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
        if (task.use_encoded_path && !task.encoded_audio_tracks.empty()) {
            std::string transaction_id;
            std::string transaction_error;
            if (!CreateAudioManifestTransactionId(&transaction_id, &transaction_error)) {
                SetEngineError(task.shared_memory,
                    L"Could not prepare the audio manifest transaction for this clip.");
                std::cerr << "[SaveClip] " << transaction_error << std::endl;
                return false;
            }
            const auto manifest_path = AudioManifestPathFor(task.output_path);
            const auto result = transactional_save::RunPair(
                task.output_path,
                manifest_path,
                [this, &task, &transaction_id](
                    const std::filesystem::path& media_temporary_path,
                    const std::filesystem::path& manifest_temporary_path,
                    std::string& writer_error) {
                    if (!MuxEncodedClip(task, media_temporary_path.wstring())) {
                        writer_error = "The temporary clip could not be muxed.";
                        return false;
                    }
                    return WriteClipAudioManifest(
                        task.output_path,
                        media_temporary_path,
                        manifest_temporary_path,
                        transaction_id,
                        task.encoded_audio_tracks,
                        &writer_error);
                });
            if (result.cleanup_error) {
                std::wcerr << L"[SaveClip] Secondary paired cleanup failure for "
                    << result.media_temporary_path.wstring() << L": "
                    << result.cleanup_error.value() << std::endl;
            }
            if (result.success) return true;
            if (result.failure != transactional_save::PairFailure::Writer
                    || !result.writer_error.empty()) {
                const std::string detail = transactional_save::DescribeFailure(result);
                const int required = MultiByteToWideChar(
                    CP_UTF8, 0, detail.c_str(), -1, nullptr, 0);
                std::wstring wide_detail;
                if (required > 0) {
                    wide_detail.resize(static_cast<size_t>(required));
                    MultiByteToWideChar(CP_UTF8, 0, detail.c_str(), -1,
                        wide_detail.data(), required);
                }
                SetEngineError(task.shared_memory,
                    wide_detail.empty()
                        ? L"The clip and its audio manifest could not be finalized."
                        : wide_detail.c_str());
            }
            return false;
        }

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
        // Older replay generations could retain substantially more video than
        // audio. Without this correction, both streams started at "sample 0"
        // while representing different wall-clock moments, causing A/V desync.
        // Current timestamp-aware snapshots and bounded GOP headroom avoid that
        // imbalance; keep this branch only for old-style snapshots.
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

        // ------------------------------------------------------------------
        // Step C: Compute PTS normalization offset.
        //
        // The physical decode start can precede the requested presentation
        // start. Subtract the logical boundary so pre-roll packets retain
        // negative PTS and the MP4 edit list hides them without re-encoding.
        // ------------------------------------------------------------------

        // Legacy raw PCM remains only for the non-production compatibility path.
        // The normal AUDIT-050 contract carries one Default Mix plus actual
        // source tracks in their own persistent AAC packet snapshots.
        const bool write_legacy_audio = task.has_audio
            && task.audio_snapshot.valid
            && !task.audio_snapshot.samples.empty();
        const bool has_contract_audio_tracks = !task.encoded_audio_tracks.empty();
        const bool write_packet_audio = has_contract_audio_tracks
            || (task.has_encoded_audio && task.encoded_audio_snapshot.valid());

        const int64_t pts_offset = snap.presentation_start_pts;

        const size_t usable_count = snap.packets.size() - keyframe_start;

        // Trimmed video clip duration in seconds.
        // Keep the source wall-clock span. A sparse capture must never be
        // shortened by converting its packet count directly into duration.
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

        if (write_legacy_audio) {
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
        ApplySdrBt709ColorMetadata(video_stream->codecpar);

        // High-resolution MP4 stream timebase; packet timestamps are rescaled
        // from the encoder-provided time base below.
        video_stream->time_base = AVRational{ 1, 90000 };
        ApplyConfiguredVideoMetadata(fmt_ctx, video_stream, video_config);

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
        struct MuxAudioTrack {
            AudioSourceMetadata metadata;
            AVStream* stream = nullptr;
            std::vector<std::vector<uint8_t>> packets;
            std::vector<int64_t> packet_pts;
            std::vector<int64_t> packet_durations;
            std::vector<uint8_t> extradata;
            uint32_t sample_rate = 0;
            uint32_t channels = 0;
            int64_t output_pts_offset = 0;
            bool is_default_mix = false;
        };
        std::vector<MuxAudioTrack> mux_audio_tracks;

        const auto append_persistent_track = [&](const EncodedAudioTrack& contract,
                                                  bool is_default_mix) {
            MuxAudioTrack output;
            output.metadata = contract.source;
            output.sample_rate = contract.snapshot.format.sample_rate;
            output.channels = contract.snapshot.format.channels;
            output.extradata = contract.snapshot.codec_extradata;
            output.output_pts_offset = -contract.presentation_start_pts_samples;
            output.is_default_mix = is_default_mix;
            for (const auto& packet : contract.snapshot.packets) {
                if (packet.data.empty() || packet.duration_samples <= 0) continue;
                output.packets.push_back(packet.data);
                output.packet_pts.push_back(packet.pts_samples);
                output.packet_durations.push_back(packet.duration_samples);
            }
            if (!output.packets.empty()) mux_audio_tracks.push_back(std::move(output));
        };

        if (has_contract_audio_tracks) {
            if (task.encoded_audio_tracks.size() > kMaxClipAudioTracks) {
                SetEngineError(task.shared_memory,
                    L"The clip contains more audio tracks than the supported alpha limit.");
                avformat_free_context(fmt_ctx);
                return false;
            }
            for (size_t index = 0; index < task.encoded_audio_tracks.size(); ++index) {
                const auto& contract = task.encoded_audio_tracks[index];
                if (!contract.valid()) {
                    SetEngineError(task.shared_memory,
                        L"An audio track no longer matches this capture generation.");
                    avformat_free_context(fmt_ctx);
                    return false;
                }
                const bool is_default_mix = index == 0;
                if (is_default_mix
                        && (contract.source.identity.type != AudioSourceType::System
                            || contract.source.identity.persistent_identity != "default-mix"
                            || contract.source.identity.display_name != "Default Mix")) {
                    SetEngineError(task.shared_memory,
                        L"The Default Mix track is missing from the audio capture contract.");
                    avformat_free_context(fmt_ctx);
                    return false;
                }
                append_persistent_track(contract, is_default_mix);
            }
            if (mux_audio_tracks.empty()) {
                SetEngineError(task.shared_memory,
                    L"No valid AAC packets were available for the captured audio tracks.");
                avformat_free_context(fmt_ctx);
                return false;
            }
            std::cout << "[MuxEncodedClip] Using persistent AAC replay: "
                << mux_audio_tracks.size() << " track(s)" << std::endl;
        }
        else if (write_packet_audio) {
            // Compatibility for a queued task created before the structured
            // contract was introduced. New saves always use the branch above.
            EncodedAudioTrack legacy;
            legacy.snapshot = task.encoded_audio_snapshot;
            legacy.presentation_start_pts_samples = task.audio_presentation_start_pts_samples;
            legacy.source.identity.id = legacy.snapshot.source_id;
            legacy.source.identity.type = AudioSourceType::System;
            legacy.source.identity.persistent_identity = "default-mix";
            legacy.source.identity.display_name = "Default Mix";
            legacy.source.identity.icon_reference = "system-audio";
            legacy.source.format = legacy.snapshot.format;
            legacy.source.state.admitted = true;
            legacy.source.state.first_active_100ns = 0;
            legacy.source.state.last_active_100ns = 0;
            append_persistent_track(legacy, true);
        }
        else if (write_legacy_audio) {
            MuxAudioTrack output;
            const uint32_t sr = task.audio_snapshot.sample_rate;
            const uint32_t ch = task.audio_snapshot.channels;
            output.sample_rate = sr;
            output.channels = ch;
            output.is_default_mix = true;
            AudioEncoder aac_enc;
            bool enc_ok = aac_enc.Initialize(
                sr, ch, task.audio_bitrate_kbps,
                [&](const uint8_t* data, uint32_t size, int64_t pts) {
                    output.packets.push_back(std::vector<uint8_t>(data, data + size));
                    output.packet_pts.push_back(pts);
                    output.packet_durations.push_back(1024);
                });
            if (enc_ok) {
                const int64_t total_frames = static_cast<int64_t>(
                    task.audio_snapshot.samples.size()) / static_cast<int64_t>(ch);
                const int64_t frames_to_encode = total_frames - audio_aligned_start_sample;
                if (frames_to_encode > 0) {
                    const float* pcm_start = task.audio_snapshot.samples.data()
                        + static_cast<size_t>(audio_aligned_start_sample) * ch;
                    aac_enc.EncodeSamples(pcm_start,
                        static_cast<uint32_t>(frames_to_encode) * ch);
                }
                aac_enc.Finalize();
                output.extradata = aac_enc.GetExtradata();
                output.output_pts_offset = audio_output_pts_offset;
                if (!output.packets.empty()) mux_audio_tracks.push_back(std::move(output));
            }
        }

        for (auto& audio : mux_audio_tracks) {
            audio.stream = avformat_new_stream(fmt_ctx, nullptr);
            if (!audio.stream) {
                SetEngineError(task.shared_memory,
                    L"Could not create the clip audio stream.");
                avformat_free_context(fmt_ctx);
                return false;
            }
            audio.stream->codecpar->codec_type = AVMEDIA_TYPE_AUDIO;
            audio.stream->codecpar->codec_id = AV_CODEC_ID_AAC;
            audio.stream->codecpar->sample_rate = static_cast<int>(audio.sample_rate);
            audio.stream->codecpar->ch_layout.nb_channels = static_cast<int>(audio.channels);
            audio.stream->codecpar->ch_layout.order = AV_CHANNEL_ORDER_UNSPEC;
            audio.stream->codecpar->frame_size = 1024;
            audio.stream->codecpar->format = AV_SAMPLE_FMT_FLTP;
            audio.stream->time_base = AVRational{1, static_cast<int>(audio.sample_rate)};
            if (audio.is_default_mix) audio.stream->disposition |= AV_DISPOSITION_DEFAULT;
            const std::string name = audio.is_default_mix
                ? "Default Mix" : audio.metadata.identity.display_name;
            av_dict_set(&audio.stream->metadata, "handler_name", name.c_str(), 0);
            av_dict_set(&audio.stream->metadata, "title", name.c_str(), 0);
            if (!audio.extradata.empty()) {
                audio.stream->codecpar->extradata = static_cast<uint8_t*>(
                    av_malloc(audio.extradata.size() + AV_INPUT_BUFFER_PADDING_SIZE));
                if (!audio.stream->codecpar->extradata) {
                    SetEngineError(task.shared_memory,
                        L"Could not allocate clip audio stream metadata.");
                    avformat_free_context(fmt_ctx);
                    return false;
                }
                memcpy(audio.stream->codecpar->extradata,
                    audio.extradata.data(), audio.extradata.size());
                memset(audio.stream->codecpar->extradata + audio.extradata.size(),
                    0, AV_INPUT_BUFFER_PADDING_SIZE);
                audio.stream->codecpar->extradata_size = static_cast<int>(audio.extradata.size());
            }
        }

        // The native publication is source-preserving. Combined mode is
        // collapsed by the UI after the file is committed; this marker keeps
        // an interrupted/failing finalization truthful about the current MP4
        // topology so the editor never invents a single-track interpretation.
        const bool native_audio_is_separated = task.separate_audio_enabled
            || mux_audio_tracks.size() > 1;
        av_dict_set(&fmt_ctx->metadata, "comment",
            native_audio_is_separated
                ? "fthr-audio-mode=separated"
                : "fthr-audio-mode=combined", 0);

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

        AVDictionary* output_options = nullptr;
        av_dict_set(&output_options, "movflags", "use_metadata_tags", 0);
        ret = avformat_write_header(fmt_ctx, &output_options);
        av_dict_free(&output_options);
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
            << (mux_audio_tracks.empty() ? "NO" : "YES")
            << " (" << mux_audio_tracks.size() << " track(s))" << std::endl;

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

            // Log first 3 packets before rescaling.
            if (video_packet_count < 3) {
                std::cout << "[VPkt #" << video_packet_count << " BEFORE rescale] "
                    << "PTS=" << av_pkt->pts
                    << " DTS=" << av_pkt->dts
                    << " duration=" << av_pkt->duration
                    << (pkt.is_keyframe ? " [KEYFRAME]" : "")
                    << std::endl;
            }

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
        // Step 4b: Write each pre-encoded AAC source track. Every source uses
        // its own sample-rate timebase and preserves its AAC packet duration;
        // there is no cross-source downmix or re-encode in the save path.
        // ------------------------------------------------------------------
        int audio_packet_count = 0;

        for (const auto& audio : mux_audio_tracks) {
            if (!audio.stream || audio.packets.empty()) continue;
            int track_packet_count = 0;
            int64_t total_track_samples = 0;
            for (size_t i = 0; i < audio.packets.size(); i++) {
                const auto& pkt_data = audio.packets[i];
                if (pkt_data.empty() || i >= audio.packet_pts.size()
                        || i >= audio.packet_durations.size()) continue;

                ret = av_new_packet(av_pkt, static_cast<int>(pkt_data.size()));
                if (ret < 0)
                    return fail_media_write(L"allocating an audio packet for", ret);

                memcpy(av_pkt->data, pkt_data.data(), pkt_data.size());
                av_pkt->pts = audio.packet_pts[i] + audio.output_pts_offset;
                av_pkt->dts = audio.packet_pts[i] + audio.output_pts_offset;
                av_pkt->duration = audio.packet_durations[i];
                av_pkt->stream_index = audio.stream->index;
                av_pkt->flags = 0;

                ret = av_interleaved_write_frame(fmt_ctx, av_pkt);
                av_packet_unref(av_pkt);
                if (ret < 0)
                    return fail_media_write(L"writing audio data to", ret);
                audio_packet_count++;
                track_packet_count++;
                total_track_samples += audio.packet_durations[i];
            }

            const double audio_written_s = audio.sample_rate > 0
                ? static_cast<double>(total_track_samples) / audio.sample_rate : 0.0;
            std::cout << "[MuxEncodedClip] Audio track '"
                << (audio.is_default_mix ? "Default Mix" : audio.metadata.identity.display_name)
                << "': " << track_packet_count << " packets ("
                << audio_written_s << "s)" << std::endl;
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
        replay_config_publish_failed_.store(false);
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
    //   Compressed hardware path: copy BGRA on-GPU into the replay encoder
    //               (native NVENC, FFmpeg AMF, or FFmpeg QSV), then EncodeFrame()
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

        FrameRateScheduler frame_scheduler(target_qpc);

        std::cout << "[CaptureThread] " << fps_ << " fps ("
            << target_ms << " ms/frame)  "
            << (nvenc_active_ ? GetActiveEncoderName() : "software")
            << " path" << std::endl;

        uint32_t consecutive_acquire_errors = 0;

        while (running_.load(std::memory_order_relaxed)) {

            capture_loop_iterations_.fetch_add(1, std::memory_order_relaxed);

            DXGI_OUTDUPL_FRAME_INFO info{};
            IDXGIResource* resource = nullptr;

            capture_thread_stage_.store(1, std::memory_order_relaxed);
            capture_acquire_attempts_.fetch_add(1, std::memory_order_relaxed);
            HRESULT hr = duplication_->AcquireNextFrame(33, &info, &resource);
            last_capture_hresult_.store(
                static_cast<int32_t>(hr), std::memory_order_relaxed);

            if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
                capture_timeouts_.fetch_add(1, std::memory_order_relaxed);
                capture_thread_stage_.store(0, std::memory_order_relaxed);
                continue;
            }

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
            capture_acquire_successes_.fetch_add(1, std::memory_order_relaxed);
            capture_thread_stage_.store(2, std::memory_order_relaxed);

            // Frame rate limiting BEFORE QueryInterface.
            // At high game FPS (e.g. 300fps, 60fps target) most frames are dropped.
            // QueryInterface is a COM call with real overhead; paying it on every
            // dropped frame wastes ~240 roundtrips/sec. Checking QPC first means
            // dropped frames cost only resource->Release() + ReleaseFrame().
            LARGE_INTEGER now;
            QueryPerformanceCounter(&now);
            if (!frame_scheduler.ShouldCapture(now.QuadPart)) {
                resource->Release();
                duplication_->ReleaseFrame();
                capture_frames_released_.fetch_add(1, std::memory_order_relaxed);
                capture_thread_stage_.store(0, std::memory_order_relaxed);
                frames_dropped_.fetch_add(1, std::memory_order_relaxed);
                continue;
            }

            ID3D11Texture2D* tex = nullptr;
            hr = resource->QueryInterface(__uuidof(ID3D11Texture2D),
                reinterpret_cast<void**>(&tex));
            resource->Release();

            if (FAILED(hr)) {
                duplication_->ReleaseFrame();
                capture_frames_released_.fetch_add(1, std::memory_order_relaxed);
                capture_thread_stage_.store(0, std::memory_order_relaxed);
                continue;
            }
            source_textures_received_.fetch_add(1, std::memory_order_relaxed);
            capture_thread_stage_.store(3, std::memory_order_relaxed);

            if (nvenc_active_ && !replay_encoder_cpu_input_) {
                // ----------------------------------------------------------
                // Same-adapter compressed replay path (native NVENC or AMF).
                // CopyResource is a pure GPU op; there is no full-frame CPU
                // readback or upload on the normal AMD path.
                // ----------------------------------------------------------
                capture_thread_stage_.store(4, std::memory_order_relaxed);
                conversion_submissions_.fetch_add(1, std::memory_order_relaxed);
                ID3D11Texture2D* encode_texture = PrepareEncodeTexture(tex);
                if (encode_texture)
                    conversion_completions_.fetch_add(1, std::memory_order_relaxed);
                capture_thread_stage_.store(5, std::memory_order_relaxed);
                const bool encoded = encode_texture && EncodeGpuReplayTexture(
                    encode_texture,
                    info.LastPresentTime.QuadPart,
                    frames_captured_.load(std::memory_order_relaxed) + 1);
                tex->Release();
                duplication_->ReleaseFrame();
                capture_frames_released_.fetch_add(1, std::memory_order_relaxed);
                capture_thread_stage_.store(0, std::memory_order_relaxed);
                if (!encoded) break;
            }
            else if (nvenc_active_ && replay_encoder_cpu_input_) {
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
                capture_frames_released_.fetch_add(1, std::memory_order_relaxed);
                capture_thread_stage_.store(4, std::memory_order_relaxed);

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

                const uint8_t* encode_data = CropMappedData(
                    static_cast<const uint8_t*>(mapped.pData), mapped.RowPitch);
                bool encoded = false;
                for (uint32_t attempt = 1; attempt <= 3 && !encoded; ++attempt) {
                    encoded = replay_encoder_->EncodeFrameCPU(
                        encode_data,
                        mapped.RowPitch,
                        info.LastPresentTime.QuadPart);
                    if (!encoded && attempt < 3) {
                        std::cerr << "[CaptureThread] Transient hybrid encoder "
                                  << "submission failure; retrying (" << attempt
                                  << "/3)." << std::endl;
                        std::this_thread::sleep_for(std::chrono::milliseconds(2));
                    }
                }
                context_->Unmap(staging_texture_, 0);
                conversion_submissions_.fetch_add(1, std::memory_order_relaxed);
                conversion_completions_.fetch_add(1, std::memory_order_relaxed);
                if (!encoded) {
                    FailReplayEncoder("hybrid NVENC CPU-input submission");
                    break;
                }
            }
            else {
                // ----------------------------------------------------------
                // x264 fallback path: CopyResource -> staging -> Map -> memcpy
                // ----------------------------------------------------------
                context_->CopyResource(staging_texture_, tex);
                tex->Release();
                duplication_->ReleaseFrame();
                capture_frames_released_.fetch_add(1, std::memory_order_relaxed);
                capture_thread_stage_.store(4, std::memory_order_relaxed);

                D3D11_MAPPED_SUBRESOURCE mapped{};
                hr = context_->Map(staging_texture_, 0, D3D11_MAP_READ, 0, &mapped);
                if (FAILED(hr)) {
                    std::cerr << "[CaptureThread] Texture map failed: 0x"
                        << std::hex << hr << std::dec << std::endl;
                    continue;
                }

                const uint8_t* full_src = static_cast<const uint8_t*>(mapped.pData);
                const uint8_t* src     = CropMappedData(full_src, mapped.RowPitch);
                const size_t   row     = static_cast<size_t>(crop_width_) * 4;
                const bool     pitched = (mapped.RowPitch != static_cast<UINT>(row));

                const size_t write_pos = ring_head_.load(std::memory_order_relaxed);
                const size_t slot_idx  = write_pos % max_frames_;
                uint8_t*     dst       = frame_pool_.GetSlot(slot_idx);

                SampleContentBGRA(
                    full_src, mapped.RowPitch, width_, height_,
                    frames_captured_.load(std::memory_order_relaxed) + 1);

                if (!pitched) {
                    std::memcpy(dst, src, row * crop_height_);
                }
                else {
                    for (uint32_t y = 0; y < crop_height_; y++) {
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
            capture_thread_stage_.store(0, std::memory_order_relaxed);
            if (fc == 1 || fc == 10 || fc == 100 || (fc % 500 == 0)) {
                std::cout << "[CaptureThread] Frames captured: " << fc << std::endl;
            }
        }

        std::cout << "[CaptureThread] Stopped. Total frames: "
                  << frames_captured_.load() << std::endl;
        if (capture_health_flags_.load() != CAPTURE_HEALTH_BACKEND_FAILED)
            capture_health_flags_.store(CAPTURE_HEALTH_NONE);
    }

    bool CaptureEngine::ConfigureCrop(const CaptureConfig& config) {
        crop_enabled_ = false;
        crop_x_ = 0;
        crop_y_ = 0;
        crop_width_ = width_;
        crop_height_ = height_;
        if (!config.crop_enabled) return true;

        const bool normalized = std::isfinite(config.crop_x)
            && std::isfinite(config.crop_y)
            && std::isfinite(config.crop_width)
            && std::isfinite(config.crop_height)
            && config.crop_x >= 0.0 && config.crop_y >= 0.0
            && config.crop_width > 0.0 && config.crop_height > 0.0
            && config.crop_x + config.crop_width <= 1.000001
            && config.crop_y + config.crop_height <= 1.000001;
        if (!normalized || width_ < 2 || height_ < 2) {
            std::cerr << "FTHR_STARTUP_WARNING: CROP_PROFILE_INVALID: "
                         "The saved crop was outside the source frame; the full "
                         "frame is being captured." << std::endl;
            return true;
        }

        const uint32_t left = std::min<uint32_t>(
            width_ - 1, static_cast<uint32_t>(std::floor(config.crop_x * width_)));
        const uint32_t top = std::min<uint32_t>(
            height_ - 1, static_cast<uint32_t>(std::floor(config.crop_y * height_)));
        const uint32_t right = std::min<uint32_t>(
            width_, static_cast<uint32_t>(std::ceil(
                (config.crop_x + config.crop_width) * width_)));
        const uint32_t bottom = std::min<uint32_t>(
            height_, static_cast<uint32_t>(std::ceil(
                (config.crop_y + config.crop_height) * height_)));
        const uint32_t crop_width = (right > left) ? ((right - left) & ~1u) : 0;
        const uint32_t crop_height = (bottom > top) ? ((bottom - top) & ~1u) : 0;
        if (crop_width < 2 || crop_height < 2
            || left + crop_width > width_ || top + crop_height > height_) {
            std::cerr << "FTHR_STARTUP_WARNING: CROP_PROFILE_INVALID: "
                         "The saved crop became too small at this resolution; "
                         "the full frame is being captured." << std::endl;
            return true;
        }
        if (left == 0 && top == 0
            && crop_width == width_ && crop_height == height_) return true;

        D3D11_TEXTURE2D_DESC description{};
        description.Width = crop_width;
        description.Height = crop_height;
        description.MipLevels = 1;
        description.ArraySize = 1;
        description.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        description.SampleDesc.Count = 1;
        description.Usage = D3D11_USAGE_DEFAULT;
        const HRESULT result = device_->CreateTexture2D(
            &description, nullptr, &crop_texture_);
        if (FAILED(result)) {
            std::cerr << "FTHR_STARTUP_WARNING: CROP_GPU_UNAVAILABLE: "
                         "The crop texture could not be created; the full frame "
                         "is being captured." << std::endl;
            return true;
        }

        crop_enabled_ = true;
        crop_x_ = left;
        crop_y_ = top;
        crop_width_ = crop_width;
        crop_height_ = crop_height;

        // A resolution preset is a bounding box. Cropped sources retain their
        // own aspect ratio instead of being stretched back to the old preset.
        if (target_width_ > 0 && target_height_ > 0) {
            const double scale = std::min(
                static_cast<double>(target_width_) / crop_width_,
                static_cast<double>(target_height_) / crop_height_);
            target_width_ = std::max<uint32_t>(2,
                static_cast<uint32_t>(std::floor(crop_width_ * scale)) & ~1u);
            target_height_ = std::max<uint32_t>(2,
                static_cast<uint32_t>(std::floor(crop_height_ * scale)) & ~1u);
        }
        std::cout << "[Crop] Encoder input " << crop_width_ << 'x'
                  << crop_height_ << " at " << crop_x_ << ',' << crop_y_
                  << " (normalized per-game profile)" << std::endl;
        return true;
    }

    ID3D11Texture2D* CaptureEngine::PrepareEncodeTexture(
        ID3D11Texture2D* source) {
        if (!crop_enabled_) return source;
        if (!source || !crop_texture_ || !context_) return nullptr;
        const D3D11_BOX box{
            crop_x_, crop_y_, 0,
            crop_x_ + crop_width_, crop_y_ + crop_height_, 1};
        context_->CopySubresourceRegion(
            crop_texture_, 0, 0, 0, 0, source, 0, &box);
        return crop_texture_;
    }

    const uint8_t* CaptureEngine::CropMappedData(
        const uint8_t* data, uint32_t stride) const {
        if (!data || !crop_enabled_) return data;
        return data + static_cast<size_t>(crop_y_) * stride
            + static_cast<size_t>(crop_x_) * 4;
    }

    bool CaptureEngine::EnsureStagingTexture() {
        if (!device_ || width_ == 0 || height_ == 0) return false;

        D3D11_TEXTURE2D_DESC description{};
        description.Width = width_;
        description.Height = height_;
        description.MipLevels = 1;
        description.ArraySize = 1;
        description.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        description.SampleDesc.Count = 1;
        description.Usage = D3D11_USAGE_STAGING;
        description.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        if (!staging_texture_) {
            const HRESULT result = device_->CreateTexture2D(
                &description, nullptr, &staging_texture_);
            if (FAILED(result)) {
                std::cerr << "[CaptureEngine] D3D11 readback texture creation failed: 0x"
                          << std::hex << result << std::dec << std::endl;
                return false;
            }
        }
        return true;
    }

    bool CaptureEngine::EncodeGpuReplayTexture(
        ID3D11Texture2D* source,
        int64_t present_qpc,
        uint64_t produced_frame) {
        if (!source || !replay_encoder_ || !context_) {
            FailReplayEncoder("D3D11 replay input setup");
            return false;
        }
        SampleContentTexture(source, produced_frame);
        if (replay_encoder_->RequiresBackendGpuPreparation()) {
            if (!replay_encoder_->PrepareGpuFrame(source, 0)) {
                FailReplayEncoder("backend GPU conversion");
                return false;
            }
        } else {
            ID3D11Texture2D* input = replay_encoder_->GetCurrentInputTexture();
            if (!input) {
                FailReplayEncoder("D3D11 replay input acquisition");
                return false;
            }

            D3D11_TEXTURE2D_DESC source_description{};
            D3D11_TEXTURE2D_DESC input_description{};
            source->GetDesc(&source_description);
            input->GetDesc(&input_description);
            if (source_description.Width != input_description.Width
                || source_description.Height != input_description.Height
                || source_description.Format != input_description.Format) {
                FailReplayEncoder("D3D11 replay texture compatibility check");
                return false;
            }
            context_->CopySubresourceRegion(
                input,
                replay_encoder_->GetCurrentInputSubresource(),
                0, 0, 0,
                source,
                0,
                nullptr);
        }
        if (!replay_encoder_->EncodeFrame(present_qpc)) {
            FailReplayEncoder("hardware frame submission");
            return false;
        }
        if (replay_config_publish_failed_.load()) {
            FailReplayEncoder("encoded stream configuration publication");
            return false;
        }
        return true;
    }

    void CaptureEngine::FailReplayEncoder(const char* operation) {
        ClearReplayForRecovery();
        capture_health_flags_.store(CAPTURE_HEALTH_BACKEND_FAILED);
        running_.store(false);
        std::cerr << "[CaptureEngine] " << operation << " failed";
        if (replay_encoder_) {
            const std::string detail = replay_encoder_->GetLastError();
            if (!detail.empty()) std::cerr << ": " << detail;
        }
        std::cerr << "; a fresh capture generation is required" << std::endl;
    }


    bool CaptureEngine::ResolveSelectedMonitor(const char* backend_name) {
        const auto result = monitor_resolver_.Resolve(monitor_device_path_);
        if (!result.ok()) {
            std::cerr << '[' << backend_name << "] "
                      << monitor::ToString(result.error) << ": "
                      << result.diagnostic << std::endl;
            SetCaptureFailure(result.diagnostic);
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
            const std::string failure = diagnostics::FormatHResultFailure(
                "CreateDXGIFactory1", hr);
            SetCaptureFailure(failure);
            std::cerr << '[' << backend_name << "] " << failure << std::endl;
            return false;
        }

        IDXGIAdapter1* capture_adapter = nullptr;
        std::string output_diagnostic;
        if (!monitor::OpenSelectedDxgiOutput(
                factory, resolved_monitor_, &capture_adapter, selected_output,
                &resolved_dxgi_output_,
                output_diagnostic)) {
            std::cerr << '[' << backend_name << "] "
                      << monitor::ToString(
                             monitor::MonitorResolveError::OutputResolutionFailed)
                      << ": " << output_diagnostic << std::endl;
            SetCaptureFailure(output_diagnostic);
            factory->Release();
            return false;
        }

        DXGI_ADAPTER_DESC1 capture_desc{};
        hr = capture_adapter->GetDesc1(&capture_desc);
        if (FAILED(hr)) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "IDXGIAdapter1::GetDesc1(selected capture adapter)", hr);
            SetCaptureFailure(failure);
            std::cerr << '[' << backend_name << "] " << failure << std::endl;
            (*selected_output)->Release();
            *selected_output = nullptr;
            capture_adapter->Release();
            factory->Release();
            return false;
        }
        D3D_FEATURE_LEVEL feature_level{};
        hr = D3D11CreateDevice(
            capture_adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
            nullptr, 0, D3D11_SDK_VERSION,
            &device_, &feature_level, &context_);
        if (FAILED(hr)) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "D3D11CreateDevice(selected monitor adapter)", hr);
            SetCaptureFailure(failure);
            std::cerr << '[' << backend_name << "] " << failure << std::endl;
            (*selected_output)->Release();
            *selected_output = nullptr;
            capture_adapter->Release();
            factory->Release();
            return false;
        }

        capture_adapter_vendor_ = EncoderVendorFromPciVendorId(
            capture_desc.VendorId);
        capture_device_adapter_luid_ = {
            capture_desc.AdapterLuid.LowPart,
            capture_desc.AdapterLuid.HighPart};
        capture_device_adapter_luid_available_ = true;
        nvidia_device_ = capture_adapter_vendor_ == EncoderVendor::Nvidia;
        DXGI_OUTPUT_DESC output_desc{};
        hr = (*selected_output)->GetDesc(&output_desc);
        if (FAILED(hr)) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "IDXGIOutput::GetDesc(selected output)", hr);
            SetCaptureFailure(failure);
            std::cerr << '[' << backend_name << "] " << failure << std::endl;
            (*selected_output)->Release();
            *selected_output = nullptr;
            capture_adapter->Release();
            factory->Release();
            return false;
        }
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
                  << " [" << EncoderVendorName(capture_adapter_vendor_)
                  << " display-owning adapter]"
                  << std::endl;
        return true;
    }


    // ===========================================================================
    // InitializeWGC
    //
    // Sets up Windows Graphics Capture for the exact persistent monitor selection.
    //
    // Key advantage over DXGI OutputDuplication:
    //   - The D3D11 device is created on the exact selected monitor adapter.
    //   - Event-driven (FrameArrived) — no 300 iteration/sec polling loop.
    //
    // Sets device_, context_, capture adapter identity, width_, height_.
    // Does NOT create staging_texture_ or duplication_ (not needed on this path).
    // ===========================================================================

    bool CaptureEngine::InitializeWGC() {
        startup_capture_backend_ = "WGC_MONITOR";
        // WGC requires Windows 10 1903+ (build 18362)
        try {
            if (!winrt::Windows::Graphics::Capture::GraphicsCaptureSession::IsSupported()) {
                std::cerr << "[WGC] GraphicsCaptureSession not supported on this system" << std::endl;
                SetCaptureFailure(
                    "api_call=GraphicsCaptureSession::IsSupported result=false");
                return false;
            }
        } catch (winrt::hresult_error const& error) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "GraphicsCaptureSession::IsSupported", error.code().value);
            SetCaptureFailure(failure);
            std::cerr << "[WGC] " << failure << std::endl;
            return false;
        } catch (...) {
            SetCaptureFailure(
                "api_call=GraphicsCaptureSession::IsSupported exception=unknown");
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
        last_capture_failure_detail_.clear();
        const HMONITOR hmonitor = reinterpret_cast<HMONITOR>(
            resolved_monitor_.hmonitor);
        HRESULT hr = S_OK;

        // --- Step 4-8: WinRT session setup (exception-safe) ---
        wgc_state_ = std::make_unique<WGCState>();
        wgc_state_->monitor_item = true;
        monitor_source_invalidated_.store(false, std::memory_order_release);
        const char* current_api = "ID3D11Device::QueryInterface(IDXGIDevice)";
        try {
            // 4a. Wrap ID3D11Device as WinRT IDirect3DDevice
            winrt::com_ptr<IDXGIDevice> dxgi_dev;
            winrt::check_hresult(device_->QueryInterface(dxgi_dev.put()));

            winrt::com_ptr<IInspectable> insp;
            current_api = "CreateDirect3D11DeviceFromDXGIDevice";
            winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi_dev.get(), insp.put()));

            current_api = "IInspectable::as(IDirect3DDevice)";
            wgc_state_->winrt_device =
                insp.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();

            // 4b. Create a capture item for the exact resolved monitor.
            current_api = "RoGetActivationFactory(GraphicsCaptureItem)";
            auto item_interop = winrt::get_activation_factory<
                winrt::Windows::Graphics::Capture::GraphicsCaptureItem,
                IGraphicsCaptureItemInterop>();

            current_api = "IGraphicsCaptureItemInterop::CreateForMonitor";
            winrt::check_hresult(item_interop->CreateForMonitor(
                hmonitor,
                winrt::guid_of<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>(),
                winrt::put_abi(wgc_state_->item)));

            current_api = "GraphicsCaptureItem::Closed(add handler)";
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
            current_api = "GraphicsCaptureItem::Size";
            auto sz = wgc_state_->item.Size();
            current_api = "Direct3D11CaptureFramePool::CreateFreeThreaded";
            wgc_state_->frame_pool =
                winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::CreateFreeThreaded(
                    wgc_state_->winrt_device,
                    winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
                    2,
                    sz);

            // 6. Apply the version-adaptive WGC capture-border policy before
            // StartCapture. If the indicator cannot be disabled, refuse this
            // WGC session so Initialize() can fall back to border-free DXGI.
            current_api = "Direct3D11CaptureFramePool::CreateCaptureSession";
            wgc_state_->session =
                wgc_state_->frame_pool.CreateCaptureSession(wgc_state_->item);
            if (!ApplyCaptureBorderPolicy("monitor")) {
                std::cerr << "[WGC] Windows privacy border would remain visible; "
                             "falling back to border-free DXGI capture."
                          << std::endl;
                ShutdownWGC();
                ShutdownD3D11();
                return false;
            }

            // 7. Subscribe FrameArrived: only wakes CaptureThread, no encode work here.
            current_api = "Direct3D11CaptureFramePool::FrameArrived(add handler)";
            wgc_state_->frame_arrived_token =
                wgc_state_->frame_pool.FrameArrived([this](auto&, auto&) {
                    {
                        std::lock_guard<std::mutex> lk(wgc_frame_mutex_);
                        wgc_frame_ready_ = true;
                    }
                    wgc_frame_cv_.notify_one();
                });

            // 8. Start
            current_api = "GraphicsCaptureSession::StartCapture";
            wgc_state_->session.StartCapture();

        } catch (winrt::hresult_error const& e) {
            std::string failure = diagnostics::FormatHResultFailure(
                current_api, e.code().value);
            const std::string winrt_message = winrt::to_string(e.message());
            if (!winrt_message.empty()) {
                failure += " winrt_message=\"";
                failure += diagnostics::JsonEscape(winrt_message);
                failure += '\"';
            }
            SetCaptureFailure(failure);
            std::cerr << "[WGC] "
                      << monitor::ToString(
                             monitor::MonitorResolveError::CaptureItemCreationFailed)
                      << ": " << failure << std::endl;
            wgc_state_.reset();
            if (context_) { context_->Release(); context_ = nullptr; }
            if (device_)  { device_->Release();  device_  = nullptr; }
            nvidia_device_ = false;
            capture_adapter_vendor_ = EncoderVendor::Software;
            return false;
        }

        std::cout << "[WGC] Ready. Capture started ("
                  << width_ << "x" << height_ << ", "
                  << EncoderVendorName(capture_adapter_vendor_)
                  << " capture adapter"
                  << ")" << std::endl;
        last_capture_failure_detail_.clear();
        return true;
    }


    // ===========================================================================
    // ApplyCaptureBorderPolicy
    //
    // The Windows Graphics Capture border is an OS privacy indicator. Match the
    // old FTHR behavior: ask the live WGC session to suppress the border before
    // StartCapture, without requiring package identity or showing a permission
    // prompt. A session is accepted only when the runtime reports that the
    // border is no longer required; otherwise callers fall back to DXGI.
    // ===========================================================================

    bool CaptureEngine::ApplyCaptureBorderPolicy(const char* capture_target) {
        if (!wgc_state_ || !wgc_state_->session) return false;

        CaptureBorderPolicyInput input;
        input.session_interface_checked = true;
        try {
            auto session3 = wgc_state_->session.try_as<
                winrt::Windows::Graphics::Capture::IGraphicsCaptureSession3>();
            input.session_interface_available = static_cast<bool>(session3);
            if (session3) {
                input.property_attempted = true;
                try {
                    // This is the same session-level opt-out used by the old
                    // build and must happen before StartCapture().
                    session3.IsBorderRequired(false);
                    input.property_set_succeeded = true;
                    input.border_required_after_attempt =
                        session3.IsBorderRequired();
                } catch (winrt::hresult_error const& error) {
                    SetCaptureFailure(diagnostics::FormatHResultFailure(
                        "IGraphicsCaptureSession3::IsBorderRequired",
                        error.code().value));
                    input.property_set_succeeded = false;
                } catch (...) {
                    SetCaptureFailure(
                        "api_call=IGraphicsCaptureSession3::IsBorderRequired exception=unknown");
                    input.property_set_succeeded = false;
                }
            }
        } catch (winrt::hresult_error const& error) {
            SetCaptureFailure(diagnostics::FormatHResultFailure(
                "GraphicsCaptureSession::QueryInterface(IGraphicsCaptureSession3)",
                error.code().value));
            input.session_interface_available = false;
        } catch (...) {
            SetCaptureFailure(
                "api_call=GraphicsCaptureSession::QueryInterface(IGraphicsCaptureSession3) exception=unknown");
            input.session_interface_available = false;
        }
        const auto decision = EvaluateCaptureBorderPolicy(input);

        std::cout << "[CaptureBorderPolicy] target=" << capture_target
                  << " session_interface="
                  << (input.session_interface_available ? "available" : "unavailable")
                  << " property_attempted=" << (input.property_attempted ? "true" : "false")
                  << " property_set=" << (input.property_set_succeeded ? "true" : "false")
                  << " border_required="
                  << (input.border_required_after_attempt ? "true" : "false")
                  << " effective=" << (decision.effective_borderless ? "true" : "false")
                  << " reason=" << CaptureBorderPolicyReasonName(decision.reason)
                  << std::endl;

        if (!decision.effective_borderless
            && last_capture_failure_detail_.empty()) {
            SetCaptureFailure(
                std::string("api_call=IGraphicsCaptureSession3::IsBorderRequired result=")
                + CaptureBorderPolicyReasonName(decision.reason));
        }

        return decision.effective_borderless;
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
    // A supported public-alpha run always routes the WGC texture into the
    // hardware encoder on that same D3D11 device. Legacy CPU-input/raw branches
    // are unreachable because initialization fails closed before threads start.
    // ===========================================================================

    void CaptureEngine::CaptureThreadWGC() {
        // WinRT calls (TryGetNextFrame, surface access) require the thread to be
        // in a COM apartment. Multi-threaded apartment is correct here — we have
        // no message pump and don't need the STA marshaling overhead.
        winrt::init_apartment(winrt::apartment_type::multi_threaded);

        std::cout << "[CaptureThread/WGC] Started ("
                  << fps_ << " fps, "
                  << (nvenc_active_ ? GetActiveEncoderName() : "software")
                  << " path, "
                  << (replay_encoder_cpu_input_ ? "hybrid CPU-input"
                      : (nvenc_active_ ? "same-adapter GPU input" : "readback"))
                  << ")" << std::endl;

        LARGE_INTEGER qpc_freq;
        QueryPerformanceFrequency(&qpc_freq);

        const double  target_ms  = 1000.0 / static_cast<double>(fps_);
        const int64_t target_qpc = static_cast<int64_t>(
            (target_ms / 1000.0) * static_cast<double>(qpc_freq.QuadPart));

        FrameRateScheduler frame_scheduler(target_qpc);
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
                if (!frame_scheduler.ShouldCapture(now.QuadPart)) {
                    frames_dropped_.fetch_add(1, std::memory_order_relaxed);
                    continue;  // frame destructor returns buffer to pool
                }

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

                if (nvenc_active_ && !replay_encoder_cpu_input_) {
                    // ----------------------------------------------------------
                    // Same-adapter compressed replay (native NVENC or AMF).
                    // A GPU CopyResource feeds the encoder-owned texture.
                    // ----------------------------------------------------------
                    ID3D11Texture2D* encode_texture = PrepareEncodeTexture(tex.get());
                    if (!encode_texture || !EncodeGpuReplayTexture(
                            encode_texture, now.QuadPart,
                            frames_captured_.load(std::memory_order_relaxed) + 1)) {
                        break;
                    }
                }
                else if (nvenc_active_ && replay_encoder_cpu_input_
                    && staging_texture_) {
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

                    const uint8_t* encode_data = CropMappedData(
                        static_cast<const uint8_t*>(mapped.pData), mapped.RowPitch);
                    bool encoded = false;
                    for (uint32_t attempt = 1; attempt <= 3 && !encoded; ++attempt) {
                        encoded = replay_encoder_->EncodeFrameCPU(
                            encode_data,
                            mapped.RowPitch, now.QuadPart);
                        if (!encoded && attempt < 3) {
                            std::cerr << "[CaptureThread/WGC] Transient hybrid encoder "
                                      << "submission failure; retrying (" << attempt
                                      << "/3)." << std::endl;
                            std::this_thread::sleep_for(std::chrono::milliseconds(2));
                        }
                    }
                    context_->Unmap(staging_texture_, 0);
                    if (!encoded) {
                        FailReplayEncoder("hybrid NVENC CPU-input submission");
                        break;
                    }
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

                    const uint8_t* full_src = static_cast<const uint8_t*>(mapped.pData);
                    const uint8_t* src = CropMappedData(full_src, mapped.RowPitch);
                    const size_t   row = static_cast<size_t>(crop_width_) * 4;
                    const size_t   write_pos = ring_head_.load(std::memory_order_relaxed);
                    const size_t   slot_idx  = write_pos % max_frames_;
                    uint8_t*       dst = frame_pool_.GetSlot(slot_idx);

                    SampleContentBGRA(
                        full_src, mapped.RowPitch, width_, height_,
                        frames_captured_.load(std::memory_order_relaxed) + 1);

                    if (mapped.RowPitch == static_cast<UINT>(row)) {
                        std::memcpy(dst, src, row * crop_height_);
                    } else {
                        for (uint32_t y = 0; y < crop_height_; y++) {
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
        startup_capture_backend_ = "WGC_WINDOW";
        try {
            if (!winrt::Windows::Graphics::Capture::GraphicsCaptureSession::IsSupported()) {
                std::cerr << "[WinCapture] GraphicsCaptureSession not supported" << std::endl;
                SetCaptureFailure(
                    "api_call=GraphicsCaptureSession::IsSupported result=false");
                return false;
            }
        } catch (winrt::hresult_error const& error) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "GraphicsCaptureSession::IsSupported", error.code().value);
            SetCaptureFailure(failure);
            std::cerr << "[WinCapture] " << failure << std::endl;
            return false;
        } catch (...) {
            SetCaptureFailure(
                "api_call=GraphicsCaptureSession::IsSupported exception=unknown");
            std::cerr << "[WinCapture] IsSupported() threw — WGC unavailable" << std::endl;
            return false;
        }

        HWND hwnd = reinterpret_cast<HWND>(target_hwnd_);
        if (!IsWindow(hwnd)) {
            SetCaptureFailure("api_call=IsWindow result=false");
            std::cerr << "[WinCapture] HWND 0x" << std::hex << target_hwnd_
                      << std::dec << " is not a valid window" << std::endl;
            return false;
        }

        // Create one WGC device and keep replay encoding on that exact adapter.
        // Do not enumerate another vendor merely because it exists elsewhere in
        // the machine; cross-adapter window capture is not alpha-qualified.
        D3D_FEATURE_LEVEL feature_level{};
        HRESULT hr = D3D11CreateDevice(
            nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
            nullptr, 0, D3D11_SDK_VERSION,
            &device_, &feature_level, &context_);
        if (FAILED(hr)) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "D3D11CreateDevice(window capture)", hr);
            SetCaptureFailure(failure);
            std::cerr << "[WinCapture] " << failure << std::endl;
            return false;
        }
        capture_adapter_vendor_ = QueryD3D11DeviceVendor(device_);
        nvidia_device_ = capture_adapter_vendor_ == EncoderVendor::Nvidia;
        std::string capture_luid_diagnostic;
        capture_device_adapter_luid_available_ = QueryD3D11DeviceAdapterLuid(
            device_, capture_device_adapter_luid_, capture_luid_diagnostic);
        if (!capture_device_adapter_luid_available_) {
            std::cerr << "[WinCapture] Could not resolve capture device adapter LUID: "
                      << capture_luid_diagnostic << std::endl;
        }

        // --- Steps 3-8: WinRT capture session (exception-safe) ---
        wgc_state_ = std::make_unique<WGCState>();
        const char* current_api = "ID3D11Device::QueryInterface(IDXGIDevice)";
        try {
            // 3. Wrap ID3D11Device as WinRT IDirect3DDevice
            winrt::com_ptr<IDXGIDevice> dxgi_dev;
            winrt::check_hresult(device_->QueryInterface(dxgi_dev.put()));
            winrt::com_ptr<IInspectable> insp;
            current_api = "CreateDirect3D11DeviceFromDXGIDevice";
            winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi_dev.get(), insp.put()));
            current_api = "IInspectable::as(IDirect3DDevice)";
            wgc_state_->winrt_device =
                insp.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();

            // 4. Create capture item from the target window
            current_api = "RoGetActivationFactory(GraphicsCaptureItem)";
            auto item_interop = winrt::get_activation_factory<
                winrt::Windows::Graphics::Capture::GraphicsCaptureItem,
                IGraphicsCaptureItemInterop>();
            current_api = "IGraphicsCaptureItemInterop::CreateForWindow";
            winrt::check_hresult(item_interop->CreateForWindow(
                hwnd,
                winrt::guid_of<winrt::Windows::Graphics::Capture::GraphicsCaptureItem>(),
                winrt::put_abi(wgc_state_->item)));

            // 5. Get captured dimensions from the item (= window content size)
            current_api = "GraphicsCaptureItem::Size";
            auto sz = wgc_state_->item.Size();
            width_  = static_cast<uint32_t>(sz.Width);
            height_ = static_cast<uint32_t>(sz.Height);
            std::cout << "[WinCapture] Window content size: " << width_ << "x" << height_ << std::endl;

            // 7. Frame pool: 2 slots, free-threaded.
            //    See InitializeWGC() comment on why CreateFreeThreaded is required.
            current_api = "Direct3D11CaptureFramePool::CreateFreeThreaded";
            wgc_state_->frame_pool =
                winrt::Windows::Graphics::Capture::Direct3D11CaptureFramePool::CreateFreeThreaded(
                    wgc_state_->winrt_device,
                    winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,
                    2,
                    sz);

            // 8. Apply the same version-adaptive border policy as monitor
            // capture. WGC ownership is independent of UI launch mode. If
            // borderless capture is unavailable, return false so the caller
            // can use the border-free DXGI fallback.
            current_api = "Direct3D11CaptureFramePool::CreateCaptureSession";
            wgc_state_->session =
                wgc_state_->frame_pool.CreateCaptureSession(wgc_state_->item);
            if (!ApplyCaptureBorderPolicy("window")) {
                std::cerr << "[WinCapture] Windows privacy border would remain visible; "
                             "falling back to border-free monitor capture."
                          << std::endl;
                ShutdownWGC();
                ShutdownD3D11();
                return false;
            }

            // 9. FrameArrived: only wakes CaptureThreadWGC, no encoding work in callback
            current_api = "Direct3D11CaptureFramePool::FrameArrived(add handler)";
            wgc_state_->frame_arrived_token =
                wgc_state_->frame_pool.FrameArrived([this](auto&, auto&) {
                    {
                        std::lock_guard<std::mutex> lk(wgc_frame_mutex_);
                        wgc_frame_ready_ = true;
                    }
                    wgc_frame_cv_.notify_one();
                });

            // 10. Start
            current_api = "GraphicsCaptureSession::StartCapture";
            wgc_state_->session.StartCapture();

        } catch (winrt::hresult_error const& e) {
            std::string failure = diagnostics::FormatHResultFailure(
                current_api, e.code().value);
            const std::string winrt_message = winrt::to_string(e.message());
            if (!winrt_message.empty()) {
                failure += " winrt_message=\"";
                failure += diagnostics::JsonEscape(winrt_message);
                failure += '\"';
            }
            SetCaptureFailure(failure);
            std::cerr << "[WinCapture] " << failure << std::endl;
            wgc_state_.reset();
            if (context_) { context_->Release(); context_ = nullptr; }
            if (device_)  { device_->Release();  device_  = nullptr; }
            nvidia_device_ = false;
            capture_adapter_vendor_ = EncoderVendor::Software;
            return false;
        }

        std::cout << "[WinCapture] Ready  "
                  << width_ << "x" << height_ << "  "
                  << EncoderVendorName(capture_adapter_vendor_)
                  << " capture adapter"
                  << std::endl;
        last_capture_failure_detail_.clear();
        return true;
    }


    // ===========================================================================
    // InitializeD3D11 - exact selected adapter/output fallback
    // ===========================================================================

    bool CaptureEngine::InitializeD3D11() {
        startup_capture_backend_ = "DXGI_OUTPUT_DUPLICATION";
        nvidia_device_ = false;
        capture_adapter_vendor_ = EncoderVendor::Software;
        IDXGIOutput* selected_output = nullptr;
        if (!InitializeMonitorCaptureDevice("DXGI", &selected_output)) {
            return false;
        }
        last_capture_failure_detail_.clear();

        IDXGIOutput1* output1 = nullptr;
        HRESULT hr = selected_output->QueryInterface(
            __uuidof(IDXGIOutput1), reinterpret_cast<void**>(&output1));
        if (FAILED(hr) || !output1) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "IDXGIOutput::QueryInterface(IDXGIOutput1)", hr);
            SetCaptureFailure(failure);
            std::cerr << "[D3D11] " << failure << std::endl;
            selected_output->Release();
            if (context_) { context_->Release(); context_ = nullptr; }
            if (device_) { device_->Release(); device_ = nullptr; }
            return false;
        }
        hr = output1->DuplicateOutput(device_, &duplication_);
        output1->Release();
        selected_output->Release();
        if (FAILED(hr) || !duplication_) {
            const std::string failure = diagnostics::FormatHResultFailure(
                "IDXGIOutput1::DuplicateOutput", hr);
            SetCaptureFailure(failure);
            std::cerr << "[D3D11] "
                      << monitor::ToString(
                             monitor::MonitorResolveError::OutputResolutionFailed)
                      << ": " << failure << std::endl;
            if (context_) { context_->Release(); context_ = nullptr; }
            if (device_) { device_->Release(); device_ = nullptr; }
            return false;
        }

        std::cout << "[D3D11] Ready - " << width_ << "x" << height_
                  << (nvidia_device_
                      ? "  [selected NVIDIA adapter - GPU zero-copy enabled]"
                      : "  [selected display-owning adapter]")
                  << std::endl;
        std::cout << "[D3D11] Duplication: " << (duplication_ ? "OK" : "NULL") << std::endl;
        std::cout << "[D3D11] Readback texture: deferred until fallback policy"
                  << std::endl;
        last_capture_failure_detail_.clear();
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
        if (crop_texture_) { crop_texture_->Release(); crop_texture_ = nullptr; }
        if (staging_texture_) { staging_texture_->Release(); staging_texture_ = nullptr; }
        if (duplication_)     { duplication_->Release();     duplication_ = nullptr; }
        if (context_)         { context_->Release();         context_ = nullptr; }
        if (device_)          { device_->Release();          device_ = nullptr; }
        nvidia_device_ = false;
        capture_adapter_vendor_ = EncoderVendor::Software;
        // nvenc_device_ / nvenc_context_ are intentionally NOT released here.
        // They must outlive the NVENC encoder session (which is finalized in Shutdown()
        // before this is called). On ACCESS_LOST reinit they stay valid.
    }


} // namespace fthr

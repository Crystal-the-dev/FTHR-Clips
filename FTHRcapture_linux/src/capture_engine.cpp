#include "capture_engine.h"
#include "capture_backend.h"
#include <chrono>
#include <iostream>
#include <cstring>
#include <cstdio>
#include <thread>
#include <mutex>
#include <time.h>

namespace fthr {

// ---------------------------------------------------------------------------
// CaptureEngine constructor/destructor — defined here so ICaptureBackend is complete
// ---------------------------------------------------------------------------

CaptureEngine::CaptureEngine() = default;
CaptureEngine::~CaptureEngine() { Shutdown(); }

// ---------------------------------------------------------------------------
// CaptureEngine::Initialize
// ---------------------------------------------------------------------------

bool CaptureEngine::Initialize(const CaptureConfig& cfg) {
    cfg_ = cfg;

    // Allocate ring buffer (buffer_seconds + small margin)
    size_t ring_ms = (static_cast<size_t>(cfg.buffer_seconds) + 5) * 1000;
    ring_ = new EncodedRingBuffer(ring_ms);

    // Start audio capture (loopback via PulseAudio monitor)
    if (cfg.audio_enabled) {
        if (cfg.multiband_enabled && !cfg.audio_categories.empty()) {
            multi_audio_.Start(cfg.audio_categories);
        } else {
            audio_.Start("");
        }
    }

    // Start capture loop thread
    running_.store(true);
    cap_thread_ = std::thread(&CaptureEngine::CaptureLoop, this);

    return true;
}

// ---------------------------------------------------------------------------
// CaptureEngine::Shutdown
// ---------------------------------------------------------------------------

void CaptureEngine::Shutdown() {
    running_.store(false);
    if (cap_thread_.joinable())
        cap_thread_.join();
    if (cfg_.audio_enabled) {
        audio_.Stop();
        multi_audio_.Stop();
    }
    encoder_.Close();
    delete ring_;
    ring_ = nullptr;
}

// ---------------------------------------------------------------------------
// CaptureEngine::CaptureLoop — backend-agnostic main loop
// ---------------------------------------------------------------------------

void CaptureEngine::CaptureLoop() {
    backend_ = CreateBestBackend(cfg_);
    if (!backend_) {
        std::cerr << "[Capture] No capture backend available — exiting" << std::endl;
        running_.store(false);
        return;
    }

    uint32_t native_w = backend_->NativeWidth();
    uint32_t native_h = backend_->NativeHeight();

    // Determine encode dimensions
    uint32_t enc_w = (cfg_.target_width  == 0) ? native_w : cfg_.target_width;
    uint32_t enc_h = (cfg_.target_height == 0) ? native_h : cfg_.target_height;

    if (cfg_.scaling_mode == 1 && (enc_w != native_w || enc_h != native_h)) {
        double ar = static_cast<double>(native_w) / native_h;
        uint32_t fit_h = static_cast<uint32_t>(enc_w / ar);
        if (fit_h <= enc_h) {
            enc_h = fit_h & ~1u;
        } else {
            enc_w = static_cast<uint32_t>(enc_h * ar) & ~1u;
        }
    }
    enc_w &= ~1u;
    enc_h &= ~1u;

    EncoderConfig enc_cfg;
    enc_cfg.src_width    = native_w;
    enc_cfg.src_height   = native_h;
    enc_cfg.enc_width    = enc_w;
    enc_cfg.enc_height   = enc_h;
    enc_cfg.fps          = cfg_.fps;
    enc_cfg.bitrate_kbps = cfg_.bitrate_kbps;
    enc_cfg.codec_pref   = cfg_.codec_pref;
    enc_cfg.preset       = cfg_.preset;

    std::string codec_used;
    if (!encoder_.Open(enc_cfg, codec_used)) {
        std::cerr << "[Capture] Encoder open failed" << std::endl;
        backend_->Shutdown();
        running_.store(false);
        return;
    }
    nvenc_active_.store(codec_used.find("nvenc") != std::string::npos);
    { std::lock_guard<std::mutex> lk(codec_mutex_); active_codec_ = codec_used; }

    int64_t frame_ns       = 1'000'000'000LL / cfg_.fps;
    int64_t next_encode_ns = 0;

    std::cout << "[Capture] Loop started: "
              << enc_w << "x" << enc_h
              << " @ " << cfg_.fps << "fps  codec=" << codec_used << std::endl;

    while (running_.load()) {
        if (paused_.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }

        RawFrame raw;
        if (!backend_->CaptureFrame(raw)) {
            if (running_.load())
                std::cerr << "[Capture] CaptureFrame failed — exiting loop" << std::endl;
            break;
        }

        // Frame rate throttling
        if (next_encode_ns == 0) next_encode_ns = raw.timestamp_ns;
        if (raw.timestamp_ns < next_encode_ns) continue;
        next_encode_ns += frame_ns;

        encoder_.EncodeFrame(raw.data, raw.stride, raw.timestamp_ns,
            [this](EncodedPacket pkt) { ring_->Push(std::move(pkt)); });

        frame_count_.fetch_add(1);
    }

    backend_->Shutdown();
    std::cout << "[Capture] Loop exited. Frames: " << frame_count_.load() << std::endl;
}

// ---------------------------------------------------------------------------
// write_pcm_wav — writes IEEE float32 WAV file
// ---------------------------------------------------------------------------

static void write_pcm_wav(const std::string& path,
                            const std::vector<float>& pcm,
                            int sample_rate, int channels) {
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) return;

    uint32_t data_bytes  = (uint32_t)(pcm.size() * sizeof(float));
    uint32_t file_size   = 36 + data_bytes;

    // RIFF header
    fwrite("RIFF", 1, 4, f);
    fwrite(&file_size,  4, 1, f);
    fwrite("WAVE", 1, 4, f);

    // fmt chunk — IEEE float PCM (format tag 3)
    fwrite("fmt ", 1, 4, f);
    uint32_t fmt_size    = 16;
    uint16_t audio_fmt   = 3;
    uint16_t ch          = (uint16_t)channels;
    uint32_t sr          = (uint32_t)sample_rate;
    uint32_t byte_rate   = sr * ch * 4;
    uint16_t block_align = (uint16_t)(ch * 4);
    uint16_t bits        = 32;
    fwrite(&fmt_size,    4, 1, f);
    fwrite(&audio_fmt,   2, 1, f);
    fwrite(&ch,          2, 1, f);
    fwrite(&sr,          4, 1, f);
    fwrite(&byte_rate,   4, 1, f);
    fwrite(&block_align, 2, 1, f);
    fwrite(&bits,        2, 1, f);

    // data chunk
    fwrite("data", 1, 4, f);
    fwrite(&data_bytes, 4, 1, f);
    fwrite(pcm.data(), sizeof(float), pcm.size(), f);
    fclose(f);
}

// ---------------------------------------------------------------------------
// CaptureEngine::SaveClip
// ---------------------------------------------------------------------------

bool CaptureEngine::SaveClip(const std::string& path, uint32_t duration_sec,
                               SharedMemoryLayout* shm) {
    if (!ring_) return false;

    uint32_t duration_ms = duration_sec * 1000;
    auto video_packets   = ring_->TakeSnapshot(duration_ms);

    if (video_packets.empty()) {
        std::cerr << "[SaveClip] No video packets in buffer" << std::endl;
        return false;
    }

    // Get audio segment
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    int64_t now_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + ts.tv_nsec;
    std::vector<float> audio_pcm = audio_.ExtractSegment(now_ns, duration_ms);

    // Write per-category WAVs when multiband is active.
    // Python reads these, mixes with preset volumes, and deletes them.
    if (cfg_.multiband_enabled) {
        for (const auto& cat_cfg : cfg_.audio_categories) {
            std::vector<float> pcm = multi_audio_.ExtractSegment(
                cat_cfg.name, now_ns, duration_ms);
            if (pcm.empty()) continue;
            // Derive WAV path: strip extension, append _<sinkname>.wav
            // cat_cfg.sink_name already starts with "fthr_" (e.g. "fthr_game"),
            // so the result is "<base>_fthr_game.wav" — matching what the Python
            // mixer looks for in _multiband_mux_worker.
            std::string wav_path = path;
            size_t dot = wav_path.rfind('.');
            if (dot != std::string::npos) wav_path = wav_path.substr(0, dot);
            wav_path += "_" + cat_cfg.sink_name + ".wav";
            write_pcm_wav(wav_path, pcm,
                          AudioMultiCapture::kSampleRate,
                          AudioMultiCapture::kChannels);
        }
    }

    // Delegate to save_clip module
    extern bool save_clip_to_file(
        const std::string& path,
        const std::vector<EncodedPacket>& video_packets,
        const std::vector<float>& audio_pcm,
        int audio_sample_rate,
        int audio_channels,
        const std::vector<uint8_t>& extradata,
        uint32_t fps,
        uint32_t width,
        uint32_t height,
        AVCodecID video_codec_id,
        SharedMemoryLayout* shm
    );

    return save_clip_to_file(
        path,
        video_packets,
        audio_pcm,
        AudioCapture::kSampleRate,
        AudioCapture::kChannels,
        encoder_.GetExtradata(),
        cfg_.fps,
        encoder_.GetWidth(),
        encoder_.GetHeight(),
        encoder_.GetCodecID(),
        shm
    );
}

// ---------------------------------------------------------------------------
// CaptureEngine::Reconfigure — hot-swap codec/preset without full reinit
// ---------------------------------------------------------------------------

void CaptureEngine::Reconfigure(uint32_t codec_pref, int preset) {
    Shutdown();   // stops thread, deletes ring_, stops audio
    cfg_.codec_pref = static_cast<CodecPref>(codec_pref);
    cfg_.preset     = preset;
    if (cfg_.preset < 1) cfg_.preset = 1;
    if (cfg_.preset > 7) cfg_.preset = 7;
    // Re-allocate ring (Shutdown() freed it)
    size_t ring_ms = (static_cast<size_t>(cfg_.buffer_seconds) + 5) * 1000;
    ring_ = new EncodedRingBuffer(ring_ms);
    // Restart audio (Shutdown() stopped it)
    if (cfg_.audio_enabled) {
        if (cfg_.multiband_enabled && !cfg_.audio_categories.empty()) {
            multi_audio_.Start(cfg_.audio_categories);
        } else {
            audio_.Start("");
        }
    }
    // Reset stale state
    nvenc_active_.store(false);
    { std::lock_guard<std::mutex> lk(codec_mutex_); active_codec_.clear(); }
    // Restart capture thread
    running_.store(true);
    cap_thread_ = std::thread(&CaptureEngine::CaptureLoop, this);
}

// ---------------------------------------------------------------------------
// CaptureEngine::GetAudioMappingsJson
// ---------------------------------------------------------------------------

static std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 4);
    for (unsigned char c : s) {
        if      (c == '"')  out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c == '\n') out += "\\n";
        else if (c == '\r') out += "\\r";
        else if (c == '\t') out += "\\t";
        else if (c < 0x20)  { char buf[8]; snprintf(buf, sizeof(buf), "\\u%04x", c); out += buf; }
        else                out += c;
    }
    return out;
}

std::string CaptureEngine::GetAudioMappingsJson() const {
    if (!cfg_.multiband_enabled) return "{}";
    auto maps = multi_audio_.GetCurrentMappings();
    std::string json = "{";
    bool first = true;
    for (auto& [app, cat] : maps) {
        if (!first) json += ",";
        json += "\"" + json_escape(app) + "\":\"" + json_escape(cat) + "\"";
        first = false;
    }
    json += "}";
    return json;
}

} // namespace fthr

#include "capture_engine.h"
#include <winrt/base.h>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <thread>

int wmain(int argc, wchar_t** argv) {
    if (argc != 3 && argc != 4) return 2;
    winrt::init_apartment(winrt::apartment_type::multi_threaded);
    fthr::CaptureConfig config;
    config.monitor_device_path = argv[1];
    config.encoder_preference = fthr::EncoderPreference::Nvidia;
    config.buffer_seconds = 15;
    config.bitrate_kbps = 16000;
    config.audio_enabled = true;
    fthr::CaptureEngine engine;
    if (!engine.Initialize(config)) return 3;
    const bool test_recording = argc == 4 && std::wstring(argv[3]) == L"--manual";
    auto recording = std::filesystem::path(argv[2]);
    recording.replace_extension(L".recording.mp4");
    uint64_t last = 0;
    for (int second = 0; second < 15; ++second) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        const auto stats = engine.GetStats();
        std::cout << "SMOKE second=" << second + 1
                  << " frames=" << stats.frames_captured
                  << " packets=" << stats.ring_used_frames
                  << " health=" << engine.GetCaptureHealthFlags() << std::endl;
        if (second > 2 && stats.frames_captured <= last) {
            engine.Shutdown();
            return 4;
        }
        last = stats.frames_captured;
        if (test_recording && second == 2 && !engine.StartRecording(recording.c_str())) {
            std::cerr << "SMOKE recording start: " << engine.GetLastRecordingError() << std::endl;
            engine.Shutdown();
            return 7;
        }
        if (test_recording && second == 10) {
            const auto start = std::chrono::steady_clock::now();
            const bool stopped = engine.StopRecording();
            std::cout << "SMOKE manual_stopped=" << stopped << " close_ms="
                      << std::chrono::duration_cast<std::chrono::milliseconds>(
                             std::chrono::steady_clock::now() - start).count() << std::endl;
            if (!stopped) {
                std::cerr << engine.GetLastRecordingError() << std::endl;
                engine.Shutdown();
                return 8;
            }
        }
    }
    fthr::SharedMemoryLayout response{};
    if (!engine.SaveClip(argv[2], 10, &response)) {
        engine.Shutdown();
        return 5;
    }
    for (int tick = 0; tick < 200 && response.engine_response == fthr::ResponseType::NONE; ++tick)
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    const bool saved = response.engine_response == fthr::ResponseType::CLIP_SAVED;
    std::cout << "SMOKE saved=" << saved << std::endl;
    engine.Shutdown();
    return saved ? 0 : 6;
}

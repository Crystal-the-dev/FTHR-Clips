// Synthetic NVENC/recording integration check; never captures the desktop.
#include "hardware_encoder.h"
#include "continuous_recording_writer.h"
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <thread>
#include <vector>

int wmain(int argc, wchar_t** argv) {
    if (argc != 3 && argc != 6) return 2;
    using Microsoft::WRL::ComPtr;
    ComPtr<IDXGIFactory1> factory;
    if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) return 3;
    ComPtr<IDXGIAdapter1> adapter;
    for (UINT i = 0; factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; ++i) {
        DXGI_ADAPTER_DESC1 desc{};
        adapter->GetDesc1(&desc);
        if (desc.VendorId == 0x10de) break;
        adapter.Reset();
    }
    if (!adapter) return 4;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    if (FAILED(D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN,
            nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
            D3D11_SDK_VERSION, &device, nullptr, &context))) return 5;
    fthr::EncoderConfig config;
    config.src_width = 1280;
    config.src_height = 976;
    config.enc_width = 1920;
    config.enc_height = 1080;
    config.fps = 60;
    config.bitrate_kbps = 8000;
    config.scaling_mode = static_cast<uint32_t>(_wtoi(argv[2]));
    const bool cpu_input = argc == 3 || std::wstring(argv[3]) != L"gpu";
    if (argc == 6) {
        config.enc_width = static_cast<uint32_t>(_wtoi(argv[4]));
        config.enc_height = static_cast<uint32_t>(_wtoi(argv[5]));
    }
    fthr::ContinuousRecordingWriter writer;
    fthr::HardwareEncoder encoder(fthr::VideoCodec::H264);
    std::atomic<bool> accepted{true};
    std::atomic<bool> recording{false};
    std::atomic<int64_t> first_recorded_pts{-1};
    if (!encoder.Initialize(config, device.Get(), context.Get(),
            [&](const uint8_t* data, uint32_t size, int64_t pts, bool key, int64_t) {
                if (recording.load()) {
                    if (key && first_recorded_pts.load() < 0) first_recorded_pts.store(pts);
                    if (!writer.PushVideo(data, size, pts, key)) accepted.store(false);
                }
            }, cpu_input)) return 6;
    std::vector<uint8_t> pixels(config.src_width * config.src_height * 4, 128);
    ComPtr<ID3D11Texture2D> texture;
    if (!cpu_input) {
        D3D11_TEXTURE2D_DESC desc{};
        desc.Width = config.src_width;
        desc.Height = config.src_height;
        desc.MipLevels = desc.ArraySize = 1;
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        desc.SampleDesc.Count = 1;
        desc.Usage = D3D11_USAGE_DEFAULT;
        desc.BindFlags = D3D11_BIND_RENDER_TARGET;
        D3D11_SUBRESOURCE_DATA data{};
        data.pSysMem = pixels.data();
        data.SysMemPitch = config.src_width * 4;
        if (FAILED(device->CreateTexture2D(&desc, &data, &texture))) return 9;
    }
    LARGE_INTEGER start{}, frequency{};
    QueryPerformanceCounter(&start);
    QueryPerformanceFrequency(&frequency);
    bool submitted = true;
    for (int frame = 0; frame < 180; ++frame) {
        // Start mid-GOP, as the UI does against an already running replay.
        if (frame == 31) {
            if (!writer.Start(std::filesystem::path(argv[1]), encoder.GetVideoConfig())) return 7;
            recording.store(true);
            encoder.RequestKeyframe();
        }
        const int64_t qpc = start.QuadPart + frame * frequency.QuadPart / config.fps;
        bool encoded = false;
        if (cpu_input) {
            encoded = encoder.EncodeFrameCPU(pixels.data(), config.src_width * 4, qpc);
        } else if (encoder.RequiresBackendGpuPreparation()) {
            encoded = encoder.PrepareGpuFrame(texture.Get()) && encoder.EncodeFrame(qpc);
        } else {
            auto* input = encoder.GetCurrentInputTexture();
            if (input) {
                context->CopyResource(input, texture.Get());
                encoded = encoder.EncodeFrame(qpc);
            }
        }
        if (!encoded) {
            std::cerr << "SMOKE submit failed at frame " << frame << ": "
                      << encoder.GetLastError() << std::endl;
            submitted = false;
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(17));
    }
    encoder.Finalize();
    const auto close_start = std::chrono::steady_clock::now();
    const bool saved = writer.Stop();
    std::cout << "SMOKE submitted=" << submitted << " accepted=" << accepted.load()
              << " saved=" << saved << " close_ms="
              << std::chrono::duration_cast<std::chrono::milliseconds>(
                     std::chrono::steady_clock::now() - close_start).count()
              << " first_recorded_pts=" << first_recorded_pts.load() << std::endl;
    return submitted && accepted.load() && saved && first_recorded_pts.load() == 31 ? 0 : 8;
}

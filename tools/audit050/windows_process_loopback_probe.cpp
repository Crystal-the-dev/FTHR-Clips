// AUDIT-050 isolated Windows process-loopback validation probe.
//
// This file is intentionally not part of the FTHR capture engine. It probes
// the documented Windows 11 process-loopback API and writes a bounded WAV when
// the host supports it. No application is rerouted and no process is injected.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace {

struct RtlOsVersionInfo {
    ULONG dwOSVersionInfoSize;
    ULONG dwMajorVersion;
    ULONG dwMinorVersion;
    ULONG dwBuildNumber;
    ULONG dwPlatformId;
    WCHAR szCSDVersion[128];
};

using RtlGetVersionFn = LONG(WINAPI*)(RtlOsVersionInfo*);

std::string HResultString(HRESULT hr) {
    std::ostringstream out;
    out << "0x" << std::hex << std::uppercase
        << static_cast<unsigned long>(hr);
    return out.str();
}

void PrintOsVersion() {
    auto ntdll = GetModuleHandleW(L"ntdll.dll");
    auto get_version = ntdll
        ? reinterpret_cast<RtlGetVersionFn>(
            GetProcAddress(ntdll, "RtlGetVersion"))
        : nullptr;
    RtlOsVersionInfo info{};
    info.dwOSVersionInfoSize = sizeof(info);
    if (get_version && get_version(&info) == 0) {
        std::cout << "OS_BUILD=" << info.dwBuildNumber << "\n";
    } else {
        std::cout << "OS_BUILD=unknown\n";
    }
}

class ActivationHandler final
    : public IActivateAudioInterfaceCompletionHandler {
public:
    ActivationHandler() : event_(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}
    ~ActivationHandler() {
        if (event_) CloseHandle(event_);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown
            || iid == __uuidof(IActivateAudioInterfaceCompletionHandler)) {
            *object = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG refs = --refs_;
        if (refs == 0) delete this;
        return refs;
    }

    HRESULT STDMETHODCALLTYPE ActivateCompleted(
        IActivateAudioInterfaceAsyncOperation* operation) override {
        if (operation) {
            operation->GetActivateResult(&result_, &activated_);
        } else {
            result_ = E_POINTER;
        }
        if (event_) SetEvent(event_);
        return S_OK;
    }

    bool Wait(DWORD timeout_ms) {
        return event_ && WaitForSingleObject(event_, timeout_ms) == WAIT_OBJECT_0;
    }

    HRESULT result() const { return result_; }
    ComPtr<IUnknown> activated() const { return activated_; }

private:
    std::atomic<ULONG> refs_{1};
    HANDLE event_ = nullptr;
    HRESULT result_ = E_PENDING;
    ComPtr<IUnknown> activated_;
};

#pragma pack(push, 1)
struct WaveHeader {
    char riff[4] = {'R', 'I', 'F', 'F'};
    uint32_t riff_size = 0;
    char wave[4] = {'W', 'A', 'V', 'E'};
    char fmt[4] = {'f', 'm', 't', ' '};
    uint32_t fmt_size = 0;
    WAVEFORMATEX format{};
    char data[4] = {'d', 'a', 't', 'a'};
    uint32_t data_size = 0;
};
#pragma pack(pop)

bool WriteWave(const std::wstring& path, const WAVEFORMATEX* format,
               const std::vector<uint8_t>& bytes) {
    if (!format) return false;
    WaveHeader header;
    header.fmt_size = sizeof(WAVEFORMATEX);
    header.format = *format;
    header.data_size = static_cast<uint32_t>(bytes.size());
    header.riff_size = 4 + 8 + header.fmt_size + 8 + header.data_size;
    std::ofstream file(path, std::ios::binary);
    if (!file) return false;
    file.write(reinterpret_cast<const char*>(&header), sizeof(header));
    file.write(reinterpret_cast<const char*>(bytes.data()),
               static_cast<std::streamsize>(bytes.size()));
    return file.good();
}

int CaptureProcess(DWORD pid, const std::wstring& output, double seconds) {
    AUDIOCLIENT_ACTIVATION_PARAMS activation{};
    activation.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    activation.ProcessLoopbackParams.TargetProcessId = pid;
    activation.ProcessLoopbackParams.ProcessLoopbackMode =
        PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;

    PROPVARIANT params;
    PropVariantInit(&params);
    params.vt = VT_BLOB;
    params.blob.cbSize = sizeof(activation);
    params.blob.pBlobData = static_cast<BYTE*>(
        CoTaskMemAlloc(sizeof(activation)));
    if (!params.blob.pBlobData) {
        std::cerr << "PROCESS_LOOPBACK_PROPVARIANT_ALLOC_FAILED\n";
        return 4;
    }
    std::memcpy(params.blob.pBlobData, &activation, sizeof(activation));

    auto* handler = new ActivationHandler();
    ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
    HRESULT hr = ActivateAudioInterfaceAsync(
        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        __uuidof(IAudioClient), &params, handler, &operation);
    PropVariantClear(&params);
    if (FAILED(hr)) {
        handler->Release();
        std::cerr << "PROCESS_LOOPBACK_ACTIVATE_FAILED "
                  << HResultString(hr) << "\n";
        return 2;
    }
    if (!handler->Wait(5000)) {
        handler->Release();
        std::cerr << "PROCESS_LOOPBACK_ACTIVATE_TIMEOUT\n";
        return 3;
    }
    hr = handler->result();
    ComPtr<IUnknown> activated = handler->activated();
    handler->Release();
    if (FAILED(hr) || !activated) {
        std::cerr << "PROCESS_LOOPBACK_UNAVAILABLE "
                  << HResultString(FAILED(hr) ? hr : E_NOINTERFACE) << "\n";
        return 2;
    }

    ComPtr<IAudioClient> client;
    hr = activated.As(&client);
    if (FAILED(hr)) {
        std::cerr << "PROCESS_LOOPBACK_CLIENT_FAILED " << HResultString(hr) << "\n";
        return 2;
    }
    WAVEFORMATEX* mix_format = nullptr;
    hr = client->GetMixFormat(&mix_format);
    if (FAILED(hr) || !mix_format) {
        std::cerr << "PROCESS_LOOPBACK_FORMAT_FAILED " << HResultString(hr) << "\n";
        return 2;
    }

    HANDLE sample_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!sample_event) {
        CoTaskMemFree(mix_format);
        return 4;
    }
    constexpr DWORD flags = AUDCLNT_STREAMFLAGS_LOOPBACK
        | AUDCLNT_STREAMFLAGS_EVENTCALLBACK;
    hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED, flags,
                            0, 0, mix_format, nullptr);
    if (SUCCEEDED(hr)) hr = client->SetEventHandle(sample_event);
    ComPtr<IAudioCaptureClient> capture;
    if (SUCCEEDED(hr)) hr = client->GetService(IID_PPV_ARGS(&capture));
    if (FAILED(hr)) {
        std::cerr << "PROCESS_LOOPBACK_INITIALIZE_FAILED "
                  << HResultString(hr) << "\n";
        CloseHandle(sample_event);
        CoTaskMemFree(mix_format);
        return 2;
    }
    hr = client->Start();
    if (FAILED(hr)) {
        std::cerr << "PROCESS_LOOPBACK_START_FAILED " << HResultString(hr) << "\n";
        CloseHandle(sample_event);
        CoTaskMemFree(mix_format);
        return 2;
    }

    std::vector<uint8_t> audio;
    const ULONGLONG deadline = GetTickCount64()
        + static_cast<ULONGLONG>(seconds * 1000.0);
    uint64_t frames = 0;
    while (GetTickCount64() < deadline) {
        WaitForSingleObject(sample_event, 250);
        UINT32 packet_frames = 0;
        if (FAILED(capture->GetNextPacketSize(&packet_frames))) break;
        while (packet_frames > 0) {
            BYTE* data = nullptr;
            UINT32 available = 0;
            DWORD packet_flags = 0;
            hr = capture->GetBuffer(&data, &available, &packet_flags,
                                    nullptr, nullptr);
            if (FAILED(hr)) break;
            const size_t packet_bytes = static_cast<size_t>(available)
                * mix_format->nBlockAlign;
            const size_t begin = audio.size();
            audio.resize(begin + packet_bytes, 0);
            if (!(packet_flags & AUDCLNT_BUFFERFLAGS_SILENT) && data) {
                std::copy(data, data + packet_bytes, audio.begin() + begin);
            }
            frames += available;
            capture->ReleaseBuffer(available);
            if (FAILED(capture->GetNextPacketSize(&packet_frames))) {
                packet_frames = 0;
                break;
            }
        }
    }
    client->Stop();
    CloseHandle(sample_event);
    const bool written = WriteWave(output, mix_format, audio);
    std::cout << "PROCESS_LOOPBACK_CAPTURE pid=" << pid
              << " frames=" << frames
              << " bytes=" << audio.size()
              << " sample_rate=" << mix_format->nSamplesPerSec
              << " channels=" << mix_format->nChannels
              << " written=" << (written ? "true" : "false") << "\n";
    CoTaskMemFree(mix_format);
    return written ? 0 : 5;
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    PrintOsVersion();
    if (argc < 2) {
        std::wcerr << L"usage: windows_process_loopback_probe.exe <pid> [wav] [seconds]\n";
        return 64;
    }
    const DWORD pid = static_cast<DWORD>(std::wcstoul(argv[1], nullptr, 10));
    const std::wstring output = argc >= 3 ? argv[2] : L"audit050-process-loopback.wav";
    const double seconds = argc >= 4 ? std::wcstod(argv[3], nullptr) : 3.0;
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) {
        std::cerr << "COM_INIT_FAILED " << HResultString(hr) << "\n";
        return 1;
    }
    const int result = CaptureProcess(pid, output, (std::max)(0.1, seconds));
    CoUninitialize();
    return result;
}

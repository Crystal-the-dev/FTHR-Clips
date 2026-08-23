// Native, generation-local Windows microphone source for AUDIT-050 Phase 2.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <audioclient.h>
#include <propkeydef.h>
#include <functiondiscoverykeys_devpkey.h>
#include <mmdeviceapi.h>
#include <propvarutil.h>
#include <wrl/client.h>

#include "windows_microphone_audio_provider.h"

#include "audio_encoder.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <limits>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

extern "C" {
#include <libavutil/channel_layout.h>
#include <libavutil/mathematics.h>
#include <libavutil/samplefmt.h>
#include <libswresample/swresample.h>
}

#pragma comment(lib, "Ole32.lib")
#pragma comment(lib, "Propsys.lib")

using Microsoft::WRL::ComPtr;

namespace fthr {
namespace {

constexpr DWORD kCaptureWaitMs = 250;

std::string Utf8FromWide(const wchar_t* value) {
    if (!value || !*value) return {};
    const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1,
        nullptr, 0, nullptr, nullptr);
    if (size <= 1) return {};
    std::string result(static_cast<size_t>(size), '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1,
            result.data(), size, nullptr, nullptr) != size) {
        return {};
    }
    result.pop_back();
    return result;
}

std::string HrText(HRESULT hr) {
    char text[32] = {};
    std::snprintf(text, sizeof(text), "0x%08lX", static_cast<unsigned long>(hr));
    return text;
}

std::optional<AVSampleFormat> WaveSampleFormat(const WAVEFORMATEX* format) {
    if (!format || format->nChannels == 0 || format->nSamplesPerSec == 0) return std::nullopt;
    WORD tag = format->wFormatTag;
    GUID sub_format{};
    if (tag == WAVE_FORMAT_EXTENSIBLE) {
        if (format->cbSize < sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX))
            return std::nullopt;
        const auto* extensible = reinterpret_cast<const WAVEFORMATEXTENSIBLE*>(format);
        sub_format = extensible->SubFormat;
        if (sub_format == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT) tag = WAVE_FORMAT_IEEE_FLOAT;
        else if (sub_format == KSDATAFORMAT_SUBTYPE_PCM) tag = WAVE_FORMAT_PCM;
        else return std::nullopt;
    }
    if (tag == WAVE_FORMAT_IEEE_FLOAT && format->wBitsPerSample == 32)
        return AV_SAMPLE_FMT_FLT;
    if (tag == WAVE_FORMAT_PCM && format->wBitsPerSample == 16)
        return AV_SAMPLE_FMT_S16;
    if (tag == WAVE_FORMAT_PCM && format->wBitsPerSample == 32)
        return AV_SAMPLE_FMT_S32;
    return std::nullopt;
}

std::string FriendlyName(IMMDevice* device) {
    if (!device) return {};
    ComPtr<IPropertyStore> properties;
    PROPVARIANT name;
    PropVariantInit(&name);
    const HRESULT hr = device->OpenPropertyStore(STGM_READ, &properties);
    const HRESULT get = SUCCEEDED(hr)
        ? properties->GetValue(PKEY_Device_FriendlyName, &name) : hr;
    const std::string result = SUCCEEDED(get) && name.vt == VT_LPWSTR
        ? Utf8FromWide(name.pwszVal) : std::string{};
    PropVariantClear(&name);
    return result;
}

struct MicrophoneSession {
    ComPtr<IAudioClient> audio_client;
    ComPtr<IAudioCaptureClient> capture_client;
    WAVEFORMATEX* format = nullptr;
    HANDLE sample_event = nullptr;
    SwrContext* resampler = nullptr;
    AVSampleFormat input_format = AV_SAMPLE_FMT_NONE;
    std::vector<float> converted;
    std::vector<uint8_t> silent_input;

    void Close() {
        if (audio_client) audio_client->Stop();
        capture_client.Reset();
        audio_client.Reset();
        if (sample_event) CloseHandle(sample_event);
        sample_event = nullptr;
        if (format) CoTaskMemFree(format);
        format = nullptr;
        swr_free(&resampler);
        converted.clear();
        silent_input.clear();
    }
    ~MicrophoneSession() { Close(); }
};

}  // namespace

bool EnumerateWindowsMicrophoneEndpoints(
    std::vector<WindowsMicrophoneEndpoint>* endpoints, std::string* error) {
    if (!endpoints) return false;
    endpoints->clear();
    const HRESULT apartment = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(apartment) && apartment != RPC_E_CHANGED_MODE) {
        if (error) *error = "microphone enumeration could not initialize COM " + HrText(apartment);
        return false;
    }
    ComPtr<IMMDeviceEnumerator> enumerator;
    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
        IID_PPV_ARGS(&enumerator));
    if (FAILED(hr)) {
        if (error) *error = "microphone enumeration could not create device enumerator " + HrText(hr);
        if (SUCCEEDED(apartment)) CoUninitialize();
        return false;
    }
    ComPtr<IMMDevice> default_device;
    std::wstring default_id;
    if (SUCCEEDED(enumerator->GetDefaultAudioEndpoint(eCapture, eConsole, &default_device))) {
        LPWSTR value = nullptr;
        if (SUCCEEDED(default_device->GetId(&value)) && value) {
            default_id = value;
            CoTaskMemFree(value);
        }
    }
    ComPtr<IMMDeviceCollection> collection;
    hr = enumerator->EnumAudioEndpoints(eCapture, DEVICE_STATEMASK_ALL, &collection);
    if (FAILED(hr)) {
        if (error) *error = "microphone enumeration could not list endpoints " + HrText(hr);
        if (SUCCEEDED(apartment)) CoUninitialize();
        return false;
    }
    UINT count = 0;
    collection->GetCount(&count);
    endpoints->reserve(count);
    for (UINT index = 0; index < count; ++index) {
        ComPtr<IMMDevice> device;
        if (FAILED(collection->Item(index, &device))) continue;
        LPWSTR endpoint_id = nullptr;
        if (FAILED(device->GetId(&endpoint_id)) || !endpoint_id) continue;
        DWORD state = 0;
        device->GetState(&state);
        WindowsMicrophoneEndpoint endpoint;
        endpoint.endpoint_id = endpoint_id;
        endpoint.display_name = FriendlyName(device.Get());
        endpoint.device_state = state;
        endpoint.is_default = endpoint.endpoint_id == default_id;
        CoTaskMemFree(endpoint_id);
        if (!endpoint.endpoint_id.empty() && !endpoint.display_name.empty())
            endpoints->push_back(std::move(endpoint));
    }
    std::sort(endpoints->begin(), endpoints->end(), [](const auto& left, const auto& right) {
        if (left.is_default != right.is_default) return left.is_default;
        return left.display_name < right.display_name;
    });
    if (SUCCEEDED(apartment)) CoUninitialize();
    return true;
}

struct WindowsMicrophoneAudioProvider::Impl {
    explicit Impl(WindowsMicrophoneAudioProviderConfig initial) : config(std::move(initial)) {}

    WindowsMicrophoneAudioProviderConfig config;
    std::thread thread;
    HANDLE stop_event = nullptr;
    std::atomic<bool> running{false};
    std::atomic<bool> failed{false};
    std::atomic<uint64_t> timeline_origin_100ns{0};
    std::atomic<uint64_t> last_packet_qpc_100ns{0};
    mutable std::mutex state_mutex;
    std::string error;
    WindowsMicrophoneRuntimeInfo info;
    std::unique_ptr<AudioEncoder> encoder;
    std::unique_ptr<EncodedAudioPacketRing> ring;
    AudioDriftController drift{kCanonicalAudioSampleRate};
    int64_t submitted_frames = 0;

    void SetError(std::string value) {
        std::lock_guard<std::mutex> lock(state_mutex);
        error = std::move(value);
    }

    bool StopRequested() const {
        return stop_event && WaitForSingleObject(stop_event, 0) == WAIT_OBJECT_0;
    }

    bool OpenSession(MicrophoneSession* session) {
        if (!session) return false;
        ComPtr<IMMDeviceEnumerator> enumerator;
        HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
            IID_PPV_ARGS(&enumerator));
        if (FAILED(hr)) {
            SetError("microphone could not create device enumerator " + HrText(hr));
            return false;
        }
        ComPtr<IMMDevice> device;
        if (config.use_default_endpoint) {
            hr = enumerator->GetDefaultAudioEndpoint(eCapture, eConsole, &device);
        } else if (!config.endpoint_id.empty()) {
            hr = enumerator->GetDevice(config.endpoint_id.c_str(), &device);
        } else {
            hr = E_INVALIDARG;
        }
        if (FAILED(hr) || !device) {
            SetError(config.use_default_endpoint
                ? "default microphone is unavailable " + HrText(hr)
                : "selected microphone is unavailable; it was not replaced " + HrText(hr));
            return false;
        }
        LPWSTR active_id = nullptr;
        if (SUCCEEDED(device->GetId(&active_id)) && active_id) {
            std::lock_guard<std::mutex> lock(state_mutex);
            info.active_endpoint_id = active_id;
            info.active_display_name = FriendlyName(device.Get());
            CoTaskMemFree(active_id);
        }
        hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
            reinterpret_cast<void**>(session->audio_client.GetAddressOf()));
        if (FAILED(hr)) {
            SetError("microphone IAudioClient activation failed " + HrText(hr));
            return false;
        }
        hr = session->audio_client->GetMixFormat(&session->format);
        if (FAILED(hr) || !session->format) {
            SetError("microphone mix-format query failed " + HrText(hr));
            return false;
        }
        const auto input_format = WaveSampleFormat(session->format);
        if (!input_format) {
            SetError("microphone source format is unsupported; source is not captured");
            return false;
        }
        session->input_format = *input_format;
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            info.input_format = {session->format->nSamplesPerSec, session->format->nChannels,
                                 "device-native"};
        }

        AVChannelLayout input_layout{};
        AVChannelLayout output_layout{};
        av_channel_layout_default(&input_layout, session->format->nChannels);
        av_channel_layout_default(&output_layout, kCanonicalAudioChannels);
        const int setup = swr_alloc_set_opts2(&session->resampler,
            &output_layout, AV_SAMPLE_FMT_FLT, kCanonicalAudioSampleRate,
            &input_layout, session->input_format,
            static_cast<int>(session->format->nSamplesPerSec), 0, nullptr);
        av_channel_layout_uninit(&input_layout);
        av_channel_layout_uninit(&output_layout);
        if (setup < 0 || !session->resampler || swr_init(session->resampler) < 0) {
            SetError("microphone canonical resampler initialization failed");
            return false;
        }
        session->sample_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!session->sample_event) {
            SetError("microphone sample event creation failed");
            return false;
        }
        hr = session->audio_client->Initialize(AUDCLNT_SHAREMODE_SHARED,
            AUDCLNT_STREAMFLAGS_EVENTCALLBACK, 0, 0, session->format, nullptr);
        if (SUCCEEDED(hr)) hr = session->audio_client->SetEventHandle(session->sample_event);
        if (SUCCEEDED(hr)) hr = session->audio_client->GetService(IID_PPV_ARGS(&session->capture_client));
        if (FAILED(hr)) {
            SetError("microphone WASAPI initialization failed " + HrText(hr));
            return false;
        }
        hr = session->audio_client->Start();
        if (FAILED(hr)) {
            SetError("microphone IAudioClient::Start failed " + HrText(hr));
            return false;
        }
        return true;
    }

    bool EnsureEncoder(uint64_t qpc_100ns) {
        if (encoder && ring) return true;
        if (qpc_100ns == 0 || !config.source.identity.id.IsValid()) {
            SetError("microphone did not receive a usable capture timestamp");
            return false;
        }
        auto new_ring = std::make_unique<EncodedAudioPacketRing>(
            config.source.identity.id, config.generation,
            AudioSourceFormat{kCanonicalAudioSampleRate, kCanonicalAudioChannels, "fltp"},
            config.retention_seconds);
        auto new_encoder = std::make_unique<AudioEncoder>();
        if (!new_encoder->Initialize(kCanonicalAudioSampleRate, kCanonicalAudioChannels,
                config.bitrate_kbps,
                [packet_ring = new_ring.get()](const uint8_t* data, uint32_t size, int64_t pts) {
                    packet_ring->Push({std::vector<uint8_t>(data, data + size), pts, 1024});
                })) {
            SetError("microphone AAC encoder initialization failed");
            return false;
        }
        new_ring->SetCodecExtradata(new_encoder->GetExtradata());
        timeline_origin_100ns.store(qpc_100ns, std::memory_order_release);
        drift.Reset();
        drift.Observe(qpc_100ns, 0);
        submitted_frames = 0;
        ring = std::move(new_ring);
        encoder = std::move(new_encoder);
        return true;
    }

    bool ConvertAndSubmit(MicrophoneSession* session, const BYTE* data,
                          uint32_t input_frames, uint64_t qpc_100ns, bool silent) {
        if (!session || input_frames == 0) return true;
        if (qpc_100ns == 0) qpc_100ns = CurrentAudioTimeline100ns();
        if (!EnsureEncoder(qpc_100ns)) return false;
        const auto adjustment = drift.Observe(qpc_100ns, submitted_frames);
        if (adjustment.sample_delta != 0 && adjustment.compensation_distance_samples > 0
                && swr_set_compensation(session->resampler, adjustment.sample_delta,
                    static_cast<int>(adjustment.compensation_distance_samples)) < 0) {
            SetError("microphone drift compensation setup failed");
            return false;
        }
        const int64_t capacity_64 = av_rescale_rnd(
            swr_get_delay(session->resampler, session->format->nSamplesPerSec) + input_frames,
            kCanonicalAudioSampleRate, session->format->nSamplesPerSec, AV_ROUND_UP)
            + std::max<int32_t>(0, adjustment.sample_delta);
        if (capacity_64 <= 0 || capacity_64 > static_cast<int64_t>(std::numeric_limits<int>::max())) {
            SetError("microphone resampler returned an invalid output capacity");
            return false;
        }
        const int capacity = static_cast<int>(capacity_64);
        session->converted.resize(static_cast<size_t>(capacity) * kCanonicalAudioChannels);
        const uint8_t* input = data;
        if (silent || !input) {
            const size_t bytes = static_cast<size_t>(input_frames) * session->format->nBlockAlign;
            session->silent_input.assign(bytes, 0);
            input = session->silent_input.data();
        }
        const uint8_t* input_planes[] = {input};
        uint8_t* output_planes[] = {reinterpret_cast<uint8_t*>(session->converted.data())};
        const int converted = swr_convert(session->resampler, output_planes, capacity,
            input_planes, static_cast<int>(input_frames));
        if (converted < 0) {
            SetError("microphone resampling failed");
            return false;
        }
        if (converted > 0) {
            const float gain = std::clamp(config.input_gain, 0.0f, 2.0f);
            if (gain != 1.0f) {
                for (float& sample : session->converted) sample *= gain;
            }
            encoder->EncodeSamples(session->converted.data(),
                static_cast<uint32_t>(converted) * kCanonicalAudioChannels);
            submitted_frames += converted;
            last_packet_qpc_100ns.store(qpc_100ns, std::memory_order_release);
        }
        {
            std::lock_guard<std::mutex> lock(state_mutex);
            info.max_observed_drift_samples = drift.max_observed_drift_samples();
        }
        return true;
    }

    HRESULT CaptureSession(MicrophoneSession* session) {
        while (!StopRequested()) {
            HANDLE waits[] = {stop_event, session->sample_event};
            const DWORD wait = WaitForMultipleObjects(2, waits, FALSE, kCaptureWaitMs);
            if (wait == WAIT_OBJECT_0) return S_FALSE;
            if (wait == WAIT_TIMEOUT) continue;
            if (wait != WAIT_OBJECT_0 + 1) return E_FAIL;
            UINT32 packet_frames = 0;
            HRESULT hr = session->capture_client->GetNextPacketSize(&packet_frames);
            if (FAILED(hr)) return hr;
            while (packet_frames > 0) {
                BYTE* data = nullptr;
                UINT32 frames = 0;
                DWORD flags = 0;
                UINT64 device_position = 0;
                UINT64 qpc_100ns = 0;
                hr = session->capture_client->GetBuffer(&data, &frames, &flags,
                    &device_position, &qpc_100ns);
                if (FAILED(hr)) return hr;
                const bool converted = ConvertAndSubmit(session, data, frames, qpc_100ns,
                    (flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0);
                const HRESULT release = session->capture_client->ReleaseBuffer(frames);
                if (!converted) return E_FAIL;
                if (FAILED(release)) return release;
                hr = session->capture_client->GetNextPacketSize(&packet_frames);
                if (FAILED(hr)) return hr;
            }
        }
        return S_FALSE;
    }

    void Run() {
        const HRESULT apartment = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        if (FAILED(apartment) && apartment != RPC_E_CHANGED_MODE) {
            SetError("microphone capture could not initialize COM " + HrText(apartment));
            failed.store(true, std::memory_order_release);
            running.store(false, std::memory_order_release);
            return;
        }
        MicrophoneSession session;
        if (!OpenSession(&session)) {
            failed.store(true, std::memory_order_release);
        } else {
            const HRESULT capture = CaptureSession(&session);
            if (FAILED(capture) && !StopRequested()) {
                SetError("microphone capture stopped " + HrText(capture)
                    + "; restart capture to use this source again");
                failed.store(true, std::memory_order_release);
            }
        }
        if (encoder) encoder->Finalize();
        running.store(false, std::memory_order_release);
        if (SUCCEEDED(apartment)) CoUninitialize();
    }
};

WindowsMicrophoneAudioProvider::WindowsMicrophoneAudioProvider(
    WindowsMicrophoneAudioProviderConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

WindowsMicrophoneAudioProvider::~WindowsMicrophoneAudioProvider() {
    Stop();
}

bool WindowsMicrophoneAudioProvider::Start() {
    if (!impl_ || impl_->running.exchange(true, std::memory_order_acq_rel)) return false;
    impl_->stop_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!impl_->stop_event) {
        impl_->SetError("microphone stop event creation failed");
        impl_->running.store(false, std::memory_order_release);
        return false;
    }
    impl_->thread = std::thread([this] { impl_->Run(); });
    return true;
}

void WindowsMicrophoneAudioProvider::Stop() {
    if (!impl_) return;
    if (impl_->stop_event) SetEvent(impl_->stop_event);
    if (impl_->thread.joinable()) impl_->thread.join();
    if (impl_->stop_event) CloseHandle(impl_->stop_event);
    impl_->stop_event = nullptr;
    impl_->running.store(false, std::memory_order_release);
}

bool WindowsMicrophoneAudioProvider::IsRunning() const {
    return impl_ && impl_->running.load(std::memory_order_acquire);
}

const std::string& WindowsMicrophoneAudioProvider::last_error() const {
    static const std::string empty;
    return impl_ ? impl_->error : empty;
}

WindowsMicrophoneRuntimeInfo WindowsMicrophoneAudioProvider::runtime_info() const {
    if (!impl_) return {};
    std::lock_guard<std::mutex> lock(impl_->state_mutex);
    WindowsMicrophoneRuntimeInfo info = impl_->info;
    info.failed = impl_->failed.load(std::memory_order_acquire);
    return info;
}

std::optional<EncodedAudioTrack> WindowsMicrophoneAudioProvider::TakeTrackForInterval(
    double presentation_start_qpc_s, double presentation_end_qpc_s,
    AudioSourceMetadata source) const {
    if (!impl_ || !impl_->ring || presentation_end_qpc_s <= presentation_start_qpc_s) return std::nullopt;
    const uint64_t origin = impl_->timeline_origin_100ns.load(std::memory_order_acquire);
    const uint64_t last = impl_->last_packet_qpc_100ns.load(std::memory_order_acquire);
    if (origin == 0 || last < origin) return std::nullopt;
    const auto range = MapAudioSourcePresentationRange(
        presentation_start_qpc_s, presentation_end_qpc_s, origin, kCanonicalAudioSampleRate);
    EncodedAudioSnapshot snapshot = impl_->ring->TakeSnapshot(
        range.start_pts_samples, range.end_pts_samples);
    if (!snapshot.valid()) return std::nullopt;
    source.format = snapshot.format;
    source.state.admitted = true;
    source.state.active_in_generation = !impl_->failed.load(std::memory_order_acquire);
    source.state.health = impl_->failed.load(std::memory_order_acquire)
        ? AudioSourceHealth::Failed : AudioSourceHealth::Active;
    // Manifest timing is clip-local packet history, not the lifetime of the
    // endpoint. In particular, a microphone active before the requested
    // replay interval must not be reported as if it began before this clip.
    source.state.first_active_100ns = static_cast<int64_t>(origin
        + (static_cast<uint64_t>(snapshot.first_pts_samples) * 10'000'000ULL)
            / kCanonicalAudioSampleRate);
    source.state.last_active_100ns = static_cast<int64_t>(origin
        + (static_cast<uint64_t>(snapshot.last_pts_samples) * 10'000'000ULL)
            / kCanonicalAudioSampleRate);
    EncodedAudioTrack track;
    track.source = std::move(source);
    track.snapshot = std::move(snapshot);
    track.presentation_start_pts_samples = range.start_pts_samples;
    return track;
}

}  // namespace fthr

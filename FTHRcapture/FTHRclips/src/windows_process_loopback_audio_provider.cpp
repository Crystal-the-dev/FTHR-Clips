// Windows 11 process-loopback application-stem implementation for AUDIT-050.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <ksmedia.h>
#include <mmdeviceapi.h>
#include <propvarutil.h>
#include <wrl/client.h>

#include "windows_process_loopback_audio_provider.h"

#include "audio_encoder.h"
#include "clip_audio_manifest.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <deque>
#include <iostream>
#include <map>
#include <mutex>
#include <limits>
#include <thread>
#include <utility>

extern "C" {
#include <libavutil/channel_layout.h>
#include <libavutil/mathematics.h>
#include <libavutil/samplefmt.h>
#include <libswresample/swresample.h>
}

#pragma comment(lib, "Ole32.lib")

using Microsoft::WRL::ComPtr;

namespace fthr {

namespace {

constexpr uint32_t kCanonicalSampleRate = 48000;
constexpr uint32_t kCanonicalChannels = 2;
constexpr DWORD kActivationTimeoutMs = 5000;
constexpr DWORD kCaptureWaitMs = 250;
constexpr uint32_t kMaxRecoveryAttempts = 3;

int64_t CurrentQpc100ns() {
    LARGE_INTEGER counter{};
    LARGE_INTEGER frequency{};
    if (!QueryPerformanceCounter(&counter) || !QueryPerformanceFrequency(&frequency)
            || frequency.QuadPart <= 0) {
        return 0;
    }
    return static_cast<int64_t>((counter.QuadPart * 10'000'000LL) / frequency.QuadPart);
}

bool WaitForStop(HANDLE stop_event, DWORD timeout_ms) {
    return WaitForSingleObject(stop_event, timeout_ms) == WAIT_OBJECT_0;
}

bool IsRecoverableAudioFailure(HRESULT hr) {
    return hr == AUDCLNT_E_DEVICE_INVALIDATED
        || hr == AUDCLNT_E_SERVICE_NOT_RUNNING
        || hr == AUDCLNT_E_RESOURCES_INVALIDATED;
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
    // Packed 24-bit PCM needs a dedicated unpacking path.  Fail the source
    // explicitly instead of reinterpreting bytes as another sample type.
    return std::nullopt;
}

float CalculateRms(const float* samples, uint32_t frames) {
    if (!samples || frames == 0) return 0.0f;
    const size_t count = static_cast<size_t>(frames) * kCanonicalChannels;
    double sum = 0.0;
    for (size_t index = 0; index < count; ++index) {
        const double sample = samples[index];
        sum += sample * sample;
    }
    return static_cast<float>(std::sqrt(sum / static_cast<double>(count)));
}

class ActivationHandler final : public IActivateAudioInterfaceCompletionHandler {
public:
    ActivationHandler() : event_(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}
    ~ActivationHandler() {
        if (event_) CloseHandle(event_);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == __uuidof(IActivateAudioInterfaceCompletionHandler)) {
            *object = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG remaining = --references_;
        if (remaining == 0) delete this;
        return remaining;
    }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(
        IActivateAudioInterfaceAsyncOperation* operation) override {
        if (operation) operation->GetActivateResult(&result_, &activated_);
        else result_ = E_POINTER;
        if (event_) SetEvent(event_);
        return S_OK;
    }

    HANDLE event() const { return event_; }
    HRESULT result() const { return result_; }
    ComPtr<IUnknown> activated() const { return activated_; }

private:
    std::atomic<ULONG> references_{1};
    HANDLE event_ = nullptr;
    HRESULT result_ = E_PENDING;
    ComPtr<IUnknown> activated_;
};

struct ProcessLoopbackSession {
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
    ~ProcessLoopbackSession() { Close(); }
};

}  // namespace

AudioSourcePresentationRange MapAudioSourcePresentationRange(
    double presentation_start_qpc_s, double presentation_end_qpc_s,
    uint64_t source_timeline_origin_100ns, uint32_t sample_rate) {
    AudioSourcePresentationRange result;
    if (source_timeline_origin_100ns == 0 || sample_rate == 0
            || presentation_end_qpc_s <= presentation_start_qpc_s) {
        return result;
    }
    const double origin_s = static_cast<double>(source_timeline_origin_100ns) / 10'000'000.0;
    result.start_pts_samples = static_cast<int64_t>(std::llround(
        (presentation_start_qpc_s - origin_s) * sample_rate));
    result.end_pts_samples = std::max<int64_t>(result.start_pts_samples + 1,
        static_cast<int64_t>(std::llround(
            (presentation_end_qpc_s - origin_s) * sample_rate)));
    return result;
}

struct WindowsProcessLoopbackAudioProvider::Impl {
    explicit Impl(WindowsProcessLoopbackProviderConfig initial,
                  ActivityCallback activity, EndedCallback ended, FailureCallback failure)
        : config(std::move(initial)), on_activity(std::move(activity)),
          on_ended(std::move(ended)), on_failure(std::move(failure)) {}

    WindowsProcessLoopbackProviderConfig config;
    ActivityCallback on_activity;
    EndedCallback on_ended;
    FailureCallback on_failure;
    std::thread thread;
    HANDLE stop_event = nullptr;
    std::atomic<bool> running{false};
    std::atomic<uint64_t> timeline_origin_100ns{0};
    mutable std::mutex state_mutex;
    std::string error;
    std::unique_ptr<AudioEncoder> encoder;
    std::unique_ptr<EncodedAudioPacketRing> ring;
    bool admitted = false;

    struct PendingBlock {
        std::vector<float> samples;
        uint32_t frames = 0;
        uint64_t qpc_100ns = 0;
    };
    std::deque<PendingBlock> pending;

    void SetError(std::string value) {
        std::lock_guard<std::mutex> lock(state_mutex);
        error = std::move(value);
    }

    bool OpenSession(ProcessLoopbackSession* session) {
        if (!session || config.target_process_id == 0) {
            SetError("process-loopback source has no target process");
            return false;
        }
        AUDIOCLIENT_ACTIVATION_PARAMS activation{};
        activation.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
        activation.ProcessLoopbackParams.TargetProcessId = config.target_process_id;
        activation.ProcessLoopbackParams.ProcessLoopbackMode = config.include_process_tree
            ? PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE
            : PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE;

        PROPVARIANT params;
        PropVariantInit(&params);
        params.vt = VT_BLOB;
        params.blob.cbSize = sizeof(activation);
        params.blob.pBlobData = static_cast<BYTE*>(CoTaskMemAlloc(sizeof(activation)));
        if (!params.blob.pBlobData) {
            SetError("process-loopback activation parameter allocation failed");
            return false;
        }
        std::memcpy(params.blob.pBlobData, &activation, sizeof(activation));

        auto* handler = new ActivationHandler();
        if (!handler || !handler->event()) {
            if (handler) handler->Release();
            PropVariantClear(&params);
            SetError("process-loopback activation event creation failed");
            return false;
        }
        ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
        HRESULT hr = ActivateAudioInterfaceAsync(
            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, __uuidof(IAudioClient),
            &params, handler, &operation);
        PropVariantClear(&params);
        if (FAILED(hr)) {
            handler->Release();
            SetError("ActivateAudioInterfaceAsync failed " + HrText(hr));
            return false;
        }
        HANDLE wait_handles[] = {stop_event, handler->event()};
        const DWORD wait = WaitForMultipleObjects(2, wait_handles, FALSE, kActivationTimeoutMs);
        if (wait != WAIT_OBJECT_0 + 1) {
            handler->Release();
            SetError(wait == WAIT_TIMEOUT ? "process-loopback activation timed out"
                                           : "process-loopback activation stopped");
            return false;
        }
        hr = handler->result();
        ComPtr<IUnknown> activated = handler->activated();
        handler->Release();
        if (FAILED(hr) || !activated || FAILED(activated.As(&session->audio_client))) {
            SetError("process-loopback activation did not return IAudioClient "
                + HrText(FAILED(hr) ? hr : E_NOINTERFACE));
            return false;
        }
        hr = session->audio_client->GetMixFormat(&session->format);
        if (FAILED(hr) || !session->format) {
            SetError("process-loopback mix format query failed " + HrText(hr));
            return false;
        }
        const auto input_format = WaveSampleFormat(session->format);
        if (!input_format) {
            SetError("process-loopback source format is unsupported; source is not captured");
            return false;
        }
        session->input_format = *input_format;

        AVChannelLayout input_layout{};
        AVChannelLayout output_layout{};
        av_channel_layout_default(&input_layout, session->format->nChannels);
        av_channel_layout_default(&output_layout, kCanonicalChannels);
        const int swr_result = swr_alloc_set_opts2(&session->resampler,
            &output_layout, AV_SAMPLE_FMT_FLT, kCanonicalSampleRate,
            &input_layout, session->input_format,
            static_cast<int>(session->format->nSamplesPerSec), 0, nullptr);
        av_channel_layout_uninit(&input_layout);
        av_channel_layout_uninit(&output_layout);
        if (swr_result < 0 || !session->resampler || swr_init(session->resampler) < 0) {
            SetError("process-loopback canonical resampler initialization failed");
            return false;
        }

        session->sample_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!session->sample_event) {
            SetError("process-loopback sample event creation failed");
            return false;
        }
        constexpr DWORD flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK;
        hr = session->audio_client->Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 0, 0,
            session->format, nullptr);
        if (SUCCEEDED(hr)) hr = session->audio_client->SetEventHandle(session->sample_event);
        if (SUCCEEDED(hr)) hr = session->audio_client->GetService(IID_PPV_ARGS(&session->capture_client));
        if (FAILED(hr)) {
            SetError("process-loopback WASAPI initialization failed " + HrText(hr));
            return false;
        }
        hr = session->audio_client->Start();
        if (FAILED(hr)) {
            SetError("process-loopback IAudioClient::Start failed " + HrText(hr));
            return false;
        }
        return true;
    }

    void EnsureEncoder(uint64_t first_qpc_100ns) {
        if (admitted || pending.empty()) return;
        const uint64_t origin = first_qpc_100ns > 0 ? first_qpc_100ns : pending.front().qpc_100ns;
        if (origin == 0) return;
        auto new_ring = std::make_unique<EncodedAudioPacketRing>(
            config.source.identity.id, config.generation,
            AudioSourceFormat{kCanonicalSampleRate, kCanonicalChannels, "fltp"},
            config.retention_seconds);
        auto new_encoder = std::make_unique<AudioEncoder>();
        if (!new_encoder->Initialize(kCanonicalSampleRate, kCanonicalChannels,
                config.bitrate_kbps,
                [ring = new_ring.get()](const uint8_t* data, uint32_t size, int64_t pts) {
                    ring->Push({std::vector<uint8_t>(data, data + size), pts, 1024});
                })) {
            SetError("process-loopback AAC encoder initialization failed");
            if (on_failure) on_failure(config.source.identity.id);
            pending.clear();
            return;
        }
        new_ring->SetCodecExtradata(new_encoder->GetExtradata());
        timeline_origin_100ns.store(origin, std::memory_order_release);
        ring = std::move(new_ring);
        encoder = std::move(new_encoder);
        admitted = true;
        for (const auto& block : pending) {
            encoder->EncodeSamples(block.samples.data(),
                block.frames * kCanonicalChannels);
        }
        pending.clear();
    }

    void SubmitNormalized(const float* samples, uint32_t frames, uint64_t qpc_100ns) {
        if (!samples || frames == 0) return;
        const float rms = CalculateRms(samples, frames);
        const auto admission = on_activity
            ? on_activity(config.source.identity.id, rms, static_cast<int64_t>(qpc_100ns))
            : AudioSourceAdmission::RejectedInvalidIdentity;
        if (!admitted) {
            PendingBlock pending_block;
            pending_block.samples.assign(samples,
                samples + static_cast<size_t>(frames) * kCanonicalChannels);
            pending_block.frames = frames;
            pending_block.qpc_100ns = qpc_100ns;
            pending.push_back(std::move(pending_block));
            while (pending.size() > 2) pending.pop_front();
            if (admission == AudioSourceAdmission::Accepted) EnsureEncoder(qpc_100ns);
            return;
        }
        if (encoder) encoder->EncodeSamples(samples, frames * kCanonicalChannels);
    }

    bool ConvertAndSubmit(ProcessLoopbackSession* session, const BYTE* data,
                          uint32_t input_frames, uint64_t qpc_100ns, bool silent) {
        if (!session || input_frames == 0) return true;
        const int64_t capacity_64 = av_rescale_rnd(
            swr_get_delay(session->resampler, session->format->nSamplesPerSec) + input_frames,
            kCanonicalSampleRate, session->format->nSamplesPerSec, AV_ROUND_UP);
        if (capacity_64 <= 0 || capacity_64 > static_cast<int64_t>(std::numeric_limits<int>::max())) {
            SetError("process-loopback resampler returned an invalid output capacity");
            return false;
        }
        const int capacity = static_cast<int>(capacity_64);
        session->converted.resize(static_cast<size_t>(capacity) * kCanonicalChannels);
        const uint8_t* input = data;
        if (silent || !input) {
            const size_t input_bytes = static_cast<size_t>(input_frames) * session->format->nBlockAlign;
            session->silent_input.assign(input_bytes, 0);
            input = session->silent_input.data();
        }
        const uint8_t* input_planes[] = {input};
        uint8_t* output_planes[] = {reinterpret_cast<uint8_t*>(session->converted.data())};
        const int converted = swr_convert(session->resampler, output_planes, capacity,
            input_planes, static_cast<int>(input_frames));
        if (converted < 0) {
            SetError("process-loopback resampling failed");
            return false;
        }
        SubmitNormalized(session->converted.data(), static_cast<uint32_t>(converted), qpc_100ns);
        return true;
    }

    HRESULT CaptureSession(ProcessLoopbackSession* session) {
        while (!WaitForStop(stop_event, 0)) {
            HANDLE wait_handles[] = {stop_event, session->sample_event};
            const DWORD wait = WaitForMultipleObjects(2, wait_handles, FALSE, kCaptureWaitMs);
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
                const HRESULT release_hr = session->capture_client->ReleaseBuffer(frames);
                if (!converted) return E_FAIL;
                if (FAILED(release_hr)) return release_hr;
                hr = session->capture_client->GetNextPacketSize(&packet_frames);
                if (FAILED(hr)) return hr;
            }
        }
        return S_FALSE;
    }

    void Run() {
        const HRESULT apartment = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        if (FAILED(apartment) && apartment != RPC_E_CHANGED_MODE) {
            SetError("process-loopback COM initialization failed " + HrText(apartment));
            if (on_failure) on_failure(config.source.identity.id);
            running.store(false, std::memory_order_release);
            return;
        }

        uint32_t recovery_attempts = 0;
        while (!WaitForStop(stop_event, 0)) {
            ProcessLoopbackSession session;
            if (!OpenSession(&session)) {
                if (WaitForStop(stop_event, 0)) break;
                if (++recovery_attempts <= kMaxRecoveryAttempts) {
                    const DWORD delay = 200u * recovery_attempts;
                    if (!WaitForStop(stop_event, delay)) continue;
                    break;
                }
                if (on_failure) on_failure(config.source.identity.id);
                break;
            }
            recovery_attempts = 0;
            const HRESULT capture_result = CaptureSession(&session);
            if (capture_result == S_FALSE || WaitForStop(stop_event, 0)) break;
            SetError("process-loopback capture failed " + HrText(capture_result));
            if (!IsRecoverableAudioFailure(capture_result)
                    || ++recovery_attempts > kMaxRecoveryAttempts) {
                if (on_failure) on_failure(config.source.identity.id);
                break;
            }
            if (WaitForStop(stop_event, 200u * recovery_attempts)) break;
        }
        if (encoder) encoder->Finalize();
        running.store(false, std::memory_order_release);
        if (SUCCEEDED(apartment)) CoUninitialize();
    }
};

WindowsProcessLoopbackAudioProvider::WindowsProcessLoopbackAudioProvider(
    WindowsProcessLoopbackProviderConfig config, ActivityCallback activity_callback,
    EndedCallback ended_callback, FailureCallback failure_callback)
    : impl_(std::make_unique<Impl>(std::move(config), std::move(activity_callback),
          std::move(ended_callback), std::move(failure_callback))) {}

WindowsProcessLoopbackAudioProvider::~WindowsProcessLoopbackAudioProvider() {
    Stop();
}

bool WindowsProcessLoopbackAudioProvider::Start() {
    if (!impl_ || impl_->running.exchange(true, std::memory_order_acq_rel)) return false;
    impl_->stop_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!impl_->stop_event) {
        impl_->SetError("process-loopback stop event creation failed");
        impl_->running.store(false, std::memory_order_release);
        return false;
    }
    impl_->thread = std::thread([this] { impl_->Run(); });
    return true;
}

void WindowsProcessLoopbackAudioProvider::Stop() {
    if (!impl_) return;
    if (impl_->stop_event) SetEvent(impl_->stop_event);
    if (impl_->thread.joinable()) impl_->thread.join();
    if (impl_->stop_event) CloseHandle(impl_->stop_event);
    impl_->stop_event = nullptr;
    impl_->running.store(false, std::memory_order_release);
}

bool WindowsProcessLoopbackAudioProvider::IsRunning() const {
    return impl_ && impl_->running.load(std::memory_order_acquire);
}

const std::string& WindowsProcessLoopbackAudioProvider::last_error() const {
    static const std::string empty;
    return impl_ ? impl_->error : empty;
}

std::optional<EncodedAudioTrack> WindowsProcessLoopbackAudioProvider::TakeTrackForInterval(
    double presentation_start_qpc_s, double presentation_end_qpc_s,
    const AudioSourceMetadata& source) const {
    if (!impl_ || !impl_->ring || presentation_end_qpc_s <= presentation_start_qpc_s) return std::nullopt;
    const uint64_t origin = impl_->timeline_origin_100ns.load(std::memory_order_acquire);
    if (origin == 0) return std::nullopt;
    const auto range = MapAudioSourcePresentationRange(
        presentation_start_qpc_s, presentation_end_qpc_s, origin, kCanonicalSampleRate);
    EncodedAudioSnapshot snapshot = impl_->ring->TakeSnapshot(
        range.start_pts_samples, range.end_pts_samples);
    if (!snapshot.valid()) return std::nullopt;
    EncodedAudioTrack track;
    track.source = source;
    track.snapshot = std::move(snapshot);
    track.presentation_start_pts_samples = range.start_pts_samples;
    return track;
}

WindowsApplicationSourceCoordinator::WindowsApplicationSourceCoordinator(
    uint64_t generation, WindowsProcessLoopbackCapability capability,
    SourceIdGenerator source_id_generator, uint32_t source_limit)
    : generation_(generation), capability_(capability),
      source_id_generator_(std::move(source_id_generator)),
      registry_(generation, source_limit) {}

std::optional<WindowsApplicationSourceBinding> WindowsApplicationSourceCoordinator::Discover(
    const WindowsAudioSessionDescriptor& descriptor) {
    if (!available() || descriptor.runtime_group_key.empty() || descriptor.process_id == 0)
        return std::nullopt;
    const auto existing = runtime_groups_.find(descriptor.runtime_group_key);
    if (existing != runtime_groups_.end()) {
        const auto* source = registry_.Find(existing->second);
        if (!source) return std::nullopt;
        return WindowsApplicationSourceBinding{descriptor.runtime_group_key,
                                                descriptor.process_id, *source};
    }
    if (!source_id_generator_) return std::nullopt;
    const auto id = source_id_generator_();
    if (!id || !id->IsValid()) return std::nullopt;
    AudioSourceMetadata source;
    source.identity = descriptor.identity;
    source.identity.id = *id;
    source.identity.type = AudioSourceType::Application;
    source.format = {kCanonicalSampleRate, kCanonicalChannels, "fltp"};
    if (!registry_.Discover(source)) return std::nullopt;
    runtime_groups_.emplace(descriptor.runtime_group_key, *id);
    // Process loopback INCLUDE operates on a target and its descendants.  The
    // registry's deterministic same-executable root is therefore the target;
    // capturing an arbitrary child would miss its sibling render/helper tree.
    const uint32_t capture_process_id = descriptor.process_group_root_id != 0
        ? descriptor.process_group_root_id : descriptor.process_id;
    return WindowsApplicationSourceBinding{descriptor.runtime_group_key,
                                            capture_process_id, source};
}

std::optional<AudioSourceId> WindowsApplicationSourceCoordinator::RetireRuntimeGroup(
    const std::string& runtime_group_key, int64_t timestamp_100ns) {
    const auto it = runtime_groups_.find(runtime_group_key);
    if (it == runtime_groups_.end()) return std::nullopt;
    registry_.MarkEnded(it->second, timestamp_100ns);
    const AudioSourceId id = it->second;
    runtime_groups_.erase(it);
    return id;
}

AudioSourceAdmission WindowsApplicationSourceCoordinator::ObserveActivity(
    const AudioSourceId& id, float rms, int64_t timestamp_100ns) {
    return registry_.ObserveActivity(id, rms, timestamp_100ns);
}

void WindowsApplicationSourceCoordinator::MarkFailed(const AudioSourceId& id) {
    registry_.MarkFailed(id);
}

std::vector<AudioSourceMetadata> WindowsApplicationSourceCoordinator::SourcesForInterval(
    int64_t start_100ns, int64_t end_100ns) const {
    return registry_.SourcesForInterval(start_100ns, end_100ns);
}

const AudioSourceMetadata* WindowsApplicationSourceCoordinator::Find(const AudioSourceId& id) const {
    return registry_.Find(id);
}

struct WindowsApplicationAudioSourceManager::Impl {
    explicit Impl(uint64_t initial_generation, uint32_t initial_retention_seconds)
        : generation(initial_generation), retention_seconds(initial_retention_seconds),
          capability(DetectWindowsProcessLoopbackCapability()) {}

    struct ProviderEntry {
        std::string runtime_group_key;
        std::unique_ptr<WindowsProcessLoopbackAudioProvider> provider;
    };

    uint64_t generation;
    uint32_t retention_seconds;
    WindowsProcessLoopbackCapability capability;
    mutable std::mutex mutex;
    std::unique_ptr<WindowsApplicationSourceCoordinator> coordinator;
    std::map<std::string, ProviderEntry> providers_by_source_id;
    std::map<std::string, AudioSourceId> active_runtime_groups;
    std::thread monitor_thread;
    HANDLE stop_event = nullptr;
    std::atomic<bool> running{false};
    std::string error;

    std::optional<AudioSourceId> GenerateSourceId() {
        std::string value;
        std::string ignored;
        if (!CreateAudioManifestTransactionId(&value, &ignored)) return std::nullopt;
        return AudioSourceId{value};
    }

    void SetError(std::string value) {
        std::lock_guard<std::mutex> lock(mutex);
        error = std::move(value);
    }

    AudioSourceAdmission OnActivity(const AudioSourceId& id, float rms, int64_t timestamp) {
        std::lock_guard<std::mutex> lock(mutex);
        return coordinator ? coordinator->ObserveActivity(id, rms, timestamp)
                           : AudioSourceAdmission::RejectedInvalidIdentity;
    }

    void OnFailure(const AudioSourceId& id) {
        std::lock_guard<std::mutex> lock(mutex);
        if (coordinator) coordinator->MarkFailed(id);
    }

    void EnsureProvider(const WindowsAudioSessionDescriptor& descriptor) {
        WindowsProcessLoopbackAudioProvider* to_start = nullptr;
        AudioSourceId start_id;
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (!coordinator) return;
            const auto binding = coordinator->Discover(descriptor);
            if (!binding) return;
            active_runtime_groups[binding->runtime_group_key] = binding->source.identity.id;
            const auto source_key = binding->source.identity.id.value;
            if (providers_by_source_id.find(source_key) != providers_by_source_id.end()) return;
            WindowsProcessLoopbackProviderConfig config;
            config.generation = generation;
            config.target_process_id = binding->process_id;
            config.include_process_tree = true;
            config.retention_seconds = retention_seconds;
            config.source = binding->source;
            auto provider = std::make_unique<WindowsProcessLoopbackAudioProvider>(
                std::move(config),
                [this](const AudioSourceId& id, float rms, int64_t timestamp) {
                    return OnActivity(id, rms, timestamp);
                },
                [this](const AudioSourceId& id, int64_t timestamp) {
                    std::lock_guard<std::mutex> callback_lock(mutex);
                    if (coordinator) coordinator->RetireRuntimeGroup(id.value, timestamp);
                },
                [this](const AudioSourceId& id) { OnFailure(id); });
            to_start = provider.get();
            start_id = binding->source.identity.id;
            providers_by_source_id.emplace(source_key,
                ProviderEntry{binding->runtime_group_key, std::move(provider)});
        }
        if (to_start && !to_start->Start()) OnFailure(start_id);
    }

    void RetireProvider(const std::string& runtime_group_key) {
        WindowsProcessLoopbackAudioProvider* provider = nullptr;
        AudioSourceId id;
        {
            std::lock_guard<std::mutex> lock(mutex);
            const auto active = active_runtime_groups.find(runtime_group_key);
            if (active == active_runtime_groups.end()) return;
            id = active->second;
            active_runtime_groups.erase(active);
            const auto entry = providers_by_source_id.find(id.value);
            if (entry != providers_by_source_id.end()) provider = entry->second.provider.get();
        }
        if (provider) provider->Stop();
        // Stop first so a final capture callback cannot reactivate an already
        // retired group while the provider thread is winding down.
        std::lock_guard<std::mutex> lock(mutex);
        if (coordinator) coordinator->RetireRuntimeGroup(runtime_group_key, CurrentQpc100ns());
    }

    void HandleUpdate(const WindowsAudioSessionUpdate& update) {
        for (const auto& descriptor : update.current) EnsureProvider(descriptor);
        for (const auto& removed : update.disappeared_runtime_groups) RetireProvider(removed);
    }

    void RunMonitor() {
        const HRESULT apartment = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        if (FAILED(apartment) && apartment != RPC_E_CHANGED_MODE) {
            SetError("Windows application-audio session monitor could not initialize COM");
            running.store(false, std::memory_order_release);
            return;
        }
        WindowsAudioSessionRegistry sessions;
        if (!sessions.Start()) {
            SetError(sessions.last_error());
            if (SUCCEEDED(apartment)) CoUninitialize();
            running.store(false, std::memory_order_release);
            return;
        }
        WindowsAudioSessionUpdate initial;
        sessions.RefreshIfNeeded(&initial);
        HandleUpdate(initial);
        while (!WaitForStop(stop_event, 250)) {
            if (!sessions.needs_refresh()) continue;
            WindowsAudioSessionUpdate update;
            if (sessions.RefreshIfNeeded(&update)) HandleUpdate(update);
            else SetError(sessions.last_error());
        }
        sessions.Stop();
        if (SUCCEEDED(apartment)) CoUninitialize();
        running.store(false, std::memory_order_release);
    }
};

WindowsApplicationAudioSourceManager::WindowsApplicationAudioSourceManager(
    uint64_t generation, uint32_t retention_seconds)
    : impl_(std::make_unique<Impl>(generation, retention_seconds)) {}

WindowsApplicationAudioSourceManager::~WindowsApplicationAudioSourceManager() {
    Stop();
}

bool WindowsApplicationAudioSourceManager::Start() {
    if (!impl_ || !impl_->capability.api_build_supported) return false;
    if (impl_->running.exchange(true, std::memory_order_acq_rel)) return true;
    impl_->stop_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!impl_->stop_event) {
        impl_->SetError("Windows application-audio stop event creation failed");
        impl_->running.store(false, std::memory_order_release);
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        impl_->coordinator = std::make_unique<WindowsApplicationSourceCoordinator>(
            impl_->generation, impl_->capability,
            [this] { return impl_->GenerateSourceId(); },
            // The provisional app-stem cap is eight. Default Mix is a separate
            // mandatory compatibility track and does not consume an app slot.
            kMaxRetainedAudioSources);
    }
    impl_->monitor_thread = std::thread([this] { impl_->RunMonitor(); });
    return true;
}

void WindowsApplicationAudioSourceManager::Stop() {
    if (!impl_) return;
    if (impl_->stop_event) SetEvent(impl_->stop_event);
    if (impl_->monitor_thread.joinable()) impl_->monitor_thread.join();
    std::vector<WindowsProcessLoopbackAudioProvider*> providers;
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        for (auto& [_, entry] : impl_->providers_by_source_id) {
            if (entry.provider) providers.push_back(entry.provider.get());
        }
        impl_->active_runtime_groups.clear();
    }
    for (auto* provider : providers) provider->Stop();
    if (impl_->stop_event) CloseHandle(impl_->stop_event);
    impl_->stop_event = nullptr;
    impl_->running.store(false, std::memory_order_release);
}

bool WindowsApplicationAudioSourceManager::available() const {
    return impl_ && impl_->capability.api_build_supported;
}

WindowsProcessLoopbackCapability WindowsApplicationAudioSourceManager::capability() const {
    return impl_ ? impl_->capability : WindowsProcessLoopbackCapability{};
}

const std::string& WindowsApplicationAudioSourceManager::last_error() const {
    static const std::string empty;
    return impl_ ? impl_->error : empty;
}

std::vector<EncodedAudioTrack> WindowsApplicationAudioSourceManager::TakeTracksForInterval(
    double presentation_start_qpc_s, double presentation_end_qpc_s) const {
    std::vector<std::pair<WindowsProcessLoopbackAudioProvider*, AudioSourceMetadata>> candidates;
    if (!impl_ || presentation_end_qpc_s <= presentation_start_qpc_s) return {};
    const int64_t start = static_cast<int64_t>(std::llround(presentation_start_qpc_s * 10'000'000.0));
    const int64_t end = static_cast<int64_t>(std::llround(presentation_end_qpc_s * 10'000'000.0));
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        if (!impl_->coordinator) return {};
        for (const auto& source : impl_->coordinator->SourcesForInterval(start, end)) {
            const auto entry = impl_->providers_by_source_id.find(source.identity.id.value);
            if (entry != impl_->providers_by_source_id.end() && entry->second.provider)
                candidates.emplace_back(entry->second.provider.get(), source);
        }
    }
    std::vector<EncodedAudioTrack> tracks;
    tracks.reserve(candidates.size());
    for (const auto& [provider, source] : candidates) {
        if (const auto track = provider->TakeTrackForInterval(
                presentation_start_qpc_s, presentation_end_qpc_s, source)) {
            tracks.push_back(*track);
        }
    }
    return tracks;
}

}  // namespace fthr

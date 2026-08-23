// windows_microphone_audio_provider.h -- native WASAPI microphone replay.
//
// The provider is generation-local.  It keeps encoded AAC packets only, maps
// them to the common QPC timeline, and never exposes endpoint identifiers in a
// completed clip manifest.

#pragma once
#ifndef FTHR_WINDOWS_MICROPHONE_AUDIO_PROVIDER_H
#define FTHR_WINDOWS_MICROPHONE_AUDIO_PROVIDER_H

#include "audio_packet_ring.h"
#include "audio_timeline.h"
#include "save_clip_task.h"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace fthr {

// Runtime/setup identity only. endpoint_id is persisted in local settings and
// passed to the engine, but is deliberately excluded from portable manifests.
struct WindowsMicrophoneEndpoint {
    std::wstring endpoint_id;
    std::string display_name;
    uint32_t device_state = 0;
    bool is_default = false;
};

// Enumerates native eCapture endpoints and their stable IDs.  The caller can
// choose to display active endpoints only while retaining a missing explicit ID
// in settings so it is never silently rebound to a different microphone.
bool EnumerateWindowsMicrophoneEndpoints(
    std::vector<WindowsMicrophoneEndpoint>* endpoints, std::string* error);

struct WindowsMicrophoneAudioProviderConfig {
    uint64_t generation = 0;
    // Empty only when use_default_endpoint is true.
    std::wstring endpoint_id;
    bool use_default_endpoint = true;
    uint32_t retention_seconds = 30;
    // Canonical 48 kHz stereo AAC is intentionally lower bitrate than the
    // Default Mix: a microphone is normally voice, while the stereo channel
    // layout keeps future native mixing format-stable.
    uint32_t bitrate_kbps = 96;
    // Capture/input gain only. It is burned exactly once into newly captured
    // microphone samples and is never reused as an editor/export gain.
    float input_gain = 1.0f;
    AudioSourceMetadata source;
};

struct WindowsMicrophoneRuntimeInfo {
    std::wstring active_endpoint_id;
    std::string active_display_name;
    AudioSourceFormat input_format;
    bool failed = false;
    int64_t max_observed_drift_samples = 0;
};

class WindowsMicrophoneAudioProvider {
public:
    explicit WindowsMicrophoneAudioProvider(WindowsMicrophoneAudioProviderConfig config);
    ~WindowsMicrophoneAudioProvider();

    WindowsMicrophoneAudioProvider(const WindowsMicrophoneAudioProvider&) = delete;
    WindowsMicrophoneAudioProvider& operator=(const WindowsMicrophoneAudioProvider&) = delete;

    bool Start();
    void Stop();
    bool IsRunning() const;
    const std::string& last_error() const;
    WindowsMicrophoneRuntimeInfo runtime_info() const;

    // A track is returned only when the provider has native AAC history for
    // the requested video interval.  The source metadata is updated with the
    // real generation-local activity range, never a device identifier.
    std::optional<EncodedAudioTrack> TakeTrackForInterval(
        double presentation_start_qpc_s, double presentation_end_qpc_s,
        AudioSourceMetadata source) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace fthr

#endif  // FTHR_WINDOWS_MICROPHONE_AUDIO_PROVIDER_H

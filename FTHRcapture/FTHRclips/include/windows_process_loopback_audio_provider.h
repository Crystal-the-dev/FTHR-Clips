// windows_process_loopback_audio_provider.h
// Windows 11 process-loopback application stems for AUDIT-050.
//
// The common audio-source model deliberately stays free of COM details.  This
// Windows-only adapter owns the documented process-loopback activation and
// connects real PCM to the existing persistent AAC replay-ring contract.

#pragma once
#ifndef FTHR_WINDOWS_PROCESS_LOOPBACK_AUDIO_PROVIDER_H
#define FTHR_WINDOWS_PROCESS_LOOPBACK_AUDIO_PROVIDER_H

#include "audio_packet_ring.h"
#include "audio_timeline.h"
#include "audio_source_model.h"
#include "save_clip_task.h"
#include "windows_audio_session_registry.h"

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace fthr {

struct WindowsProcessLoopbackProviderConfig {
    uint64_t generation = 0;
    uint32_t target_process_id = 0;
    // The documented INCLUDE mode captures the target and its child process
    // tree.  The provider never uses the exclude mode because a FTHR stem is
    // defined by one deterministic session group.
    bool include_process_tree = true;
    uint32_t retention_seconds = 30;
    uint32_t bitrate_kbps = 128;
    AudioSourceMetadata source;
};

// Owns one real Windows process-loopback IAudioClient.  It does not create an
// AAC encoder or replay ring until the common activity gate admits the source,
// so a merely discovered/silent session cannot become a stored clip stem.
class WindowsProcessLoopbackAudioProvider {
public:
    using ActivityCallback = std::function<AudioSourceAdmission(
        const AudioSourceId&, float rms, int64_t timestamp_100ns)>;
    using EndedCallback = std::function<void(const AudioSourceId&, int64_t timestamp_100ns)>;
    using FailureCallback = std::function<void(const AudioSourceId&)>;

    WindowsProcessLoopbackAudioProvider(WindowsProcessLoopbackProviderConfig config,
                                        ActivityCallback activity_callback,
                                        EndedCallback ended_callback,
                                        FailureCallback failure_callback);
    ~WindowsProcessLoopbackAudioProvider();

    WindowsProcessLoopbackAudioProvider(const WindowsProcessLoopbackAudioProvider&) = delete;
    WindowsProcessLoopbackAudioProvider& operator=(const WindowsProcessLoopbackAudioProvider&) = delete;

    // Starts a dedicated bounded event-driven capture thread.  A successful
    // return means the thread was created; runtime activation remains reported
    // through the source failure callback rather than being fabricated as audio.
    bool Start();
    void Stop();
    bool IsRunning() const;
    bool HasCapturedAudio() const;
    const std::string& last_error() const;

    // Returns a contract track only when this provider actually encoded packets
    // overlapping the requested video presentation interval.
    std::optional<EncodedAudioTrack> TakeTrackForInterval(
        double presentation_start_qpc_s, double presentation_end_qpc_s,
        const AudioSourceMetadata& source) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

struct WindowsApplicationSourceBinding {
    std::string runtime_group_key;
    uint32_t process_id = 0;
    AudioSourceMetadata source;
};

// Pure source-lifecycle coordinator.  It is intentionally testable without a
// COM apartment, audio endpoint, or Windows 11 host.  The production manager
// below supplies the real provider, while tests can drive discovery/activity/
// exit/restart through this same lifecycle boundary.
class WindowsApplicationSourceCoordinator {
public:
    using SourceIdGenerator = std::function<std::optional<AudioSourceId>()>;

    WindowsApplicationSourceCoordinator(uint64_t generation,
                                        WindowsProcessLoopbackCapability capability,
                                        SourceIdGenerator source_id_generator,
                                        uint32_t source_limit = kMaxRetainedAudioSources,
                                        uint32_t retention_seconds = 30);

    bool available() const { return capability_.api_build_supported; }
    const WindowsProcessLoopbackCapability& capability() const { return capability_; }

    // A runtime group maps to exactly one provider/source while it is alive.
    // A disappeared group is retired rather than merged on a later restart: the
    // current grouping policy is root-PID scoped, so this avoids PID-reuse or
    // name-only timeline corruption.
    std::optional<WindowsApplicationSourceBinding> Discover(
        const WindowsAudioSessionDescriptor& descriptor);
    std::optional<AudioSourceId> RetireRuntimeGroup(
        const std::string& runtime_group_key, int64_t timestamp_100ns);
    AudioSourceAdmission ObserveActivity(const AudioSourceId& id, float rms,
                                         int64_t timestamp_100ns);
    void MarkFailed(const AudioSourceId& id);

    std::vector<AudioSourceMetadata> SourcesForInterval(
        int64_t start_100ns, int64_t end_100ns) const;
    const AudioSourceMetadata* Find(const AudioSourceId& id) const;
    size_t provider_candidate_count() const { return runtime_groups_.size(); }

private:
    uint64_t generation_;
    WindowsProcessLoopbackCapability capability_;
    SourceIdGenerator source_id_generator_;
    AudioSourceRegistry registry_;
    int64_t retention_100ns_;
    std::map<std::string, AudioSourceId> runtime_groups_;
};

// Windows-only owner that keeps one session registry and one provider lifecycle
// coherent for a capture generation.  It is deliberately absent on Windows 10:
// no activation attempt, synthetic source, or per-app metadata is produced.
class WindowsApplicationAudioSourceManager {
public:
    WindowsApplicationAudioSourceManager(uint64_t generation, uint32_t retention_seconds);
    ~WindowsApplicationAudioSourceManager();

    WindowsApplicationAudioSourceManager(const WindowsApplicationAudioSourceManager&) = delete;
    WindowsApplicationAudioSourceManager& operator=(const WindowsApplicationAudioSourceManager&) = delete;

    bool Start();
    void Stop();
    bool available() const;
    WindowsProcessLoopbackCapability capability() const;
    const std::string& last_error() const;

    std::vector<EncodedAudioTrack> TakeTracksForInterval(
        double presentation_start_qpc_s, double presentation_end_qpc_s) const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace fthr

#endif  // FTHR_WINDOWS_PROCESS_LOOPBACK_AUDIO_PROVIDER_H

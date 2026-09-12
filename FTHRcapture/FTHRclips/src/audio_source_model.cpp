#include "audio_source_model.h"

#include <algorithm>
#include <cctype>
#include <tuple>
#include <utility>

namespace fthr {

namespace {

bool IsUuidCharacter(char value) {
    return (value >= '0' && value <= '9')
        || (value >= 'a' && value <= 'f')
        || (value >= 'A' && value <= 'F')
        || value == '-';
}

bool IsManifestCharacter(unsigned char value) {
    return std::isalnum(value) || value == ' ' || value == '.' || value == '_'
        || value == '-' || value == '+' || value == '(' || value == ')' || value == '\'';
}

}  // namespace

bool AudioSourceId::IsValid() const {
    if (value.size() != 36) return false;
    for (size_t index = 0; index < value.size(); ++index) {
        if (index == 8 || index == 13 || index == 18 || index == 23) {
            if (value[index] != '-') return false;
        } else if (!IsUuidCharacter(value[index]) || value[index] == '-') {
            return false;
        }
    }
    return true;
}

AudioActivityGate::AudioActivityGate(AudioActivityGateConfig config)
    : config_(config) {
    if (config_.activate_rms < config_.deactivate_rms)
        config_.activate_rms = config_.deactivate_rms;
    config_.activate_blocks = std::max<uint32_t>(1, config_.activate_blocks);
    config_.deactivate_blocks = std::max<uint32_t>(1, config_.deactivate_blocks);
}

bool AudioActivityGate::Observe(float rms) {
    rms = std::max(0.0f, rms);
    if (!active_) {
        quiet_blocks_ = 0;
        loud_blocks_ = rms >= config_.activate_rms ? loud_blocks_ + 1 : 0;
        if (loud_blocks_ >= config_.activate_blocks) {
            active_ = true;
            quiet_blocks_ = 0;
        }
    } else {
        loud_blocks_ = 0;
        quiet_blocks_ = rms <= config_.deactivate_rms ? quiet_blocks_ + 1 : 0;
        if (quiet_blocks_ >= config_.deactivate_blocks) {
            active_ = false;
            loud_blocks_ = 0;
        }
    }
    return active_;
}

void AudioActivityGate::Reset() {
    loud_blocks_ = 0;
    quiet_blocks_ = 0;
    active_ = false;
}

AudioSourceRegistry::AudioSourceRegistry(uint64_t generation, uint32_t limit)
    : generation_(generation), limit_(std::max<uint32_t>(1, limit)) {}

bool AudioSourceRegistry::Discover(AudioSourceMetadata metadata) {
    if (!metadata.identity.id.IsValid()) return false;
    metadata.identity.persistent_identity = SanitizeAudioSourceText(
        metadata.identity.persistent_identity, 128);
    metadata.identity.display_name = SanitizeAudioSourceText(
        metadata.identity.display_name, 80);
    metadata.identity.icon_reference = SanitizeAudioSourceText(
        metadata.identity.icon_reference, 128);
    if (metadata.identity.persistent_identity.empty()
            || metadata.identity.display_name.empty()) {
        return false;
    }
    if (metadata.format.sample_rate == 0 || metadata.format.channels == 0)
        return false;
    const AudioSourceId id = metadata.identity.id;
    const auto [_, inserted] = sources_.emplace(id, std::move(metadata));
    if (inserted) {
        gates_.try_emplace(id);
        terminal_at_100ns_.erase(id);
    }
    return inserted;
}

AudioSourceAdmission AudioSourceRegistry::ObserveActivity(
    const AudioSourceId& id, float rms, int64_t timestamp_100ns) {
    const auto source_it = sources_.find(id);
    const auto gate_it = gates_.find(id);
    if (source_it == sources_.end() || gate_it == gates_.end())
        return AudioSourceAdmission::RejectedInvalidIdentity;

    AudioSourceState& state = source_it->second.state;
    const bool active = gate_it->second.Observe(rms);
    state.active_in_generation = active;
    if (!active) return state.admitted
        ? AudioSourceAdmission::AlreadyAdmitted
        : AudioSourceAdmission::RejectedAtCapacity;

    if (!state.admitted) {
        if (admitted_count_ >= limit_) {
            // The initial alpha policy never steals history from an existing
            // audible stem. It makes the deterministic rejection observable
            // instead of silently dropping a source later in the save path.
            return AudioSourceAdmission::RejectedAtCapacity;
        }
        state.admitted = true;
        ++admitted_count_;
    }
    state.health = AudioSourceHealth::Active;
    if (state.first_active_100ns < 0) state.first_active_100ns = timestamp_100ns;
    state.last_active_100ns = timestamp_100ns;
    return AudioSourceAdmission::Accepted;
}

void AudioSourceRegistry::MarkEnded(const AudioSourceId& id, int64_t timestamp_100ns) {
    const auto it = sources_.find(id);
    if (it == sources_.end()) return;
    it->second.state.active_in_generation = false;
    if (it->second.state.health != AudioSourceHealth::Failed)
        it->second.state.health = AudioSourceHealth::Ended;
    terminal_at_100ns_[id] = timestamp_100ns;
    // Keep an admitted source counted until the retention window has elapsed
    // so its AAC history remains eligible for a replay save.
}

void AudioSourceRegistry::MarkFailed(const AudioSourceId& id) {
    const auto it = sources_.find(id);
    if (it == sources_.end()) return;
    it->second.state.health = AudioSourceHealth::Failed;
    it->second.state.active_in_generation = false;
    ++it->second.state.failure_count;
}

uint32_t AudioSourceRegistry::PruneEndedOlderThan(int64_t cutoff_100ns) {
    uint32_t pruned = 0;
    for (auto it = sources_.begin(); it != sources_.end();) {
        const auto& state = it->second.state;
        const bool terminal = state.health == AudioSourceHealth::Ended
            || state.health == AudioSourceHealth::Failed;
        const auto terminal_at = terminal_at_100ns_.find(it->first);
        if (!terminal || state.active_in_generation || terminal_at == terminal_at_100ns_.end()
                || terminal_at->second < 0 || terminal_at->second >= cutoff_100ns) {
            ++it;
            continue;
        }
        if (state.admitted && admitted_count_ > 0) --admitted_count_;
        gates_.erase(it->first);
        terminal_at_100ns_.erase(it->first);
        it = sources_.erase(it);
        ++pruned;
    }
    return pruned;
}

uint32_t AudioSourceRegistry::ReleaseAdmissionsOlderThan(int64_t cutoff_100ns) {
    uint32_t released = 0;
    for (auto& [id, source] : sources_) {
        auto& state = source.state;
        const auto terminal_at = terminal_at_100ns_.find(id);
        const int64_t age_anchor = terminal_at != terminal_at_100ns_.end()
            ? terminal_at->second : state.last_active_100ns;
        if (!state.admitted || state.active_in_generation || age_anchor < 0
                || age_anchor >= cutoff_100ns) {
            continue;
        }
        state.admitted = false;
        // A live runtime group may go quiet for longer than the replay
        // window. Release its admission slot without making its identity
        // terminal; a later audible block must be able to re-admit it.
        if (state.health != AudioSourceHealth::Ended
                && state.health != AudioSourceHealth::Failed)
            state.health = AudioSourceHealth::Discovered;
        const auto gate = gates_.find(id);
        if (gate != gates_.end()) gate->second.Reset();
        if (admitted_count_ > 0) --admitted_count_;
        ++released;
    }
    return released;
}

std::vector<AudioSourceMetadata> AudioSourceRegistry::SourcesForInterval(
    int64_t start_100ns, int64_t end_100ns) const {
    std::vector<AudioSourceMetadata> result;
    if (end_100ns < start_100ns) return result;
    for (const auto& [id, source] : sources_) {
        const auto& state = source.state;
        if (!state.admitted || state.first_active_100ns < 0
                || state.last_active_100ns < start_100ns
                || state.first_active_100ns > end_100ns) {
            continue;
        }
        result.push_back(source);
    }
    std::sort(result.begin(), result.end(), [](const auto& left, const auto& right) {
        return std::make_tuple(PresentationPriority(left.identity.type),
                               left.state.first_active_100ns, left.identity.display_name,
                               left.identity.id.value)
             < std::make_tuple(PresentationPriority(right.identity.type),
                               right.state.first_active_100ns, right.identity.display_name,
                               right.identity.id.value);
    });
    return result;
}

const AudioSourceMetadata* AudioSourceRegistry::Find(const AudioSourceId& id) const {
    const auto it = sources_.find(id);
    return it == sources_.end() ? nullptr : &it->second;
}

int AudioSourceRegistry::AdmissionPriority(AudioSourceType type) {
    switch (type) {
    case AudioSourceType::System: return 0;
    case AudioSourceType::Microphone: return 1;
    case AudioSourceType::Application: return 2;
    }
    return 3;
}

int AudioSourceRegistry::PresentationPriority(AudioSourceType type) {
    switch (type) {
    case AudioSourceType::Application: return 0;
    case AudioSourceType::System: return 1;
    case AudioSourceType::Microphone: return 2;
    }
    return 3;
}

std::string SanitizeAudioSourceText(const std::string& value, size_t max_length) {
    std::string result;
    result.reserve(std::min(value.size(), max_length));
    bool previous_space = false;
    for (const unsigned char character : value) {
        if (!IsManifestCharacter(character)) continue;
        if (character == ' ') {
            if (result.empty() || previous_space) continue;
            previous_space = true;
        } else {
            previous_space = false;
        }
        result.push_back(static_cast<char>(character));
        if (result.size() == max_length) break;
    }
    while (!result.empty() && result.back() == ' ') result.pop_back();
    return result;
}

const char* AudioSourceTypeName(AudioSourceType type) {
    switch (type) {
    case AudioSourceType::Application: return "application";
    case AudioSourceType::Microphone: return "microphone";
    case AudioSourceType::System: return "system";
    }
    return "unknown";
}

}  // namespace fthr

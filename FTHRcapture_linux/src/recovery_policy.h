#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>

namespace fthr {

struct RecoveryDecision {
    bool retry = false;
    bool exhausted = false;
    uint32_t attempt = 0;
    std::chrono::milliseconds backoff{0};
};

// Pure policy kept separate from CaptureEngine so retry limits can be tested
// with a fake backend without a compositor or FFmpeg encoder.
class CaptureRecoveryPolicy {
public:
    explicit CaptureRecoveryPolicy(uint64_t healthy_reset_frames,
                                   uint32_t max_attempts = 3)
        : healthy_reset_frames_(std::max<uint64_t>(1, healthy_reset_frames))
        , max_attempts_(std::max<uint32_t>(1, max_attempts)) {}

    RecoveryDecision OnGenerationFailed(uint64_t produced_frames) {
        if (produced_frames >= healthy_reset_frames_)
            attempts_ = 0;
        ++attempts_;
        if (attempts_ > max_attempts_)
            return {false, true, attempts_, std::chrono::milliseconds(0)};
        const size_t index = std::min<size_t>(attempts_ - 1, kBackoffs.size() - 1);
        return {true, false, attempts_, kBackoffs[index]};
    }

    uint32_t Attempts() const noexcept { return attempts_; }

private:
    inline static constexpr std::array<std::chrono::milliseconds, 3> kBackoffs{
        std::chrono::milliseconds(250),
        std::chrono::milliseconds(750),
        std::chrono::milliseconds(1500),
    };
    uint64_t healthy_reset_frames_;
    uint32_t max_attempts_;
    uint32_t attempts_ = 0;
};

// Production backoff primitive with injected clock/sleeper behavior.  The
// 50 ms slices bound shutdown latency and make cancellation deterministic in
// the native fake-backend harness without sleeping the test process.
template <typename IsRunning, typename Sleeper>
bool WaitForRecoveryBackoff(std::chrono::milliseconds remaining,
                            IsRunning&& is_running,
                            Sleeper&& sleep) {
    while (is_running() && remaining.count() > 0) {
        const auto slice = std::min(remaining, std::chrono::milliseconds(50));
        sleep(slice);
        remaining -= slice;
    }
    return is_running();
}

} // namespace fthr

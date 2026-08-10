#include "recovery_policy.h"

#include <cassert>
#include <chrono>
#include <cstdint>

namespace {

class FakeBackend {
public:
    explicit FakeBackend(uint64_t frames_before_failure)
        : remaining_(frames_before_failure) {}

    bool CaptureFrame() {
        if (remaining_ == 0) return false;
        --remaining_;
        return true;
    }

private:
    uint64_t remaining_;
};

uint64_t RunGeneration(FakeBackend& backend) {
    uint64_t frames = 0;
    while (backend.CaptureFrame()) ++frames;
    return frames;
}

} // namespace

int main() {
    fthr::CaptureRecoveryPolicy policy(300);
    FakeBackend first(10);
    auto decision = policy.OnGenerationFailed(RunGeneration(first));
    assert(decision.retry && !decision.exhausted);
    assert(decision.attempt == 1 && decision.backoff.count() == 250);

    // A healthy generation makes a later failure a new incident.
    FakeBackend healthy(300);
    decision = policy.OnGenerationFailed(RunGeneration(healthy));
    assert(decision.retry && decision.attempt == 1);

    for (uint32_t expected = 2; expected <= 3; ++expected) {
        FakeBackend short_lived(1);
        decision = policy.OnGenerationFailed(RunGeneration(short_lived));
        assert(decision.retry && decision.attempt == expected);
    }
    FakeBackend terminal(0);
    decision = policy.OnGenerationFailed(RunGeneration(terminal));
    assert(!decision.retry && decision.exhausted);

    bool running = true;
    uint32_t sleep_calls = 0;
    const bool completed = fthr::WaitForRecoveryBackoff(
        std::chrono::milliseconds(1500),
        [&running] { return running; },
        [&running, &sleep_calls](std::chrono::milliseconds slice) {
            assert(slice.count() == 50);
            ++sleep_calls;
            running = false;
        });
    assert(!completed);
    assert(sleep_calls == 1);
    return 0;
}

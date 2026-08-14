#include "audio_ring_buffer.h"

#include <cassert>
#include <cmath>
#include <cstdint>
#include <vector>

namespace {

void PushSeconds(fthr::AudioRingBuffer& ring, int seconds, uint32_t sample_rate) {
    std::vector<float> samples(sample_rate, 0.25f);
    for (int second = 0; second < seconds; ++second) {
        ring.Push(
            samples.data(),
            sample_rate,
            static_cast<uint64_t>(second) * 10'000'000ULL);
    }
}

void TestExactAudioWindowUsesRequestedEnd() {
    constexpr uint32_t sample_rate = 1000;
    fthr::AudioRingBuffer ring(10 * sample_rate, sample_rate, 1, 0);
    PushSeconds(ring, 10, sample_rate);

    const auto snapshot = ring.TakeSnapshot(5.0, 8.0);
    assert(snapshot.valid);
    assert(snapshot.samples.size() == 5 * sample_rate);
    assert(std::abs(snapshot.qpc_start_s - 3.0) < 0.0011);
    assert(snapshot.qpc_end_s <= 8.0);
    assert(8.0 - snapshot.qpc_end_s < 0.0011);
}

void TestAudioShorterThanVideoIsExplicit() {
    constexpr uint32_t sample_rate = 1000;
    fthr::AudioRingBuffer short_ring(10 * sample_rate, sample_rate, 1, 0);
    PushSeconds(short_ring, 2, sample_rate);
    const auto short_snapshot = short_ring.TakeSnapshot(5.0, 2.0);
    assert(short_snapshot.valid);
    assert(short_snapshot.samples.size() == 2 * sample_rate);

}

void TestNoMicrophoneKeepsDesktopAudio() {
    constexpr uint32_t sample_rate = 1000;
    // Microphone capture is an independent UI-side ring. Its absence must not
    // change the engine's desktop-audio interval.
    fthr::AudioRingBuffer desktop_ring(10 * sample_rate, sample_rate, 1, 0);
    PushSeconds(desktop_ring, 5, sample_rate);
    const auto desktop_only = desktop_ring.TakeSnapshot(5.0, 5.0);
    assert(desktop_only.valid);
    assert(desktop_only.samples.size() == 5 * sample_rate);
}

void TestNoDesktopAudioIsVideoOnly() {
    constexpr uint32_t sample_rate = 1000;
    fthr::AudioRingBuffer empty_ring(10 * sample_rate, sample_rate, 1, 0);
    const auto no_desktop_audio = empty_ring.TakeSnapshot(5.0, 2.0);
    assert(!no_desktop_audio.valid);
    assert(no_desktop_audio.samples.empty());
}

void TestMissingQpcFallsBackToAvailableAudio() {
    constexpr uint32_t sample_rate = 1000;
    fthr::AudioRingBuffer ring(5 * sample_rate, sample_rate, 1, 0);
    std::vector<float> samples(2 * sample_rate, 0.25f);
    ring.Push(samples.data(), 2 * sample_rate, 0);
    const auto snapshot = ring.TakeSnapshot(2.0, 100.0);
    assert(snapshot.valid);
    assert(snapshot.samples.size() == 2 * sample_rate);
    assert(snapshot.qpc_start_s == 0.0);
    assert(std::abs(snapshot.qpc_end_s - 2.0) < 0.001);
}

} // namespace

int main() {
    TestExactAudioWindowUsesRequestedEnd();
    TestAudioShorterThanVideoIsExplicit();
    TestNoMicrophoneKeepsDesktopAudio();
    TestNoDesktopAudioIsVideoOnly();
    TestMissingQpcFallsBackToAvailableAudio();
    return 0;
}

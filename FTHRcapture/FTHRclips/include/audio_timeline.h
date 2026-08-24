// audio_timeline.h -- shared QPC-to-audio timeline and bounded drift policy.
//
// Every native Windows audio provider maps its device packets to the video
// presentation clock through this small, platform-neutral contract.  It owns
// no device handles and deliberately carries no shared-memory state.

#pragma once
#ifndef FTHR_AUDIO_TIMELINE_H
#define FTHR_AUDIO_TIMELINE_H

#include <cstdint>

namespace fthr {

inline constexpr uint32_t kCanonicalAudioSampleRate = 48000;
inline constexpr uint32_t kCanonicalAudioChannels = 2;

// Converts the process-wide QueryPerformanceCounter clock to 100-nanosecond
// units. WASAPI's non-zero QPC timestamps are already in this same domain.
// Providers use this only when a driver omits a packet timestamp, so every
// native source still has a monotonic timeline anchor.
uint64_t CurrentAudioTimeline100ns();

struct AudioSourcePresentationRange {
    // Negative starts intentionally preserve a source that became audible
    // after the beginning of the selected video interval.
    int64_t start_pts_samples = 0;
    int64_t end_pts_samples = 0;
};

AudioSourcePresentationRange MapAudioSourcePresentationRange(
    double presentation_start_qpc_s, double presentation_end_qpc_s,
    uint64_t source_timeline_origin_100ns, uint32_t sample_rate);

// Converts an AAC/sample position back to the shared QPC timeline. Negative
// positions are valid for encoder delay and must remain signed during the
// conversion instead of wrapping through uint64_t.
int64_t AudioSamplePositionToTimeline100ns(
    uint64_t source_timeline_origin_100ns, int64_t sample_position,
    uint32_t sample_rate);

struct AudioDriftCorrection {
    int64_t observed_drift_samples = 0;
    int32_t sample_delta = 0;
    uint32_t compensation_distance_samples = 0;
};

// Bounds how quickly an independent endpoint may be resampled toward the
// common QPC clock.  A device clock is never jumped: at most max_ppm worth of
// samples is added or removed over one bounded compensation window.
class AudioDriftController {
public:
    explicit AudioDriftController(uint32_t sample_rate = kCanonicalAudioSampleRate,
                                  uint32_t max_ppm = 100);

    AudioDriftCorrection Observe(uint64_t qpc_100ns, int64_t submitted_frames);
    void Reset();

    int64_t max_observed_drift_samples() const { return max_observed_drift_samples_; }
    uint64_t origin_qpc_100ns() const { return origin_qpc_100ns_; }

private:
    uint32_t sample_rate_;
    uint32_t max_ppm_;
    uint64_t origin_qpc_100ns_ = 0;
    uint64_t last_adjustment_qpc_100ns_ = 0;
    int64_t max_observed_drift_samples_ = 0;
};

}  // namespace fthr

#endif  // FTHR_AUDIO_TIMELINE_H

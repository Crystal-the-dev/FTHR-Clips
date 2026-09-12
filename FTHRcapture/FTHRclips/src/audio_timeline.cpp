#include "audio_timeline.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#ifdef min
#undef min
#endif
#ifdef max
#undef max
#endif
#endif

namespace fthr {

uint64_t CurrentAudioTimeline100ns() {
#ifdef _WIN32
    LARGE_INTEGER counter{};
    LARGE_INTEGER frequency{};
    if (!QueryPerformanceCounter(&counter) || !QueryPerformanceFrequency(&frequency)
            || frequency.QuadPart <= 0) {
        return 0;
    }
    const uint64_t ticks = static_cast<uint64_t>(counter.QuadPart);
    const uint64_t ticks_per_second = static_cast<uint64_t>(frequency.QuadPart);
    return (ticks / ticks_per_second) * 10'000'000ULL
        + ((ticks % ticks_per_second) * 10'000'000ULL) / ticks_per_second;
#else
    return 0;
#endif
}

AudioTimelinePacketAdjustment ReconcileAudioTimelinePacket(
    uint64_t next_timeline_100ns, uint64_t packet_qpc_100ns,
    uint32_t input_frames, uint32_t input_sample_rate,
    uint32_t canonical_sample_rate, uint32_t max_synthetic_gap_seconds) {
    AudioTimelinePacketAdjustment result;
    if (packet_qpc_100ns == 0 || input_sample_rate == 0
            || canonical_sample_rate == 0) return result;
    const uint64_t packet_duration = (static_cast<uint64_t>(input_frames)
        * 10'000'000ULL) / input_sample_rate;
    const uint64_t packet_end = packet_qpc_100ns + packet_duration;
    if (next_timeline_100ns == 0) {
        result.next_timeline_100ns = packet_end;
        return result;
    }
    if (packet_qpc_100ns > next_timeline_100ns) {
        result.gap_100ns = packet_qpc_100ns - next_timeline_100ns;
        const uint64_t max_gap_100ns = max_synthetic_gap_seconds >
                std::numeric_limits<uint64_t>::max() / 10'000'000ULL
            ? std::numeric_limits<uint64_t>::max()
            : static_cast<uint64_t>(max_synthetic_gap_seconds) * 10'000'000ULL;
        if (result.gap_100ns > max_gap_100ns) {
            result.large_gap = true;
            // Do not advance the cursor on a gap that the caller cannot
            // represent with bounded synthetic audio. Providers must fail or
            // restart the source; advancing here would collapse media time and
            // encode the resumed packet at an earlier AAC PTS.
            result.next_timeline_100ns = next_timeline_100ns;
            return result;
        }
        const uint64_t gap_frames = (result.gap_100ns * canonical_sample_rate)
            / 10'000'000ULL;
        const uint64_t maximum = static_cast<uint64_t>(max_synthetic_gap_seconds)
            * canonical_sample_rate;
        if (gap_frames > maximum) {
            result.large_gap = true;
            result.next_timeline_100ns = next_timeline_100ns;
            return result;
        }
        result.silence_frames = static_cast<uint32_t>(std::min<uint64_t>(
            gap_frames, std::numeric_limits<uint32_t>::max()));
    } else if (packet_qpc_100ns < next_timeline_100ns && input_frames > 0) {
        const uint64_t overlap_100ns = next_timeline_100ns - packet_qpc_100ns;
        const uint64_t overlap_frames = overlap_100ns >= packet_duration
            ? input_frames
            : (overlap_100ns * input_sample_rate) / 10'000'000ULL;
        result.skip_input_frames = static_cast<uint32_t>(std::min<uint64_t>(
            overlap_frames, input_frames));
    }
    result.next_timeline_100ns = std::max(next_timeline_100ns, packet_end);
    return result;
}

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

int64_t AudioSamplePositionToTimeline100ns(
    uint64_t source_timeline_origin_100ns, int64_t sample_position,
    uint32_t sample_rate) {
    if (source_timeline_origin_100ns == 0 || sample_rate == 0) return 0;

    constexpr int64_t kTimelineUnitsPerSecond = 10'000'000;
    const int64_t whole_seconds = sample_position / static_cast<int64_t>(sample_rate);
    const int64_t remaining_samples = sample_position % static_cast<int64_t>(sample_rate);
    if (whole_seconds > std::numeric_limits<int64_t>::max() / kTimelineUnitsPerSecond) {
        return std::numeric_limits<int64_t>::max();
    }
    if (whole_seconds < std::numeric_limits<int64_t>::min() / kTimelineUnitsPerSecond) {
        return 0;
    }
    const int64_t whole_delta = whole_seconds * kTimelineUnitsPerSecond;
    const int64_t remainder_delta =
        (remaining_samples * kTimelineUnitsPerSecond)
            / static_cast<int64_t>(sample_rate);
    if (remainder_delta > 0
            && whole_delta > std::numeric_limits<int64_t>::max() - remainder_delta) {
        return std::numeric_limits<int64_t>::max();
    }
    if (remainder_delta < 0
            && whole_delta < std::numeric_limits<int64_t>::min() - remainder_delta) {
        return 0;
    }
    const int64_t delta = whole_delta + remainder_delta;

    if (delta >= 0) {
        const uint64_t positive_delta = static_cast<uint64_t>(delta);
        const uint64_t maximum = static_cast<uint64_t>(
            std::numeric_limits<int64_t>::max());
        if (source_timeline_origin_100ns > maximum - positive_delta) {
            return std::numeric_limits<int64_t>::max();
        }
        return static_cast<int64_t>(source_timeline_origin_100ns + positive_delta);
    }

    const uint64_t negative_delta = static_cast<uint64_t>(-(delta + 1)) + 1;
    if (negative_delta >= source_timeline_origin_100ns) return 0;
    const uint64_t result = source_timeline_origin_100ns - negative_delta;
    const uint64_t maximum = static_cast<uint64_t>(
        std::numeric_limits<int64_t>::max());
    return result > maximum
        ? std::numeric_limits<int64_t>::max()
        : static_cast<int64_t>(result);
}

AudioDriftController::AudioDriftController(uint32_t sample_rate, uint32_t max_ppm)
    : sample_rate_(std::max<uint32_t>(1, sample_rate)),
      max_ppm_(std::clamp<uint32_t>(max_ppm, 1, 500)) {}

AudioDriftCorrection AudioDriftController::Observe(uint64_t qpc_100ns,
                                                    int64_t submitted_frames) {
    AudioDriftCorrection result;
    if (qpc_100ns == 0 || submitted_frames < 0) return result;
    if (origin_qpc_100ns_ == 0) {
        origin_qpc_100ns_ = qpc_100ns;
        last_adjustment_qpc_100ns_ = qpc_100ns;
        return result;
    }
    if (qpc_100ns < origin_qpc_100ns_) return result;

    const int64_t expected_frames = static_cast<int64_t>(std::llround(
        static_cast<double>(qpc_100ns - origin_qpc_100ns_) * sample_rate_ / 10'000'000.0));
    result.observed_drift_samples = expected_frames - submitted_frames;
    max_observed_drift_samples_ = std::max(max_observed_drift_samples_,
        std::llabs(result.observed_drift_samples));

    constexpr uint64_t kMinimumAdjustmentInterval100ns = 5'000'000;  // 500 ms
    if (qpc_100ns - last_adjustment_qpc_100ns_ < kMinimumAdjustmentInterval100ns
            || std::llabs(result.observed_drift_samples) <= 1) {
        return result;
    }

    const uint64_t elapsed_100ns = qpc_100ns - last_adjustment_qpc_100ns_;
    const uint64_t distance = std::max<uint64_t>(sample_rate_ / 2,
        (elapsed_100ns * sample_rate_) / 10'000'000ULL);
    const int64_t maximum_delta = std::max<int64_t>(1,
        static_cast<int64_t>((distance * max_ppm_) / 1'000'000ULL));
    const int64_t clamped_delta = std::clamp(result.observed_drift_samples,
        -maximum_delta, maximum_delta);
    result.sample_delta = static_cast<int32_t>(std::clamp<int64_t>(clamped_delta,
        std::numeric_limits<int32_t>::min(), std::numeric_limits<int32_t>::max()));
    result.compensation_distance_samples = static_cast<uint32_t>(std::min<uint64_t>(
        distance, std::numeric_limits<uint32_t>::max()));
    last_adjustment_qpc_100ns_ = qpc_100ns;
    return result;
}

void AudioDriftController::Reset() {
    origin_qpc_100ns_ = 0;
    last_adjustment_qpc_100ns_ = 0;
    max_observed_drift_samples_ = 0;
}

}  // namespace fthr

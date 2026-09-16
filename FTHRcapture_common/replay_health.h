#pragma once

// Pure replay packet health policy shared by the native save boundary and
// deterministic tests.  This intentionally has no FFmpeg, Windows, or ring
// buffer dependency: a caller supplies the already selected packet timeline.

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace fthr::replay_health {

struct Sample {
    int64_t wall_qpc = 0;
    int64_t pts = 0;
    uint64_t generation = 0;
};

struct Input {
    std::vector<Sample> packets;
    uint32_t requested_duration_seconds = 0;
    double expected_fps = 0.0;
    int64_t wall_qpc_frequency = 0;
    int64_t target_end_wall_qpc = 0;
    double pts_per_second = 0.0;
    bool full_history = false;
};

enum class Failure {
    None,
    Empty,
    InsufficientPackets,
    InsufficientCoverage,
    StaleEnd,
    GenerationDiscontinuity,
    InvalidTimeline,
    LargeTimelineGap,
    LowEffectiveRate,
};

inline const char* FailureName(Failure failure) noexcept {
    switch (failure) {
    case Failure::None: return "NONE";
    case Failure::Empty: return "EMPTY";
    case Failure::InsufficientPackets: return "INSUFFICIENT_PACKETS";
    case Failure::InsufficientCoverage: return "INSUFFICIENT_COVERAGE";
    case Failure::StaleEnd: return "STALE_END";
    case Failure::GenerationDiscontinuity: return "GENERATION_DISCONTINUITY";
    case Failure::InvalidTimeline: return "INVALID_TIMELINE";
    case Failure::LargeTimelineGap: return "LARGE_TIMELINE_GAP";
    case Failure::LowEffectiveRate: return "LOW_EFFECTIVE_RATE";
    }
    return "UNKNOWN";
}

struct Report {
    bool accepted = false;
    Failure failure = Failure::Empty;
    size_t packet_count = 0;
    double requested_duration_s = 0.0;
    double actual_coverage_s = 0.0;
    double largest_gap_s = 0.0;
    double largest_wall_gap_s = 0.0;
    double largest_pts_gap_s = 0.0;
    double effective_packet_rate = 0.0;
    int64_t first_wall_qpc = 0;
    int64_t last_wall_qpc = 0;
    int64_t first_pts = 0;
    int64_t last_pts = 0;
    uint64_t generation = 0;
    bool generation_continuous = false;
    bool full_history = false;
    bool partial_history = false;
};

inline Report Evaluate(const Input& input) noexcept {
    Report report;
    report.packet_count = input.packets.size();
    report.requested_duration_s =
        static_cast<double>(input.requested_duration_seconds);
    report.full_history = input.full_history;
    report.partial_history = !input.full_history;

    if (input.packets.empty()) {
        report.failure = Failure::Empty;
        return report;
    }
    report.first_pts = input.packets.front().pts;
    report.last_pts = input.packets.back().pts;
    report.generation = input.packets.front().generation;
    report.generation_continuous = true;
    for (const auto& packet : input.packets) {
        if (packet.generation != report.generation) {
            report.generation_continuous = false;
            report.failure = Failure::GenerationDiscontinuity;
            return report;
        }
    }
    if (input.packets.size() < 2
        || !std::isfinite(input.expected_fps)
        || input.expected_fps <= 0.0
        || input.expected_fps > 1000.0) {
        report.failure = Failure::InsufficientPackets;
        return report;
    }

    const bool use_qpc = input.wall_qpc_frequency > 0
        && input.packets.front().wall_qpc > 0;
    if (use_qpc) {
        report.first_wall_qpc = input.packets.front().wall_qpc;
        report.last_wall_qpc = input.packets.back().wall_qpc;
    }
    const double units_per_second = use_qpc
        ? static_cast<double>(input.wall_qpc_frequency)
        : (input.pts_per_second > 0.0
            ? input.pts_per_second
            : static_cast<double>(input.expected_fps));
    if (!(units_per_second > 0.0) || !std::isfinite(units_per_second)) {
        report.failure = Failure::InvalidTimeline;
        return report;
    }

    double previous = use_qpc
        ? static_cast<double>(input.packets.front().wall_qpc)
        : static_cast<double>(input.packets.front().pts);
    if (!std::isfinite(previous)) {
        report.failure = Failure::InvalidTimeline;
        return report;
    }
    const double pts_units_per_second = input.pts_per_second > 0.0
        ? input.pts_per_second
        : static_cast<double>(input.expected_fps);
    if (!(pts_units_per_second > 0.0)
        || !std::isfinite(pts_units_per_second)) {
        report.failure = Failure::InvalidTimeline;
        return report;
    }
    double previous_pts = static_cast<double>(input.packets.front().pts);
    if (!std::isfinite(previous_pts)) {
        report.failure = Failure::InvalidTimeline;
        return report;
    }
    for (size_t index = 1; index < input.packets.size(); ++index) {
        const auto& packet = input.packets[index];
        const double current = use_qpc
            ? static_cast<double>(packet.wall_qpc)
            : static_cast<double>(packet.pts);
        const double gap_units = current - previous;
        if (!(gap_units > 0.0) || !std::isfinite(current)) {
            report.failure = Failure::InvalidTimeline;
            return report;
        }
        const double gap_seconds = gap_units / units_per_second;
        report.largest_wall_gap_s = use_qpc
            ? std::max(report.largest_wall_gap_s, gap_seconds) : 0.0;
        previous = current;

        const double current_pts = static_cast<double>(packet.pts);
        const double pts_gap_units = current_pts - previous_pts;
        if (!(pts_gap_units > 0.0) || !std::isfinite(current_pts)) {
            report.failure = Failure::InvalidTimeline;
            return report;
        }
        report.largest_pts_gap_s = std::max(
            report.largest_pts_gap_s, pts_gap_units / pts_units_per_second);
        previous_pts = current_pts;
    }
    // A healthy capture must have both a monotonic wall-clock progression and
    // a monotonic media timeline. A QPC-only check can otherwise hide a
    // malformed encoder PTS stream with nominal wall timing.
    report.largest_gap_s = std::max(
        report.largest_wall_gap_s, report.largest_pts_gap_s);
    report.actual_coverage_s =
        (previous - (use_qpc
            ? static_cast<double>(input.packets.front().wall_qpc)
            : static_cast<double>(input.packets.front().pts)))
        / units_per_second;
    if (!(report.actual_coverage_s > 0.0)
        || !std::isfinite(report.actual_coverage_s)) {
        report.failure = Failure::InvalidTimeline;
        return report;
    }
    if (use_qpc && input.target_end_wall_qpc > 0
        && input.target_end_wall_qpc >= report.last_wall_qpc) {
        const double end_staleness_s = static_cast<double>(
            input.target_end_wall_qpc - report.last_wall_qpc)
            / units_per_second;
        if (end_staleness_s > 1.0) {
            report.failure = Failure::StaleEnd;
            return report;
        }
    }
    report.effective_packet_rate =
        static_cast<double>(input.packets.size() - 1)
        / report.actual_coverage_s;

    // A full-history selection is expected to cover the requested interval.
    // Permit one frame period for endpoint semantics, but do not allow a few
    // tightly clustered packets to masquerade as a complete replay.
    const double minimum_full_coverage = std::max(
        0.0,
        report.requested_duration_s * 0.90 - 1.0 / input.expected_fps);
    if (input.full_history
        && report.actual_coverage_s < minimum_full_coverage) {
        report.failure = Failure::InsufficientCoverage;
        return report;
    }

    // Partial history is valid after startup/recovery, but a save still needs
    // a meaningful interval. Four tightly clustered packets cannot produce a
    // useful requested clip; a dense ~1 second startup interval can.
    const double minimum_partial_coverage = std::min(
        0.5, report.requested_duration_s > 0.0
            ? report.requested_duration_s : 0.5);
    const size_t minimum_interval_packets = static_cast<size_t>(std::ceil(
        input.expected_fps * report.actual_coverage_s * 0.25));
    if (!input.full_history
        && (report.actual_coverage_s < minimum_partial_coverage
            || input.packets.size() < std::max<size_t>(
                6, minimum_interval_packets))) {
        report.failure = Failure::InsufficientCoverage;
        return report;
    }

    // A short scheduling hiccup is recoverable.  A gap above 250 ms (or four
    // nominal frame periods for unusually low configured FPS) is evidence
    // that the selected replay cannot be published as a continuous clip.
    const double max_bounded_gap_s =
        std::max(0.250, 4.0 / input.expected_fps);
    if (report.largest_gap_s > max_bounded_gap_s) {
        report.failure = Failure::LargeTimelineGap;
        return report;
    }

    // Do not reject a tiny partial startup history solely for having few
    // packets. Once an interval is established, however, a sustained rate
    // below half the configured cadence is not a valid native replay.
    if (input.packets.size() > 4
        && report.effective_packet_rate < input.expected_fps * 0.5) {
        report.failure = Failure::LowEffectiveRate;
        return report;
    }
    report.accepted = true;
    report.failure = Failure::None;
    return report;
}

} // namespace fthr::replay_health

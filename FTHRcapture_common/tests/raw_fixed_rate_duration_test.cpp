#include "replay_interval.h"

#include <cassert>
#include <cstddef>

int main() {
    constexpr size_t fps = 60;
    const auto normal = fthr::replay_interval::SelectFixedRateFrames(
        61 * fps, 61 * fps, 30 * fps, fps);
    assert(normal.full_history);
    assert(normal.frame_count == 30 * fps);
    assert(static_cast<double>(normal.frame_count) / fps == 30.0);
    assert(normal.decode_start == 30 * fps);

    const auto extended = fthr::replay_interval::SelectFixedRateFrames(
        121 * fps, 121 * fps, 60 * fps, fps);
    assert(extended.full_history);
    assert(static_cast<double>(extended.frame_count) / fps == 60.0);

    // The 2 GiB physical cap can leave only a few seconds on large frames.
    // That is correctly classified as partial physical history, not AUDIT-042.
    const auto capacity_limited = fthr::replay_interval::SelectFixedRateFrames(
        4 * fps, 4 * fps, 30 * fps, fps);
    assert(!capacity_limited.full_history);
    assert(static_cast<double>(capacity_limited.frame_count) / fps == 3.0);
    return 0;
}

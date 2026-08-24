#pragma once
#ifndef FTHR_FRAME_RATE_SCHEDULER_H
#define FTHR_FRAME_RATE_SCHEDULER_H

#include <cstdint>
#include <limits>

namespace fthr {

// Keeps the target cadence anchored to a QPC deadline. Resetting the clock to
// every accepted host frame aliases 90 Hz input to 45 fps for a 60 fps target;
// advancing the deadline by whole intervals preserves the requested cadence.
class FrameRateScheduler {
public:
    explicit FrameRateScheduler(int64_t interval_ticks)
        : interval_ticks_(interval_ticks > 0 ? interval_ticks : 1) {}

    bool ShouldCapture(int64_t now_ticks) {
        if (!started_) {
            started_ = true;
            next_deadline_ticks_ = AddInterval(now_ticks);
            return true;
        }
        if (now_ticks < next_deadline_ticks_) return false;

        const int64_t elapsed = now_ticks - next_deadline_ticks_;
        const int64_t intervals = elapsed / interval_ticks_ + 1;
        const int64_t room = std::numeric_limits<int64_t>::max()
            - next_deadline_ticks_;
        next_deadline_ticks_ = intervals > room / interval_ticks_
            ? std::numeric_limits<int64_t>::max()
            : next_deadline_ticks_ + intervals * interval_ticks_;
        return true;
    }

private:
    int64_t AddInterval(int64_t ticks) const {
        const int64_t room = std::numeric_limits<int64_t>::max() - ticks;
        return interval_ticks_ > room
            ? std::numeric_limits<int64_t>::max()
            : ticks + interval_ticks_;
    }

    int64_t interval_ticks_ = 1;
    int64_t next_deadline_ticks_ = 0;
    bool started_ = false;
};

} // namespace fthr

#endif // FTHR_FRAME_RATE_SCHEDULER_H

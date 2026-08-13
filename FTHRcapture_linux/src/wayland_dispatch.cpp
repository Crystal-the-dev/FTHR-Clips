#include "wayland_dispatch.h"

#include <algorithm>
#include <cerrno>
#include <cstdint>

namespace fthr {
namespace {

constexpr auto kCancellationPollSlice = std::chrono::milliseconds(50);

bool IsDisconnectError(int error) noexcept {
    return error == EPIPE || error == ECONNRESET || error == ENOTCONN;
}

WaylandWaitResult ClassifyFailure(
    wl_display* display,
    const WaylandOperations& operations,
    int error) {
    if (IsDisconnectError(error) || operations.get_error(display) != 0)
        return WaylandWaitResult::Disconnected;
    return WaylandWaitResult::Error;
}

int PollTimeout(WaylandDeadline deadline) {
    const auto remaining = deadline - std::chrono::steady_clock::now();
    if (remaining <= WaylandDeadline::duration::zero()) return 0;
    const auto bounded = std::min(
        std::chrono::ceil<std::chrono::milliseconds>(remaining),
        kCancellationPollSlice);
    return static_cast<int>(bounded.count());
}

class PreparedRead final {
public:
    PreparedRead(wl_display* display, const WaylandOperations& operations)
        : display_(display), operations_(operations) {}

    ~PreparedRead() {
        if (active_) operations_.cancel_read(display_);
    }

    PreparedRead(const PreparedRead&) = delete;
    PreparedRead& operator=(const PreparedRead&) = delete;

    void MarkPrepared() noexcept { active_ = true; }
    void MarkRead() noexcept { active_ = false; }

private:
    wl_display* display_;
    const WaylandOperations& operations_;
    bool active_ = false;
};

struct RoundtripState {
    bool done = false;
};

void RoundtripDone(void* data, wl_callback*, uint32_t) {
    static_cast<RoundtripState*>(data)->done = true;
}

const wl_callback_listener kRoundtripListener = {RoundtripDone};

} // namespace

const WaylandOperations& DefaultWaylandOperations() {
    static const WaylandOperations operations{
        [](wl_display* display) { return wl_display_dispatch_pending(display); },
        [](wl_display* display) { return wl_display_prepare_read(display); },
        [](wl_display* display) { wl_display_cancel_read(display); },
        [](wl_display* display) { return wl_display_read_events(display); },
        [](wl_display* display) { return wl_display_flush(display); },
        [](wl_display* display) { return wl_display_get_fd(display); },
        [](wl_display* display) { return wl_display_get_error(display); },
        [](pollfd* fds, nfds_t count, int timeout) {
            return ::poll(fds, count, timeout);
        },
        [](wl_display* display) { return wl_display_sync(display); },
        [](wl_callback* callback, const wl_callback_listener* listener, void* data) {
            return wl_callback_add_listener(callback, listener, data);
        },
        [](wl_callback* callback) { wl_callback_destroy(callback); },
    };
    return operations;
}

const char* WaylandWaitResultName(WaylandWaitResult result) noexcept {
    switch (result) {
    case WaylandWaitResult::EventReceived: return "event received";
    case WaylandWaitResult::Timeout: return "timed out";
    case WaylandWaitResult::Cancelled: return "cancelled";
    case WaylandWaitResult::Disconnected: return "display disconnected";
    case WaylandWaitResult::Error: return "event wait failed";
    }
    return "unknown Wayland wait result";
}

WaylandWaitResult WaitForWaylandEvent(
    wl_display* display,
    WaylandDeadline deadline,
    const WaylandPredicate& keep_running,
    const WaylandOperations& operations) {
    if (!display) return WaylandWaitResult::Error;

    while (keep_running()) {
        const int dispatched = operations.dispatch_pending(display);
        if (dispatched < 0) {
            const int error = errno;
            return ClassifyFailure(display, operations, error);
        }
        if (dispatched > 0) return WaylandWaitResult::EventReceived;
        if (std::chrono::steady_clock::now() >= deadline)
            return WaylandWaitResult::Timeout;

        errno = 0;
        if (operations.prepare_read(display) != 0) {
            const int error = errno;
            if (error != EAGAIN)
                return ClassifyFailure(display, operations, error);
            continue;
        }

        PreparedRead prepared(display, operations);
        prepared.MarkPrepared();
        if (!keep_running()) return WaylandWaitResult::Cancelled;

        bool needs_write = false;
        errno = 0;
        if (operations.flush(display) < 0) {
            const int error = errno;
            if (error == EAGAIN) needs_write = true;
            else return ClassifyFailure(display, operations, error);
        }

        while (keep_running()) {
            const int timeout = PollTimeout(deadline);
            if (timeout == 0) return WaylandWaitResult::Timeout;

            pollfd display_fd{
                operations.get_fd(display),
                static_cast<short>(POLLIN | (needs_write ? POLLOUT : 0)),
                0,
            };
            errno = 0;
            const int poll_result = operations.poll_fds(&display_fd, 1, timeout);
            if (poll_result < 0) {
                const int error = errno;
                if (error == EINTR) continue;
                return ClassifyFailure(display, operations, error);
            }
            if (poll_result == 0) {
                if (std::chrono::steady_clock::now() >= deadline)
                    return WaylandWaitResult::Timeout;
                continue;
            }
            if ((display_fd.revents & (POLLHUP | POLLERR)) != 0)
                return WaylandWaitResult::Disconnected;
            if ((display_fd.revents & POLLNVAL) != 0)
                return WaylandWaitResult::Error;

            if (needs_write && (display_fd.revents & POLLOUT) != 0) {
                errno = 0;
                if (operations.flush(display) >= 0) {
                    needs_write = false;
                } else {
                    const int error = errno;
                    if (error != EAGAIN)
                        return ClassifyFailure(display, operations, error);
                }
            }

            if ((display_fd.revents & POLLIN) != 0) {
                errno = 0;
                if (operations.read_events(display) < 0) {
                    const int error = errno;
                    prepared.MarkRead();
                    return ClassifyFailure(display, operations, error);
                }
                prepared.MarkRead();
                const int pending = operations.dispatch_pending(display);
                if (pending < 0) {
                    const int error = errno;
                    return ClassifyFailure(display, operations, error);
                }
                return WaylandWaitResult::EventReceived;
            }
        }
        return WaylandWaitResult::Cancelled;
    }
    return WaylandWaitResult::Cancelled;
}

WaylandWaitResult DispatchWaylandUntil(
    wl_display* display,
    WaylandDeadline deadline,
    const WaylandPredicate& keep_running,
    const WaylandPredicate& complete,
    const WaylandOperations& operations) {
    while (keep_running()) {
        if (complete()) return WaylandWaitResult::EventReceived;
        const auto result = WaitForWaylandEvent(
            display, deadline, keep_running, operations);
        if (result != WaylandWaitResult::EventReceived) return result;
    }
    return WaylandWaitResult::Cancelled;
}

WaylandWaitResult BoundedWaylandRoundtrip(
    wl_display* display,
    WaylandDeadline deadline,
    const WaylandPredicate& keep_running,
    const WaylandOperations& operations) {
    if (!display) return WaylandWaitResult::Error;
    wl_callback* callback = operations.sync(display);
    if (!callback) return WaylandWaitResult::Error;

    RoundtripState state;
    if (operations.add_callback_listener(
            callback, &kRoundtripListener, &state) != 0) {
        operations.destroy_callback(callback);
        return WaylandWaitResult::Error;
    }

    const auto result = DispatchWaylandUntil(
        display,
        deadline,
        keep_running,
        [&state] { return state.done; },
        operations);
    operations.destroy_callback(callback);
    return result;
}

} // namespace fthr

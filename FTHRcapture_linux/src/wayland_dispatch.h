#pragma once

#include <chrono>
#include <functional>

#include <poll.h>
#include <wayland-client.h>

namespace fthr {

enum class WaylandWaitResult {
    EventReceived,
    Timeout,
    Cancelled,
    Disconnected,
    Error,
};

// Injectable libwayland/poll seam. Production uses DefaultWaylandOperations();
// native tests use a pipe-backed fake display without requiring a compositor.
struct WaylandOperations {
    std::function<int(wl_display*)> dispatch_pending;
    std::function<int(wl_display*)> prepare_read;
    std::function<void(wl_display*)> cancel_read;
    std::function<int(wl_display*)> read_events;
    std::function<int(wl_display*)> flush;
    std::function<int(wl_display*)> get_fd;
    std::function<int(wl_display*)> get_error;
    std::function<int(pollfd*, nfds_t, int)> poll_fds;
    std::function<wl_callback*(wl_display*)> sync;
    std::function<int(wl_callback*, const wl_callback_listener*, void*)>
        add_callback_listener;
    std::function<void(wl_callback*)> destroy_callback;
};

using WaylandDeadline = std::chrono::steady_clock::time_point;
using WaylandPredicate = std::function<bool()>;

const WaylandOperations& DefaultWaylandOperations();
const char* WaylandWaitResultName(WaylandWaitResult result) noexcept;

// Wait for and dispatch at least one default-queue event. The display must be
// owned by the calling thread. Every successful prepare_read is paired with
// read_events or cancel_read before this function returns.
WaylandWaitResult WaitForWaylandEvent(
    wl_display* display,
    WaylandDeadline deadline,
    const WaylandPredicate& keep_running,
    const WaylandOperations& operations = DefaultWaylandOperations());

// Dispatch until the supplied protocol callback state is satisfied, or until
// the shared deadline/cancellation/error terminates the wait.
WaylandWaitResult DispatchWaylandUntil(
    wl_display* display,
    WaylandDeadline deadline,
    const WaylandPredicate& keep_running,
    const WaylandPredicate& complete,
    const WaylandOperations& operations = DefaultWaylandOperations());

// Deadline-aware replacement for wl_display_roundtrip().
WaylandWaitResult BoundedWaylandRoundtrip(
    wl_display* display,
    WaylandDeadline deadline,
    const WaylandPredicate& keep_running,
    const WaylandOperations& operations = DefaultWaylandOperations());

} // namespace fthr

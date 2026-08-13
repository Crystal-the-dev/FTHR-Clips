#include "wayland_dispatch.h"

#include <atomic>
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <thread>

#include <unistd.h>

namespace {

using namespace std::chrono_literals;
namespace fthr_wl = fthr;

class FakeDisplay final {
public:
    FakeDisplay() {
        const int result = pipe(pipe_fds_);
        assert(result == 0);
    }

    ~FakeDisplay() {
        if (pipe_fds_[0] >= 0) close(pipe_fds_[0]);
        if (pipe_fds_[1] >= 0) close(pipe_fds_[1]);
    }

    FakeDisplay(const FakeDisplay&) = delete;
    FakeDisplay& operator=(const FakeDisplay&) = delete;

    wl_display* Display() noexcept {
        return reinterpret_cast<wl_display*>(&display_token_);
    }

    wl_callback* Callback() noexcept {
        return reinterpret_cast<wl_callback*>(&callback_token_);
    }

    void SignalEvent() const {
        const char byte = 'x';
        assert(write(pipe_fds_[1], &byte, 1) == 1);
    }

    void Disconnect() {
        close(pipe_fds_[1]);
        pipe_fds_[1] = -1;
    }

    fthr_wl::WaylandOperations Operations() {
        return {
            [this](wl_display*) {
                ++dispatch_calls;
                if (pending_prepare_event) {
                    pending_prepare_event = false;
                    return 1;
                }
                if (sync_event_pending && callback_listener) {
                    sync_event_pending = false;
                    callback_listener->done(
                        callback_data, Callback(), callback_serial++);
                    return 1;
                }
                return 0;
            },
            [this](wl_display*) {
                ++prepare_calls;
                if (prepare_failures > 0) {
                    --prepare_failures;
                    pending_prepare_event = true;
                    errno = EAGAIN;
                    return -1;
                }
                assert(!prepared);
                prepared = true;
                return 0;
            },
            [this](wl_display*) {
                assert(prepared);
                prepared = false;
                ++cancel_calls;
            },
            [this](wl_display*) {
                assert(prepared);
                char byte = 0;
                const auto bytes = read(pipe_fds_[0], &byte, 1);
                prepared = false;
                ++read_calls;
                if (bytes != 1) {
                    errno = ECONNRESET;
                    return -1;
                }
                if (emit_sync_on_read) sync_event_pending = true;
                return 0;
            },
            [](wl_display*) { return 0; },
            [this](wl_display*) { return pipe_fds_[0]; },
            [this](wl_display*) { return display_error; },
            [this](pollfd* fds, nfds_t count, int timeout) {
                ++poll_calls;
                return ::poll(fds, count, timeout);
            },
            [this](wl_display*) {
                ++sync_calls;
                return Callback();
            },
            [this](wl_callback*, const wl_callback_listener* listener, void* data) {
                callback_listener = listener;
                callback_data = data;
                return 0;
            },
            [this](wl_callback*) {
                ++destroy_callback_calls;
                callback_listener = nullptr;
                callback_data = nullptr;
            },
        };
    }

    int prepare_failures = 0;
    int display_error = 0;
    bool emit_sync_on_read = false;
    bool prepared = false;
    int dispatch_calls = 0;
    int prepare_calls = 0;
    int cancel_calls = 0;
    int read_calls = 0;
    int poll_calls = 0;
    int sync_calls = 0;
    int destroy_callback_calls = 0;

private:
    int pipe_fds_[2]{-1, -1};
    int display_token_ = 0;
    int callback_token_ = 0;
    bool pending_prepare_event = false;
    bool sync_event_pending = false;
    uint32_t callback_serial = 1;
    const wl_callback_listener* callback_listener = nullptr;
    void* callback_data = nullptr;
};

auto DeadlineAfter(std::chrono::milliseconds duration) {
    return std::chrono::steady_clock::now() + duration;
}

void TestEventArrivesBeforeDeadline() {
    FakeDisplay fake;
    auto operations = fake.Operations();
    fake.SignalEvent();

    const auto result = fthr_wl::WaitForWaylandEvent(
        fake.Display(), DeadlineAfter(1s), [] { return true; }, operations);

    assert(result == fthr_wl::WaylandWaitResult::EventReceived);
    assert(fake.read_calls == 1);
    assert(fake.cancel_calls == 0);
    assert(!fake.prepared);
}

void TestNoEventHasBoundedTimeoutWithoutBusySpin() {
    FakeDisplay fake;
    auto operations = fake.Operations();
    const auto started = std::chrono::steady_clock::now();

    const auto result = fthr_wl::WaitForWaylandEvent(
        fake.Display(), DeadlineAfter(125ms), [] { return true; }, operations);
    const auto elapsed = std::chrono::steady_clock::now() - started;

    assert(result == fthr_wl::WaylandWaitResult::Timeout);
    assert(elapsed < 750ms);
    assert(fake.poll_calls >= 2 && fake.poll_calls <= 4);
    assert(fake.cancel_calls == 1);
    assert(!fake.prepared);
}

void TestShutdownCancelsPreparedReadPromptly() {
    FakeDisplay fake;
    auto operations = fake.Operations();
    std::atomic<bool> running{true};
    std::thread stop_thread([&running] {
        std::this_thread::sleep_for(15ms);
        running.store(false);
    });
    const auto started = std::chrono::steady_clock::now();

    const auto result = fthr_wl::WaitForWaylandEvent(
        fake.Display(), DeadlineAfter(2s),
        [&running] { return running.load(); }, operations);
    const auto elapsed = std::chrono::steady_clock::now() - started;
    stop_thread.join();

    assert(result == fthr_wl::WaylandWaitResult::Cancelled);
    assert(elapsed < 500ms);
    assert(fake.cancel_calls == 1);
    assert(!fake.prepared);
}

void TestDisplayHangupIsDisconnected() {
    FakeDisplay fake;
    auto operations = fake.Operations();
    fake.Disconnect();

    const auto result = fthr_wl::WaitForWaylandEvent(
        fake.Display(), DeadlineAfter(1s), [] { return true; }, operations);

    assert(result == fthr_wl::WaylandWaitResult::Disconnected);
    assert(fake.cancel_calls == 1);
    assert(!fake.prepared);
}

void TestPendingEventsAreDispatchedBeforeReadPreparationRetry() {
    FakeDisplay fake;
    fake.prepare_failures = 1;
    auto operations = fake.Operations();

    const auto result = fthr_wl::WaitForWaylandEvent(
        fake.Display(), DeadlineAfter(1s), [] { return true; }, operations);

    assert(result == fthr_wl::WaylandWaitResult::EventReceived);
    assert(fake.prepare_calls == 1);
    assert(fake.dispatch_calls == 2);
    assert(fake.cancel_calls == 0);
}

void TestBoundedRoundtripCompletesOnSyncCallback() {
    FakeDisplay fake;
    fake.emit_sync_on_read = true;
    auto operations = fake.Operations();
    fake.SignalEvent();

    const auto result = fthr_wl::BoundedWaylandRoundtrip(
        fake.Display(), DeadlineAfter(1s), [] { return true; }, operations);

    assert(result == fthr_wl::WaylandWaitResult::EventReceived);
    assert(fake.sync_calls == 1);
    assert(fake.destroy_callback_calls == 1);
}

void TestInitializationRoundtripTimesOutAndCancelsRead() {
    FakeDisplay fake;
    auto operations = fake.Operations();

    const auto result = fthr_wl::BoundedWaylandRoundtrip(
        fake.Display(), DeadlineAfter(40ms), [] { return true; }, operations);

    assert(result == fthr_wl::WaylandWaitResult::Timeout);
    assert(fake.cancel_calls == 1);
    assert(fake.destroy_callback_calls == 1);
    assert(!fake.prepared);
}

} // namespace

int main() {
    TestEventArrivesBeforeDeadline();
    TestNoEventHasBoundedTimeoutWithoutBusySpin();
    TestShutdownCancelsPreparedReadPromptly();
    TestDisplayHangupIsDisconnected();
    TestPendingEventsAreDispatchedBeforeReadPreparationRetry();
    TestBoundedRoundtripCompletesOnSyncCallback();
    TestInitializationRoundtripTimesOutAndCancelsRead();
    return 0;
}

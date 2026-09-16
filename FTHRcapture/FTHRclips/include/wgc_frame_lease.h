#pragma once

namespace fthr::wgc {

// A checked-out WGC frame must be closed on every exit, including skipped
// frames and exceptions. Keep all surface/texture references inside this lease.
template <typename Frame>
class FrameLease {
public:
    explicit FrameLease(Frame& frame) noexcept : frame_(frame) {}
    FrameLease(const FrameLease&) = delete;
    FrameLease& operator=(const FrameLease&) = delete;
    ~FrameLease() noexcept {
        try {
            frame_.Close();
        } catch (...) {
            // Device/item loss can also invalidate Close. Never throw while
            // unwinding; acquisition reports source loss on the next poll.
        }
    }

private:
    Frame& frame_;
};

} // namespace fthr::wgc

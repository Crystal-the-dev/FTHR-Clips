#pragma once

#include <cstdint>

namespace fthr {

// NVIDIA requires a registered input resource to remain mapped until
// nvEncLockBitstream() has returned successfully for the corresponding output.
// This small state machine makes that ownership rule explicit and testable.
enum class NvencInputSlotState : uint8_t {
    Available = 0,
    Mapped,
    Submitted,
    CompletionSignaled,
    OutputLocked,
};

class NvencInputSlotLifecycle final {
public:
    constexpr NvencInputSlotLifecycle() noexcept = default;

    bool OnMapped() noexcept {
        return Transition(NvencInputSlotState::Available, NvencInputSlotState::Mapped);
    }

    bool OnSubmitted() noexcept {
        return Transition(NvencInputSlotState::Mapped, NvencInputSlotState::Submitted);
    }

    bool OnCompletionSignaled() noexcept {
        return Transition(
            NvencInputSlotState::Submitted,
            NvencInputSlotState::CompletionSignaled);
    }

    bool OnOutputLocked() noexcept {
        return Transition(
            NvencInputSlotState::CompletionSignaled,
            NvencInputSlotState::OutputLocked);
    }

    bool OnRejectedSubmissionUnmapped() noexcept {
        return Transition(NvencInputSlotState::Mapped, NvencInputSlotState::Available);
    }

    bool OnCompletedInputUnmapped() noexcept {
        return Transition(NvencInputSlotState::OutputLocked, NvencInputSlotState::Available);
    }

    constexpr NvencInputSlotState state() const noexcept { return state_; }
    constexpr bool is_available() const noexcept {
        return state_ == NvencInputSlotState::Available;
    }

    void ResetForShutdown() noexcept { state_ = NvencInputSlotState::Available; }

private:
    bool Transition(NvencInputSlotState expected, NvencInputSlotState next) noexcept {
        if (state_ != expected) return false;
        state_ = next;
        return true;
    }

    NvencInputSlotState state_{NvencInputSlotState::Available};
};

} // namespace fthr

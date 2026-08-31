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
    OutputConsumed,
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

    bool OnOutputConsumed() noexcept {
        return Transition(
            NvencInputSlotState::OutputLocked,
            NvencInputSlotState::OutputConsumed);
    }

    bool OnRejectedSubmissionUnmapped() noexcept {
        return Transition(NvencInputSlotState::Mapped, NvencInputSlotState::Available);
    }

    bool OnCompletedInputUnmapped() noexcept {
        return Transition(
            NvencInputSlotState::OutputConsumed,
            NvencInputSlotState::Available);
    }

    constexpr NvencInputSlotState state() const noexcept { return state_; }
    constexpr bool is_available() const noexcept {
        return state_ == NvencInputSlotState::Available;
    }
    constexpr bool is_ready_to_unmap() const noexcept {
        return state_ == NvencInputSlotState::OutputConsumed;
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

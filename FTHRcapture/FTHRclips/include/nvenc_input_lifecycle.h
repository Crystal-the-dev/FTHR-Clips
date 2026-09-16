#pragma once

#include <atomic>
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
    NvencInputSlotLifecycle() noexcept = default;

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

    NvencInputSlotState state() const noexcept {
        return static_cast<NvencInputSlotState>(
            state_.load(std::memory_order_acquire));
    }
    bool is_available() const noexcept {
        return state() == NvencInputSlotState::Available;
    }
    bool is_ready_to_unmap() const noexcept {
        return state() == NvencInputSlotState::OutputConsumed;
    }

    void ResetForShutdown() noexcept {
        state_.store(
            static_cast<uint8_t>(NvencInputSlotState::Available),
            std::memory_order_release);
    }

private:
    bool Transition(NvencInputSlotState expected, NvencInputSlotState next) noexcept {
        auto expected_value = static_cast<uint8_t>(expected);
        return state_.compare_exchange_strong(
            expected_value,
            static_cast<uint8_t>(next),
            std::memory_order_acq_rel,
            std::memory_order_acquire);
    }

    std::atomic<uint8_t> state_{
        static_cast<uint8_t>(NvencInputSlotState::Available)};
};

} // namespace fthr

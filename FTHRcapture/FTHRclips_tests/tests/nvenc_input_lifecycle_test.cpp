#include "nvenc_input_lifecycle.h"

#include <cstdlib>
#include <iostream>

namespace {

void Require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "NVENC lifecycle test failed: " << message << std::endl;
        std::exit(1);
    }
}

} // namespace

int RunNvencInputLifecycleTests() {
    int checks = 0;

    fthr::NvencInputSlotLifecycle slot;
    Require(slot.OnMapped(), "available slot must map"); ++checks;
    Require(slot.OnSubmitted(), "mapped slot must submit"); ++checks;

    // This was the production bug: unmapping immediately after submission must
    // be rejected until nvEncLockBitstream has confirmed output completion.
    Require(!slot.OnCompletedInputUnmapped(),
        "submitted input must not unmap before output lock returns"); ++checks;
    Require(!slot.is_available(), "premature unmap must not recycle the slot"); ++checks;

    Require(!slot.OnOutputLocked(),
        "worker must not lock output before its completion event"); ++checks;
    Require(slot.OnCompletionSignaled(),
        "submitted slot must accept its completion event"); ++checks;
    Require(slot.OnOutputLocked(), "submitted slot must accept output completion"); ++checks;
    Require(slot.OnCompletedInputUnmapped(),
        "completed slot must unmap and recycle"); ++checks;
    Require(slot.is_available(), "completed slot must return to the pool"); ++checks;

    for (int frame = 0; frame < 1000; ++frame) {
        Require(slot.OnMapped(), "1000-cycle map transition"); ++checks;
        Require(slot.OnSubmitted(), "1000-cycle submit transition"); ++checks;
        Require(slot.OnCompletionSignaled(), "1000-cycle completion transition"); ++checks;
        Require(slot.OnOutputLocked(), "1000-cycle output transition"); ++checks;
        Require(slot.OnCompletedInputUnmapped(), "1000-cycle recycle transition"); ++checks;
    }

    return checks;
}

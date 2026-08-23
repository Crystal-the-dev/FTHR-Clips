// windows_capture_border_policy.h
// Version-adaptive policy for the supported Windows Graphics Capture border API.

#pragma once
#ifndef FTHR_WINDOWS_CAPTURE_BORDER_POLICY_H
#define FTHR_WINDOWS_CAPTURE_BORDER_POLICY_H

#include <cstdint>

namespace fthr {

// GraphicsCaptureSession.IsBorderRequired and GraphicsCaptureAccess(Borderless)
// are available from Windows build 20348. WGC itself remains available earlier.
inline constexpr uint32_t kBorderlessCaptureMinimumBuild = 20348;

enum class CaptureBorderAccessStatus : uint8_t {
    NotRequested,
    Allowed,
    DeniedBySystem,
    NotDeclaredByApp,
    DeniedByUser,
    UserPromptRequired,
    RequestFailed,
};

enum class CaptureBorderPolicyReason : uint8_t {
    NotWgc,
    BuildUnsupported,
    Unpackaged,
    CapabilityNotDeclared,
    AccessRequestRequired,
    AccessDenied,
    SessionInterfaceUnavailable,
    SessionPropertyFailed,
    BorderStillRequired,
    BorderlessEnabled,
};

struct CaptureBorderPlatformCapability {
    uint32_t os_build = 0;
    bool package_identity = false;
    // This is a build-time release-contract value. It must only be true in a
    // package whose manifest declares graphicsCaptureWithoutBorder.
    bool borderless_capability_declared = false;
};

struct CaptureBorderPolicyInput {
    bool wgc_session = true;
    CaptureBorderPlatformCapability platform;
    bool access_request_attempted = false;
    CaptureBorderAccessStatus access_status = CaptureBorderAccessStatus::NotRequested;
    bool session_interface_checked = false;
    bool session_interface_available = false;
    bool property_attempted = false;
    bool property_set_succeeded = false;
    bool border_required_after_attempt = true;
};

struct CaptureBorderPolicyDecision {
    bool api_build_supported = false;
    bool request_borderless_access = false;
    bool attempt_disable_border = false;
    bool effective_borderless = false;
    CaptureBorderPolicyReason reason = CaptureBorderPolicyReason::BuildUnsupported;
};

struct CaptureBorderRuntimeState {
    // Never re-prompt during capture recovery or monitor switching. A new app
    // process may ask Windows again if its prior system consent has changed.
    bool access_request_attempted = false;
    CaptureBorderAccessStatus access_status = CaptureBorderAccessStatus::NotRequested;
};

CaptureBorderPlatformCapability DetectCaptureBorderPlatformCapability();
CaptureBorderPolicyDecision EvaluateCaptureBorderPolicy(
    const CaptureBorderPolicyInput& input);
const char* CaptureBorderAccessStatusName(CaptureBorderAccessStatus status);
const char* CaptureBorderPolicyReasonName(CaptureBorderPolicyReason reason);

}  // namespace fthr

#endif  // FTHR_WINDOWS_CAPTURE_BORDER_POLICY_H

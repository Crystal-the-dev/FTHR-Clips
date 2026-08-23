// windows_capture_border_policy.cpp

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <appmodel.h>

#include "windows_capture_border_policy.h"

namespace fthr {
namespace {

struct RtlOsVersionInfo {
    ULONG dwOSVersionInfoSize;
    ULONG dwMajorVersion;
    ULONG dwMinorVersion;
    ULONG dwBuildNumber;
    ULONG dwPlatformId;
    WCHAR szCSDVersion[128];
};
using RtlGetVersionFn = LONG(WINAPI*)(RtlOsVersionInfo*);

uint32_t DetectWindowsBuildNumber() {
    const HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    const auto get_version = ntdll ? reinterpret_cast<RtlGetVersionFn>(
        GetProcAddress(ntdll, "RtlGetVersion")) : nullptr;
    RtlOsVersionInfo version{};
    version.dwOSVersionInfoSize = sizeof(version);
    if (get_version && get_version(&version) == 0) {
        return version.dwBuildNumber;
    }
    return 0;
}

bool HasCurrentPackageIdentity() {
    UINT32 package_name_length = 0;
    const LONG result = GetCurrentPackageFullName(&package_name_length, nullptr);
    return result == ERROR_INSUFFICIENT_BUFFER && package_name_length > 0;
}

constexpr bool BuildDeclaresBorderlessCapability() {
#if defined(FTHR_BORDERLESS_CAPTURE_CAPABILITY_DECLARED) \
        && FTHR_BORDERLESS_CAPTURE_CAPABILITY_DECLARED
    return true;
#else
    return false;
#endif
}

}  // namespace

CaptureBorderPlatformCapability DetectCaptureBorderPlatformCapability() {
    CaptureBorderPlatformCapability capability;
    capability.os_build = DetectWindowsBuildNumber();
    capability.package_identity = HasCurrentPackageIdentity();
    capability.borderless_capability_declared = BuildDeclaresBorderlessCapability();
    return capability;
}

CaptureBorderPolicyDecision EvaluateCaptureBorderPolicy(
        const CaptureBorderPolicyInput& input) {
    CaptureBorderPolicyDecision decision;
    if (!input.wgc_session) {
        decision.reason = CaptureBorderPolicyReason::NotWgc;
        return decision;
    }

    decision.api_build_supported =
        input.platform.os_build >= kBorderlessCaptureMinimumBuild;
    if (!decision.api_build_supported) {
        decision.reason = CaptureBorderPolicyReason::BuildUnsupported;
        return decision;
    }
    if (!input.platform.package_identity) {
        decision.reason = CaptureBorderPolicyReason::Unpackaged;
        return decision;
    }
    if (!input.platform.borderless_capability_declared) {
        decision.reason = CaptureBorderPolicyReason::CapabilityNotDeclared;
        return decision;
    }

    if (input.access_status == CaptureBorderAccessStatus::NotRequested) {
        decision.request_borderless_access = !input.access_request_attempted;
        decision.reason = CaptureBorderPolicyReason::AccessRequestRequired;
        return decision;
    }
    if (input.access_status != CaptureBorderAccessStatus::Allowed) {
        decision.reason = CaptureBorderPolicyReason::AccessDenied;
        return decision;
    }

    if (!input.session_interface_checked) {
        decision.attempt_disable_border = true;
        decision.reason = CaptureBorderPolicyReason::BorderlessEnabled;
        return decision;
    }
    if (!input.session_interface_available) {
        decision.reason = CaptureBorderPolicyReason::SessionInterfaceUnavailable;
        return decision;
    }
    if (!input.property_attempted || !input.property_set_succeeded) {
        decision.reason = CaptureBorderPolicyReason::SessionPropertyFailed;
        return decision;
    }
    if (input.border_required_after_attempt) {
        decision.reason = CaptureBorderPolicyReason::BorderStillRequired;
        return decision;
    }

    decision.effective_borderless = true;
    decision.reason = CaptureBorderPolicyReason::BorderlessEnabled;
    return decision;
}

const char* CaptureBorderAccessStatusName(CaptureBorderAccessStatus status) {
    switch (status) {
    case CaptureBorderAccessStatus::NotRequested: return "not_requested";
    case CaptureBorderAccessStatus::Allowed: return "allowed";
    case CaptureBorderAccessStatus::DeniedBySystem: return "denied_by_system";
    case CaptureBorderAccessStatus::NotDeclaredByApp: return "not_declared_by_app";
    case CaptureBorderAccessStatus::DeniedByUser: return "denied_by_user";
    case CaptureBorderAccessStatus::UserPromptRequired: return "user_prompt_required";
    case CaptureBorderAccessStatus::RequestFailed: return "request_failed";
    }
    return "unknown";
}

const char* CaptureBorderPolicyReasonName(CaptureBorderPolicyReason reason) {
    switch (reason) {
    case CaptureBorderPolicyReason::NotWgc: return "not_wgc";
    case CaptureBorderPolicyReason::BuildUnsupported: return "build_unsupported";
    case CaptureBorderPolicyReason::Unpackaged: return "unpackaged";
    case CaptureBorderPolicyReason::CapabilityNotDeclared: return "capability_not_declared";
    case CaptureBorderPolicyReason::AccessRequestRequired: return "access_request_required";
    case CaptureBorderPolicyReason::AccessDenied: return "access_denied";
    case CaptureBorderPolicyReason::SessionInterfaceUnavailable: return "session_interface_unavailable";
    case CaptureBorderPolicyReason::SessionPropertyFailed: return "session_property_failed";
    case CaptureBorderPolicyReason::BorderStillRequired: return "border_still_required";
    case CaptureBorderPolicyReason::BorderlessEnabled: return "borderless_enabled";
    }
    return "unknown";
}

}  // namespace fthr

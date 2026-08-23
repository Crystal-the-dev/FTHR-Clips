#include "windows_capture_border_policy.h"

#include <cstdlib>
#include <iostream>

namespace {

int border_policy_checks = 0;

void CheckCaptureBorder(bool condition, const char* message) {
    ++border_policy_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

fthr::CaptureBorderPolicyInput SupportedPackageInput() {
    fthr::CaptureBorderPolicyInput input;
    input.platform.os_build = 22631;
    input.platform.package_identity = true;
    input.platform.borderless_capability_declared = true;
    return input;
}

void UnsupportedBuildPreservesTheWindowsIndicator() {
    auto input = SupportedPackageInput();
    input.platform.os_build = 19045;
    const auto decision = fthr::EvaluateCaptureBorderPolicy(input);
    CheckCaptureBorder(!decision.api_build_supported
            && !decision.request_borderless_access
            && !decision.attempt_disable_border
            && !decision.effective_borderless
            && decision.reason == fthr::CaptureBorderPolicyReason::BuildUnsupported,
        "Windows 10 19045 does not claim borderless WGC support");
    CheckCaptureBorder(fthr::kBorderlessCaptureMinimumBuild == 20348,
        "the documented borderless API build remains explicit");
}

void AccessRequiresIdentityAndManifestCapability() {
    auto unpackaged = SupportedPackageInput();
    unpackaged.platform.package_identity = false;
    auto decision = fthr::EvaluateCaptureBorderPolicy(unpackaged);
    CheckCaptureBorder(decision.reason == fthr::CaptureBorderPolicyReason::Unpackaged
            && !decision.request_borderless_access,
        "an unpackaged executable never requests borderless access");

    auto missing_capability = SupportedPackageInput();
    missing_capability.platform.borderless_capability_declared = false;
    decision = fthr::EvaluateCaptureBorderPolicy(missing_capability);
    CheckCaptureBorder(decision.reason == fthr::CaptureBorderPolicyReason::CapabilityNotDeclared
            && !decision.request_borderless_access,
        "a package without the manifest capability never requests borderless access");

    auto request_needed = SupportedPackageInput();
    decision = fthr::EvaluateCaptureBorderPolicy(request_needed);
    CheckCaptureBorder(decision.api_build_supported
            && decision.request_borderless_access
            && !decision.attempt_disable_border
            && !decision.effective_borderless
            && decision.reason == fthr::CaptureBorderPolicyReason::AccessRequestRequired,
        "a supported capable package requests only Borderless access first");
}

void DenialNeverBreaksCaptureOrClaimsSuccess() {
    for (const auto status : {
             fthr::CaptureBorderAccessStatus::DeniedBySystem,
             fthr::CaptureBorderAccessStatus::NotDeclaredByApp,
             fthr::CaptureBorderAccessStatus::DeniedByUser,
             fthr::CaptureBorderAccessStatus::UserPromptRequired,
             fthr::CaptureBorderAccessStatus::RequestFailed}) {
        auto input = SupportedPackageInput();
        input.access_request_attempted = true;
        input.access_status = status;
        const auto decision = fthr::EvaluateCaptureBorderPolicy(input);
        CheckCaptureBorder(!decision.request_borderless_access
                && !decision.attempt_disable_border
                && !decision.effective_borderless
                && decision.reason == fthr::CaptureBorderPolicyReason::AccessDenied,
            "a denied or failed access request preserves normal WGC capture");
    }
}

void SessionApiAndPropertyResultsRemainTruthful() {
    auto unavailable = SupportedPackageInput();
    unavailable.access_request_attempted = true;
    unavailable.access_status = fthr::CaptureBorderAccessStatus::Allowed;
    unavailable.session_interface_checked = true;
    const auto unavailable_decision = fthr::EvaluateCaptureBorderPolicy(unavailable);
    CheckCaptureBorder(!unavailable_decision.effective_borderless
            && unavailable_decision.reason
                == fthr::CaptureBorderPolicyReason::SessionInterfaceUnavailable,
        "a missing runtime session interface is not reported as borderless");

    auto failed = unavailable;
    failed.session_interface_available = true;
    failed.property_attempted = true;
    failed.property_set_succeeded = false;
    const auto failed_decision = fthr::EvaluateCaptureBorderPolicy(failed);
    CheckCaptureBorder(!failed_decision.effective_borderless
            && failed_decision.reason == fthr::CaptureBorderPolicyReason::SessionPropertyFailed,
        "a failed border property write is not reported as borderless");

    auto still_required = failed;
    still_required.property_set_succeeded = true;
    const auto still_required_decision = fthr::EvaluateCaptureBorderPolicy(still_required);
    CheckCaptureBorder(!still_required_decision.effective_borderless
            && still_required_decision.reason == fthr::CaptureBorderPolicyReason::BorderStillRequired,
        "a session that still requires the border is not reported as disabled");

    auto enabled = still_required;
    enabled.border_required_after_attempt = false;
    const auto enabled_decision = fthr::EvaluateCaptureBorderPolicy(enabled);
    CheckCaptureBorder(enabled_decision.effective_borderless
            && enabled_decision.reason == fthr::CaptureBorderPolicyReason::BorderlessEnabled,
        "an allowed session with a false readback is the only borderless success state");
}

void SessionRecreationAndNonWgcPathsStaySafe() {
    auto recovery = SupportedPackageInput();
    recovery.access_request_attempted = true;
    recovery.access_status = fthr::CaptureBorderAccessStatus::Allowed;
    const auto recovery_decision = fthr::EvaluateCaptureBorderPolicy(recovery);
    CheckCaptureBorder(!recovery_decision.request_borderless_access
            && recovery_decision.attempt_disable_border,
        "a recreated WGC session reuses granted access without a second prompt");

    const auto monitor_switch_decision = fthr::EvaluateCaptureBorderPolicy(recovery);
    CheckCaptureBorder(monitor_switch_decision.attempt_disable_border
            && !monitor_switch_decision.request_borderless_access,
        "a monitor switch applies the already-granted policy to its new WGC session");

    auto ui_start = SupportedPackageInput();
    auto background_start = SupportedPackageInput();
    const auto ui_decision = fthr::EvaluateCaptureBorderPolicy(ui_start);
    const auto background_decision = fthr::EvaluateCaptureBorderPolicy(background_start);
    CheckCaptureBorder(ui_decision.request_borderless_access
            && background_decision.request_borderless_access
            && ui_decision.reason == background_decision.reason,
        "UI and background starts use the same borderless-access policy");

    auto dxgi = recovery;
    dxgi.wgc_session = false;
    const auto dxgi_decision = fthr::EvaluateCaptureBorderPolicy(dxgi);
    CheckCaptureBorder(!dxgi_decision.api_build_supported
            && !dxgi_decision.request_borderless_access
            && !dxgi_decision.attempt_disable_border
            && dxgi_decision.reason == fthr::CaptureBorderPolicyReason::NotWgc,
        "DXGI never participates in the WGC border policy");
}

}  // namespace

int RunWindowsCaptureBorderPolicyTests() {
    UnsupportedBuildPreservesTheWindowsIndicator();
    AccessRequiresIdentityAndManifestCapability();
    DenialNeverBreaksCaptureOrClaimsSuccess();
    SessionApiAndPropertyResultsRemainTruthful();
    SessionRecreationAndNonWgcPathsStaySafe();
    std::cout << "Windows capture border policy tests: " << border_policy_checks
              << " checks passed" << std::endl;
    return border_policy_checks;
}

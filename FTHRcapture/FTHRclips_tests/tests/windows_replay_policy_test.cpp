#include "replay_encoder.h"
#include "encoded_ring_buffer.h"
#include "frame_rate_scheduler.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

using fthr::EncoderVendor;
using fthr::EncoderPreference;
using fthr::ReplayEncoderBackend;
using fthr::ReplayStartupError;
using fthr::VideoCodec;

int policy_checks = 0;

void CheckPolicy(bool condition, const char* message) {
    ++policy_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

void SameAdapterVendorMatrixIsAuthoritative() {
    const auto nvidia = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Nvidia, VideoCodec::H264);
    const auto amd = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Amd, VideoCodec::HEVC);
    const auto intel = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, VideoCodec::AV1);
    CheckPolicy(nvidia.allowed
            && nvidia.backend == ReplayEncoderBackend::NativeNvenc,
        "selected NVIDIA monitor uses same-adapter NVENC");
    CheckPolicy(amd.allowed
            && amd.backend == ReplayEncoderBackend::FfmpegAmf,
        "selected AMD monitor uses same-adapter AMF");
    CheckPolicy(intel.allowed
            && intel.backend == ReplayEncoderBackend::FfmpegQsv,
        "selected Intel monitor uses same-adapter QSV");
    CheckPolicy(nvidia.same_adapter && amd.same_adapter && intel.same_adapter,
        "all alpha production selections are same-adapter");
}

void MissingRuntimeAndUnsupportedCodecStayFailed() {
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "NVENC runtime unavailable")
                == ReplayStartupError::HardwareEncoderUnavailable,
        "missing NVIDIA runtime is distinct");
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "AMF runtime unavailable: amfrt64.dll not found")
                == ReplayStartupError::HardwareEncoderUnavailable,
        "missing AMD runtime is distinct");
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "oneVPL runtime unavailable")
                == ReplayStartupError::HardwareEncoderUnavailable,
        "missing Intel runtime is distinct");
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "av1_qsv unsupported by this hardware")
                == ReplayStartupError::RequestedCodecUnsupported,
        "unsupported requested codec is distinct");
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Nvidia, static_cast<VideoCodec>(99)).error
                == ReplayStartupError::RequestedCodecUnsupported,
        "unknown codec cannot silently become H.264");
}

void CaptureAdapterCannotBeStolenByAnotherVendor() {
    const auto intel = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, VideoCodec::H264);
    CheckPolicy(intel.encoder_vendor == EncoderVendor::Intel
            && intel.backend == ReplayEncoderBackend::FfmpegQsv,
        "unrelated NVIDIA presence cannot steal Intel monitor selection");
    CheckPolicy(!fthr::IsAutomaticCrossAdapterReplayAllowed(),
        "cross-adapter path is disabled until explicitly qualified");
    const auto unknown = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Software, VideoCodec::H264);
    CheckPolicy(!unknown.allowed
            && unknown.error == ReplayStartupError::CaptureAdapterUnsupported,
        "unknown capture adapter refuses startup instead of enumerating vendors");
    const auto amd_on_nvidia = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Nvidia, EncoderPreference::Amd, VideoCodec::H264);
    CheckPolicy(!amd_on_nvidia.allowed
            && amd_on_nvidia.error
                == ReplayStartupError::CrossAdapterPathUnavailable,
        "requested AMF adapter cannot consume another adapter's monitor texture");
}

void ExplicitNvidiaSelectionEnablesQualifiedHybridPath() {
    const auto selected = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, EncoderPreference::Nvidia, VideoCodec::HEVC);
    CheckPolicy(selected.allowed && !selected.same_adapter,
        "explicit NVIDIA selection permits the hybrid CPU-input path");
    CheckPolicy(selected.encoder_vendor == EncoderVendor::Nvidia
            && selected.backend == ReplayEncoderBackend::NativeNvenc,
        "explicit NVIDIA selection resolves to native NVENC");

    const auto unsupported = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, EncoderPreference::Amd, VideoCodec::H264);
    CheckPolicy(!unsupported.allowed
            && unsupported.error == ReplayStartupError::CrossAdapterPathUnavailable,
        "unimplemented AMD cross-adapter selection stays rejected");
}

void RawCapacityNeverOverclaimsConfiguredHistory() {
    const auto capacity = fthr::CalculateRawReplayCapacity(
        1920, 1080, 4, 60, 60, 512);
    CheckPolicy(capacity.frame_capacity == 64,
        "512 MiB raw BGRA capacity uses exact whole-frame count");
    CheckPolicy(capacity.capacity_milliseconds == 1066,
        "raw capacity reports actual duration at effective FPS");
    CheckPolicy(!capacity.meets_requested_duration,
        "raw capacity cannot claim the configured 60 seconds");
    const auto invalid = fthr::CalculateRawReplayCapacity(
        0, 1080, 4, 60, 30, 512);
    CheckPolicy(invalid.frame_capacity == 0
            && !invalid.meets_requested_duration,
        "invalid raw geometry reports no replay capability");
    CheckPolicy(!fthr::IsAutomaticRawReplayFallbackAllowed(),
        "raw replay fallback is disabled for the public alpha");
}

int CountScheduledFrames(int host_fps, int target_fps, int seconds) {
    constexpr int64_t ticks_per_second = 90000000;
    fthr::FrameRateScheduler scheduler(ticks_per_second / target_fps);
    int accepted = 0;
    for (int frame = 0; frame < host_fps * seconds; ++frame) {
        const int64_t now = static_cast<int64_t>(frame)
            * ticks_per_second / host_fps;
        if (scheduler.ShouldCapture(now)) ++accepted;
    }
    return accepted;
}

void DeadlineSchedulerPreservesCadenceAcrossRefreshRates() {
    CheckPolicy(CountScheduledFrames(90, 60, 2) == 120,
        "90 Hz input preserves a 60 fps deadline cadence");
    CheckPolicy(CountScheduledFrames(144, 60, 2) == 120,
        "144 Hz input preserves a 60 fps deadline cadence");
    CheckPolicy(CountScheduledFrames(60, 120, 2) == 120,
        "target above host refresh accepts every available frame");

    fthr::FrameRateScheduler scheduler(10);
    CheckPolicy(scheduler.ShouldCapture(0) && !scheduler.ShouldCapture(9)
            && scheduler.ShouldCapture(10),
        "deadline scheduler accepts the first frame and exact deadlines");
    CheckPolicy(scheduler.ShouldCapture(1000) && !scheduler.ShouldCapture(1001),
        "a long stall advances missed deadlines without a catch-up burst");
}

void EncodedReplayCapacityHasBoundedHeadroom() {
    CheckPolicy(fthr::CalculateEncodedReplaySlotCapacity(30, 60) == 2100,
        "30-second replay keeps five seconds of bounded headroom");
    CheckPolicy(fthr::CalculateEncodedReplaySlotCapacity(300, 60) == 18300,
        "long replay does not reserve a second full history");
    CheckPolicy(fthr::CalculateEncodedReplaySlotCapacity(0, 60) == 0
            && fthr::CalculateEncodedReplaySlotCapacity(30, 0) == 0,
        "invalid replay timing cannot allocate a ring");
}

void ActiveTruthAndGenerationAreScoped() {
    const auto selected = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Amd, VideoCodec::AV1);
    const auto active = fthr::EvaluateActiveReplayCapability(
        selected,
        {EncoderVendor::Amd, ReplayEncoderBackend::FfmpegAmf,
         VideoCodec::AV1, true, "av1_amf"},
        true,
        7);
    CheckPolicy(active.initialized && active.requested_codec == VideoCodec::AV1
            && active.active_codec == VideoCodec::AV1,
        "active codec truth comes from initialized backend");
    CheckPolicy(active.active_backend == ReplayEncoderBackend::FfmpegAmf
            && active.hardware && active.same_adapter,
        "active backend and adapter truth remain explicit");
    CheckPolicy(active.generation == 7,
        "capability truth is scoped to one capture generation");

    const auto stale = fthr::EvaluateActiveReplayCapability(
        selected,
        {EncoderVendor::Amd, ReplayEncoderBackend::FfmpegAmf,
         VideoCodec::H264, true, "h264_amf"},
        true,
        8);
    CheckPolicy(!stale.initialized
            && stale.error == ReplayStartupError::RequestedCodecUnsupported,
        "a backend reporting the wrong codec is rejected");
    CheckPolicy(active.generation != stale.generation,
        "restart/topology generation invalidates old capability truth");
}

void ProductionThreeByThreeMatrixAndRegressionsHold() {
    for (const auto vendor : {
             EncoderVendor::Nvidia, EncoderVendor::Amd, EncoderVendor::Intel}) {
        for (const auto codec : {
                 VideoCodec::H264, VideoCodec::HEVC, VideoCodec::AV1}) {
            const auto selection = fthr::SelectWindowsReplayPolicy(vendor, codec);
            CheckPolicy(selection.allowed && selection.same_adapter,
                "3x3 source matrix selects a same-adapter hardware candidate");
        }
    }
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Nvidia, VideoCodec::AV1).backend
                == ReplayEncoderBackend::NativeNvenc,
        "NVIDIA policy regression remains protected");
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Amd, VideoCodec::AV1).backend
                == ReplayEncoderBackend::FfmpegAmf,
        "AMD policy regression remains protected");
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Intel, VideoCodec::AV1).backend
                == ReplayEncoderBackend::FfmpegQsv,
        "Intel policy regression remains protected");
}

} // namespace

int RunWindowsReplayPolicyTests() {
    SameAdapterVendorMatrixIsAuthoritative();
    MissingRuntimeAndUnsupportedCodecStayFailed();
    CaptureAdapterCannotBeStolenByAnotherVendor();
    ExplicitNvidiaSelectionEnablesQualifiedHybridPath();
    RawCapacityNeverOverclaimsConfiguredHistory();
    DeadlineSchedulerPreservesCadenceAcrossRefreshRates();
    EncodedReplayCapacityHasBoundedHeadroom();
    ActiveTruthAndGenerationAreScoped();
    ProductionThreeByThreeMatrixAndRegressionsHold();
    std::cout << "Windows replay policy tests: " << policy_checks
              << " checks passed" << std::endl;
    return policy_checks;
}

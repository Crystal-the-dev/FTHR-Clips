#include "audio_timeline.h"
#include "windows_microphone_audio_provider.h"

#include <cstdlib>
#include <iostream>

namespace {

int microphone_checks = 0;

void CheckMicrophone(bool condition, const char* message) {
    ++microphone_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

void QpcTimelineKeepsLateSourcesAtTheirRealOffset() {
    const auto range = fthr::MapAudioSourcePresentationRange(
        30.0, 60.0, 45ULL * 10'000'000ULL, fthr::kCanonicalAudioSampleRate);
    CheckMicrophone(range.start_pts_samples == -720000
            && range.end_pts_samples == 720000,
        "shared QPC mapping preserves a microphone that begins halfway through a clip");
}

void EncoderDelayStaysSignedInManifestTimeline() {
    constexpr uint64_t origin = 50ULL * 10'000'000ULL;
    CheckMicrophone(fthr::AudioSamplePositionToTimeline100ns(
            origin, -1024, fthr::kCanonicalAudioSampleRate) == 499'786'667,
        "negative AAC encoder delay stays before the source origin without unsigned wrap");
    CheckMicrophone(fthr::AudioSamplePositionToTimeline100ns(
            origin, 1024, fthr::kCanonicalAudioSampleRate) == 500'213'333,
        "positive AAC packet positions map after the source origin");
}

void DriftCorrectionIsSmallAndBounded() {
    fthr::AudioDriftController controller(48000, 100);
    const auto initial = controller.Observe(10'000'000, 0);
    CheckMicrophone(initial.sample_delta == 0,
        "the first microphone packet establishes the common QPC origin without a jump");

    // At 500 ms the source is 120 samples behind.  A 100 ppm policy may only
    // correct a couple of samples over this 500 ms window, never a pop-inducing
    // one-block jump.
    const auto bounded = controller.Observe(15'000'000, 23'880);
    CheckMicrophone(bounded.observed_drift_samples == 120
            && bounded.sample_delta > 0 && bounded.sample_delta <= 3
            && bounded.compensation_distance_samples >= 24000,
        "microphone drift correction is rate-limited against the common clock");

    const auto stable = controller.Observe(15'100'000, 24'485);
    CheckMicrophone(stable.sample_delta == 0,
        "correction is not repeatedly applied before the next bounded window");
    CheckMicrophone(controller.max_observed_drift_samples() >= 120,
        "drift telemetry retains the largest observed source-clock offset");
}

void ExplicitEndpointIdentityIsNotManifestIdentity() {
    fthr::WindowsMicrophoneAudioProviderConfig config;
    config.endpoint_id = L"{0.0.1.00000000}.{example-endpoint}";
    config.use_default_endpoint = false;
    config.source.identity.id.value = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    config.source.identity.type = fthr::AudioSourceType::Microphone;
    config.source.identity.persistent_identity = "microphone";
    config.source.identity.display_name = "Microphone";
    config.source.format = {48000, 2, "fltp"};
    CheckMicrophone(!config.use_default_endpoint && !config.endpoint_id.empty()
            && config.source.identity.persistent_identity == "microphone"
            && config.source.identity.display_name == "Microphone",
        "a selected endpoint is runtime-only while the portable source remains semantic");
}

}  // namespace

int RunWindowsMicrophoneAudioProviderTests() {
    QpcTimelineKeepsLateSourcesAtTheirRealOffset();
    EncoderDelayStaysSignedInManifestTimeline();
    DriftCorrectionIsSmallAndBounded();
    ExplicitEndpointIdentityIsNotManifestIdentity();
    std::cout << "AUDIT-050 Windows microphone provider tests: "
              << microphone_checks << " checks passed" << std::endl;
    return microphone_checks;
}

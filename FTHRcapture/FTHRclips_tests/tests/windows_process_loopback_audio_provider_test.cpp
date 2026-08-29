#include "windows_process_loopback_audio_provider.h"

#include <windows.h>

#include <cstdlib>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

int process_loopback_checks = 0;

void CheckProcessLoopback(bool condition, const char* message) {
    ++process_loopback_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

fthr::WindowsAudioSessionDescriptor Session(
    const char* group_key, uint32_t pid, const char* identity, const char* display) {
    fthr::WindowsAudioSessionDescriptor descriptor;
    descriptor.process_id = pid;
    descriptor.process_group_root_id = pid;
    descriptor.runtime_group_key = group_key;
    descriptor.identity.type = fthr::AudioSourceType::Application;
    descriptor.identity.persistent_identity = identity;
    descriptor.identity.display_name = display;
    descriptor.identity.icon_reference = "windows-app-icon";
    return descriptor;
}

fthr::WindowsApplicationSourceCoordinator::SourceIdGenerator Generator(
    std::vector<std::string> ids) {
    return [ids = std::move(ids), next = size_t{0}]() mutable
        -> std::optional<fthr::AudioSourceId> {
        if (next >= ids.size()) return std::nullopt;
        return fthr::AudioSourceId{ids[next++]};
    };
}

void CapabilityGateDoesNotAttemptWindows10Sources() {
    fthr::WindowsProcessLoopbackCapability unsupported;
    unsupported.os_build = 19045;
    unsupported.api_build_supported = false;
    fthr::WindowsApplicationSourceCoordinator coordinator(
        1, unsupported, Generator({"10000000-0000-4000-8000-000000000001"}));
    CheckProcessLoopback(!coordinator.available(),
        "Windows 10 capability leaves the per-app source coordinator unavailable");
    CheckProcessLoopback(!coordinator.Discover(Session("discord#77", 77, "discord", "Discord")),
        "unsupported capability creates no provider candidate or fabricated source");
}

void DiscoveryActivityAndSilentFilteringUseTheCommonModel() {
    fthr::WindowsProcessLoopbackCapability supported;
    supported.os_build = 22631;
    supported.api_build_supported = true;
    fthr::WindowsApplicationSourceCoordinator coordinator(
        2, supported, Generator({
            "20000000-0000-4000-8000-000000000001",
            "20000000-0000-4000-8000-000000000002",
            "20000000-0000-4000-8000-000000000003"}));
    const auto discord = coordinator.Discover(Session("discord#77", 77, "discord", "Discord"));
    const auto firefox = coordinator.Discover(Session("firefox#88", 88, "firefox", "Firefox"));
    CheckProcessLoopback(discord && firefox && coordinator.provider_candidate_count() == 2,
        "eligible discovered sessions create one deterministic provider candidate each");
    CheckProcessLoopback(discord->source.format.sample_rate == 48000
            && discord->source.format.channels == 2
            && discord->source.identity.type == fthr::AudioSourceType::Application,
        "provider sources declare the canonical processing format and application type");
    CheckProcessLoopback(coordinator.ObserveActivity(discord->source.identity.id, 0.020f, 100)
            == fthr::AudioSourceAdmission::RejectedAtCapacity,
        "first meaningful block is held by activity hysteresis");
    CheckProcessLoopback(coordinator.ObserveActivity(discord->source.identity.id, 0.020f, 200)
            == fthr::AudioSourceAdmission::Accepted,
        "second meaningful block admits the real application source");
    const auto audible = coordinator.SourcesForInterval(0, 300);
    CheckProcessLoopback(audible.size() == 1
            && audible.front().identity.display_name == "Discord",
        "silent discovered Firefox does not publish a final stem");
    CheckProcessLoopback(audible.front().identity.id == discord->source.identity.id
            && audible.front().identity.persistent_identity == "discord",
        "manifest-facing source identity is real, stable for the runtime group, and not fabricated");
    auto child = Session("discord#7", 17, "discord", "Discord");
    child.process_group_root_id = 7;
    const auto grouped = coordinator.Discover(child);
    CheckProcessLoopback(grouped && grouped->process_id == 7,
        "process-loopback targets the deterministic group root so sibling helpers are not missed");
    const auto late = fthr::MapAudioSourcePresentationRange(0.0, 30.0,
        15ULL * 10'000'000ULL, 48000);
    CheckProcessLoopback(late.start_pts_samples == -720000
            && late.end_pts_samples == 720000,
        "a source beginning at 15 seconds retains its leading timeline gap instead of moving to t=0");
}

void ExitRestartFailureAndGenerationHistoryStayIsolated() {
    fthr::WindowsProcessLoopbackCapability supported{22631, true, false};
    fthr::WindowsApplicationSourceCoordinator coordinator(
        3, supported, Generator({
            "30000000-0000-4000-8000-000000000001",
            "30000000-0000-4000-8000-000000000002"}));
    const auto first = coordinator.Discover(Session("spotify#99", 99, "spotify", "Spotify"));
    CheckProcessLoopback(first.has_value(), "Spotify source is discoverable on supported Windows");
    coordinator.ObserveActivity(first->source.identity.id, 0.020f, 1000);
    coordinator.ObserveActivity(first->source.identity.id, 0.020f, 1100);
    const auto retired = coordinator.RetireRuntimeGroup("spotify#99", 1200);
    CheckProcessLoopback(retired && *retired == first->source.identity.id
            && coordinator.SourcesForInterval(900, 1250).size() == 1,
        "source exit retires only its runtime group while retaining replay history");
    const auto restart = coordinator.Discover(Session("spotify#99", 199, "spotify", "Spotify"));
    CheckProcessLoopback(restart && !(restart->source.identity.id == first->source.identity.id),
        "a restarted root-PID group receives a fresh source instead of unsafe name-only merging");
    coordinator.MarkFailed(restart->source.identity.id);
    CheckProcessLoopback(coordinator.SourcesForInterval(900, 1250).size() == 1,
        "one failed future source does not erase another source's retained history");

    fthr::WindowsApplicationSourceCoordinator next_generation(
        4, supported, Generator({"40000000-0000-4000-8000-000000000001"}));
    CheckProcessLoopback(next_generation.SourcesForInterval(900, 1250).empty(),
        "capture generations never inherit prior application identities or history");
}

void SourceLimitAndTrackContractRemainBounded() {
    fthr::WindowsProcessLoopbackCapability supported{22631, true, false};
    fthr::WindowsApplicationSourceCoordinator coordinator(
        5, supported, Generator({
            "50000000-0000-4000-8000-000000000001",
            "50000000-0000-4000-8000-000000000002",
            "50000000-0000-4000-8000-000000000003"}), 2);
    const auto one = coordinator.Discover(Session("a#1", 1, "a", "A"));
    const auto two = coordinator.Discover(Session("b#2", 2, "b", "B"));
    const auto three = coordinator.Discover(Session("c#3", 3, "c", "C"));
    CheckProcessLoopback(one && two && three, "silent candidates do not consume the active-stem limit");
    coordinator.ObserveActivity(one->source.identity.id, 0.02f, 100);
    coordinator.ObserveActivity(one->source.identity.id, 0.02f, 200);
    coordinator.ObserveActivity(two->source.identity.id, 0.02f, 300);
    coordinator.ObserveActivity(two->source.identity.id, 0.02f, 400);
    coordinator.ObserveActivity(three->source.identity.id, 0.02f, 500);
    CheckProcessLoopback(coordinator.ObserveActivity(three->source.identity.id, 0.02f, 600)
            == fthr::AudioSourceAdmission::RejectedAtCapacity,
        "the source admission limit rejects a ninth-equivalent stem deterministically");
    const auto sources = coordinator.SourcesForInterval(0, 700);
    CheckProcessLoopback(sources.size() == 2
            && sources[0].identity.id.IsValid() && sources[1].identity.id.IsValid(),
        "only admitted sources can become valid multi-stream manifest contracts");
}

void ExpiredReplayHistoryReleasesAnApplicationSlot() {
    fthr::WindowsProcessLoopbackCapability supported{22631, true, false};
    fthr::WindowsApplicationSourceCoordinator coordinator(
        51, supported, Generator({
            "51000000-0000-4000-8000-000000000001",
            "51000000-0000-4000-8000-000000000002",
            "51000000-0000-4000-8000-000000000003"}), 2, 1);
    const auto one = coordinator.Discover(Session("one#1", 1, "one", "One"));
    const auto two = coordinator.Discover(Session("two#2", 2, "two", "Two"));
    const auto three = coordinator.Discover(Session("three#3", 3, "three", "Three"));
    coordinator.ObserveActivity(one->source.identity.id, 0.02f, 100);
    coordinator.ObserveActivity(one->source.identity.id, 0.02f, 200);
    coordinator.RetireRuntimeGroup("one#1", 300);
    coordinator.ObserveActivity(two->source.identity.id, 0.02f, 400);
    coordinator.ObserveActivity(two->source.identity.id, 0.02f, 500);

    coordinator.ObserveActivity(three->source.identity.id, 0.02f, 20'000'000);
    CheckProcessLoopback(
        coordinator.ObserveActivity(
            three->source.identity.id, 0.02f, 20'000'100)
            == fthr::AudioSourceAdmission::Accepted,
        "an inactive source older than the replay window releases its stem slot");
    const auto current = coordinator.SourcesForInterval(0, 21'000'000);
    CheckProcessLoopback(current.size() == 2
            && current[0].identity.display_name == "Two"
            && current[1].identity.display_name == "Three",
        "slot recycling retains current sources without reviving expired history");
}

void EightApplicationStemBoundaryIsExplicit() {
    fthr::WindowsProcessLoopbackCapability supported{22631, true, false};
    fthr::WindowsApplicationSourceCoordinator coordinator(
        6, supported, Generator({
            "60000000-0000-4000-8000-000000000001",
            "60000000-0000-4000-8000-000000000002",
            "60000000-0000-4000-8000-000000000003",
            "60000000-0000-4000-8000-000000000004",
            "60000000-0000-4000-8000-000000000005",
            "60000000-0000-4000-8000-000000000006",
            "60000000-0000-4000-8000-000000000007",
            "60000000-0000-4000-8000-000000000008",
            "60000000-0000-4000-8000-000000000009"}));
    std::vector<fthr::WindowsApplicationSourceBinding> bindings;
    for (uint32_t index = 0; index < 9; ++index) {
        const std::string number = std::to_string(index + 1);
        const auto binding = coordinator.Discover(Session(
            ("app#" + number).c_str(), index + 1,
            ("app" + number).c_str(), ("App " + number).c_str()));
        CheckProcessLoopback(binding.has_value(), "all nine candidate sessions are discoverable");
        bindings.push_back(*binding);
        coordinator.ObserveActivity(binding->source.identity.id, 0.02f,
            static_cast<int64_t>(index) * 1000);
    }
    for (uint32_t index = 0; index < 8; ++index) {
        CheckProcessLoopback(coordinator.ObserveActivity(bindings[index].source.identity.id,
                0.02f, static_cast<int64_t>(index) * 1000 + 100)
                == fthr::AudioSourceAdmission::Accepted,
            "each of the first eight real app stems is admitted");
    }
    CheckProcessLoopback(coordinator.ObserveActivity(bindings[8].source.identity.id,
            0.02f, 8100) == fthr::AudioSourceAdmission::RejectedAtCapacity,
        "the ninth real app stem is rejected without displacing retained history");
    CheckProcessLoopback(coordinator.SourcesForInterval(0, 9000).size() == 8,
        "the production app-stem boundary is exactly eight before Default Mix is added");
}

void OptionalLiveApplicationCaptureDiagnostics() {
    wchar_t enabled[2] = {};
    if (GetEnvironmentVariableW(
            L"FTHR_TEST_LIVE_AUDIO_CAPTURE", enabled, 2) == 0) return;
    fthr::WindowsApplicationAudioSourceManager manager(77, 10);
    if (!manager.Start()) {
        std::cout << "LIVE APPLICATION CAPTURE: unavailable: "
                  << manager.last_error() << std::endl;
        return;
    }
    Sleep(4000);
    LARGE_INTEGER counter{};
    LARGE_INTEGER frequency{};
    QueryPerformanceCounter(&counter);
    QueryPerformanceFrequency(&frequency);
    const double end = static_cast<double>(counter.QuadPart)
        / static_cast<double>(frequency.QuadPart);
    const auto tracks = manager.TakeTracksForInterval(end - 3.5, end);
    std::cout << "LIVE APPLICATION CAPTURE: " << tracks.size()
              << " audible stem(s)" << std::endl;
    for (const auto& track : tracks) {
        std::cout << "  CAPTURED " << track.source.identity.display_name
                  << " (" << track.snapshot.packets.size() << " AAC packets)"
                  << std::endl;
    }
    manager.Stop();
}

}  // namespace

int RunWindowsProcessLoopbackAudioProviderTests() {
    CapabilityGateDoesNotAttemptWindows10Sources();
    DiscoveryActivityAndSilentFilteringUseTheCommonModel();
    ExitRestartFailureAndGenerationHistoryStayIsolated();
    SourceLimitAndTrackContractRemainBounded();
    ExpiredReplayHistoryReleasesAnApplicationSlot();
    EightApplicationStemBoundaryIsExplicit();
    OptionalLiveApplicationCaptureDiagnostics();
    std::cout << "AUDIT-050 Windows process-loopback provider tests: "
              << process_loopback_checks << " checks passed" << std::endl;
    return process_loopback_checks;
}

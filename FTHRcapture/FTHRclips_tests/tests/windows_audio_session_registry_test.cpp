#include "windows_audio_session_registry.h"

#include <windows.h>

#include <cstdlib>
#include <iostream>

namespace {

int windows_audio_checks = 0;

void CheckWindowsAudio(bool condition, const char* message) {
    ++windows_audio_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

void BuildCapabilityIsNotMarketingName() {
    CheckWindowsAudio(!fthr::IsWindowsProcessLoopbackBuildSupported(19045),
        "Windows 10 19045 does not claim official process-loopback support");
    CheckWindowsAudio(fthr::IsWindowsProcessLoopbackBuildSupported(20348),
        "minimum documented process-loopback build is admitted by capability");
    CheckWindowsAudio(fthr::IsWindowsProcessLoopbackBuildSupported(22631),
        "supported Windows 11 build is admitted by capability");
}

void NamesAndRuntimeGroupingAreLocalAndDeterministic() {
    CheckWindowsAudio(
        fthr::NormalizeWindowsExecutableIdentity(L"Discord.EXE") == "discord",
        "executable identity is normalized without a path");
    CheckWindowsAudio(
        fthr::ResolveWindowsAudioSourceName(L"VALORANT-Win64-Shipping.exe") == "VALORANT",
        "known local game executable gets a readable label");
    CheckWindowsAudio(
        fthr::ResolveWindowsAudioSourceName(L"firefox.exe") == "Firefox",
        "known local browser executable gets a readable label");
    CheckWindowsAudio(
        fthr::ResolveWindowsAudioSourceName(L"chrome.exe") == "Google Chrome",
        "Chromium browser executable uses the product name users recognize");
    CheckWindowsAudio(
        fthr::ResolveWindowsAudioSourceName(L"chrome.exe", L"Helium") == "Helium",
        "branded Chromium browser uses its signed product description");
    CheckWindowsAudio(
        fthr::ResolveWindowsAudioSourceName(
            L"FPSAimTrainer-Win64-Shipping.exe") == "KovaaK's",
        "KovaaK executable gets the game name users recognize");
    CheckWindowsAudio(
        fthr::ResolveWindowsAudioSourceName(
            L"unknown-player.exe", L"Example Audio Player") == "Example Audio Player",
        "unknown executables use their local version-resource description");
    CheckWindowsAudio(
        fthr::BuildWindowsRuntimeGroupKey(4321, L"Discord.exe") == "discord#4321",
        "grouping is process-tree-root scoped rather than name-only");
    CheckWindowsAudio(
        fthr::BuildWindowsRuntimeGroupKey(0, L"Discord.exe").empty(),
        "zero process root cannot become an application group");
}

void OptionalLiveSessionDiagnostics() {
    wchar_t enabled[2] = {};
    if (GetEnvironmentVariableW(
            L"FTHR_TEST_LIVE_AUDIO_SESSIONS", enabled, 2) == 0) return;
    const HRESULT apartment = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    fthr::WindowsAudioSessionRegistry registry;
    if (!registry.Start()) {
        std::cout << "LIVE AUDIO SESSIONS: unavailable: " << registry.last_error()
                  << std::endl;
    } else {
        fthr::WindowsAudioSessionUpdate update;
        registry.RequestRefresh();
        registry.RefreshIfNeeded(&update);
        std::cout << "LIVE AUDIO SESSIONS: " << update.current.size()
                  << " application group(s)" << std::endl;
        for (const auto& source : update.current) {
            std::cout << "  " << (source.currently_active ? "ACTIVE " : "idle   ")
                      << source.identity.display_name << " ["
                      << source.identity.persistent_identity << "]" << std::endl;
        }
        registry.Stop();
    }
    if (SUCCEEDED(apartment)) CoUninitialize();
}

}  // namespace

int RunWindowsAudioSessionRegistryTests() {
    BuildCapabilityIsNotMarketingName();
    NamesAndRuntimeGroupingAreLocalAndDeterministic();
    OptionalLiveSessionDiagnostics();
    std::cout << "AUDIT-050 Windows session registry tests: " << windows_audio_checks
              << " checks passed" << std::endl;
    return windows_audio_checks;
}

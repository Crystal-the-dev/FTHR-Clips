#include "windows_audio_session_registry.h"

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
        fthr::BuildWindowsRuntimeGroupKey(4321, L"Discord.exe") == "discord#4321",
        "grouping is process-tree-root scoped rather than name-only");
    CheckWindowsAudio(
        fthr::BuildWindowsRuntimeGroupKey(0, L"Discord.exe").empty(),
        "zero process root cannot become an application group");
}

}  // namespace

int RunWindowsAudioSessionRegistryTests() {
    BuildCapabilityIsNotMarketingName();
    NamesAndRuntimeGroupingAreLocalAndDeterministic();
    std::cout << "AUDIT-050 Windows session registry tests: " << windows_audio_checks
              << " checks passed" << std::endl;
    return windows_audio_checks;
}

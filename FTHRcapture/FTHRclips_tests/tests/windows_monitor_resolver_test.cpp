#include "windows_monitor_resolver.h"

#include <cstdlib>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

using fthr::monitor::AdapterLuid;
using fthr::monitor::DxgiOutputIdentity;
using fthr::monitor::MonitorResolveError;
using fthr::monitor::MonitorResolver;
using fthr::monitor::MonitorTopologyEntry;

int checks = 0;

void Check(bool condition, const char* message) {
    ++checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

MonitorTopologyEntry Monitor(
    const wchar_t* path,
    AdapterLuid luid,
    uint32_t source_id,
    uint32_t target_id,
    uintptr_t hmonitor,
    const wchar_t* gdi_name,
    bool primary = false) {
    MonitorTopologyEntry entry;
    entry.monitor_device_path = path;
    entry.adapter_luid = luid;
    entry.source_id = source_id;
    entry.target_id = target_id;
    entry.hmonitor = hmonitor;
    entry.source_gdi_name = gdi_name;
    entry.primary = primary;
    entry.active = true;
    return entry;
}

class FakeTopologySource final : public fthr::monitor::IMonitorTopologySource {
public:
    explicit FakeTopologySource(std::vector<MonitorTopologyEntry> entries)
        : entries_(std::move(entries)) {}

    fthr::monitor::TopologyQueryResult QueryActiveTopology() override {
        return {MonitorResolveError::None, entries_, {}};
    }

    void Set(std::vector<MonitorTopologyEntry> entries) {
        entries_ = std::move(entries);
    }

private:
    std::vector<MonitorTopologyEntry> entries_;
};

void OneMonitorOneAdapter() {
    FakeTopologySource source({Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true)});
    auto result = MonitorResolver(source).Resolve(L"path-a");
    Check(result.ok(), "one monitor resolves");
    Check(result.monitor.adapter_luid == AdapterLuid{1, 0}, "one adapter identity preserved");
}

void TwoMonitorsSameAdapter() {
    FakeTopologySource source({
        Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true),
        Monitor(L"PATH-B", {1, 0}, 1, 11, 200, L"DISPLAY2")});
    auto result = MonitorResolver(source).Resolve(L"path-b");
    Check(result.ok() && result.monitor.target_id == 11, "same-adapter target identity selected");
}

void SecondaryMonitorSelected() {
    FakeTopologySource source({
        Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true),
        Monitor(L"PATH-B", {1, 0}, 1, 11, 200, L"DISPLAY2")});
    auto result = MonitorResolver(source).Resolve(L"PATH-B");
    Check(result.ok() && result.monitor.hmonitor == 200, "secondary HMONITOR selected");
}

void TwoAdaptersOneMonitorEach() {
    FakeTopologySource source({
        Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true),
        Monitor(L"PATH-B", {2, -1}, 0, 20, 200, L"DISPLAY2")});
    auto result = MonitorResolver(source).Resolve(L"PATH-B");
    Check(result.ok() && result.monitor.adapter_luid == AdapterLuid{2, -1},
          "selected monitor owns second adapter");
}

void EnumerationOrderChanges() {
    const auto a = Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true);
    const auto b = Monitor(L"PATH-B", {2, 0}, 0, 20, 200, L"DISPLAY2");
    FakeTopologySource source({a, b});
    const auto first = MonitorResolver(source).Resolve(L"PATH-B");
    source.Set({b, a});
    const auto second = MonitorResolver(source).Resolve(L"PATH-B");
    Check(first.ok() && second.ok() && first.monitor == second.monitor,
          "enumeration order does not change identity");
}

void PrimaryMonitorChanges() {
    auto a = Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true);
    auto b = Monitor(L"PATH-B", {1, 0}, 1, 11, 200, L"DISPLAY2", false);
    FakeTopologySource source({a, b});
    const auto first = MonitorResolver(source).Resolve(L"PATH-B");
    a.primary = false;
    b.primary = true;
    source.Set({a, b});
    const auto second = MonitorResolver(source).Resolve(L"PATH-B");
    Check(first.ok() && second.ok() && second.monitor.hmonitor == 200,
          "primary change does not redirect selection");
}

void SelectedMonitorMissing() {
    FakeTopologySource source({Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true)});
    const auto result = MonitorResolver(source).Resolve(L"PATH-B");
    Check(result.error == MonitorResolveError::MonitorNotFound,
          "missing monitor returns typed error");
}

void DevicePathNormalization() {
    Check(fthr::monitor::NormalizeMonitorDevicePath(L"  \\\\?\\DISPLAY/ABC  ") ==
              L"\\\\?\\display\\abc",
          "device path normalized case/slashes/outer whitespace");
}

void MatchingLuidMultipleOutputs() {
    const auto selected = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const std::vector<DxgiOutputIdentity> outputs{
        {{7, 0}, 100, L"DISPLAY1", 0},
        {{7, 0}, 200, L"DISPLAY2", 1}};
    const auto result = fthr::monitor::ResolveDxgiOutput(selected, outputs);
    Check(result.ok() && result.output_index == 1, "matching LUID still resolves exact output");
}

void OutputRemovedAfterResolution() {
    const auto selected = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const std::vector<DxgiOutputIdentity> outputs{{{7, 0}, 100, L"DISPLAY1", 0}};
    const auto result = fthr::monitor::ResolveDxgiOutput(selected, outputs);
    Check(result.error == MonitorResolveError::OutputResolutionFailed,
          "removed output fails instead of redirecting");
}

void StaleTransientMappingRejected() {
    const auto old_entry = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const auto changed_entry = Monitor(L"PATH-B", {8, 0}, 0, 21, 300, L"DISPLAY3");
    FakeTopologySource source({changed_entry});
    Check(!MonitorResolver(source).IsCurrent(old_entry), "stale transient mapping rejected");
}

void NoFallbackToPrimary() {
    FakeTopologySource source({Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true)});
    const auto result = MonitorResolver(source).Resolve(L"MISSING");
    Check(!result.ok() && result.monitor.hmonitor == 0,
          "resolver never returns primary for missing selection");
}

void NoFallbackToOutputZero() {
    const auto selected = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const std::vector<DxgiOutputIdentity> outputs{{{7, 0}, 100, L"DISPLAY1", 0}};
    const auto result = fthr::monitor::ResolveDxgiOutput(selected, outputs);
    Check(!result.ok() && result.output_index == UINT32_MAX,
          "resolver never returns output zero for missing selected output");
}

} // namespace

int main() {
    OneMonitorOneAdapter();
    TwoMonitorsSameAdapter();
    SecondaryMonitorSelected();
    TwoAdaptersOneMonitorEach();
    EnumerationOrderChanges();
    PrimaryMonitorChanges();
    SelectedMonitorMissing();
    DevicePathNormalization();
    MatchingLuidMultipleOutputs();
    OutputRemovedAfterResolution();
    StaleTransientMappingRejected();
    NoFallbackToPrimary();
    NoFallbackToOutputZero();
    std::cout << "windows_monitor_resolver_test: 13 scenarios passed ("
              << checks << " checks)" << std::endl;
    return 0;
}

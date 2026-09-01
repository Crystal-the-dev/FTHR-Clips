#ifndef NOMINMAX
#define NOMINMAX
#endif

#include "windows_monitor_resolver.h"
#include "windows_native_error.h"

#include <windows.h>
#include <dxgi1_2.h>

#include <algorithm>
#include <cwctype>
#include <sstream>
#include <utility>

namespace fthr::monitor {
namespace {

struct MonitorHandleEntry {
    std::wstring gdi_name;
    uintptr_t handle = 0;
    bool primary = false;
};

struct MonitorHandleCollection {
    std::vector<MonitorHandleEntry> entries;
    DWORD first_error = ERROR_SUCCESS;
    bool failed = false;
};

BOOL CALLBACK CollectMonitorHandle(
    HMONITOR monitor, HDC, LPRECT, LPARAM context_value) {
    auto* collection = reinterpret_cast<MonitorHandleCollection*>(context_value);
    MONITORINFOEXW info{};
    info.cbSize = sizeof(info);
    if (GetMonitorInfoW(monitor, &info)) {
        collection->entries.push_back({
            info.szDevice,
            reinterpret_cast<uintptr_t>(monitor),
            (info.dwFlags & MONITORINFOF_PRIMARY) != 0});
    } else if (!collection->failed) {
        collection->first_error = GetLastError();
        collection->failed = true;
    }
    return TRUE;
}

bool EqualInsensitive(const std::wstring& left, const std::wstring& right) {
    if (left.size() != right.size()) return false;
    return std::equal(left.begin(), left.end(), right.begin(),
        [](wchar_t a, wchar_t b) { return towlower(a) == towlower(b); });
}

AdapterLuid ToAdapterLuid(const LUID& luid) {
    return {luid.LowPart, luid.HighPart};
}

uint64_t HashBytes(uint64_t hash, const void* data, size_t size) {
    constexpr uint64_t kPrime = 1099511628211ULL;
    const auto* bytes = static_cast<const uint8_t*>(data);
    for (size_t index = 0; index < size; ++index) {
        hash ^= bytes[index];
        hash *= kPrime;
    }
    return hash;
}

uint64_t TopologyGeneration(const std::vector<MonitorTopologyEntry>& entries) {
    std::vector<std::wstring> identities;
    identities.reserve(entries.size());
    for (const auto& entry : entries) {
        std::wostringstream value;
        value << NormalizeMonitorDevicePath(entry.monitor_device_path) << L'|'
              << entry.adapter_luid.high_part << L':' << entry.adapter_luid.low_part << L'|'
              << entry.source_id << L':' << entry.target_id << L'|'
              << entry.source_gdi_name << L'|' << entry.hmonitor;
        identities.push_back(value.str());
    }
    std::sort(identities.begin(), identities.end());

    uint64_t hash = 1469598103934665603ULL;
    for (const auto& identity : identities) {
        hash = HashBytes(hash, identity.data(), identity.size() * sizeof(wchar_t));
    }
    return hash;
}

std::string Win32Diagnostic(const char* operation, LONG error) {
    return diagnostics::FormatWin32Failure(
        operation, static_cast<DWORD>(error));
}

} // namespace

const char* ToString(MonitorResolveError error) noexcept {
    switch (error) {
    case MonitorResolveError::None: return "NONE";
    case MonitorResolveError::MonitorNotFound: return "MONITOR_NOT_FOUND";
    case MonitorResolveError::MonitorDisconnected: return "MONITOR_DISCONNECTED";
    case MonitorResolveError::MonitorTopologyChanged: return "MONITOR_TOPOLOGY_CHANGED";
    case MonitorResolveError::OutputResolutionFailed: return "OUTPUT_RESOLUTION_FAILED";
    case MonitorResolveError::CaptureItemCreationFailed: return "CAPTURE_ITEM_CREATION_FAILED";
    }
    return "MONITOR_UNKNOWN_ERROR";
}

std::wstring NormalizeMonitorDevicePath(const std::wstring& device_path) {
    auto first = std::find_if_not(device_path.begin(), device_path.end(), iswspace);
    auto last = std::find_if_not(device_path.rbegin(), device_path.rend(), iswspace).base();
    if (first >= last) return {};

    std::wstring normalized(first, last);
    std::transform(normalized.begin(), normalized.end(), normalized.begin(),
        [](wchar_t value) {
            if (value == L'/') return L'\\';
            return static_cast<wchar_t>(towlower(value));
        });
    return normalized;
}

TopologyQueryResult WindowsMonitorTopologySource::QueryActiveTopology() {
    UINT32 path_count = 0;
    UINT32 mode_count = 0;
    LONG status = GetDisplayConfigBufferSizes(
        QDC_ONLY_ACTIVE_PATHS, &path_count, &mode_count);
    if (status != ERROR_SUCCESS) {
        return {MonitorResolveError::MonitorTopologyChanged, {},
                Win32Diagnostic("GetDisplayConfigBufferSizes", status)};
    }

    std::vector<DISPLAYCONFIG_PATH_INFO> paths;
    std::vector<DISPLAYCONFIG_MODE_INFO> modes;
    do {
        paths.resize(path_count);
        modes.resize(mode_count);
        status = QueryDisplayConfig(
            QDC_ONLY_ACTIVE_PATHS,
            &path_count,
            paths.data(),
            &mode_count,
            modes.data(),
            nullptr);
        if (status == ERROR_INSUFFICIENT_BUFFER) {
            status = GetDisplayConfigBufferSizes(
                QDC_ONLY_ACTIVE_PATHS, &path_count, &mode_count);
            if (status != ERROR_SUCCESS) break;
        }
    } while (status == ERROR_INSUFFICIENT_BUFFER);

    if (status != ERROR_SUCCESS) {
        return {MonitorResolveError::MonitorTopologyChanged, {},
                Win32Diagnostic("QueryDisplayConfig", status)};
    }
    paths.resize(path_count);

    MonitorHandleCollection handle_collection;
    if (!EnumDisplayMonitors(
            nullptr, nullptr, CollectMonitorHandle,
            reinterpret_cast<LPARAM>(&handle_collection))) {
        return {MonitorResolveError::MonitorTopologyChanged, {},
                Win32Diagnostic("EnumDisplayMonitors", GetLastError())};
    }
    if (handle_collection.failed) {
        return {MonitorResolveError::MonitorTopologyChanged, {},
                Win32Diagnostic("GetMonitorInfoW",
                                handle_collection.first_error)};
    }
    const auto& handles = handle_collection.entries;

    TopologyQueryResult result;
    for (const auto& path : paths) {
        if ((path.flags & DISPLAYCONFIG_PATH_ACTIVE) == 0) continue;

        DISPLAYCONFIG_SOURCE_DEVICE_NAME source_name{};
        source_name.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME;
        source_name.header.size = sizeof(source_name);
        source_name.header.adapterId = path.sourceInfo.adapterId;
        source_name.header.id = path.sourceInfo.id;
        const LONG source_status = DisplayConfigGetDeviceInfo(
            &source_name.header);
        if (source_status != ERROR_SUCCESS) {
            return {MonitorResolveError::MonitorTopologyChanged, {},
                    Win32Diagnostic(
                        "DisplayConfigGetDeviceInfo(GET_SOURCE_NAME)",
                        source_status)};
        }

        DISPLAYCONFIG_TARGET_DEVICE_NAME target_name{};
        target_name.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME;
        target_name.header.size = sizeof(target_name);
        target_name.header.adapterId = path.targetInfo.adapterId;
        target_name.header.id = path.targetInfo.id;
        const LONG target_status = DisplayConfigGetDeviceInfo(
            &target_name.header);
        if (target_status != ERROR_SUCCESS) {
            return {MonitorResolveError::MonitorTopologyChanged, {},
                    Win32Diagnostic(
                        "DisplayConfigGetDeviceInfo(GET_TARGET_NAME)",
                        target_status)};
        }
        if (target_name.monitorDevicePath[0] == L'\0') continue;

        MonitorTopologyEntry entry;
        entry.monitor_device_path = NormalizeMonitorDevicePath(
            target_name.monitorDevicePath);
        entry.adapter_luid = ToAdapterLuid(path.sourceInfo.adapterId);
        entry.source_id = path.sourceInfo.id;
        entry.target_id = path.targetInfo.id;
        entry.source_gdi_name = source_name.viewGdiDeviceName;
        entry.friendly_name = target_name.monitorFriendlyDeviceName;
        entry.active = path.targetInfo.targetAvailable != FALSE;

        const auto handle = std::find_if(handles.begin(), handles.end(),
            [&](const MonitorHandleEntry& candidate) {
                return EqualInsensitive(candidate.gdi_name, entry.source_gdi_name);
            });
        if (handle != handles.end()) {
            entry.hmonitor = handle->handle;
            entry.primary = handle->primary;
        } else {
            entry.active = false;
        }
        result.monitors.push_back(std::move(entry));
    }

    result.topology_generation = TopologyGeneration(result.monitors);
    for (auto& monitor : result.monitors) {
        monitor.topology_generation = result.topology_generation;
    }
    return result;
}

MonitorResolveResult MonitorResolver::Resolve(
    const std::wstring& monitor_device_path) {
    const std::wstring selected = NormalizeMonitorDevicePath(monitor_device_path);
    if (selected.empty()) {
        return {MonitorResolveError::MonitorNotFound, {},
                "persistent monitor device path is empty"};
    }

    auto topology = source_.QueryActiveTopology();
    if (!topology.ok()) {
        return {topology.error, {}, topology.diagnostic};
    }

    const MonitorTopologyEntry* match = nullptr;
    for (const auto& entry : topology.monitors) {
        if (NormalizeMonitorDevicePath(entry.monitor_device_path) != selected) continue;
        if (match) {
            return {MonitorResolveError::MonitorTopologyChanged, {},
                    "monitor device path appears more than once in active topology"};
        }
        match = &entry;
    }
    if (!match) {
        return {MonitorResolveError::MonitorNotFound, {},
                "selected monitor device path is not in the active topology"};
    }
    if (!match->active || match->hmonitor == 0) {
        return {MonitorResolveError::MonitorDisconnected, {},
                "selected monitor has no active HMONITOR"};
    }
    return {MonitorResolveError::None, *match, {}};
}

bool MonitorResolver::IsCurrent(
    const MonitorTopologyEntry& previously_resolved) {
    const auto current = Resolve(previously_resolved.monitor_device_path);
    return current.ok() && current.monitor == previously_resolved;
}

DxgiOutputResolveResult ResolveDxgiOutput(
    const MonitorTopologyEntry& selected_monitor,
    const std::vector<DxgiOutputIdentity>& outputs) {
    const DxgiOutputIdentity* match = nullptr;
    for (const auto& output : outputs) {
        if (output.adapter_luid != selected_monitor.adapter_luid) continue;
        const bool exact_handle = selected_monitor.hmonitor != 0
            && output.hmonitor == selected_monitor.hmonitor;
        const bool exact_name = !selected_monitor.source_gdi_name.empty()
            && EqualInsensitive(output.source_gdi_name,
                                selected_monitor.source_gdi_name);
        if (!exact_handle && !exact_name) continue;
        if (match && match->output_index != output.output_index) {
            return {MonitorResolveError::OutputResolutionFailed, UINT32_MAX,
                    "selected monitor matched multiple DXGI outputs"};
        }
        match = &output;
    }
    if (!match) {
        return {MonitorResolveError::OutputResolutionFailed, UINT32_MAX,
                "selected monitor has no matching output on its adapter"};
    }
    return {MonitorResolveError::None, match->output_index, {}};
}

bool OpenSelectedDxgiOutput(
    IDXGIFactory1* factory,
    const MonitorTopologyEntry& selected_monitor,
    IDXGIAdapter1** adapter,
    IDXGIOutput** output,
    DxgiOutputIdentity* output_identity,
    std::string& diagnostic) {
    if (!factory || !adapter || !output) {
        diagnostic = "invalid DXGI output resolver arguments";
        return false;
    }
    *adapter = nullptr;
    *output = nullptr;
    if (output_identity) *output_identity = {};

    for (UINT adapter_index = 0;; ++adapter_index) {
        IDXGIAdapter1* candidate_adapter = nullptr;
        const HRESULT adapter_status = factory->EnumAdapters1(
            adapter_index, &candidate_adapter);
        if (adapter_status == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(adapter_status) || !candidate_adapter) {
            diagnostic = diagnostics::FormatHResultFailure(
                "IDXGIFactory1::EnumAdapters1", adapter_status);
            return false;
        }
        DXGI_ADAPTER_DESC1 adapter_desc{};
        const HRESULT adapter_desc_status = candidate_adapter->GetDesc1(
            &adapter_desc);
        if (FAILED(adapter_desc_status)) {
            diagnostic = diagnostics::FormatHResultFailure(
                "IDXGIAdapter1::GetDesc1", adapter_desc_status);
            candidate_adapter->Release();
            return false;
        }
        if (ToAdapterLuid(adapter_desc.AdapterLuid)
                != selected_monitor.adapter_luid) {
            candidate_adapter->Release();
            continue;
        }

        std::vector<DxgiOutputIdentity> identities;
        for (UINT output_index = 0;; ++output_index) {
            IDXGIOutput* candidate_output = nullptr;
            const HRESULT output_status = candidate_adapter->EnumOutputs(
                output_index, &candidate_output);
            if (output_status == DXGI_ERROR_NOT_FOUND) break;
            if (FAILED(output_status) || !candidate_output) {
                diagnostic = diagnostics::FormatHResultFailure(
                    "IDXGIAdapter1::EnumOutputs", output_status);
                candidate_adapter->Release();
                return false;
            }
            DXGI_OUTPUT_DESC output_desc{};
            const HRESULT output_desc_status = candidate_output->GetDesc(
                &output_desc);
            if (FAILED(output_desc_status)) {
                diagnostic = diagnostics::FormatHResultFailure(
                    "IDXGIOutput::GetDesc", output_desc_status);
                candidate_output->Release();
                candidate_adapter->Release();
                return false;
            }
            identities.push_back({
                selected_monitor.adapter_luid,
                reinterpret_cast<uintptr_t>(output_desc.Monitor),
                output_desc.DeviceName,
                output_index});
            candidate_output->Release();
        }

        const auto resolved = ResolveDxgiOutput(selected_monitor, identities);
        if (!resolved.ok()) {
            diagnostic = resolved.diagnostic;
            candidate_adapter->Release();
            return false;
        }
        const HRESULT reopen_status = candidate_adapter->EnumOutputs(
            resolved.output_index, output);
        if (FAILED(reopen_status) || !*output) {
            diagnostic = diagnostics::FormatHResultFailure(
                "IDXGIAdapter1::EnumOutputs(reopen selected output)",
                reopen_status);
            candidate_adapter->Release();
            return false;
        }
        if (output_identity) {
            const auto match = std::find_if(
                identities.begin(), identities.end(),
                [&resolved](const DxgiOutputIdentity& identity) {
                    return identity.output_index == resolved.output_index;
                });
            if (match != identities.end()) *output_identity = *match;
        }
        *adapter = candidate_adapter;
        return true;
    }

    diagnostic = "selected monitor adapter LUID is not present in DXGI";
    return false;
}

} // namespace fthr::monitor

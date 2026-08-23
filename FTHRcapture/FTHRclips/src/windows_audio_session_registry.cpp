#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <audiopolicy.h>
#include <mmdeviceapi.h>
#include <tlhelp32.h>
#include <wrl/client.h>

#include "windows_audio_session_registry.h"

#include <algorithm>
#include <cwctype>
#include <filesystem>
#include <map>
#include <sstream>

#pragma comment(lib, "Mmdevapi.lib")
#pragma comment(lib, "Ole32.lib")

using Microsoft::WRL::ComPtr;

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

std::wstring ToLower(std::wstring text) {
    std::transform(text.begin(), text.end(), text.begin(), [](wchar_t character) {
        return static_cast<wchar_t>(towlower(character));
    });
    return text;
}

std::string ToUtf8(const std::wstring& text) {
    if (text.empty()) return {};
    const int required = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
        text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    if (required <= 0) return {};
    std::string result(static_cast<size_t>(required), '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
            text.data(), static_cast<int>(text.size()), result.data(), required,
            nullptr, nullptr) <= 0) {
        return {};
    }
    return result;
}

std::wstring ExecutableBasename(uint32_t process_id) {
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, process_id);
    if (!process) return {};
    std::wstring path(32768, L'\0');
    DWORD length = static_cast<DWORD>(path.size());
    const bool ok = QueryFullProcessImageNameW(process, 0, path.data(), &length) != FALSE;
    CloseHandle(process);
    if (!ok || length == 0) return {};
    path.resize(length);
    return std::filesystem::path(path).filename().wstring();
}

uint32_t ParentProcessId(uint32_t process_id) {
    const HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    uint32_t parent = 0;
    if (Process32FirstW(snapshot, &entry)) {
        do {
            if (entry.th32ProcessID == process_id) {
                parent = entry.th32ParentProcessID;
                break;
            }
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    return parent;
}

uint32_t ProcessGroupRoot(uint32_t process_id, const std::wstring& executable_basename) {
    const std::wstring wanted = ToLower(executable_basename);
    uint32_t root = process_id;
    uint32_t cursor = process_id;
    // Only merge a child into an ancestor when the executable identity agrees.
    // That makes launcher/game boundaries conservative rather than guessing.
    for (uint32_t depth = 0; depth < 32; ++depth) {
        const uint32_t parent = ParentProcessId(cursor);
        if (parent == 0 || parent == cursor) break;
        const std::wstring parent_name = ExecutableBasename(parent);
        if (parent_name.empty() || ToLower(parent_name) != wanted) break;
        root = parent;
        cursor = parent;
    }
    return root;
}

AudioSourceIdentity IdentityForProcess(uint32_t process_id,
                                       const std::wstring& executable_basename) {
    AudioSourceIdentity identity;
    // Runtime creation gets the final opaque UUID from the source provider.
    // The session registry never derives an ID from PID, because PIDs are
    // recyclable and must not be a persistent source identity.
    identity.type = AudioSourceType::Application;
    identity.persistent_identity = NormalizeWindowsExecutableIdentity(executable_basename);
    identity.display_name = ResolveWindowsAudioSourceName(executable_basename);
    identity.icon_reference = "windows-app-icon";
    (void)process_id;
    return identity;
}

}  // namespace

WindowsProcessLoopbackCapability DetectWindowsProcessLoopbackCapability() {
    WindowsProcessLoopbackCapability result;
    const HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    const auto get_version = ntdll ? reinterpret_cast<RtlGetVersionFn>(
        GetProcAddress(ntdll, "RtlGetVersion")) : nullptr;
    RtlOsVersionInfo version{};
    version.dwOSVersionInfoSize = sizeof(version);
    if (get_version && get_version(&version) == 0)
        result.os_build = version.dwBuildNumber;
    result.api_build_supported = IsWindowsProcessLoopbackBuildSupported(result.os_build);
    return result;
}

bool IsWindowsProcessLoopbackBuildSupported(uint32_t os_build) {
    return os_build >= kProcessLoopbackMinimumBuild;
}

std::string NormalizeWindowsExecutableIdentity(const std::wstring& executable_basename) {
    std::wstring identity = ToLower(executable_basename);
    const std::wstring suffix = L".exe";
    if (identity.size() > suffix.size()
            && identity.compare(identity.size() - suffix.size(), suffix.size(), suffix) == 0) {
        identity.resize(identity.size() - suffix.size());
    }
    return SanitizeAudioSourceText(ToUtf8(identity), 128);
}

std::string ResolveWindowsAudioSourceName(const std::wstring& executable_basename) {
    const std::string identity = NormalizeWindowsExecutableIdentity(executable_basename);
    if (identity == "valorant-win64-shipping") return "VALORANT";
    if (identity == "discord") return "Discord";
    if (identity == "firefox") return "Firefox";
    if (identity == "spotify") return "Spotify";
    if (identity.empty()) return "Application";
    std::string result = identity;
    result[0] = static_cast<char>(toupper(static_cast<unsigned char>(result[0])));
    return result;
}

std::string BuildWindowsRuntimeGroupKey(uint32_t root_process_id,
                                        const std::wstring& executable_basename) {
    const std::string identity = NormalizeWindowsExecutableIdentity(executable_basename);
    if (identity.empty() || root_process_id == 0) return {};
    return identity + "#" + std::to_string(root_process_id);
}

class WindowsAudioSessionRegistry::SessionNotification final
    : public IAudioSessionNotification {
public:
    explicit SessionNotification(WindowsAudioSessionRegistry* owner) : owner_(owner) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == __uuidof(IAudioSessionNotification)) {
            *object = static_cast<IAudioSessionNotification*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG refs = --refs_;
        if (refs == 0) delete this;
        return refs;
    }
    HRESULT STDMETHODCALLTYPE OnSessionCreated(IAudioSessionControl*) override {
        if (owner_) owner_->MarkDirty();
        return S_OK;
    }

private:
    std::atomic<ULONG> refs_{1};
    WindowsAudioSessionRegistry* owner_;
};

WindowsAudioSessionRegistry::WindowsAudioSessionRegistry() = default;

WindowsAudioSessionRegistry::~WindowsAudioSessionRegistry() {
    Stop();
}

bool WindowsAudioSessionRegistry::Start() {
    if (started_) return true;
    last_error_.clear();
    ComPtr<IMMDeviceEnumerator> enumerator;
    ComPtr<IMMDevice> device;
    ComPtr<IAudioSessionManager2> manager;
    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
        CLSCTX_ALL, IID_PPV_ARGS(&enumerator));
    if (SUCCEEDED(hr)) hr = enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device);
    if (SUCCEEDED(hr)) hr = device->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL,
        nullptr, reinterpret_cast<void**>(manager.GetAddressOf()));
    if (FAILED(hr)) {
        last_error_ = "could not open the default render-session manager";
        return false;
    }
    notification_ = new SessionNotification(this);
    hr = manager->RegisterSessionNotification(notification_);
    if (FAILED(hr)) {
        notification_->Release();
        notification_ = nullptr;
        last_error_ = "could not register for audio-session lifecycle notifications";
        return false;
    }
    session_manager_ = manager.Detach();
    started_ = true;
    dirty_.store(true, std::memory_order_release);
    WindowsAudioSessionUpdate ignored;
    return RefreshIfNeeded(&ignored);
}

void WindowsAudioSessionRegistry::Stop() {
    auto* manager = static_cast<IAudioSessionManager2*>(session_manager_);
    if (manager && notification_) manager->UnregisterSessionNotification(notification_);
    if (notification_) notification_->Release();
    notification_ = nullptr;
    if (manager) manager->Release();
    session_manager_ = nullptr;
    current_.clear();
    started_ = false;
    dirty_.store(false, std::memory_order_release);
}

bool WindowsAudioSessionRegistry::RefreshIfNeeded(WindowsAudioSessionUpdate* update) {
    if (!update) return false;
    if (!started_) return false;
    if (!dirty_.exchange(false, std::memory_order_acq_rel)) {
        update->current = current_;
        return true;
    }
    if (!Refresh(update)) {
        dirty_.store(true, std::memory_order_release);
        return false;
    }
    return true;
}

bool WindowsAudioSessionRegistry::Refresh(WindowsAudioSessionUpdate* update) {
    auto* manager = static_cast<IAudioSessionManager2*>(session_manager_);
    if (!manager || !update) return false;
    ComPtr<IAudioSessionEnumerator> sessions;
    HRESULT hr = manager->GetSessionEnumerator(&sessions);
    if (FAILED(hr)) {
        last_error_ = "could not enumerate Windows audio sessions";
        return false;
    }
    int count = 0;
    if (FAILED(sessions->GetCount(&count))) {
        last_error_ = "could not count Windows audio sessions";
        return false;
    }

    std::map<std::string, WindowsAudioSessionDescriptor> groups;
    for (int index = 0; index < count; ++index) {
        ComPtr<IAudioSessionControl> control;
        ComPtr<IAudioSessionControl2> control2;
        if (FAILED(sessions->GetSession(index, &control)) || FAILED(control.As(&control2)))
            continue;
        DWORD pid = 0;
        if (FAILED(control2->GetProcessId(&pid)) || pid == 0) continue;
        const std::wstring executable = ExecutableBasename(pid);
        if (executable.empty()) continue;
        const uint32_t root_pid = ProcessGroupRoot(pid, executable);
        const std::string group_key = BuildWindowsRuntimeGroupKey(root_pid, executable);
        if (group_key.empty()) continue;

        auto [it, inserted] = groups.emplace(group_key, WindowsAudioSessionDescriptor{});
        auto& descriptor = it->second;
        if (inserted) {
            descriptor.process_id = pid;
            descriptor.process_group_root_id = root_pid;
            descriptor.runtime_group_key = group_key;
            descriptor.identity = IdentityForProcess(pid, executable);
        }
        AudioSessionState state = AudioSessionStateInactive;
        if (SUCCEEDED(control->GetState(&state)) && state == AudioSessionStateActive)
            descriptor.currently_active = true;
    }

    std::map<std::string, bool> previous;
    for (const auto& session : current_) previous.emplace(session.runtime_group_key, true);
    std::map<std::string, bool> next;
    for (const auto& [key, session] : groups) {
        next.emplace(key, true);
        if (!previous.count(key)) update->appeared_runtime_groups.push_back(key);
    }
    for (const auto& [key, _] : previous) {
        if (!next.count(key)) update->disappeared_runtime_groups.push_back(key);
    }
    update->current.clear();
    update->current.reserve(groups.size());
    for (auto& [_, session] : groups) update->current.push_back(std::move(session));
    current_ = update->current;
    return true;
}

}  // namespace fthr

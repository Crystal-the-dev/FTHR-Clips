// AUDIT-050 isolated WASAPI session discovery probe.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <audiopolicy.h>
#include <mmdeviceapi.h>
#include <wrl/client.h>

#include <atomic>
#include <iostream>

using Microsoft::WRL::ComPtr;

namespace {

class SessionNotification final : public IAudioSessionNotification {
public:
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
    HRESULT STDMETHODCALLTYPE OnSessionCreated(IAudioSessionControl* session) override {
        (void)session;
        ++created_;
        std::cout << "SESSION_CREATED\n";
        return S_OK;
    }
    ULONG created() const { return created_.load(); }

private:
    std::atomic<ULONG> refs_{1};
    std::atomic<ULONG> created_{0};
};

} // namespace

int wmain() {
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) return 1;
    ComPtr<IMMDeviceEnumerator> enumerator;
    ComPtr<IMMDevice> device;
    ComPtr<IAudioSessionManager2> manager;
    ComPtr<IAudioSessionEnumerator> sessions;
    hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                          CLSCTX_ALL, IID_PPV_ARGS(&enumerator));
    if (SUCCEEDED(hr)) hr = enumerator->GetDefaultAudioEndpoint(
        eRender, eConsole, &device);
    if (SUCCEEDED(hr)) hr = device->Activate(
        __uuidof(IAudioSessionManager2), CLSCTX_ALL, nullptr,
        reinterpret_cast<void**>(manager.GetAddressOf()));
    if (SUCCEEDED(hr)) hr = manager->GetSessionEnumerator(&sessions);
    if (FAILED(hr)) {
        std::cout << "SESSION_DISCOVERY_FAILED 0x" << std::hex
                  << static_cast<unsigned long>(hr) << "\n";
        CoUninitialize();
        return 2;
    }
    int count = 0;
    sessions->GetCount(&count);
    std::cout << "SESSION_COUNT=" << count << "\n";
    for (int i = 0; i < count; ++i) {
        ComPtr<IAudioSessionControl> control;
        ComPtr<IAudioSessionControl2> control2;
        if (FAILED(sessions->GetSession(i, &control))) continue;
        if (FAILED(control.As(&control2))) continue;
        DWORD pid = 0;
        LPWSTR display = nullptr;
        LPWSTR identifier = nullptr;
        control2->GetProcessId(&pid);
        control->GetDisplayName(&display);
        control2->GetSessionInstanceIdentifier(&identifier);
        std::wcout << L"SESSION pid=" << pid << L" display="
                   << (display ? display : L"") << L" id="
                   << (identifier ? identifier : L"") << L"\n";
        CoTaskMemFree(display);
        CoTaskMemFree(identifier);
    }
    auto* notification = new SessionNotification();
    const HRESULT registered = manager->RegisterSessionNotification(notification);
    Sleep(1000);
    if (SUCCEEDED(registered)) manager->UnregisterSessionNotification(notification);
    const ULONG created = notification->created();
    notification->Release();
    std::cout << "SESSION_NOTIFICATION_REGISTERED="
              << (SUCCEEDED(registered) ? "true" : "false")
              << " created_during_wait=" << created << "\n";
    CoUninitialize();
    return SUCCEEDED(registered) ? 0 : 3;
}

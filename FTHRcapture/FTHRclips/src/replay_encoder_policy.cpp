#include "replay_encoder.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>

namespace fthr {

const char* EncoderVendorName(EncoderVendor vendor) noexcept {
    switch (vendor) {
    case EncoderVendor::Nvidia: return "NVIDIA";
    case EncoderVendor::Amd: return "AMD";
    case EncoderVendor::Intel: return "Intel";
    case EncoderVendor::Software: return "unknown/software";
    }
    return "unknown/software";
}

EncoderVendor QueryD3D11DeviceVendor(ID3D11Device* device) noexcept {
    if (!device) return EncoderVendor::Software;
    IDXGIDevice* dxgi_device = nullptr;
    if (FAILED(device->QueryInterface(
            __uuidof(IDXGIDevice),
            reinterpret_cast<void**>(&dxgi_device)))) {
        return EncoderVendor::Software;
    }

    IDXGIAdapter* adapter = nullptr;
    const HRESULT adapter_result = dxgi_device->GetAdapter(&adapter);
    dxgi_device->Release();
    if (FAILED(adapter_result) || !adapter) {
        return EncoderVendor::Software;
    }

    DXGI_ADAPTER_DESC description{};
    const HRESULT description_result = adapter->GetDesc(&description);
    adapter->Release();
    return SUCCEEDED(description_result)
        ? EncoderVendorFromPciVendorId(description.VendorId)
        : EncoderVendor::Software;
}

} // namespace fthr

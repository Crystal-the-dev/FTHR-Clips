#include "capture_backend.h"
#include "backend_wlr.h"
#include "backend_ext.h"
#if FTHR_X11_CAPTURE
#include "backend_x11.h"
#endif
#include <cstdlib>
#include <iostream>

namespace fthr {

std::unique_ptr<ICaptureBackend> CreateBestBackend(
    const CaptureConfig& cfg,
    const std::atomic<bool>* running) {
    const auto cancelled = [running] {
        return running && !running->load();
    };
    if (cancelled()) return nullptr;

    bool has_wayland = (std::getenv("WAYLAND_DISPLAY") != nullptr);

    if (has_wayland) {
        // 1. wlr-screencopy (Hyprland, Sway, wlroots compositors)
        auto wlr = std::make_unique<WlrBackend>(running);
        if (wlr->Initialize(cfg)) {
            std::cerr << "[Backend] Using wlr-screencopy" << std::endl;
            return wlr;
        }
        if (cancelled()) return nullptr;

        // 2. ext-image-copy-capture-v1 (KDE Plasma 6+, GNOME 46+)
        auto ext = std::make_unique<ExtBackend>(running);
        if (ext->Initialize(cfg)) {
            std::cerr << "[Backend] Using ext-image-copy-capture-v1" << std::endl;
            return ext;
        }
        if (cancelled()) return nullptr;

        std::cerr << "[Backend] No Wayland capture backend available; "
                     "refusing XWayland/x11grab fallback" << std::endl;
        return nullptr;
    }

    // Native X11 only. The UI resolves the selected connector through RandR
    // and passes a physical root-window rectangle. The engine process itself
    // is the cancellation boundary for XCB calls that ignore AVIOInterruptCB.
    if (cancelled()) return nullptr;
#if FTHR_X11_CAPTURE
    auto x11 = std::make_unique<X11Backend>(running);
    if (x11->Initialize(cfg)) {
        std::cerr << "[Backend] Using x11grab" << std::endl;
        return x11;
    }
#else
    std::cerr << "[Backend] Native X11 capture disabled at build time" << std::endl;
#endif

    std::cerr << "[Backend] No capture backend available on this system" << std::endl;
    return nullptr;
}

} // namespace fthr

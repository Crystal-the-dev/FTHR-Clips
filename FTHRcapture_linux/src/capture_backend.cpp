#include "capture_backend.h"
#include "backend_wlr.h"
#include "backend_ext.h"
#if FTHR_EXPERIMENTAL_X11GRAB
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

        std::cerr << "[Backend] No Wayland capture backend available" << std::endl;
    }

    // x11grab remains opt-in until AUDIT-044 has a proven bounded-cancellation
    // architecture. Never silently fall back to it in an alpha build.
    if (cancelled()) return nullptr;
#if FTHR_EXPERIMENTAL_X11GRAB
    auto x11 = std::make_unique<X11Backend>();
    if (x11->Initialize(cfg)) {
        std::cerr << "[Backend] Using x11grab" << std::endl;
        return x11;
    }
#else
    std::cerr << "[Backend] x11grab disabled for alpha: AUDIT-044 bounded "
                 "cancellation unresolved" << std::endl;
#endif

    std::cerr << "[Backend] No capture backend available on this system" << std::endl;
    return nullptr;
}

} // namespace fthr

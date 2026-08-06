#include "capture_backend.h"
#include "backend_wlr.h"
#include "backend_ext.h"
#include "backend_x11.h"
#include <cstdlib>
#include <iostream>

namespace fthr {

std::unique_ptr<ICaptureBackend> CreateBestBackend(const CaptureConfig& cfg) {
    bool has_wayland = (std::getenv("WAYLAND_DISPLAY") != nullptr);

    if (has_wayland) {
        // 1. wlr-screencopy (Hyprland, Sway, wlroots compositors)
        auto wlr = std::make_unique<WlrBackend>();
        if (wlr->Initialize(cfg)) {
            std::cerr << "[Backend] Using wlr-screencopy" << std::endl;
            return wlr;
        }

        // 2. ext-image-copy-capture-v1 (KDE Plasma 6+, GNOME 46+)
        auto ext = std::make_unique<ExtBackend>();
        if (ext->Initialize(cfg)) {
            std::cerr << "[Backend] Using ext-image-copy-capture-v1" << std::endl;
            return ext;
        }

        std::cerr << "[Backend] No Wayland capture backend available" << std::endl;
    }

    // 3. x11grab (pure X11 session, or fallback)
    auto x11 = std::make_unique<X11Backend>();
    if (x11->Initialize(cfg)) {
        std::cerr << "[Backend] Using x11grab" << std::endl;
        return x11;
    }

    std::cerr << "[Backend] No capture backend available on this system" << std::endl;
    return nullptr;
}

} // namespace fthr

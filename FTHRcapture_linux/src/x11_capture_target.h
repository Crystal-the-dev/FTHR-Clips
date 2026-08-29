#pragma once

#include <cstdint>
#include <string_view>

namespace fthr {

struct X11CaptureTarget {
    uint32_t x = 0;
    uint32_t y = 0;
    uint32_t width = 0;
    uint32_t height = 0;
};

// Parse the private UI -> engine target form "@x11:x,y,width,height".
// Connector-to-geometry resolution happens through RandR in the UI process;
// the engine accepts only a fully validated physical root-window rectangle.
bool ParseX11CaptureTarget(std::string_view value, X11CaptureTarget& target);

} // namespace fthr

#include "x11_capture_target.h"

#include <cassert>
#include <iostream>

int main() {
    fthr::X11CaptureTarget target{};

    assert(fthr::ParseX11CaptureTarget(
        "@x11:0,1440,1920,1080", target));
    assert(target.x == 0);
    assert(target.y == 1440);
    assert(target.width == 1920);
    assert(target.height == 1080);

    assert(fthr::ParseX11CaptureTarget(
        "@x11:1920,0,2560,1440", target));
    assert(target.x == 1920);
    assert(target.y == 0);
    assert(target.width == 2560);
    assert(target.height == 1440);

    assert(!fthr::ParseX11CaptureTarget("DP-1", target));
    assert(!fthr::ParseX11CaptureTarget("@x11:-1,0,1920,1080", target));
    assert(!fthr::ParseX11CaptureTarget("@x11:0,0,0,1080", target));
    assert(!fthr::ParseX11CaptureTarget("@x11:0,0,1920", target));
    assert(!fthr::ParseX11CaptureTarget("@x11:0,0,1920,1080,4", target));
    assert(!fthr::ParseX11CaptureTarget(
        "@x11:2147483647,0,1,1080", target));

    std::cout << "X11 capture target tests passed\n";
    return 0;
}

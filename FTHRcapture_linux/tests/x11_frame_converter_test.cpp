#include "x11_frame_converter.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>

extern "C" {
#include <libavutil/pixfmt.h>
}

namespace {

struct Rgba {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
    uint8_t alpha;
};

} // namespace

int main() {
    constexpr int width = 6;
    constexpr int height = 2;
    constexpr int source_stride = width * 4 + 8;
    constexpr std::array<Rgba, width> colors{{
        {255, 0, 0, 255},
        {0, 255, 0, 255},
        {0, 0, 255, 255},
        {127, 127, 127, 255},
        {0, 0, 0, 255},
        {255, 255, 255, 255},
    }};
    std::array<uint8_t, source_stride * height> source{};
    source.fill(0xa5); // visible padding: conversion must skip it per row
    for (int row = 0; row < height; ++row) {
        for (int column = 0; column < width; ++column) {
            const Rgba color = colors[static_cast<size_t>(column)];
            const size_t offset = static_cast<size_t>(row) * source_stride +
                                  static_cast<size_t>(column) * 4;
            source[offset + 0] = color.red;
            source[offset + 1] = color.green;
            source[offset + 2] = color.blue;
            source[offset + 3] = color.alpha;
        }
    }

    AVFrame frame{};
    frame.data[0] = source.data();
    frame.linesize[0] = source_stride;
    frame.width = width;
    frame.height = height;
    frame.format = AV_PIX_FMT_RGBA;

    fthr::X11FrameConverter converter;
    assert(converter.Convert(frame));
    assert(converter.Width() == width);
    assert(converter.Height() == height);
    assert(converter.Stride() == width * 4);

    const uint8_t* converted = converter.Data();
    for (int row = 0; row < height; ++row) {
        for (int column = 0; column < width; ++column) {
            const Rgba expected = colors[static_cast<size_t>(column)];
            const size_t offset = static_cast<size_t>(row) * converter.Stride() +
                                  static_cast<size_t>(column) * 4;
            assert(converted[offset + 0] == expected.blue);
            assert(converted[offset + 1] == expected.green);
            assert(converted[offset + 2] == expected.red);
        }
    }

    converter.Reset();
    assert(converter.Width() == 0);
    assert(converter.Height() == 0);
    std::cout << "X11 frame conversion tests passed\n";
    return 0;
}

#pragma once

#include <cstdint>
#include <vector>

extern "C" {
#include <libavutil/frame.h>
}

struct SwsContext;

namespace fthr {

// Reusable X11 raw-frame conversion. FFmpeg owns the source planes and their
// real linesizes; this class owns one tightly packed BGR0 destination buffer.
class X11FrameConverter {
public:
    X11FrameConverter() = default;
    ~X11FrameConverter();

    X11FrameConverter(const X11FrameConverter&) = delete;
    X11FrameConverter& operator=(const X11FrameConverter&) = delete;

    bool Convert(const AVFrame& frame);
    void Reset();

    const uint8_t* Data() const { return buffer_.data(); }
    uint32_t Width() const { return width_; }
    uint32_t Height() const { return height_; }
    uint32_t Stride() const { return width_ * 4; }

private:
    SwsContext* context_ = nullptr;
    uint32_t width_ = 0;
    uint32_t height_ = 0;
    int source_format_ = -1;
    std::vector<uint8_t> buffer_;
};

} // namespace fthr

#include "x11_frame_converter.h"

#include <cstddef>
#include <limits>

extern "C" {
#include <libavutil/pixfmt.h>
#include <libswscale/swscale.h>
}

namespace fthr {

X11FrameConverter::~X11FrameConverter() { Reset(); }

bool X11FrameConverter::Convert(const AVFrame& frame) {
    if (frame.width <= 0 || frame.height <= 0 || frame.format < 0 ||
        !frame.data[0] || frame.linesize[0] == 0) {
        return false;
    }
    const auto width = static_cast<uint32_t>(frame.width);
    const auto height = static_cast<uint32_t>(frame.height);
    if (width > static_cast<uint32_t>(std::numeric_limits<int>::max() / 4) ||
        width > std::numeric_limits<size_t>::max() / 4 / height)
        return false;

    if (!context_ || width != width_ || height != height_ ||
        frame.format != source_format_) {
        sws_freeContext(context_);
        context_ = sws_getContext(
            frame.width, frame.height,
            static_cast<AVPixelFormat>(frame.format),
            frame.width, frame.height, AV_PIX_FMT_BGR0,
            SWS_POINT, nullptr, nullptr, nullptr);
        if (!context_) {
            width_ = height_ = 0;
            source_format_ = -1;
            buffer_.clear();
            return false;
        }
        width_ = width;
        height_ = height;
        source_format_ = frame.format;
        buffer_.assign(static_cast<size_t>(width_) * height_ * 4, 0);
    }

    uint8_t* destination[1] = {buffer_.data()};
    const int destination_linesize[1] = {static_cast<int>(Stride())};
    const int converted = sws_scale(
        context_, frame.data, frame.linesize, 0, frame.height,
        destination, destination_linesize);
    return converted == frame.height;
}

void X11FrameConverter::Reset() {
    sws_freeContext(context_);
    context_ = nullptr;
    width_ = height_ = 0;
    source_format_ = -1;
    buffer_.clear();
}

} // namespace fthr

#pragma once
#include "capture_backend.h"
#include <vector>
extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/pixfmt.h>
}

namespace fthr {

class X11Backend final : public ICaptureBackend {
public:
    X11Backend() = default;
    ~X11Backend() override { Shutdown(); }

    bool Initialize(const CaptureConfig& cfg) override;
    bool CaptureFrame(RawFrame& out) override;
    void Shutdown() override;

    BackendType Type() const override { return BackendType::X11Grab; }
    uint32_t NativeWidth()  const override { return native_w_; }
    uint32_t NativeHeight() const override { return native_h_; }

private:
    AVFormatContext* fmt_ctx_      = nullptr;
    AVCodecContext*  dec_ctx_      = nullptr;
    AVPacket*        pkt_          = nullptr;
    AVFrame*         frame_        = nullptr;
    struct SwsContext* sws_        = nullptr;
    int              video_stream_ = -1;

    uint32_t native_w_ = 0;
    uint32_t native_h_ = 0;

    std::vector<uint8_t> buf_;  // BGR0 output buffer, reused per frame
};

} // namespace fthr

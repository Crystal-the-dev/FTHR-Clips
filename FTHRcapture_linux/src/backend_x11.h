#pragma once
#include "capture_backend.h"
#include "x11_frame_converter.h"
#include <atomic>
extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/pixfmt.h>
}

namespace fthr {

class X11Backend final : public ICaptureBackend {
public:
    explicit X11Backend(const std::atomic<bool>* running) : running_(running) {}
    ~X11Backend() override { Shutdown(); }

    bool Initialize(const CaptureConfig& cfg) override;
    bool CaptureFrame(RawFrame& out) override;
    void Shutdown() override;

    BackendType Type() const override { return BackendType::X11Grab; }
    uint32_t NativeWidth()  const override { return native_w_; }
    uint32_t NativeHeight() const override { return native_h_; }

private:
    static int InterruptCallback(void* opaque);

    const std::atomic<bool>* running_ = nullptr;
    AVFormatContext* fmt_ctx_      = nullptr;
    AVCodecContext*  dec_ctx_      = nullptr;
    AVPacket*        pkt_          = nullptr;
    AVFrame*         frame_        = nullptr;
    int              video_stream_ = -1;

    uint32_t native_w_ = 0;
    uint32_t native_h_ = 0;
    X11FrameConverter converter_;
};

} // namespace fthr

#include "backend_x11.h"
#include "x11_capture_target.h"
#include <iostream>
#include <cstdlib>
#include <string>
#include <time.h>
extern "C" {
#include <libavformat/avformat.h>
#include <libavdevice/avdevice.h>
#include <libavcodec/avcodec.h>
#include <libavutil/dict.h>
#include <libswscale/swscale.h>
}

namespace fthr {

int X11Backend::InterruptCallback(void* opaque) {
    const auto* backend = static_cast<const X11Backend*>(opaque);
    return backend && backend->running_ && !backend->running_->load() ? 1 : 0;
}

bool X11Backend::Initialize(const CaptureConfig& cfg) {
    const char* display_env = std::getenv("DISPLAY");
    if (!display_env) {
        std::cerr << "[X11Backend] DISPLAY not set" << std::endl;
        return false;
    }

    avdevice_register_all();

    const AVInputFormat* ifmt = av_find_input_format("x11grab");
    if (!ifmt) {
        std::cerr << "[X11Backend] x11grab not available in FFmpeg" << std::endl;
        return false;
    }

    AVDictionary* opts = nullptr;
    av_dict_set(&opts, "framerate", std::to_string(cfg.fps).c_str(), 0);
    av_dict_set(&opts, "draw_mouse", "0", 0);

    X11CaptureTarget target{};
    if (!ParseX11CaptureTarget(cfg.target_output, target)) {
        std::cerr << "[X11Backend] Invalid or unresolved selected-monitor "
                     "geometry; refusing whole-desktop fallback" << std::endl;
        av_dict_free(&opts);
        return false;
    }
    const std::string video_size =
        std::to_string(target.width) + "x" + std::to_string(target.height);
    av_dict_set(&opts, "video_size", video_size.c_str(), 0);
    av_dict_set(&opts, "x", std::to_string(target.x).c_str(), 0);
    av_dict_set(&opts, "y", std::to_string(target.y).c_str(), 0);
    std::cerr << "[X11Backend] Selected root rect "
              << target.width << "x" << target.height
              << "+" << target.x << "+" << target.y << std::endl;

    fmt_ctx_ = avformat_alloc_context();
    if (!fmt_ctx_) {
        av_dict_free(&opts);
        std::cerr << "[X11Backend] Could not allocate format context" << std::endl;
        return false;
    }
    fmt_ctx_->interrupt_callback.callback = &X11Backend::InterruptCallback;
    fmt_ctx_->interrupt_callback.opaque = this;
    int ret = avformat_open_input(&fmt_ctx_, display_env, ifmt, &opts);
    av_dict_free(&opts);
    if (ret < 0) {
        char err[128]; av_strerror(ret, err, sizeof(err));
        std::cerr << "[X11Backend] avformat_open_input failed: " << err << std::endl;
        return false;
    }
    if (avformat_find_stream_info(fmt_ctx_, nullptr) < 0) {
        std::cerr << "[X11Backend] avformat_find_stream_info failed" << std::endl;
        avformat_close_input(&fmt_ctx_);
        return false;
    }

    video_stream_ = -1;
    for (unsigned i = 0; i < fmt_ctx_->nb_streams; ++i) {
        if (fmt_ctx_->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_VIDEO) {
            video_stream_ = static_cast<int>(i);
            break;
        }
    }
    if (video_stream_ < 0) {
        std::cerr << "[X11Backend] No video stream" << std::endl;
        avformat_close_input(&fmt_ctx_);
        return false;
    }

    AVCodecParameters* cp = fmt_ctx_->streams[video_stream_]->codecpar;
    const AVCodec* dec = avcodec_find_decoder(cp->codec_id);
    if (!dec) {
        avformat_close_input(&fmt_ctx_);
        return false;
    }
    dec_ctx_ = avcodec_alloc_context3(dec);
    if (!dec_ctx_ || avcodec_parameters_to_context(dec_ctx_, cp) < 0) {
        avcodec_free_context(&dec_ctx_);
        avformat_close_input(&fmt_ctx_);
        return false;
    }
    if (avcodec_open2(dec_ctx_, dec, nullptr) < 0) {
        avformat_close_input(&fmt_ctx_);
        return false;
    }

    native_w_ = static_cast<uint32_t>(dec_ctx_->width);
    native_h_ = static_cast<uint32_t>(dec_ctx_->height);

    pkt_   = av_packet_alloc();
    frame_ = av_frame_alloc();
    if (!pkt_ || !frame_ || native_w_ == 0 || native_h_ == 0) {
        Shutdown();
        std::cerr << "[X11Backend] Invalid stream dimensions or allocation failure"
                  << std::endl;
        return false;
    }
    std::cerr << "[X11Backend] Ready: " << native_w_ << "x" << native_h_ << std::endl;
    return true;
}

bool X11Backend::CaptureFrame(RawFrame& out) {
    while (true) {
        int ret = av_read_frame(fmt_ctx_, pkt_);
        if (ret < 0) {
            av_packet_unref(pkt_);
            return false;
        }
        if (pkt_->stream_index != video_stream_) {
            av_packet_unref(pkt_);
            continue;
        }
        ret = avcodec_send_packet(dec_ctx_, pkt_);
        av_packet_unref(pkt_);
        if (ret < 0) return false;
        ret = avcodec_receive_frame(dec_ctx_, frame_);
        if (ret == AVERROR(EAGAIN)) continue;
        if (ret < 0) return false;
        break;
    }

    // swscale consumes FFmpeg's real per-plane linesizes. Never assume the
    // source row pitch equals width * bytes-per-pixel, and never assume BGRA.
    if (!converter_.Convert(*frame_)) {
        av_frame_unref(frame_);
        return false;
    }
    native_w_ = converter_.Width();
    native_h_ = converter_.Height();

    av_frame_unref(frame_);

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    out.data         = converter_.Data();
    out.stride       = converter_.Stride();
    out.width        = native_w_;
    out.height       = native_h_;
    out.av_pix_fmt   = AV_PIX_FMT_BGR0;
    out.timestamp_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + ts.tv_nsec;
    return true;
}

void X11Backend::Shutdown() {
    converter_.Reset();
    if (frame_)   { av_frame_free(&frame_);        frame_   = nullptr; }
    if (pkt_)     { av_packet_free(&pkt_);         pkt_     = nullptr; }
    if (dec_ctx_) { avcodec_free_context(&dec_ctx_); dec_ctx_ = nullptr; }
    if (fmt_ctx_) { avformat_close_input(&fmt_ctx_); fmt_ctx_ = nullptr; }
}

} // namespace fthr

#include "backend_x11.h"
#include <iostream>
#include <cstdlib>
#include <time.h>
extern "C" {
#include <libavformat/avformat.h>
#include <libavdevice/avdevice.h>
#include <libavcodec/avcodec.h>
#include <libavutil/dict.h>
#include <libswscale/swscale.h>
}

namespace fthr {

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

    fmt_ctx_ = nullptr;
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
    avcodec_parameters_to_context(dec_ctx_, cp);
    if (avcodec_open2(dec_ctx_, dec, nullptr) < 0) {
        avformat_close_input(&fmt_ctx_);
        return false;
    }

    native_w_ = static_cast<uint32_t>(dec_ctx_->width);
    native_h_ = static_cast<uint32_t>(dec_ctx_->height);

    // Pre-create sws context for BGR0 conversion
    sws_ = sws_getContext(
        static_cast<int>(native_w_), static_cast<int>(native_h_), dec_ctx_->pix_fmt,
        static_cast<int>(native_w_), static_cast<int>(native_h_), AV_PIX_FMT_BGR0,
        SWS_BILINEAR, nullptr, nullptr, nullptr);
    if (!sws_) {
        avcodec_free_context(&dec_ctx_);
        avformat_close_input(&fmt_ctx_);
        return false;
    }

    pkt_   = av_packet_alloc();
    frame_ = av_frame_alloc();
    buf_.resize(static_cast<size_t>(native_w_) * native_h_ * 4, 0);

    std::cerr << "[X11Backend] Ready: " << native_w_ << "x" << native_h_ << std::endl;
    return true;
}

bool X11Backend::CaptureFrame(RawFrame& out) {
    while (true) {
        int ret = av_read_frame(fmt_ctx_, pkt_);
        if (ret < 0) return false;
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

    // If resolution changed (e.g. screen resize), recreate sws context
    if (static_cast<uint32_t>(frame_->width)  != native_w_ ||
        static_cast<uint32_t>(frame_->height) != native_h_) {
        native_w_ = static_cast<uint32_t>(frame_->width);
        native_h_ = static_cast<uint32_t>(frame_->height);
        sws_freeContext(sws_);
        sws_ = sws_getContext(
            frame_->width, frame_->height,
            static_cast<AVPixelFormat>(frame_->format),
            frame_->width, frame_->height, AV_PIX_FMT_BGR0,
            SWS_BILINEAR, nullptr, nullptr, nullptr);
        buf_.resize(static_cast<size_t>(native_w_) * native_h_ * 4, 0);
        if (!sws_) { av_frame_unref(frame_); return false; }
    }

    uint8_t* dst[1]  = { buf_.data() };
    int      lns[1]  = { static_cast<int>(native_w_ * 4) };
    sws_scale(sws_, frame_->data, frame_->linesize,
              0, static_cast<int>(native_h_), dst, lns);
    av_frame_unref(frame_);

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    out.data         = buf_.data();
    out.stride       = native_w_ * 4;
    out.width        = native_w_;
    out.height       = native_h_;
    out.av_pix_fmt   = AV_PIX_FMT_BGR0;
    out.timestamp_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + ts.tv_nsec;
    return true;
}

void X11Backend::Shutdown() {
    if (sws_)     { sws_freeContext(sws_);        sws_     = nullptr; }
    if (frame_)   { av_frame_free(&frame_);        frame_   = nullptr; }
    if (pkt_)     { av_packet_free(&pkt_);         pkt_     = nullptr; }
    if (dec_ctx_) { avcodec_free_context(&dec_ctx_); dec_ctx_ = nullptr; }
    if (fmt_ctx_) { avformat_close_input(&fmt_ctx_); fmt_ctx_ = nullptr; }
    buf_.clear();
}

} // namespace fthr

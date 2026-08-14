#include "ring_buffer.h"
#include "save_clip.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/imgutils.h>
}

namespace {

constexpr int kFps = 30;
constexpr int kFrameCount = 12 * kFps;
constexpr int64_t kNanosecondsPerSecond = 1'000'000'000LL;

void receive_packets(
    AVCodecContext* encoder,
    AVPacket* packet,
    fthr::EncodedRingBuffer& ring) {
    while (true) {
        const int ret = avcodec_receive_packet(encoder, packet);
        if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF) return;
        assert(ret >= 0);
        assert(packet->pts != AV_NOPTS_VALUE);

        fthr::EncodedPacket encoded;
        encoded.data.assign(packet->data, packet->data + packet->size);
        encoded.pts = packet->pts;
        encoded.dts = packet->dts;
        encoded.is_keyframe = (packet->flags & AV_PKT_FLAG_KEY) != 0;
        encoded.wall_time_ns = packet->pts * kNanosecondsPerSecond / kFps;
        ring.Push(std::move(encoded));
        av_packet_unref(packet);
    }
}

double probe_video_duration(const std::filesystem::path& path) {
    AVFormatContext* input = nullptr;
    assert(avformat_open_input(&input, path.string().c_str(), nullptr, nullptr) >= 0);
    assert(avformat_find_stream_info(input, nullptr) >= 0);

    const int stream_index = av_find_best_stream(
        input, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    assert(stream_index >= 0);
    const AVStream* stream = input->streams[stream_index];
    assert(stream->start_time == 0);
    assert(stream->duration != AV_NOPTS_VALUE);
    const double duration = stream->duration * av_q2d(stream->time_base);

    AVPacket* packet = av_packet_alloc();
    assert(packet);
    int64_t first_pts = AV_NOPTS_VALUE;
    while (av_read_frame(input, packet) >= 0) {
        if (packet->stream_index == stream_index && first_pts == AV_NOPTS_VALUE)
            first_pts = packet->pts;
        av_packet_unref(packet);
    }
    av_packet_free(&packet);
    avformat_close_input(&input);

    // The mux retains the prior keyframe for decode, but presents from t=0.
    assert(first_pts < 0);
    return duration;
}

} // namespace

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::filesystem::path output = argv[1];
    std::error_code ignored;
    std::filesystem::remove(output, ignored);
    std::filesystem::remove(output.string() + ".partial", ignored);

    const AVCodec* codec = avcodec_find_encoder_by_name("libopenh264");
    assert(codec);
    AVCodecContext* encoder = avcodec_alloc_context3(codec);
    assert(encoder);
    encoder->width = 160;
    encoder->height = 90;
    encoder->pix_fmt = AV_PIX_FMT_YUV420P;
    encoder->time_base = {1, kFps};
    encoder->framerate = {kFps, 1};
    encoder->gop_size = 4 * kFps;
    encoder->max_b_frames = 0;
    encoder->bit_rate = 300'000;
    encoder->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
    assert(avcodec_open2(encoder, codec, nullptr) >= 0);

    AVFrame* frame = av_frame_alloc();
    AVPacket* packet = av_packet_alloc();
    assert(frame && packet);
    frame->format = encoder->pix_fmt;
    frame->width = encoder->width;
    frame->height = encoder->height;
    assert(av_frame_get_buffer(frame, 32) >= 0);

    fthr::EncodedRingBuffer ring(17'000, kFps);
    for (int i = 0; i < kFrameCount; ++i) {
        assert(av_frame_make_writable(frame) >= 0);
        for (int y = 0; y < frame->height; ++y)
            std::fill_n(frame->data[0] + y * frame->linesize[0], frame->width,
                        static_cast<uint8_t>(16 + (i % 200)));
        for (int y = 0; y < frame->height / 2; ++y) {
            std::fill_n(frame->data[1] + y * frame->linesize[1], frame->width / 2, 128);
            std::fill_n(frame->data[2] + y * frame->linesize[2], frame->width / 2, 128);
        }
        frame->pts = i;
        assert(avcodec_send_frame(encoder, frame) >= 0);
        receive_packets(encoder, packet, ring);
    }
    assert(avcodec_send_frame(encoder, nullptr) >= 0);
    receive_packets(encoder, packet, ring);

    std::vector<uint8_t> extradata(
        encoder->extradata, encoder->extradata + encoder->extradata_size);
    const auto snapshot = ring.TakeSnapshot(5'000, 12 * kNanosecondsPerSecond);
    assert(snapshot.full_history);
    assert(!snapshot.packets.empty());
    assert(snapshot.packets.front().is_keyframe);
    assert(snapshot.packets.front().pts < snapshot.presentation_start_pts);
    assert(snapshot.presentation_start_pts == 7 * kFps);

    std::string error;
    const bool saved = fthr::save_clip_to_file(
        output.string(),
        snapshot.packets,
        snapshot.presentation_start_pts,
        {},
        48'000,
        2,
        extradata,
        kFps,
        encoder->width,
        encoder->height,
        encoder->codec_id,
        nullptr,
        &error);
    if (!saved) std::cerr << error << '\n';
    assert(saved);

    av_packet_free(&packet);
    av_frame_free(&frame);
    avcodec_free_context(&encoder);

    const double duration = probe_video_duration(output);
    const double frame_tolerance = 1.0 / kFps;
    assert(std::abs(duration - 5.0) <= frame_tolerance);
    std::cout << "actual MP4 duration=" << duration
              << "s, expected=5s, physical keyframe pre-roll retained\n";
    return 0;
}

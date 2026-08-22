// AUDIT-050 isolated FFmpeg audio decode/mix probe.
//
// Decodes all AAC tracks in one native process, mixes selected stems into
// canonical float32 stereo, and reports CPU/RMS. It is deliberately not wired
// to QMediaPlayer or the production viewer.

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/channel_layout.h>
#include <libavutil/error.h>
#include <libavutil/samplefmt.h>
#include <libavutil/timestamp.h>
#include <libswresample/swresample.h>
}

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

namespace {

constexpr int kRate = 48'000;
constexpr int kChannels = 2;

std::string ErrorText(int error) {
    char buffer[AV_ERROR_MAX_STRING_SIZE]{};
    av_strerror(error, buffer, sizeof(buffer));
    return buffer;
}

bool DecodeFrame(AVCodecContext* codec, SwrContext* swr, AVFrame* frame,
                 std::vector<float>& output) {
    while (true) {
        const int ret = avcodec_receive_frame(codec, frame);
        if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF) return true;
        if (ret < 0) {
            std::cerr << "decode frame failed: " << ErrorText(ret) << '\n';
            return false;
        }
        const int capacity = av_rescale_rnd(
            swr_get_delay(swr, codec->sample_rate) + frame->nb_samples,
            kRate, codec->sample_rate, AV_ROUND_UP);
        std::vector<float> converted(static_cast<size_t>(capacity) * kChannels);
        uint8_t* out_planes[] = {
            reinterpret_cast<uint8_t*>(converted.data()), nullptr};
        const int samples = swr_convert(
            swr, out_planes, capacity,
            const_cast<const uint8_t**>(frame->extended_data),
            frame->nb_samples);
        if (samples < 0) {
            std::cerr << "resample failed: " << ErrorText(samples) << '\n';
            return false;
        }
        converted.resize(static_cast<size_t>(samples) * kChannels);
        output.insert(output.end(), converted.begin(), converted.end());
    }
}

bool DecodeAudioOrdinal(const std::string& path, unsigned ordinal,
                        std::vector<float>& output) {
    AVFormatContext* format = nullptr;
    int ret = avformat_open_input(&format, path.c_str(), nullptr, nullptr);
    if (ret < 0) {
        std::cerr << "open failed: " << ErrorText(ret) << '\n';
        return false;
    }
    ret = avformat_find_stream_info(format, nullptr);
    if (ret < 0) {
        std::cerr << "stream info failed: " << ErrorText(ret) << '\n';
        avformat_close_input(&format);
        return false;
    }
    int stream_index = -1;
    unsigned seen = 0;
    for (unsigned i = 0; i < format->nb_streams; ++i) {
        if (format->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO) {
            if (seen++ == ordinal) {
                stream_index = static_cast<int>(i);
                break;
            }
        }
    }
    if (stream_index < 0) {
        avformat_close_input(&format);
        return false;
    }
    const AVCodecParameters* parameters =
        format->streams[stream_index]->codecpar;
    const AVCodec* decoder = avcodec_find_decoder(parameters->codec_id);
    AVCodecContext* codec = decoder ? avcodec_alloc_context3(decoder) : nullptr;
    if (!codec || avcodec_parameters_to_context(codec, parameters) < 0
        || avcodec_open2(codec, decoder, nullptr) < 0) {
        std::cerr << "decoder setup failed for ordinal " << ordinal << '\n';
        avcodec_free_context(&codec);
        avformat_close_input(&format);
        return false;
    }
    AVChannelLayout output_layout;
    av_channel_layout_default(&output_layout, kChannels);
    SwrContext* swr = nullptr;
    ret = swr_alloc_set_opts2(
        &swr, &output_layout, AV_SAMPLE_FMT_FLT, kRate,
        &codec->ch_layout, codec->sample_fmt, codec->sample_rate,
        0, nullptr);
    av_channel_layout_uninit(&output_layout);
    if (ret < 0 || !swr || swr_init(swr) < 0) {
        std::cerr << "resampler setup failed\n";
        swr_free(&swr);
        avcodec_free_context(&codec);
        avformat_close_input(&format);
        return false;
    }
    AVPacket* packet = av_packet_alloc();
    AVFrame* frame = av_frame_alloc();
    if (!packet || !frame) {
        av_packet_free(&packet);
        av_frame_free(&frame);
        swr_free(&swr);
        avcodec_free_context(&codec);
        avformat_close_input(&format);
        return false;
    }
    while ((ret = av_read_frame(format, packet)) >= 0) {
        if (packet->stream_index == stream_index) {
            ret = avcodec_send_packet(codec, packet);
            if (ret >= 0 && !DecodeFrame(codec, swr, frame, output)) ret = -1;
        }
        av_packet_unref(packet);
        if (ret < 0) break;
    }
    if (ret >= 0 || ret == AVERROR_EOF) {
        if (avcodec_send_packet(codec, nullptr) >= 0) {
            DecodeFrame(codec, swr, frame, output);
        }
        const int delay = swr_get_delay(swr, codec->sample_rate);
        if (delay > 0) {
            std::vector<float> tail(static_cast<size_t>(delay + 1024) * kChannels);
            uint8_t* planes[] = {reinterpret_cast<uint8_t*>(tail.data()), nullptr};
            const int samples = swr_convert(swr, planes, delay + 1024, nullptr, 0);
            if (samples > 0) output.insert(output.end(), tail.begin(),
                                            tail.begin() + samples * kChannels);
        }
    }
    av_packet_free(&packet);
    av_frame_free(&frame);
    swr_free(&swr);
    avcodec_free_context(&codec);
    avformat_close_input(&format);
    return ret >= 0 || ret == AVERROR_EOF;
}

double Rms(const std::vector<float>& samples) {
    if (samples.empty()) return 0.0;
    long double sum = 0.0;
    for (float sample : samples) sum += sample * sample;
    return std::sqrt(static_cast<double>(sum / samples.size()));
}

std::vector<float> Mix(const std::vector<std::vector<float>>& tracks,
                       bool mute_first) {
    size_t sample_count = 0;
    for (const auto& track : tracks) sample_count = std::max(sample_count, track.size());
    std::vector<float> mixed(sample_count, 0.0f);
    for (size_t track_index = 0; track_index < tracks.size(); ++track_index) {
        const float gain = mute_first && track_index == 0 ? 0.0f : 1.0f;
        for (size_t i = 0; i < tracks[track_index].size(); ++i) {
            mixed[i] += tracks[track_index][i] * gain;
        }
    }
    return mixed;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: ffmpeg_audio_mix_probe.exe <mp4> <csv>\n";
        return 64;
    }
    AVFormatContext* probe = nullptr;
    if (avformat_open_input(&probe, argv[1], nullptr, nullptr) < 0
        || avformat_find_stream_info(probe, nullptr) < 0) {
        std::cerr << "cannot inspect input\n";
        avformat_close_input(&probe);
        return 2;
    }
    unsigned audio_count = 0;
    for (unsigned i = 0; i < probe->nb_streams; ++i) {
        audio_count += probe->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO;
    }
    avformat_close_input(&probe);
    if (audio_count < 2) {
        std::cerr << "input needs a default mix plus at least one stem\n";
        return 2;
    }
    std::ofstream report(argv[2]);
    report << "stems,samples,decode_mix_ms,rms_all,rms_first_muted\n";
    for (unsigned stems : {1u, 4u, 8u}) {
        if (audio_count < stems + 1) continue;
        const auto start = std::chrono::steady_clock::now();
        std::vector<std::vector<float>> tracks;
        for (unsigned stem = 0; stem < stems; ++stem) {
            std::vector<float> decoded;
            if (!DecodeAudioOrdinal(argv[1], stem + 1, decoded)) return 3;
            tracks.push_back(std::move(decoded));
        }
        const auto mixed = Mix(tracks, false);
        const auto muted = Mix(tracks, true);
        const auto end = std::chrono::steady_clock::now();
        const double elapsed = std::chrono::duration<double, std::milli>(end - start).count();
        report << stems << ',' << mixed.size() / kChannels << ','
               << std::fixed << std::setprecision(3) << elapsed << ','
               << Rms(mixed) << ',' << Rms(muted) << '\n';
        std::cout << "MIX_CASE stems=" << stems
                  << " samples=" << mixed.size() / kChannels
                  << " decode_mix_ms=" << elapsed
                  << " rms_all=" << Rms(mixed)
                  << " rms_first_muted=" << Rms(muted) << '\n';
    }
    return report.good() ? 0 : 4;
}

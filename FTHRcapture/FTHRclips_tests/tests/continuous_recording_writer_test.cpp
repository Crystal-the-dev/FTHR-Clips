#include "continuous_recording_writer.h"
#include "audio_encoder.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <thread>
#include <vector>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
}

namespace {

constexpr int kFps = 30;
int recording_checks = 0;

void CheckRecording(bool condition, const char* message) {
    ++recording_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

bool ReceivePackets(
    AVCodecContext* encoder,
    AVPacket* packet,
    fthr::ContinuousRecordingWriter& writer) {
    while (true) {
        const int result = avcodec_receive_packet(encoder, packet);
        if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) return true;
        if (result < 0 || packet->pts == AV_NOPTS_VALUE) return false;
        const bool accepted = writer.PushVideo(
            packet->data,
            static_cast<uint32_t>(packet->size),
            packet->pts,
            (packet->flags & AV_PKT_FLAG_KEY) != 0);
        av_packet_unref(packet);
        if (!accepted) return false;
    }
}

bool ProbeMedia(const std::filesystem::path& path, bool expect_audio) {
    AVFormatContext* input = nullptr;
    if (avformat_open_input(&input, path.string().c_str(), nullptr, nullptr) < 0)
        return false;
    const bool found_info = avformat_find_stream_info(input, nullptr) >= 0;
    const int video_index = found_info ? av_find_best_stream(
        input, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0) : -1;
    const int audio_index = found_info ? av_find_best_stream(
        input, AVMEDIA_TYPE_AUDIO, -1, -1, nullptr, 0) : -1;
    const AVCodecParameters* video_parameters = video_index >= 0
        ? input->streams[video_index]->codecpar : nullptr;
    const bool correct_sdr_color = video_parameters
        && video_parameters->color_range == AVCOL_RANGE_MPEG
        && video_parameters->color_primaries == AVCOL_PRI_BT709
        && video_parameters->color_trc == AVCOL_TRC_BT709
        && video_parameters->color_space == AVCOL_SPC_BT709;
    bool read_video = false;
    bool read_audio = false;
    AVPacket* packet = av_packet_alloc();
    if (packet) {
        while (av_read_frame(input, packet) >= 0) {
            if (packet->stream_index == video_index && packet->size > 0)
                read_video = true;
            if (packet->stream_index == audio_index && packet->size > 0)
                read_audio = true;
            av_packet_unref(packet);
            if (read_video && (!expect_audio || read_audio)) break;
        }
        av_packet_free(&packet);
    }
    avformat_close_input(&input);
    return video_index >= 0 && read_video && correct_sdr_color
        && (!expect_audio || (audio_index >= 0 && read_audio));
}

size_t CountBox(const std::vector<uint8_t>& bytes, const char name[4]) {
    size_t count = 0;
    for (size_t index = 4; index + 4 <= bytes.size(); ++index) {
        if (std::equal(name, name + 4, bytes.begin() + index)) ++count;
    }
    return count;
}

std::filesystem::path MakeRecordingPath(const char* name) {
    const auto stamp = std::chrono::high_resolution_clock::now()
        .time_since_epoch().count();
    return std::filesystem::temp_directory_path()
        / (std::string("fthr-") + name + "-" + std::to_string(stamp) + ".mp4");
}

void FragmentedRecordingSurvivesAnUnfinishedCopy() {
    const AVCodec* codec = avcodec_find_encoder_by_name("libopenh264");
    CheckRecording(codec != nullptr, "bundled OpenH264 encoder is available");

    AVCodecContext* encoder = avcodec_alloc_context3(codec);
    CheckRecording(encoder != nullptr, "test encoder context allocated");
    encoder->width = 160;
    encoder->height = 90;
    encoder->pix_fmt = AV_PIX_FMT_YUV420P;
    encoder->time_base = {1, kFps};
    encoder->framerate = {kFps, 1};
    encoder->gop_size = kFps;
    encoder->max_b_frames = 0;
    encoder->bit_rate = 300'000;
    encoder->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
    CheckRecording(avcodec_open2(encoder, codec, nullptr) >= 0,
        "test H.264 encoder opened");

    fthr::EncodedVideoConfig config;
    config.codec = fthr::VideoCodec::H264;
    config.width = static_cast<uint32_t>(encoder->width);
    config.height = static_cast<uint32_t>(encoder->height);
    config.frame_rate = {kFps, 1};
    config.time_base = {1, kFps};
    config.bitrate_kbps = 300;
    config.max_keyframe_interval_frames = kFps;
    config.packet_format = fthr::EncodedPacketFormat::LengthPrefixedNalUnits;
    config.codec_extradata.assign(
        encoder->extradata, encoder->extradata + encoder->extradata_size);
    CheckRecording(!config.codec_extradata.empty(),
        "test H.264 decoder configuration is available");

    const auto output = MakeRecordingPath("recording-complete");
    const auto interrupted = MakeRecordingPath("recording-interrupted");
    fthr::ContinuousRecordingWriter writer;
    fthr::AudioEncoder audio_encoder;
    bool audio_packets_accepted = true;
    CheckRecording(audio_encoder.Initialize(
        48'000, 2, 128,
        [&writer, &audio_packets_accepted](
            const uint8_t* data, uint32_t size, int64_t pts) {
            if (!writer.PushAudio(data, size, pts, 1024))
                audio_packets_accepted = false;
        }), "test AAC encoder starts");
    fthr::ContinuousRecordingAudioConfig audio_config;
    audio_config.sample_rate = audio_encoder.GetSampleRate();
    audio_config.channels = audio_encoder.GetChannels();
    audio_config.codec_extradata = audio_encoder.GetExtradata();
    CheckRecording(audio_config.valid(),
        "test AAC decoder configuration is available");
    CheckRecording(writer.Start(output, config, audio_config),
        "fragmented recording writer starts");

    AVFrame* frame = av_frame_alloc();
    AVPacket* packet = av_packet_alloc();
    CheckRecording(frame && packet, "test frame and packet allocated");
    frame->format = encoder->pix_fmt;
    frame->width = encoder->width;
    frame->height = encoder->height;
    CheckRecording(av_frame_get_buffer(frame, 32) >= 0,
        "test frame storage allocated");
    std::vector<float> audio_samples(1600 * 2, 0.0f);

    // Twelve seconds closes eleven independent one-second fragments while
    // still running quickly in the native release suite.
    for (int index = 0; index < 12 * kFps; ++index) {
        CheckRecording(av_frame_make_writable(frame) >= 0,
            "test frame remains writable");
        for (int row = 0; row < frame->height; ++row) {
            std::fill_n(
                frame->data[0] + row * frame->linesize[0],
                frame->width,
                static_cast<uint8_t>(16 + (index % 180)));
        }
        for (int row = 0; row < frame->height / 2; ++row) {
            std::fill_n(
                frame->data[1] + row * frame->linesize[1],
                frame->width / 2, static_cast<uint8_t>(128));
            std::fill_n(
                frame->data[2] + row * frame->linesize[2],
                frame->width / 2, static_cast<uint8_t>(128));
        }
        frame->pts = index;
        CheckRecording(avcodec_send_frame(encoder, frame) >= 0,
            "test frame submitted");
        CheckRecording(ReceivePackets(encoder, packet, writer),
            "encoded packet accepted by recording writer");
        audio_encoder.EncodeSamples(
            audio_samples.data(), static_cast<uint32_t>(audio_samples.size()));
    }
    CheckRecording(avcodec_send_frame(encoder, nullptr) >= 0,
        "test encoder flushed");
    CheckRecording(ReceivePackets(encoder, packet, writer),
        "flushed packets accepted by recording writer");
    audio_encoder.Finalize();
    CheckRecording(audio_packets_accepted,
        "AAC packets are accepted by recording writer");

    // Copy the still-open file after the worker crosses the final keyframe.
    // The copy has no trailer and represents sudden process or power loss.
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    {
        std::ifstream source(output, std::ios::binary);
        std::ofstream destination(interrupted, std::ios::binary);
        destination << source.rdbuf();
        CheckRecording(source.good() || source.eof(),
            "active fragmented recording can be copied for crash simulation");
        CheckRecording(destination.good(),
            "unfinished recording copy is written");
    }

    CheckRecording(writer.Stop(), "normal recording close succeeds");
    CheckRecording(ProbeMedia(output, true),
        "normally closed video plus AAC recording is playable");
    CheckRecording(ProbeMedia(interrupted, true),
        "video plus AAC recording copied before Stop is independently playable");

    std::ifstream file(output, std::ios::binary);
    const std::vector<uint8_t> bytes{
        std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
    const char moov[4] = {'m', 'o', 'o', 'v'};
    const char moof[4] = {'m', 'o', 'o', 'f'};
    CheckRecording(CountBox(bytes, moov) >= 1,
        "recovery metadata is present at recording start");
    CheckRecording(CountBox(bytes, moof) >= 2,
        "recording contains multiple independent media fragments");

    av_packet_free(&packet);
    av_frame_free(&frame);
    avcodec_free_context(&encoder);
    std::error_code ignored;
    std::filesystem::remove(output, ignored);
    std::filesystem::remove(interrupted, ignored);
}

void MissingDecoderConfigurationFailsBeforeCreatingAWriter() {
    fthr::EncodedVideoConfig config;
    config.codec = fthr::VideoCodec::H264;
    config.width = 1920;
    config.height = 1080;
    config.frame_rate = {60, 1};
    config.time_base = {1, 60};
    config.packet_format = fthr::EncodedPacketFormat::LengthPrefixedNalUnits;

    fthr::ContinuousRecordingWriter writer;
    const auto output = MakeRecordingPath("recording-invalid");
    CheckRecording(!writer.Start(output, config),
        "writer refuses a stream with no decoder configuration");
    CheckRecording(!std::filesystem::exists(output),
        "invalid start never creates a misleading recording file");
}

} // namespace

int RunContinuousRecordingWriterTests() {
    FragmentedRecordingSurvivesAnUnfinishedCopy();
    MissingDecoderConfigurationFailsBeforeCreatingAWriter();
    std::cout << "Continuous recording writer tests: " << recording_checks
              << " checks passed" << std::endl;
    return recording_checks;
}

// In-process FFmpeg decoder/mixer for the FTHR clip viewer.
//
// The bridge demuxes every requested stream through one AVFormatContext,
// resamples only when necessary to 48 kHz float32 stereo, retains a bounded
// packet-derived segment queue, then supplies a single mixed PCM stream. It
// is deliberately synchronous: the Python controller calls it only on its
// decoder worker, never from the Qt UI or QAudioSink pull callback.

#include "ffmpeg_playback_bridge.h"

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/channel_layout.h>
#include <libavutil/error.h>
#include <libavutil/samplefmt.h>
#include <libavutil/mathematics.h>
#include <libswresample/swresample.h>
}

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr int kSampleRate = 48'000;
constexpr int kChannels = 2;
constexpr float kLimiterCeiling = 0.98f;
// Keep every demux pass bounded.  This permits normal interleaved streams to
// fill a mixer block while avoiding a large decoded-audio runway when a
// selected stream is missing or malformed.
constexpr int kReadPacketBudget = 64;

std::string ErrorText(const int result) {
    char buffer[AV_ERROR_MAX_STRING_SIZE]{};
    av_strerror(result, buffer, sizeof(buffer));
    return buffer;
}

void WriteError(char* destination, const size_t capacity,
                const std::string& message) {
    if (!destination || capacity == 0) return;
    const size_t copied = std::min(capacity - 1, message.size());
    std::memcpy(destination, message.data(), copied);
    destination[copied] = '\0';
}

int ResampleCapacity(const int64_t input_samples, const int source_rate) {
    const int64_t converted = av_rescale_rnd(
        input_samples, kSampleRate, source_rate, AV_ROUND_UP) + 32;
    return static_cast<int>(std::clamp<int64_t>(converted, 1, INT_MAX));
}

struct Segment {
    int64_t first_sample = 0;
    std::vector<float> samples;

    [[nodiscard]] int64_t FrameCount() const {
        return static_cast<int64_t>(samples.size() / kChannels);
    }

    [[nodiscard]] int64_t LastSampleExclusive() const {
        return first_sample + FrameCount();
    }
};

struct DecoderState {
    int stream_index = -1;
    AVRational time_base{};
    AVCodecContext* codec = nullptr;
    SwrContext* resampler = nullptr;
    std::deque<Segment> segments;
    int64_t decoded_until = std::numeric_limits<int64_t>::min();
    int64_t fallback_next_sample = 0;
    bool input_eof = false;
    bool flushed = false;
    bool failed = false;

    ~DecoderState() {
        swr_free(&resampler);
        avcodec_free_context(&codec);
    }

    DecoderState() = default;
    DecoderState(const DecoderState&) = delete;
    DecoderState& operator=(const DecoderState&) = delete;
};

class Mixer {
public:
    ~Mixer() {
        av_frame_free(&frame_);
        av_packet_free(&packet_);
        avformat_close_input(&format_);
    }

    bool Open(const char* path, const int* stream_indexes, const int stream_count,
              std::string& error) {
        if (!path || !*path || !stream_indexes || stream_count < 1 || stream_count > 16) {
            error = "invalid playback stream selection";
            return false;
        }
        int result = avformat_open_input(&format_, path, nullptr, nullptr);
        if (result < 0) {
            error = "could not open clip: " + ErrorText(result);
            return false;
        }
        result = avformat_find_stream_info(format_, nullptr);
        if (result < 0) {
            error = "could not inspect clip streams: " + ErrorText(result);
            return false;
        }
        for (int input_index = 0; input_index < stream_count; ++input_index) {
            const int stream_index = stream_indexes[input_index];
            if (stream_index < 0 || stream_index >= static_cast<int>(format_->nb_streams)) {
                continue;
            }
            AVStream* stream = format_->streams[stream_index];
            if (stream->codecpar->codec_type != AVMEDIA_TYPE_AUDIO) continue;
            if (std::any_of(decoders_.begin(), decoders_.end(),
                            [stream_index](const auto& decoder) {
                                return decoder->stream_index == stream_index;
                            })) {
                continue;
            }
            auto decoder = std::make_unique<DecoderState>();
            if (!ConfigureDecoder(*decoder, stream_index, *stream, error)) continue;
            decoders_.push_back(std::move(decoder));
        }
        if (decoders_.empty()) {
            if (error.empty()) error = "no selected audio stream could be decoded";
            return false;
        }
        packet_ = av_packet_alloc();
        frame_ = av_frame_alloc();
        if (!packet_ || !frame_) {
            error = "could not allocate FFmpeg playback packet/frame";
            return false;
        }
        return true;
    }

    int Pull(float* output, const int requested_frames,
             const float* gains, const int gain_count, const float master_gain) {
        if (!output || requested_frames <= 0 || gain_count != static_cast<int>(decoders_.size())) {
            return -1;
        }
        const int64_t first = cursor_sample_;
        const int64_t end = first + requested_frames;
        if (!DecodeUntil(end)) return -1;

        bool has_pcm_at_or_after_cursor = false;
        for (const auto& decoder : decoders_) {
            for (const Segment& segment : decoder->segments) {
                if (segment.LastSampleExclusive() > first) {
                    has_pcm_at_or_after_cursor = true;
                    break;
                }
            }
            if (has_pcm_at_or_after_cursor) break;
        }
        if (input_eof_ && !has_pcm_at_or_after_cursor) return 0;

        std::fill(output, output + static_cast<size_t>(requested_frames) * kChannels, 0.0f);
        int active_sources = 0;
        for (int index = 0; index < gain_count; ++index) {
            active_sources += gains[index] > 0.0f && !decoders_[index]->failed;
        }
        const float headroom = 1.0f / std::sqrt(static_cast<float>(std::max(1, active_sources)));

        for (int index = 0; index < gain_count; ++index) {
            const float gain = std::max(0.0f, gains[index]);
            if (gain == 0.0f) continue;
            MixDecoder(*decoders_[index], first, end, gain, output);
        }
        const float master = std::clamp(master_gain, 0.0f, 1.0f);
        for (int frame = 0; frame < requested_frames; ++frame) {
            for (int channel = 0; channel < kChannels; ++channel) {
                const size_t sample = static_cast<size_t>(frame) * kChannels + channel;
                output[sample] = std::clamp(output[sample] * headroom,
                                            -kLimiterCeiling, kLimiterCeiling) * master;
            }
        }
        cursor_sample_ = end;
        return requested_frames;
    }

    bool Seek(const int64_t position_ms, std::string& error) {
        const int64_t clamped_ms = std::max<int64_t>(0, position_ms);
        const int result = av_seek_frame(
            format_, -1, av_rescale_q(clamped_ms, AVRational{1, 1000}, AV_TIME_BASE_Q),
            AVSEEK_FLAG_BACKWARD);
        if (result < 0) {
            error = "could not seek clip audio: " + ErrorText(result);
            return false;
        }
        avformat_flush(format_);
        cursor_sample_ = av_rescale_q(clamped_ms, AVRational{1, 1000},
                                      AVRational{1, kSampleRate});
        input_eof_ = false;
        for (const auto& decoder : decoders_) {
            avcodec_flush_buffers(decoder->codec);
            swr_close(decoder->resampler);
            if (swr_init(decoder->resampler) < 0) {
                error = "could not reset clip audio resampler";
                return false;
            }
            decoder->segments.clear();
            decoder->decoded_until = std::numeric_limits<int64_t>::min();
            decoder->fallback_next_sample = cursor_sample_;
            decoder->input_eof = false;
            decoder->flushed = false;
            decoder->failed = false;
        }
        return true;
    }

    [[nodiscard]] bool IsEof() const { return input_eof_; }
    [[nodiscard]] bool SourceFailed(const int source_ordinal) const {
        return source_ordinal < 0 || source_ordinal >= static_cast<int>(decoders_.size())
            || decoders_[source_ordinal]->failed;
    }

private:
    bool ConfigureDecoder(DecoderState& decoder, const int stream_index,
                          AVStream& stream, std::string& error) {
        const AVCodec* codec = avcodec_find_decoder(stream.codecpar->codec_id);
        if (!codec) {
            error = "no FFmpeg decoder for selected audio stream";
            return false;
        }
        decoder.codec = avcodec_alloc_context3(codec);
        if (!decoder.codec) {
            error = "could not allocate selected audio decoder";
            return false;
        }
        int result = avcodec_parameters_to_context(decoder.codec, stream.codecpar);
        // The controller owns exactly one decoder worker. Do not let each
        // selected AAC stem create an unbounded FFmpeg worker pool.
        decoder.codec->thread_count = 1;
        if (result < 0 || (result = avcodec_open2(decoder.codec, codec, nullptr)) < 0) {
            error = "could not open selected audio decoder: " + ErrorText(result);
            return false;
        }
        AVChannelLayout output_layout{};
        av_channel_layout_default(&output_layout, kChannels);
        result = swr_alloc_set_opts2(
            &decoder.resampler, &output_layout, AV_SAMPLE_FMT_FLT, kSampleRate,
            &decoder.codec->ch_layout, decoder.codec->sample_fmt,
            decoder.codec->sample_rate, 0, nullptr);
        av_channel_layout_uninit(&output_layout);
        if (result < 0 || !decoder.resampler || swr_init(decoder.resampler) < 0) {
            error = "could not create selected audio resampler";
            return false;
        }
        decoder.stream_index = stream_index;
        decoder.time_base = stream.time_base;
        return true;
    }

    bool DecodeUntil(const int64_t target_sample) {
        if (ReadyFor(target_sample)) return true;
        for (int count = 0; count < kReadPacketBudget; ++count) {
            const int result = av_read_frame(format_, packet_);
            if (result == AVERROR_EOF) {
                input_eof_ = true;
                for (const auto& decoder : decoders_) FlushDecoder(*decoder);
                return true;
            }
            if (result < 0) return false;
            for (const auto& decoder : decoders_) {
                if (decoder->stream_index == packet_->stream_index) {
                    if (!SendPacket(*decoder, packet_)) decoder->failed = true;
                    break;
                }
            }
            av_packet_unref(packet_);
            if (ReadyFor(target_sample)) return true;
        }
        // The queue remains bounded even for a malformed stream. A later pull
        // continues decoding; meanwhile QAudioSink receives an intentional
        // short silence instead of blocking an audio callback indefinitely.
        return true;
    }

    [[nodiscard]] bool ReadyFor(const int64_t target_sample) const {
        return std::all_of(decoders_.begin(), decoders_.end(),
                           [target_sample](const auto& decoder) {
                               return decoder->input_eof || decoder->failed
                                   || decoder->decoded_until >= target_sample;
                           });
    }

    bool SendPacket(DecoderState& decoder, const AVPacket* packet) {
        int result = avcodec_send_packet(decoder.codec, packet);
        if (result == AVERROR(EAGAIN)) {
            if (!ReceiveFrames(decoder)) return false;
            result = avcodec_send_packet(decoder.codec, packet);
        }
        if (result < 0 && result != AVERROR_EOF) return false;
        return ReceiveFrames(decoder);
    }

    void FlushDecoder(DecoderState& decoder) {
        if (decoder.flushed) return;
        decoder.flushed = true;
        decoder.input_eof = true;
        if (!decoder.failed) {
            if (!SendPacket(decoder, nullptr)) decoder.failed = true;
            const int delayed = static_cast<int>(swr_get_delay(
                decoder.resampler, decoder.codec->sample_rate));
            if (delayed > 0 && !decoder.failed) {
                const int capacity = ResampleCapacity(delayed, decoder.codec->sample_rate);
                std::vector<float> tail(static_cast<size_t>(capacity) * kChannels);
                uint8_t* planes[] = {reinterpret_cast<uint8_t*>(tail.data()), nullptr};
                const int converted = swr_convert(decoder.resampler, planes, capacity, nullptr, 0);
                if (converted > 0) {
                    tail.resize(static_cast<size_t>(converted) * kChannels);
                    AppendSegment(decoder, decoder.fallback_next_sample, std::move(tail));
                }
            }
        }
    }

    bool ReceiveFrames(DecoderState& decoder) {
        while (true) {
            const int result = avcodec_receive_frame(decoder.codec, frame_);
            if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) return true;
            if (result < 0) return false;
            const int capacity = ResampleCapacity(
                swr_get_delay(decoder.resampler, decoder.codec->sample_rate) + frame_->nb_samples,
                decoder.codec->sample_rate);
            std::vector<float> converted(static_cast<size_t>(capacity) * kChannels);
            uint8_t* planes[] = {reinterpret_cast<uint8_t*>(converted.data()), nullptr};
            const int samples = swr_convert(
                decoder.resampler, planes, capacity,
                const_cast<const uint8_t**>(frame_->extended_data), frame_->nb_samples);
            if (samples < 0) {
                av_frame_unref(frame_);
                return false;
            }
            converted.resize(static_cast<size_t>(samples) * kChannels);
            int64_t source_timestamp = frame_->best_effort_timestamp;
            if (source_timestamp == AV_NOPTS_VALUE) source_timestamp = frame_->pts;
            const int64_t first = source_timestamp == AV_NOPTS_VALUE
                ? decoder.fallback_next_sample
                : std::max<int64_t>(0, av_rescale_q(source_timestamp, decoder.time_base,
                                                     AVRational{1, kSampleRate}));
            av_frame_unref(frame_);
            AppendSegment(decoder, first, std::move(converted));
        }
    }

    void AppendSegment(DecoderState& decoder, const int64_t first,
                       std::vector<float>&& samples) {
        const int64_t frames = static_cast<int64_t>(samples.size() / kChannels);
        if (frames == 0) return;
        decoder.fallback_next_sample = first + frames;
        decoder.decoded_until = std::max(decoder.decoded_until, first + frames);
        decoder.segments.push_back(Segment{first, std::move(samples)});
    }

    void MixDecoder(DecoderState& decoder, const int64_t first,
                    const int64_t end, const float gain, float* output) {
        while (!decoder.segments.empty()
               && decoder.segments.front().LastSampleExclusive() <= first) {
            decoder.segments.pop_front();
        }
        for (const Segment& segment : decoder.segments) {
            const int64_t overlap_first = std::max(first, segment.first_sample);
            const int64_t overlap_end = std::min(end, segment.LastSampleExclusive());
            if (overlap_first >= overlap_end) continue;
            const size_t source_offset = static_cast<size_t>(overlap_first - segment.first_sample) * kChannels;
            const size_t output_offset = static_cast<size_t>(overlap_first - first) * kChannels;
            const size_t sample_count = static_cast<size_t>(overlap_end - overlap_first) * kChannels;
            for (size_t sample = 0; sample < sample_count; ++sample) {
                output[output_offset + sample] += segment.samples[source_offset + sample] * gain;
            }
        }
    }

    AVFormatContext* format_ = nullptr;
    AVPacket* packet_ = nullptr;
    AVFrame* frame_ = nullptr;
    std::vector<std::unique_ptr<DecoderState>> decoders_;
    int64_t cursor_sample_ = 0;
    bool input_eof_ = false;
};

} // namespace

struct FTHRPlaybackMixer {
    Mixer mixer;
};

extern "C" FTHRPlaybackMixer* fthr_playback_open(
    const char* media_path_utf8, const int* stream_indexes, const int stream_count,
    char* error_text, const size_t error_text_capacity) {
    auto handle = std::make_unique<FTHRPlaybackMixer>();
    std::string error;
    if (!handle->mixer.Open(media_path_utf8, stream_indexes, stream_count, error)) {
        WriteError(error_text, error_text_capacity, error);
        return nullptr;
    }
    return handle.release();
}

extern "C" int fthr_playback_pull(
    FTHRPlaybackMixer* mixer, float* interleaved_float_stereo, const int frame_count,
    const float* gains, const int gain_count, const float master_gain) {
    if (!mixer) return -1;
    return mixer->mixer.Pull(interleaved_float_stereo, frame_count, gains, gain_count, master_gain);
}

extern "C" int fthr_playback_seek(
    FTHRPlaybackMixer* mixer, const int64_t position_milliseconds,
    char* error_text, const size_t error_text_capacity) {
    if (!mixer) {
        WriteError(error_text, error_text_capacity, "playback mixer is not open");
        return 0;
    }
    std::string error;
    if (!mixer->mixer.Seek(position_milliseconds, error)) {
        WriteError(error_text, error_text_capacity, error);
        return 0;
    }
    return 1;
}

extern "C" int fthr_playback_is_eof(const FTHRPlaybackMixer* mixer) {
    return mixer && mixer->mixer.IsEof() ? 1 : 0;
}

extern "C" int fthr_playback_source_failed(
    const FTHRPlaybackMixer* mixer, const int source_ordinal) {
    return mixer && mixer->mixer.SourceFailed(source_ordinal) ? 1 : 0;
}

extern "C" void fthr_playback_close(FTHRPlaybackMixer* mixer) {
    delete mixer;
}

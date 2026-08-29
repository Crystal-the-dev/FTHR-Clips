// Crash-resilient continuous recording from the capture engine's existing
// compressed video and audio packets.
//
// The writer deliberately does not encode, read GPU textures back to the CPU,
// create sidecars, or perform a second remux after recording. It writes a
// fragmented MP4 and commits a completed fragment at every video keyframe.
// Consequently a process or power failure can lose only the open fragment,
// not the entire recording.

#pragma once
#ifndef FTHR_CONTINUOUS_RECORDING_WRITER_H
#define FTHR_CONTINUOUS_RECORDING_WRITER_H

#include "encoded_video_config.h"

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

struct AVFormatContext;
struct AVStream;

namespace fthr {

struct ContinuousRecordingAudioConfig {
    uint32_t sample_rate = 0;
    uint32_t channels = 0;
    std::vector<uint8_t> codec_extradata;

    bool valid() const noexcept {
        return sample_rate > 0 && channels > 0 && !codec_extradata.empty();
    }
};

class ContinuousRecordingWriter {
public:
    ContinuousRecordingWriter() = default;
    ~ContinuousRecordingWriter();

    ContinuousRecordingWriter(const ContinuousRecordingWriter&) = delete;
    ContinuousRecordingWriter& operator=(const ContinuousRecordingWriter&) = delete;

    bool Start(
        const std::filesystem::path& output_path,
        const EncodedVideoConfig& video_config,
        const ContinuousRecordingAudioConfig& audio_config = {});

    bool PushVideo(
        const uint8_t* data,
        uint32_t size,
        int64_t pts,
        bool is_keyframe);
    bool PushAudio(
        const uint8_t* data,
        uint32_t size,
        int64_t pts_samples,
        int64_t duration_samples = 1024);

    // Stops accepting packets, drains the bounded queue, flushes the final
    // fragment, writes the optional trailer, closes the file, and joins.
    bool Stop();

    bool IsRunning() const noexcept {
        return running_.load(std::memory_order_acquire);
    }
    bool HasFailed() const noexcept {
        return failed_.load(std::memory_order_acquire);
    }
    std::string LastError() const;

private:
    enum class PacketKind { Video, Audio };

    struct QueuedPacket {
        PacketKind kind = PacketKind::Video;
        std::vector<uint8_t> data;
        int64_t pts = 0;
        int64_t duration = 0;
        bool is_keyframe = false;
    };

    static constexpr size_t kMaxQueuedBytes = 64u * 1024u * 1024u;

    bool Enqueue(QueuedPacket packet);
    void WriterThread();
    bool WritePacket(const QueuedPacket& packet);
    bool FlushFragment();
    void CloseContainer(bool write_trailer);
    void Fail(std::string message);
    void FailLocked(std::string message);

    EncodedVideoConfig video_config_;
    ContinuousRecordingAudioConfig audio_config_;
    AVFormatContext* format_context_ = nullptr;
    AVStream* video_stream_ = nullptr;
    AVStream* audio_stream_ = nullptr;

    std::deque<QueuedPacket> queue_;
    size_t queued_bytes_ = 0;
    mutable std::mutex mutex_;
    std::condition_variable condition_;
    std::thread thread_;
    bool accepting_ = false;
    std::string last_error_;

    bool video_started_ = false;
    bool audio_started_ = false;
    int64_t first_video_pts_ = 0;
    int64_t first_audio_pts_ = 0;
    uint64_t video_packets_written_ = 0;
    uint64_t audio_packets_written_ = 0;
    std::atomic<bool> running_{false};
    std::atomic<bool> failed_{false};
    std::atomic<bool> audio_enabled_{false};
};

} // namespace fthr

#endif // FTHR_CONTINUOUS_RECORDING_WRITER_H

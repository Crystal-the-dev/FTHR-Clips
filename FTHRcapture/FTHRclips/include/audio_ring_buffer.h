// Legacy raw float32 PCM ring with mutex-protected writes and snapshots.
// Snapshots own their data and can end at the video QPC boundary. Without an
// explicit endpoint, selection uses the configured safety margin. Production
// replay uses the encoded audio packet ring.

#pragma once
#ifndef FTHR_AUDIO_RING_BUFFER_H
#define FTHR_AUDIO_RING_BUFFER_H

#include <cstdint>
#include <vector>
#include <atomic>
#include <mutex>


namespace fthr {


    // Owned interleaved float32 samples and their QPC interval in seconds.
    // MuxEncodedClip intersects this interval with the selected video.
    struct AudioPCMSnapshot {
        std::vector<float> samples;               // Interleaved float32 stereo
        uint32_t           sample_rate = 48000;
        uint32_t           channels = 2;

        // QPC-based wall-clock time (seconds) of the FIRST and LAST samples in
        // this snapshot. Derived from WASAPI's pu64QPCPosition (100ns units).
        // Same clock domain as HardwareEncoder's QPC PTS, so alignment is exact.
        double             qpc_start_s = 0.0;
        double             qpc_end_s = 0.0;

        bool               valid = false;
    };


    class AudioRingBuffer {
    public:
        // capacity_frames: total stereo frames pre-allocated.
        //   Recommended: sample_rate * (buffer_seconds + 4) for headroom.
        //   Default safety: 0.5s (24000 samples at 48kHz) excluded from newest end.
        AudioRingBuffer(uint32_t capacity_frames,
            uint32_t sample_rate = 48000,
            uint32_t channels = 2,
            uint32_t safety_frames = 24000);

        AudioRingBuffer(const AudioRingBuffer&) = delete;
        AudioRingBuffer& operator=(const AudioRingBuffer&) = delete;


        // Store interleaved float32 frames from the capture thread. qpc_100ns is
        // the first frame timestamp from WASAPI; zero permits approximate alignment.
        // volume is linear gain, with 1.0 representing unity.
        void Push(const float* interleaved_data,
            uint32_t      frame_count,
            uint64_t      qpc_100ns,
            float         volume = 1.0f);


        // Copy duration_s ending at end_qpc_s, or apply the legacy safety margin
        // when no endpoint is supplied. The save thread may block during the copy.
        AudioPCMSnapshot TakeSnapshot(
            double duration_s, double end_qpc_s = 0.0) const;


        // Stats
        uint32_t GetSampleRate()     const { return sample_rate_; }
        uint32_t GetChannels()       const { return channels_; }
        uint64_t GetFramesPushed()   const { return head_.load(std::memory_order_relaxed); }
        size_t   GetCapacityFrames() const { return capacity_frames_; }


    private:
        uint32_t capacity_frames_;
        uint32_t sample_rate_;
        uint32_t channels_;
        uint32_t safety_frames_;

        // Flat circular storage: capacity_frames_ * channels_ floats.
        std::vector<float> storage_;

        // Monotonically increasing frame counter.
        // Write slot index = head_ % capacity_frames_.
        std::atomic<uint64_t> head_{ 0 };

        // Number of valid frames written, capped at capacity_frames_.
        std::atomic<uint64_t> frames_written_{ 0 };

        // Per-frame QPC timestamp ring (100-nanosecond units from WASAPI).
        // Indexed the same way as storage_: qpc_ring_[head_ % capacity_frames_].
        // Only one entry written per Push() call (one WASAPI packet = one QPC value).
        // Frames within a packet are assigned by linear interpolation at read time.
        // This lets TakeSnapshot return the true wall-clock start/end of the snapshot.
        std::vector<uint64_t> qpc_ring_;

        mutable std::mutex ring_mutex_;
    };


} // namespace fthr

#endif // FTHR_AUDIO_RING_BUFFER_H

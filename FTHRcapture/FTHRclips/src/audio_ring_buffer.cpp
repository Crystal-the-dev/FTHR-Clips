// audio_ring_buffer.cpp
// FTHR Capture Engine - raw PCM replay ring

#include "audio_ring_buffer.h"

#include <algorithm>
#include <cassert>
#include <cstring>
#include <iostream>

namespace fthr {

AudioRingBuffer::AudioRingBuffer(
    uint32_t capacity_frames,
    uint32_t sample_rate,
    uint32_t channels,
    uint32_t safety_frames)
    : capacity_frames_(capacity_frames),
      sample_rate_(sample_rate),
      channels_(channels),
      safety_frames_(safety_frames) {
    assert(capacity_frames_ > 0);
    assert(channels_ > 0);
    storage_.resize(static_cast<size_t>(capacity_frames_) * channels_, 0.0f);
    qpc_ring_.resize(capacity_frames_, 0ULL);
    std::cout << "[AudioRingBuffer] Initialized: "
              << capacity_frames_ << " frames x " << channels_ << "ch @ "
              << sample_rate_ << "Hz ("
              << storage_.size() * sizeof(float) / 1024 / 1024 << " MB)"
              << std::endl;
}

void AudioRingBuffer::Push(
    const float* interleaved_data,
    uint32_t frame_count,
    uint64_t qpc_100ns,
    float volume) {
    if (!interleaved_data || frame_count == 0) return;
    std::lock_guard<std::mutex> lock(ring_mutex_);
    const uint64_t write_head = head_.load(std::memory_order_relaxed);
    const uint64_t ticks_per_frame = sample_rate_ > 0
        ? 10'000'000ULL / sample_rate_
        : 208ULL;

    for (uint32_t frame = 0; frame < frame_count; ++frame) {
        const size_t slot = static_cast<size_t>(
            (write_head + frame) % capacity_frames_);
        const size_t source = static_cast<size_t>(frame) * channels_;
        const size_t destination = slot * channels_;
        if (volume == 1.0f) {
            std::memcpy(
                storage_.data() + destination,
                interleaved_data + source,
                channels_ * sizeof(float));
        } else {
            for (uint32_t channel = 0; channel < channels_; ++channel) {
                storage_[destination + channel] =
                    interleaved_data[source + channel] * volume;
            }
        }
        qpc_ring_[slot] = qpc_100ns > 0
            ? qpc_100ns + static_cast<uint64_t>(frame) * ticks_per_frame
            : 0ULL;
    }

    head_.fetch_add(frame_count, std::memory_order_release);
    const uint64_t previous = frames_written_.load(std::memory_order_relaxed);
    frames_written_.store(
        std::min(
            previous + frame_count,
            static_cast<uint64_t>(capacity_frames_)),
        std::memory_order_relaxed);
}

AudioPCMSnapshot AudioRingBuffer::TakeSnapshot(
    double duration_s, double end_qpc_s) const {
    AudioPCMSnapshot snapshot;
    snapshot.sample_rate = sample_rate_;
    snapshot.channels = channels_;
    if (duration_s <= 0.0) return snapshot;

    std::lock_guard<std::mutex> lock(ring_mutex_);
    const uint64_t current_head = head_.load(std::memory_order_acquire);
    const uint64_t frames_written = frames_written_.load(
        std::memory_order_relaxed);
    if (frames_written == 0) return snapshot;

    const uint64_t oldest = current_head - frames_written;
    uint64_t end_exclusive = current_head;
    if (end_qpc_s > 0.0) {
        const uint64_t newest_qpc = qpc_ring_[static_cast<size_t>(
            (current_head - 1) % capacity_frames_)];
        if (newest_qpc > 0) {
            const uint64_t target = static_cast<uint64_t>(
                end_qpc_s * 10'000'000.0 + 0.5);
            while (end_exclusive > oldest) {
                const uint64_t position = end_exclusive - 1;
                const uint64_t qpc = qpc_ring_[static_cast<size_t>(
                    position % capacity_frames_)];
                if (qpc > 0 && qpc <= target) break;
                --end_exclusive;
            }
        }
    } else if (safety_frames_ > 0) {
        const uint64_t safety = std::min<uint64_t>(
            safety_frames_, frames_written);
        end_exclusive -= safety;
    }
    if (end_exclusive <= oldest) return snapshot;

    const uint64_t wanted = static_cast<uint64_t>(
        duration_s * sample_rate_ + 0.5);
    const uint64_t available = end_exclusive - oldest;
    const uint64_t count = std::min(wanted, available);
    if (count == 0) return snapshot;
    const uint64_t start = end_exclusive - count;

    snapshot.samples.resize(static_cast<size_t>(count) * channels_);
    for (uint64_t frame = 0; frame < count; ++frame) {
        const size_t source_slot = static_cast<size_t>(
            (start + frame) % capacity_frames_);
        std::memcpy(
            snapshot.samples.data() + static_cast<size_t>(frame) * channels_,
            storage_.data() + source_slot * channels_,
            channels_ * sizeof(float));
    }

    const uint64_t first_qpc = qpc_ring_[static_cast<size_t>(
        start % capacity_frames_)];
    const uint64_t last_qpc = qpc_ring_[static_cast<size_t>(
        (end_exclusive - 1) % capacity_frames_)];
    snapshot.qpc_start_s = first_qpc > 0
        ? static_cast<double>(first_qpc) / 10'000'000.0
        : 0.0;
    snapshot.qpc_end_s = last_qpc > 0
        ? static_cast<double>(last_qpc) / 10'000'000.0
        : snapshot.qpc_start_s + static_cast<double>(count) / sample_rate_;
    snapshot.valid = true;

    std::cout << "[AudioRingBuffer] TakeSnapshot: " << count << " frames ("
              << static_cast<double>(count) / sample_rate_ << "s), QPC "
              << snapshot.qpc_start_s << "s - " << snapshot.qpc_end_s << "s"
              << std::endl;
    return snapshot;
}

} // namespace fthr

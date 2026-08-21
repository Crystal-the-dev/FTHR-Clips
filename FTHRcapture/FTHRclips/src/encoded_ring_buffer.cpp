// encoded_ring_buffer.cpp
// FTHR Capture Engine - encoded packet replay ring

#include "encoded_ring_buffer.h"

#include "replay_interval.h"

#include <cassert>
#include <cstring>
#include <iostream>

namespace fthr {

EncodedRingBuffer::EncodedRingBuffer(
    size_t capacity, uint32_t fps, int64_t qpc_freq)
    : capacity_(capacity), fps_(fps), qpc_freq_(qpc_freq) {
    assert(capacity_ > 0);
    slots_.resize(capacity_);
    slot_states_ = std::make_unique<std::atomic<uint32_t>[]>(capacity_);
    slot_mutexes_ = std::make_unique<std::mutex[]>(capacity_);
    for (size_t i = 0; i < capacity_; ++i) {
        slot_states_[i].store(
            static_cast<uint32_t>(SlotState::EMPTY),
            std::memory_order_relaxed);
    }
    std::cout << "[EncodedRingBuffer] Initialized: "
              << capacity_ << " slots @ " << fps_ << " fps" << std::endl;
}

void EncodedRingBuffer::Push(
    const uint8_t* encoded_data,
    uint32_t size,
    int64_t pts,
    bool is_keyframe,
    int64_t wall_qpc) {
    const uint64_t write_pos = head_.load(std::memory_order_relaxed);
    const size_t slot_idx = static_cast<size_t>(write_pos % capacity_);
    std::lock_guard<std::mutex> slot_lock(slot_mutexes_[slot_idx]);

    slot_states_[slot_idx].store(
        static_cast<uint32_t>(SlotState::WRITING),
        std::memory_order_relaxed);
    auto& slot = slots_[slot_idx];
    slot.data.resize(size);
    if (size > 0 && encoded_data)
        std::memcpy(slot.data.data(), encoded_data, size);
    slot.pts = pts;
    slot.wall_qpc = wall_qpc;
    slot.is_keyframe = is_keyframe;
    slot.valid = true;
    slot.absolute_position = write_pos;

    slot_states_[slot_idx].store(
        static_cast<uint32_t>(SlotState::READY),
        std::memory_order_release);
    head_.fetch_add(1, std::memory_order_release);
    const size_t previous = count_.load(std::memory_order_relaxed);
    if (previous < capacity_)
        count_.fetch_add(1, std::memory_order_relaxed);
    latest_wall_qpc_.store(wall_qpc, std::memory_order_release);
    publication_cv_.notify_all();
}

bool EncodedRingBuffer::SetVideoConfig(const EncodedVideoConfig& config) {
    std::lock_guard<std::mutex> lock(video_config_mutex_);
    if (video_config_set_ && !(video_config_ == config)) {
        std::cerr << "[EncodedRingBuffer] Rejected stream config change within "
                     "one capture generation" << std::endl;
        return false;
    }
    video_config_ = config;
    video_config_set_ = true;
    std::cout << "[EncodedRingBuffer] Video config set: "
              << VideoCodecName(config.codec) << ' '
              << config.width << 'x' << config.height << ", "
              << config.codec_extradata.size() << " config bytes" << std::endl;
    return true;
}

EncodedRingSnapshot EncodedRingBuffer::TakeSnapshotByTime(
    uint32_t duration_seconds, int64_t target_end_qpc) const {
    using replay_interval::Sample;
    if (duration_seconds == 0 || qpc_freq_ <= 0) return {};

    struct PacketRef {
        uint64_t position;
        Sample sample;
    };

    for (int attempt = 0; attempt < 3; ++attempt) {
        EncodedRingSnapshot snapshot;
        {
            std::lock_guard<std::mutex> lock(video_config_mutex_);
            snapshot.video_config = video_config_;
        }

        const uint64_t current_head = head_.load(std::memory_order_acquire);
        const size_t current_count = count_.load(std::memory_order_acquire);
        if (current_count == 0) return snapshot;
        if (target_end_qpc <= 0) {
            target_end_qpc = latest_wall_qpc_.load(std::memory_order_acquire);
        }

        const uint64_t oldest = current_head - current_count;
        std::vector<PacketRef> refs;
        refs.reserve(current_count);
        for (uint64_t pos = oldest; pos < current_head; ++pos) {
            const size_t slot_idx = static_cast<size_t>(pos % capacity_);
            std::lock_guard<std::mutex> lock(slot_mutexes_[slot_idx]);
            const auto state = slot_states_[slot_idx].load(
                std::memory_order_acquire);
            const auto& packet = slots_[slot_idx];
            if (state != static_cast<uint32_t>(SlotState::READY)
                || !packet.valid
                || packet.absolute_position != pos
                || packet.wall_qpc <= 0) {
                continue;
            }
            refs.push_back({pos, {packet.wall_qpc, packet.is_keyframe}});
        }

        std::vector<Sample> samples;
        samples.reserve(refs.size());
        for (const auto& ref : refs) samples.push_back(ref.sample);
        const auto selection = replay_interval::Select(
            samples,
            target_end_qpc,
            static_cast<int64_t>(duration_seconds) * qpc_freq_);
        if (!selection.valid) return snapshot;

        snapshot.packets.reserve(
            selection.end - selection.decode_start + 1);
        bool stale = false;
        for (size_t i = selection.decode_start; i <= selection.end; ++i) {
            const uint64_t pos = refs[i].position;
            const size_t slot_idx = static_cast<size_t>(pos % capacity_);
            std::lock_guard<std::mutex> lock(slot_mutexes_[slot_idx]);
            const auto& source = slots_[slot_idx];
            if (!source.valid || source.absolute_position != pos) {
                stale = true;
                break;
            }
            snapshot.packets.push_back(source);
        }
        if (stale) continue;

        snapshot.full_history = selection.full_history;
        int64_t requested_duration_ticks = DurationInVideoTicks(
            snapshot.video_config, duration_seconds);
        if (requested_duration_ticks <= 0) {
            requested_duration_ticks = static_cast<int64_t>(fps_)
                * static_cast<int64_t>(duration_seconds);
        }
        snapshot.presentation_start_pts = replay_interval::PresentationStartPts(
            snapshot.packets.back().pts,
            requested_duration_ticks,
            snapshot.full_history,
            snapshot.packets.front().pts);
        snapshot.qpc_start_s = static_cast<double>(
            snapshot.packets.front().wall_qpc) / qpc_freq_;
        snapshot.qpc_end_s = static_cast<double>(
            snapshot.packets.back().wall_qpc) / qpc_freq_;
        snapshot.media_end_qpc_s = snapshot.qpc_end_s;
        snapshot.presentation_end_qpc_s =
            static_cast<double>(target_end_qpc) / qpc_freq_;
        snapshot.presentation_start_qpc_s = snapshot.full_history
            ? snapshot.presentation_end_qpc_s - duration_seconds
            : snapshot.qpc_start_s;

        std::cout << "[EncodedRingBuffer] TakeSnapshot: "
                  << snapshot.packets.size() << " packets, QPC "
                  << snapshot.qpc_start_s << "s - " << snapshot.qpc_end_s
                  << "s, "
                  << (snapshot.full_history ? "full history" : "partial history")
                  << std::endl;
        return snapshot;
    }

    std::cerr << "[EncodedRingBuffer] ring advanced during snapshot retries"
              << std::endl;
    return {};
}

bool EncodedRingBuffer::WaitUntilPublished(
    int64_t target_qpc, std::chrono::milliseconds timeout) const {
    if (target_qpc <= 0) return true;
    std::unique_lock<std::mutex> lock(publication_mutex_);
    return publication_cv_.wait_for(lock, timeout, [this, target_qpc] {
        return latest_wall_qpc_.load(std::memory_order_acquire) >= target_qpc;
    });
}

void EncodedRingBuffer::Clear() {
    for (size_t i = 0; i < capacity_; ++i) {
        std::lock_guard<std::mutex> lock(slot_mutexes_[i]);
        slots_[i].valid = false;
        slots_[i].absolute_position = std::numeric_limits<uint64_t>::max();
        slot_states_[i].store(
            static_cast<uint32_t>(SlotState::EMPTY),
            std::memory_order_release);
    }
    count_.store(0, std::memory_order_release);
    head_.store(0, std::memory_order_release);
    latest_wall_qpc_.store(0, std::memory_order_release);
}

} // namespace fthr

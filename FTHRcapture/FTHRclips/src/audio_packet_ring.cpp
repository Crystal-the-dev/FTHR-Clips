#include "audio_packet_ring.h"

#include <algorithm>
#include <limits>

namespace fthr {

EncodedAudioPacketRing::EncodedAudioPacketRing(
    AudioSourceId source_id, uint64_t generation, AudioSourceFormat format,
    uint32_t retention_seconds)
    : source_id_(std::move(source_id)), generation_(generation), format_(std::move(format)),
      retention_seconds_(std::max<uint32_t>(1, retention_seconds)),
      retention_samples_(static_cast<int64_t>(format_.sample_rate) * retention_seconds_) {}

void EncodedAudioPacketRing::SetCodecExtradata(std::vector<uint8_t> codec_extradata) {
    std::lock_guard<std::mutex> lock(mutex_);
    codec_extradata_ = std::move(codec_extradata);
}

bool EncodedAudioPacketRing::Push(EncodedAudioPacket packet) {
    if (packet.data.empty() || packet.duration_samples <= 0) {
        std::lock_guard<std::mutex> lock(mutex_);
        ++stats_.rejected_empty_packets;
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!packets_.empty() && packet.pts_samples < packets_.back().pts_samples) {
        ++stats_.rejected_regressing_packets;
        return false;
    }
    byte_count_ += packet.data.size();
    packets_.push_back(std::move(packet));
    ++stats_.accepted_packets;
    TrimLocked();
    return true;
}

EncodedAudioSnapshot EncodedAudioPacketRing::TakeSnapshot(
    int64_t start_pts_samples, int64_t end_pts_samples) const {
    EncodedAudioSnapshot result;
    result.source_id = source_id_;
    result.generation = generation_;
    result.format = format_;
    if (end_pts_samples <= start_pts_samples) return result;

    std::lock_guard<std::mutex> lock(mutex_);
    result.codec_extradata = codec_extradata_;
    for (const auto& packet : packets_) {
        const int64_t packet_end = packet.pts_samples + packet.duration_samples;
        if (packet_end <= start_pts_samples) continue;
        if (packet.pts_samples >= end_pts_samples) break;
        result.packets.push_back(packet);
    }
    if (!result.packets.empty()) {
        result.first_pts_samples = result.packets.front().pts_samples;
        const auto& last = result.packets.back();
        result.last_pts_samples = last.pts_samples + last.duration_samples;
    }
    return result;
}

size_t EncodedAudioPacketRing::packet_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return packets_.size();
}

size_t EncodedAudioPacketRing::byte_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return byte_count_;
}

EncodedAudioRingStats EncodedAudioPacketRing::stats() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return stats_;
}

void EncodedAudioPacketRing::TrimLocked() {
    if (packets_.empty()) return;
    const int64_t newest = packets_.back().pts_samples + packets_.back().duration_samples;
    const int64_t oldest_allowed = newest - retention_samples_;
    while (!packets_.empty()) {
        const auto& packet = packets_.front();
        if (packet.pts_samples + packet.duration_samples > oldest_allowed) break;
        byte_count_ -= packet.data.size();
        packets_.pop_front();
        ++stats_.trimmed_packets;
    }
}

}  // namespace fthr

// encoded_ring_buffer.h
// FTHR Capture Engine - Encoded packet ring buffer
//
// Stores AVCC-format encoded H.264 packets produced by HardwareEncoder.
// Replaces the raw BGRA FramePool on the NVENC path, dropping RAM usage
// from ~8GB to ~60MB for a 30-second buffer at 1080p/60fps/16Mbps.
//
// Threading model:
//   Push()         - single producer; publishes one sequence-numbered slot.
//   TakeSnapshot() - save thread; takes short per-slot locks while copying.
// This avoids a global capture lock while preventing a wrapped slot from being
// overwritten during its deep copy. Selection is by capture timestamp, not by
// packet count, and includes the keyframe immediately before the visible start.
//
// Data format:
//   Packets are stored in AVCC format (4-byte big-endian length prefix per
//   NAL unit, SPS/PPS stripped). SPS/PPS are stored separately in extradata_
//   and returned with every snapshot for the muxer to use as stream extradata.
//   Conversion from Annex B happens once at Push() time in HardwareEncoder.

#pragma once
#ifndef FTHR_ENCODED_RING_BUFFER_H
#define FTHR_ENCODED_RING_BUFFER_H

#include <cstdint>
#include <memory>
#include <vector>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <limits>
#include <mutex>


namespace fthr {


    // ---------------------------------------------------------------------------
    // SlotState
    //
    // Per-slot lifecycle flags used by the published-slot protocol.
    //   EMPTY   - slot has never been written (ring not yet full)
    //   WRITING - Push() is actively writing to this slot
    //   READY   - slot data is complete and safe to read
    // ---------------------------------------------------------------------------
    enum class SlotState : uint32_t {
        EMPTY   = 0,
        WRITING = 1,
        READY   = 2,
    };


    // ---------------------------------------------------------------------------
    // EncodedRingPacket
    //
    // One compressed video frame stored in the ring buffer.
    // data is AVCC format - ready to hand directly to av_interleaved_write_frame.
    //
    // Named EncodedRingPacket (not EncodedPacket) to avoid collision with
    // the EncodedPacket struct defined in video_encoder.h (pool-backed, different
    // members). Both live in namespace fthr so the names must be distinct.
    // ---------------------------------------------------------------------------
    struct EncodedRingPacket {
        std::vector<uint8_t> data;         // AVCC-format NAL units (SPS/PPS excluded)
        int64_t              pts = 0;
        int64_t              wall_qpc = 0; // Raw QPC ticks at capture time (same clock as WASAPI)
        bool                 is_keyframe = false;
        bool                 valid = false; // False on uninitialized slots
        uint64_t             absolute_position =
            std::numeric_limits<uint64_t>::max();
    };


    // ---------------------------------------------------------------------------
    // EncodedRingSnapshot
    //
    // Returned by TakeSnapshot(). Caller owns all data - safe to use while
    // CaptureThread continues encoding into the ring buffer.
    // ---------------------------------------------------------------------------
    struct EncodedRingSnapshot {
        std::vector<EncodedRingPacket> packets;   // Ordered oldest -> newest
        std::vector<uint8_t>           extradata; // AVCC decoder config record (SPS/PPS)

        // Wall-clock QPC range of packets in this snapshot (seconds).
        // Derived from first/last packet wall_qpc, converted using qpc_freq.
        // Same clock domain as AudioPCMSnapshot::qpc_start_s / qpc_end_s.
        double qpc_start_s = 0.0;
        double qpc_end_s   = 0.0;
        int64_t presentation_start_pts = 0;
        double presentation_start_qpc_s = 0.0;
        double presentation_end_qpc_s = 0.0;
        double media_end_qpc_s = 0.0;
        bool full_history = false;
    };


    // ---------------------------------------------------------------------------
    // EncodedRingBuffer
    // ---------------------------------------------------------------------------
    class EncodedRingBuffer {
    public:
        // capacity:  number of packet slots to pre-allocate.
        // fps:       capture framerate / encoded packet timebase.
        // qpc_freq:  QueryPerformanceFrequency value, used to convert wall_qpc to seconds.
        // Recommended capacity = buffer_seconds * fps * 2 (generous headroom).
        explicit EncodedRingBuffer(size_t capacity, uint32_t fps, int64_t qpc_freq = 0);

        // Not copyable or movable - owns large pre-allocated storage.
        EncodedRingBuffer(const EncodedRingBuffer&) = delete;
        EncodedRingBuffer& operator=(const EncodedRingBuffer&) = delete;


        // -----------------------------------------------------------------------
        // Push
        //
        // Store one encoded packet in the next ring slot.
        // Called from CaptureThread - must be fast.
        //
        // avcc_data: AVCC-format bytes (SPS/PPS already stripped).
        //            HardwareEncoder performs the Annex B -> AVCC conversion
        //            before calling Push, so no conversion happens here.
        // -----------------------------------------------------------------------
        void Push(const uint8_t* avcc_data, uint32_t size,
            int64_t pts, bool is_keyframe, int64_t wall_qpc = 0);


        // -----------------------------------------------------------------------
        // SetExtradata
        //
        // Store the AVCC decoder configuration record (SPS + PPS).
        // Called once by HardwareEncoder after nvEncGetSequenceParams succeeds.
        // Thread-safe (guarded by extradata_mutex_).
        // -----------------------------------------------------------------------
        void SetExtradata(const uint8_t* data, size_t size);


        // -----------------------------------------------------------------------
        // TakeSnapshot
        //
        // Copy the requested wall-clock interval plus decoder keyframe pre-roll.
        //
        // Called from SaveClipThread - blocking is acceptable here.
        // -----------------------------------------------------------------------
        EncodedRingSnapshot TakeSnapshotByTime(
            uint32_t duration_seconds, int64_t target_end_qpc) const;

        bool WaitUntilPublished(
            int64_t target_qpc, std::chrono::milliseconds timeout) const;

        // Invalidate replay across a backend recovery. Slot storage is retained
        // to avoid reallocations; atomic state/count publication makes a
        // concurrent snapshot safely observe an empty/non-ready buffer.
        void Clear();


        // -----------------------------------------------------------------------
        // Stats
        // -----------------------------------------------------------------------
        size_t   GetCount()    const { return count_.load(std::memory_order_relaxed); }
        size_t   GetCapacity() const { return capacity_; }
        uint64_t GetPushCount() const { return head_.load(std::memory_order_relaxed); }


    private:
        size_t              capacity_;
        uint32_t            fps_;
        int64_t             qpc_freq_;

        std::vector<EncodedRingPacket>  slots_;

        // Per-slot atomic state flags (SlotState enum stored as uint32_t).
        // Separate from slots_ because std::atomic is not copyable/movable,
        // which would prevent std::vector<EncodedRingPacket> from compiling.
        // Indexed identically to slots_: state for slot i is slot_states_[i].
        std::unique_ptr<std::atomic<uint32_t>[]> slot_states_;
        std::unique_ptr<std::mutex[]> slot_mutexes_;

        // Monotonically increasing push counter.
        // Slot index = head_ % capacity_.
        std::atomic<uint64_t>  head_{ 0 };

        // Number of valid slots, capped at capacity_.
        std::atomic<size_t>    count_{ 0 };
        std::atomic<int64_t>   latest_wall_qpc_{ 0 };

        mutable std::mutex publication_mutex_;
        mutable std::condition_variable publication_cv_;

        // SPS/PPS decoder config record for the MP4 muxer.
        std::vector<uint8_t>   extradata_;
        mutable std::mutex     extradata_mutex_;
    };


} // namespace fthr

#endif // FTHR_ENCODED_RING_BUFFER_H

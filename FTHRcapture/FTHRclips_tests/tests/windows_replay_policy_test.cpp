#include "replay_encoder.h"
#include "encoded_ring_buffer.h"
#include "frame_rate_scheduler.h"
#include "replay_health.h"
#include "save_clip_task.h"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

namespace {

using fthr::EncoderVendor;
using fthr::EncoderPreference;
using fthr::ReplayEncoderBackend;
using fthr::ReplayStartupError;
using fthr::VideoCodec;

int policy_checks = 0;

void CheckPolicy(bool condition, const char* message) {
    ++policy_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

void SameAdapterVendorMatrixIsAuthoritative() {
    const auto nvidia = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Nvidia, VideoCodec::H264);
    const auto amd = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Amd, VideoCodec::HEVC);
    const auto intel = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, VideoCodec::AV1);
    CheckPolicy(nvidia.allowed
            && nvidia.backend == ReplayEncoderBackend::NativeNvenc,
        "selected NVIDIA monitor uses same-adapter NVENC");
    CheckPolicy(amd.allowed
            && amd.backend == ReplayEncoderBackend::FfmpegAmf,
        "selected AMD monitor uses same-adapter AMF");
    CheckPolicy(intel.allowed
            && intel.backend == ReplayEncoderBackend::FfmpegQsv,
        "selected Intel monitor uses same-adapter QSV");
    CheckPolicy(nvidia.same_adapter && amd.same_adapter && intel.same_adapter,
        "all alpha production selections are same-adapter");
}

void MissingRuntimeAndUnsupportedCodecStayFailed() {
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "NVENC runtime unavailable")
                == ReplayStartupError::HardwareEncoderUnavailable,
        "missing NVIDIA runtime is distinct");
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "AMF runtime unavailable: amfrt64.dll not found")
                == ReplayStartupError::HardwareEncoderUnavailable,
        "missing AMD runtime is distinct");
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "oneVPL runtime unavailable")
                == ReplayStartupError::HardwareEncoderUnavailable,
        "missing Intel runtime is distinct");
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "av1_qsv unsupported by this hardware")
                == ReplayStartupError::RequestedCodecUnsupported,
        "unsupported requested codec is distinct");
    CheckPolicy(fthr::ClassifyReplayInitializationFailure(
                    "h264_qsv unsupported on this Intel adapter")
                == ReplayStartupError::RequestedCodecUnsupported,
        "old or incapable Intel hardware is classified from capability failure, not model name");
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Nvidia, static_cast<VideoCodec>(99)).error
                == ReplayStartupError::RequestedCodecUnsupported,
        "unknown codec cannot silently become H.264");
}

void CaptureAdapterCannotBeStolenByAnotherVendor() {
    const auto intel = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, VideoCodec::H264);
    CheckPolicy(intel.encoder_vendor == EncoderVendor::Intel
            && intel.backend == ReplayEncoderBackend::FfmpegQsv,
        "unrelated NVIDIA presence cannot steal Intel monitor selection");
    CheckPolicy(!fthr::IsAutomaticCrossAdapterReplayAllowed(),
        "cross-adapter path is disabled until explicitly qualified");
    const auto unknown = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Software, VideoCodec::H264);
    CheckPolicy(!unknown.allowed
            && unknown.error == ReplayStartupError::CaptureAdapterUnsupported,
        "unknown capture adapter refuses startup instead of enumerating vendors");
    const auto amd_on_nvidia = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Nvidia, EncoderPreference::Amd, VideoCodec::H264);
    CheckPolicy(!amd_on_nvidia.allowed
            && amd_on_nvidia.error
                == ReplayStartupError::CrossAdapterPathUnavailable,
        "requested AMF adapter cannot consume another adapter's monitor texture");
}

void ExplicitNvidiaSelectionEnablesQualifiedHybridPath() {
    const auto selected = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, EncoderPreference::Nvidia, VideoCodec::HEVC);
    CheckPolicy(selected.allowed && !selected.same_adapter,
        "explicit NVIDIA selection permits the hybrid CPU-input path");
    CheckPolicy(selected.encoder_vendor == EncoderVendor::Nvidia
            && selected.backend == ReplayEncoderBackend::NativeNvenc,
        "explicit NVIDIA selection resolves to native NVENC");

    const auto unsupported = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Intel, EncoderPreference::Amd, VideoCodec::H264);
    CheckPolicy(!unsupported.allowed
            && unsupported.error == ReplayStartupError::CrossAdapterPathUnavailable,
        "unimplemented AMD cross-adapter selection stays rejected");
}

void RawCapacityNeverOverclaimsConfiguredHistory() {
    const auto capacity = fthr::CalculateRawReplayCapacity(
        1920, 1080, 4, 60, 60, 512);
    CheckPolicy(capacity.frame_capacity == 64,
        "512 MiB raw BGRA capacity uses exact whole-frame count");
    CheckPolicy(capacity.capacity_milliseconds == 1066,
        "raw capacity reports actual duration at effective FPS");
    CheckPolicy(!capacity.meets_requested_duration,
        "raw capacity cannot claim the configured 60 seconds");
    const auto invalid = fthr::CalculateRawReplayCapacity(
        0, 1080, 4, 60, 30, 512);
    CheckPolicy(invalid.frame_capacity == 0
            && !invalid.meets_requested_duration,
        "invalid raw geometry reports no replay capability");
    CheckPolicy(!fthr::IsAutomaticRawReplayFallbackAllowed(),
        "raw replay fallback is disabled for the public alpha");
}

int CountScheduledFrames(int host_fps, int target_fps, int seconds) {
    constexpr int64_t ticks_per_second = 90000000;
    fthr::FrameRateScheduler scheduler(ticks_per_second / target_fps);
    int accepted = 0;
    for (int frame = 0; frame < host_fps * seconds; ++frame) {
        const int64_t now = static_cast<int64_t>(frame)
            * ticks_per_second / host_fps;
        if (scheduler.ShouldCapture(now)) ++accepted;
    }
    return accepted;
}

void DeadlineSchedulerPreservesCadenceAcrossRefreshRates() {
    CheckPolicy(CountScheduledFrames(90, 60, 2) == 120,
        "90 Hz input preserves a 60 fps deadline cadence");
    CheckPolicy(CountScheduledFrames(144, 60, 2) == 120,
        "144 Hz input preserves a 60 fps deadline cadence");
    CheckPolicy(CountScheduledFrames(60, 120, 2) == 120,
        "target above host refresh accepts every available frame");

    fthr::FrameRateScheduler scheduler(10);
    CheckPolicy(scheduler.ShouldCapture(0) && !scheduler.ShouldCapture(9)
            && scheduler.ShouldCapture(10),
        "deadline scheduler accepts the first frame and exact deadlines");
    CheckPolicy(scheduler.ShouldCapture(1000) && !scheduler.ShouldCapture(1001),
        "a long stall advances missed deadlines without a catch-up burst");
}

void EncodedReplayCapacityHasBoundedHeadroom() {
    CheckPolicy(fthr::CalculateEncodedReplaySlotCapacity(30, 60) == 2100,
        "30-second replay keeps five seconds of bounded headroom");
    CheckPolicy(fthr::CalculateEncodedReplaySlotCapacity(300, 60) == 18300,
        "long replay does not reserve a second full history");
    CheckPolicy(fthr::CalculateEncodedReplaySlotCapacity(0, 60) == 0
            && fthr::CalculateEncodedReplaySlotCapacity(30, 0) == 0,
        "invalid replay timing cannot allocate a ring");
}

fthr::replay_health::Input ReplayHealthFixture(
    uint32_t seconds, uint32_t fps, bool full_history = true) {
    fthr::replay_health::Input input;
    input.requested_duration_seconds = seconds;
    input.expected_fps = static_cast<double>(fps);
    input.wall_qpc_frequency = 1'000'000;
    input.full_history = full_history;
    const int64_t frame_period = input.wall_qpc_frequency / fps;
    const size_t frame_count = static_cast<size_t>(seconds) * fps + 1;
    input.packets.reserve(frame_count);
    for (size_t index = 0; index < frame_count; ++index) {
        input.packets.push_back({
            1'000'000 + static_cast<int64_t>(index) * frame_period,
            static_cast<int64_t>(index),
            4});
    }
    return input;
}

void ReplayHealthRejectsSparseNativeTimeline() {
    auto sparse = ReplayHealthFixture(5, 60);
    sparse.packets = {
        {1'000'000, 0, 4},
        {2'500'000, 1, 4},
        {4'000'000, 2, 4},
        {6'000'000, 3, 4},
    };
    const auto report = fthr::replay_health::Evaluate(sparse);
    CheckPolicy(!report.accepted
            && report.failure == fthr::replay_health::Failure::LargeTimelineGap
            && report.packet_count == 4
            && report.largest_gap_s >= 1.5,
        "four packets across several seconds are rejected before muxing");

    sparse.packets = {
        {1'000'000, 0, 4},
        {1'016'666, 1, 4},
        {1'033'332, 2, 4},
        {1'049'998, 3, 4},
    };
    const auto clustered = fthr::replay_health::Evaluate(sparse);
    CheckPolicy(!clustered.accepted
            && clustered.failure
                == fthr::replay_health::Failure::InsufficientCoverage,
        "four tightly clustered packets cannot claim full requested history");
}

void ReplayHealthAllowsHealthyAndPartialHistory() {
    for (const uint32_t seconds : {5u, 30u, 60u}) {
        const auto report = fthr::replay_health::Evaluate(
            ReplayHealthFixture(seconds, 60));
        CheckPolicy(report.accepted && report.generation_continuous
                && report.effective_packet_rate > 59.0,
            "normal 60 fps replay is accepted for the requested interval");
    }

    auto partial = ReplayHealthFixture(5, 60, false);
    partial.packets.resize(4);
    const auto partial_report = fthr::replay_health::Evaluate(partial);
    CheckPolicy(!partial_report.accepted
            && partial_report.failure
                == fthr::replay_health::Failure::InsufficientCoverage,
        "four packets cannot claim a requested replay even as partial history");

    const auto dense_partial = fthr::replay_health::Evaluate(
        ReplayHealthFixture(1, 30, false));
    CheckPolicy(dense_partial.accepted && dense_partial.partial_history,
        "dense one-second startup history remains valid as partial history");

    auto short_gap = ReplayHealthFixture(5, 60);
    // One missed scheduling deadline is bounded and must not reject the clip.
    for (size_t index = 61; index < short_gap.packets.size(); ++index) {
        short_gap.packets[index].wall_qpc += 2 * 16'666;
    }
    const auto short_gap_report = fthr::replay_health::Evaluate(short_gap);
    CheckPolicy(short_gap_report.accepted
            && short_gap_report.largest_gap_s < 0.250,
        "a short bounded capture gap remains acceptable");
}

void ReplayHealthRejectsGenerationChange() {
    auto input = ReplayHealthFixture(1, 60);
    input.packets[30].generation = 5;
    const auto report = fthr::replay_health::Evaluate(input);
    CheckPolicy(!report.accepted
            && report.failure
                == fthr::replay_health::Failure::GenerationDiscontinuity,
        "a replay crossing capture generations is rejected");
}

void ReplayHealthRejectsStaleSaveBoundary() {
    auto input = ReplayHealthFixture(5, 60);
    input.target_end_wall_qpc = input.packets.back().wall_qpc + 1'000'001;
    const auto report = fthr::replay_health::Evaluate(input);
    CheckPolicy(!report.accepted
            && report.failure == fthr::replay_health::Failure::StaleEnd,
        "a replay whose newest packet is stale at save time is rejected");
}

void EncodedRingGenerationIsolatedAcrossClear() {
    fthr::EncodedRingBuffer ring(16, 60, 1'000'000);
    const uint8_t packet = 0x01;
    ring.Push(&packet, 1, 0, true, 1'000'000);
    const auto before = ring.TakeSnapshotByTime(1, 1'000'000);
    CheckPolicy(before.packets.size() == 1
            && before.capture_generation == before.packets.front().generation,
        "snapshot exposes the packet generation before recovery");

    const uint64_t old_generation = ring.GetGeneration();
    ring.Clear();
    CheckPolicy(ring.GetGeneration() == old_generation + 1
            && ring.GetCount() == 0,
        "clearing the encoded ring advances its internal generation");
    ring.Push(&packet, 1, 0, true, 2'000'000);
    const auto after = ring.TakeSnapshotByTime(1, 2'000'000);
    CheckPolicy(after.packets.size() == 1
            && after.capture_generation == ring.GetGeneration()
            && after.packets.front().generation != before.packets.front().generation,
        "post-recovery snapshots contain only the fresh generation");

    fthr::EncodedRingBuffer cutoff_ring(16, 60, 1'000'000);
    cutoff_ring.Clear(1'500'000);
    cutoff_ring.Push(&packet, 1, 0, true, 1'400'000);
    CheckPolicy(cutoff_ring.GetCount() == 0,
        "delayed pre-recovery output at the cutoff is rejected");
    cutoff_ring.Push(&packet, 1, 1, true, 1'600'000);
    CheckPolicy(cutoff_ring.GetCount() == 1,
        "post-recovery output beyond the cutoff is accepted");
}

void ActiveTruthAndGenerationAreScoped() {
    const auto selected = fthr::SelectWindowsReplayPolicy(
        EncoderVendor::Amd, VideoCodec::AV1);
    const auto active = fthr::EvaluateActiveReplayCapability(
        selected,
        {EncoderVendor::Amd, ReplayEncoderBackend::FfmpegAmf,
         VideoCodec::AV1, true, "av1_amf"},
        true,
        7);
    CheckPolicy(active.initialized && active.requested_codec == VideoCodec::AV1
            && active.active_codec == VideoCodec::AV1,
        "active codec truth comes from initialized backend");
    CheckPolicy(active.active_backend == ReplayEncoderBackend::FfmpegAmf
            && active.hardware && active.same_adapter,
        "active backend and adapter truth remain explicit");
    CheckPolicy(active.generation == 7,
        "capability truth is scoped to one capture generation");

    const auto stale = fthr::EvaluateActiveReplayCapability(
        selected,
        {EncoderVendor::Amd, ReplayEncoderBackend::FfmpegAmf,
         VideoCodec::H264, true, "h264_amf"},
        true,
        8);
    CheckPolicy(!stale.initialized
            && stale.error == ReplayStartupError::RequestedCodecUnsupported,
        "a backend reporting the wrong codec is rejected");
    CheckPolicy(active.generation != stale.generation,
        "restart/topology generation invalidates old capability truth");
}

void ProductionThreeByThreeMatrixAndRegressionsHold() {
    for (const auto vendor : {
             EncoderVendor::Nvidia, EncoderVendor::Amd, EncoderVendor::Intel}) {
        for (const auto codec : {
                 VideoCodec::H264, VideoCodec::HEVC, VideoCodec::AV1}) {
            const auto selection = fthr::SelectWindowsReplayPolicy(vendor, codec);
            CheckPolicy(selection.allowed && selection.same_adapter,
                "3x3 source matrix selects a same-adapter hardware candidate");
        }
    }
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Nvidia, VideoCodec::AV1).backend
                == ReplayEncoderBackend::NativeNvenc,
        "NVIDIA policy regression remains protected");
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Amd, VideoCodec::AV1).backend
                == ReplayEncoderBackend::FfmpegAmf,
        "AMD policy regression remains protected");
    CheckPolicy(fthr::SelectWindowsReplayPolicy(
                    EncoderVendor::Intel, VideoCodec::AV1).backend
                == ReplayEncoderBackend::FfmpegQsv,
        "Intel policy regression remains protected");
}

fthr::SaveClipTask QueueTask(uint32_t id) {
    fthr::SaveClipTask task;
    task.task_id = id;
    task.output_path = L"queue-test.mp4";
    return task;
}

void SaveQueueIsBoundedAndRejectsWithoutBlocking() {
    fthr::SaveClipQueue queue;
    for (uint32_t id = 1; id <= queue.GetCapacity(); ++id) {
        CheckPolicy(queue.Push(QueueTask(id)),
            "save queue accepts work up to its documented bound");
    }
    CheckPolicy(queue.GetQueueDepth() == queue.GetCapacity(),
        "save queue exposes its full pending depth");
    CheckPolicy(!queue.Push(QueueTask(99)),
        "save queue rejects overload instead of growing without bound");

    fthr::SaveClipTask popped;
    CheckPolicy(queue.Pop(popped) && popped.task_id == 1,
        "bounded save queue preserves FIFO ownership");
    CheckPolicy(queue.Push(QueueTask(5)),
        "save queue accepts new work after one pending slot is released");
    queue.Shutdown();
    size_t drained = 0;
    while (queue.Pop(popped)) ++drained;
    CheckPolicy(drained == queue.GetCapacity(),
        "shutdown drains every accepted save task");
    CheckPolicy(!queue.Push(QueueTask(100)),
        "shutdown visibly rejects new save work");
}

void SlowSaveConsumerDoesNotStopIndependentProgress() {
    fthr::SaveClipQueue queue;
    std::atomic<bool> consumer_started{false};
    std::atomic<bool> release_consumer{false};
    std::atomic<uint64_t> independent_progress{0};
    CheckPolicy(queue.Push(QueueTask(1)),
        "slow-consumer fixture queues its first save");

    std::thread consumer([&] {
        fthr::SaveClipTask task;
        if (!queue.Pop(task)) return;
        consumer_started.store(true, std::memory_order_release);
        while (!release_consumer.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
    });
    while (!consumer_started.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }
    std::thread progress([&] {
        while (!release_consumer.load(std::memory_order_acquire)) {
            independent_progress.fetch_add(1, std::memory_order_relaxed);
            std::this_thread::yield();
        }
    });

    CheckPolicy(queue.Push(QueueTask(2)),
        "producer remains non-blocking while the save consumer is stalled");
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    CheckPolicy(independent_progress.load(std::memory_order_relaxed) > 0,
        "independent capture-like progress continues during a slow save");
    release_consumer.store(true, std::memory_order_release);
    progress.join();
    consumer.join();
    queue.Shutdown();
    fthr::SaveClipTask pending;
    CheckPolicy(queue.Pop(pending) && pending.task_id == 2,
        "queued save remains intact after the slow consumer resumes");
    CheckPolicy(!queue.Pop(pending),
        "drained slow-consumer queue exits cleanly at shutdown");
}

void FailedSaveLeavesQueuedSuccessorAvailable() {
    fthr::SaveClipQueue queue;
    CheckPolicy(queue.Push(QueueTask(1)),
        "failure-followup fixture queues the first save");
    CheckPolicy(queue.Push(QueueTask(2)),
        "failure-followup fixture queues the successor save");

    fthr::SaveClipTask failed;
    CheckPolicy(queue.Pop(failed) && failed.task_id == 1,
        "save consumer owns the failed task independently");
    // The writer failure is simulated at the consumer boundary. Queue
    // ownership of a later request must not be lost with the failed task.
    queue.Shutdown();
    fthr::SaveClipTask successor;
    CheckPolicy(queue.Pop(successor) && successor.task_id == 2,
        "a queued save remains available after a prior save failure");
    CheckPolicy(!queue.Pop(successor),
        "failed-save followup queue drains exactly once");
}

void ShutdownDrainsNonEmptyQueueWithActiveConsumer() {
    fthr::SaveClipQueue queue;
    CheckPolicy(queue.Push(QueueTask(1)),
        "active-shutdown fixture queues its active task");
    CheckPolicy(queue.Push(QueueTask(2)),
        "active-shutdown fixture retains a pending task");
    std::atomic<bool> consumer_started{false};
    std::atomic<bool> release_consumer{false};

    std::thread consumer([&] {
        fthr::SaveClipTask task;
        if (!queue.Pop(task)) return;
        consumer_started.store(true, std::memory_order_release);
        while (!release_consumer.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
    });
    while (!consumer_started.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }

    queue.Shutdown();
    CheckPolicy(queue.IsShutdown(),
        "shutdown is observable while a save consumer is active");
    CheckPolicy(!queue.Push(QueueTask(3)),
        "shutdown rejects a rapid save request after the active task");
    release_consumer.store(true, std::memory_order_release);
    consumer.join();

    fthr::SaveClipTask pending;
    CheckPolicy(queue.Pop(pending) && pending.task_id == 2,
        "shutdown preserves the non-empty queue for draining");
    CheckPolicy(!queue.Pop(pending),
        "shutdown reports empty only after active and pending work drain");
    queue.Shutdown();
    CheckPolicy(queue.IsShutdown(),
        "repeated shutdown remains idempotent");
}

void RapidSaveRequestsPreserveAcceptedOrder() {
    fthr::SaveClipQueue queue;
    uint32_t next_id = 1;
    for (int round = 0; round < 32; ++round) {
        for (size_t slot = 0; slot < queue.GetCapacity(); ++slot) {
            CheckPolicy(queue.Push(QueueTask(next_id)),
                "rapid save request is accepted within the queue bound");
            ++next_id;
        }
        for (uint32_t expected = next_id - 4; expected < next_id; ++expected) {
            fthr::SaveClipTask task;
            CheckPolicy(queue.Pop(task) && task.task_id == expected,
                "rapid save requests preserve FIFO order");
        }
    }
    queue.Shutdown();
    fthr::SaveClipTask task;
    CheckPolicy(!queue.Pop(task),
        "rapid request fixture shuts down with no leaked tasks");
}

} // namespace

int RunWindowsReplayPolicyTests() {
    SameAdapterVendorMatrixIsAuthoritative();
    MissingRuntimeAndUnsupportedCodecStayFailed();
    CaptureAdapterCannotBeStolenByAnotherVendor();
    ExplicitNvidiaSelectionEnablesQualifiedHybridPath();
    RawCapacityNeverOverclaimsConfiguredHistory();
    DeadlineSchedulerPreservesCadenceAcrossRefreshRates();
    EncodedReplayCapacityHasBoundedHeadroom();
    ReplayHealthRejectsSparseNativeTimeline();
    ReplayHealthAllowsHealthyAndPartialHistory();
    ReplayHealthRejectsGenerationChange();
    ReplayHealthRejectsStaleSaveBoundary();
    EncodedRingGenerationIsolatedAcrossClear();
    ActiveTruthAndGenerationAreScoped();
    ProductionThreeByThreeMatrixAndRegressionsHold();
    SaveQueueIsBoundedAndRejectsWithoutBlocking();
    SlowSaveConsumerDoesNotStopIndependentProgress();
    FailedSaveLeavesQueuedSuccessorAvailable();
    ShutdownDrainsNonEmptyQueueWithActiveConsumer();
    RapidSaveRequestsPreserveAcceptedOrder();
    std::cout << "Windows replay policy tests: " << policy_checks
              << " checks passed" << std::endl;
    return policy_checks;
}

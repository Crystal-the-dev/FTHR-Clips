#include "windows_monitor_resolver.h"
#include "encoded_ring_buffer.h"
#include "encoded_video_config.h"
#include "encoded_video_config_ffmpeg.h"
#include "replay_encoder.h"

#include <cstdlib>
#include <iostream>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using fthr::monitor::AdapterLuid;
using fthr::monitor::DxgiOutputIdentity;
using fthr::monitor::MonitorResolveError;
using fthr::monitor::MonitorResolver;
using fthr::monitor::MonitorTopologyEntry;
using fthr::EncodedPacketFormat;
using fthr::EncodedRingBuffer;
using fthr::EncodedVideoConfig;
using fthr::VideoCodec;

int checks = 0;

void Check(bool condition, const char* message) {
    ++checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

MonitorTopologyEntry Monitor(
    const wchar_t* path,
    AdapterLuid luid,
    uint32_t source_id,
    uint32_t target_id,
    uintptr_t hmonitor,
    const wchar_t* gdi_name,
    bool primary = false) {
    MonitorTopologyEntry entry;
    entry.monitor_device_path = path;
    entry.adapter_luid = luid;
    entry.source_id = source_id;
    entry.target_id = target_id;
    entry.hmonitor = hmonitor;
    entry.source_gdi_name = gdi_name;
    entry.primary = primary;
    entry.active = true;
    return entry;
}

class FakeTopologySource final : public fthr::monitor::IMonitorTopologySource {
public:
    explicit FakeTopologySource(std::vector<MonitorTopologyEntry> entries)
        : entries_(std::move(entries)) {}

    fthr::monitor::TopologyQueryResult QueryActiveTopology() override {
        return {MonitorResolveError::None, entries_, {}};
    }

    void Set(std::vector<MonitorTopologyEntry> entries) {
        entries_ = std::move(entries);
    }

private:
    std::vector<MonitorTopologyEntry> entries_;
};

void OneMonitorOneAdapter() {
    FakeTopologySource source({Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true)});
    auto result = MonitorResolver(source).Resolve(L"path-a");
    Check(result.ok(), "one monitor resolves");
    Check(result.monitor.adapter_luid == AdapterLuid{1, 0}, "one adapter identity preserved");
}

void TwoMonitorsSameAdapter() {
    FakeTopologySource source({
        Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true),
        Monitor(L"PATH-B", {1, 0}, 1, 11, 200, L"DISPLAY2")});
    auto result = MonitorResolver(source).Resolve(L"path-b");
    Check(result.ok() && result.monitor.target_id == 11, "same-adapter target identity selected");
}

void SecondaryMonitorSelected() {
    FakeTopologySource source({
        Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true),
        Monitor(L"PATH-B", {1, 0}, 1, 11, 200, L"DISPLAY2")});
    auto result = MonitorResolver(source).Resolve(L"PATH-B");
    Check(result.ok() && result.monitor.hmonitor == 200, "secondary HMONITOR selected");
}

void TwoAdaptersOneMonitorEach() {
    FakeTopologySource source({
        Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true),
        Monitor(L"PATH-B", {2, -1}, 0, 20, 200, L"DISPLAY2")});
    auto result = MonitorResolver(source).Resolve(L"PATH-B");
    Check(result.ok() && result.monitor.adapter_luid == AdapterLuid{2, -1},
          "selected monitor owns second adapter");
}

void EnumerationOrderChanges() {
    const auto a = Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true);
    const auto b = Monitor(L"PATH-B", {2, 0}, 0, 20, 200, L"DISPLAY2");
    FakeTopologySource source({a, b});
    const auto first = MonitorResolver(source).Resolve(L"PATH-B");
    source.Set({b, a});
    const auto second = MonitorResolver(source).Resolve(L"PATH-B");
    Check(first.ok() && second.ok() && first.monitor == second.monitor,
          "enumeration order does not change identity");
}

void PrimaryMonitorChanges() {
    auto a = Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true);
    auto b = Monitor(L"PATH-B", {1, 0}, 1, 11, 200, L"DISPLAY2", false);
    FakeTopologySource source({a, b});
    const auto first = MonitorResolver(source).Resolve(L"PATH-B");
    a.primary = false;
    b.primary = true;
    source.Set({a, b});
    const auto second = MonitorResolver(source).Resolve(L"PATH-B");
    Check(first.ok() && second.ok() && second.monitor.hmonitor == 200,
          "primary change does not redirect selection");
}

void SelectedMonitorMissing() {
    FakeTopologySource source({Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true)});
    const auto result = MonitorResolver(source).Resolve(L"PATH-B");
    Check(result.error == MonitorResolveError::MonitorNotFound,
          "missing monitor returns typed error");
}

void DevicePathNormalization() {
    Check(fthr::monitor::NormalizeMonitorDevicePath(L"  \\\\?\\DISPLAY/ABC  ") ==
              L"\\\\?\\display\\abc",
          "device path normalized case/slashes/outer whitespace");
}

void MatchingLuidMultipleOutputs() {
    const auto selected = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const std::vector<DxgiOutputIdentity> outputs{
        {{7, 0}, 100, L"DISPLAY1", 0},
        {{7, 0}, 200, L"DISPLAY2", 1}};
    const auto result = fthr::monitor::ResolveDxgiOutput(selected, outputs);
    Check(result.ok() && result.output_index == 1, "matching LUID still resolves exact output");
}

void OutputRemovedAfterResolution() {
    const auto selected = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const std::vector<DxgiOutputIdentity> outputs{{{7, 0}, 100, L"DISPLAY1", 0}};
    const auto result = fthr::monitor::ResolveDxgiOutput(selected, outputs);
    Check(result.error == MonitorResolveError::OutputResolutionFailed,
          "removed output fails instead of redirecting");
}

void StaleTransientMappingRejected() {
    const auto old_entry = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const auto changed_entry = Monitor(L"PATH-B", {8, 0}, 0, 21, 300, L"DISPLAY3");
    FakeTopologySource source({changed_entry});
    Check(!MonitorResolver(source).IsCurrent(old_entry), "stale transient mapping rejected");
}

void NoFallbackToPrimary() {
    FakeTopologySource source({Monitor(L"PATH-A", {1, 0}, 0, 10, 100, L"DISPLAY1", true)});
    const auto result = MonitorResolver(source).Resolve(L"MISSING");
    Check(!result.ok() && result.monitor.hmonitor == 0,
          "resolver never returns primary for missing selection");
}

void NoFallbackToOutputZero() {
    const auto selected = Monitor(L"PATH-B", {7, 0}, 1, 11, 200, L"DISPLAY2");
    const std::vector<DxgiOutputIdentity> outputs{{{7, 0}, 100, L"DISPLAY1", 0}};
    const auto result = fthr::monitor::ResolveDxgiOutput(selected, outputs);
    Check(!result.ok() && result.output_index == UINT32_MAX,
          "resolver never returns output zero for missing selected output");
}

EncodedVideoConfig VideoConfig(VideoCodec codec) {
    EncodedVideoConfig config;
    config.codec = codec;
    config.width = 1920;
    config.height = 1080;
    config.frame_rate = {60, 1};
    config.time_base = {1, 60};
    config.bitrate_kbps = 16000;
    config.max_keyframe_interval_frames = 240;
    config.max_b_frames = 0;
    config.packet_format = codec == VideoCodec::AV1
        ? EncodedPacketFormat::LowOverheadObu
        : EncodedPacketFormat::LengthPrefixedNalUnits;
    return config;
}

void H264CodecMapsCorrectly() {
    Check(fthr::ToAvCodecId(VideoCodec::H264) == AV_CODEC_ID_H264,
          "H.264 maps to FFmpeg H.264 codec id");
}

void HevcConfigExistsWithoutProductionEnablement() {
    const auto config = VideoConfig(VideoCodec::HEVC);
    Check(config.codec == VideoCodec::HEVC
              && fthr::ToAvCodecId(config.codec) == AV_CODEC_ID_HEVC,
          "HEVC config and mux mapping exist");
    Check(!fthr::IsProductionReplayCodecEnabled(config.codec),
          "HEVC remains disabled in production");
}

void Av1ConfigExistsWithoutProductionEnablement() {
    const auto config = VideoConfig(VideoCodec::AV1);
    Check(config.codec == VideoCodec::AV1
              && fthr::ToAvCodecId(config.codec) == AV_CODEC_ID_AV1,
          "AV1 config and mux mapping exist");
    Check(!fthr::IsProductionReplayCodecEnabled(config.codec),
          "AV1 remains disabled in production");
}

void RingStoresAndSnapshotsGenericConfig() {
    EncodedRingBuffer ring(8, 60, 1000);
    auto config = VideoConfig(VideoCodec::HEVC);
    config.codec_extradata = {1, 2, 3, 4};
    ring.SetVideoConfig(config);
    const uint8_t packet[] = {9, 8, 7};
    ring.Push(packet, sizeof(packet), 41, true, 1000);

    const auto snapshot = ring.TakeSnapshotByTime(1, 1000);
    Check(snapshot.video_config == config,
          "ring snapshot preserves codec-neutral video config");
    Check(snapshot.video_config.codec_extradata == config.codec_extradata,
          "codec extradata survives ring snapshot");
}

void PacketTimingAndKeyframeMetadataRemainUnchanged() {
    EncodedRingBuffer ring(8, 60, 1000);
    ring.SetVideoConfig(VideoConfig(VideoCodec::H264));
    const uint8_t packet[] = {4, 5, 6};
    ring.Push(packet, sizeof(packet), 123, true, 2000);

    const auto snapshot = ring.TakeSnapshotByTime(1, 2000);
    Check(snapshot.packets.size() == 1 && snapshot.packets[0].pts == 123,
          "packet PTS unchanged through codec-neutral ring");
    Check(snapshot.packets[0].is_keyframe,
          "keyframe metadata unchanged through codec-neutral ring");
}

void CodecNeutralRingKeepsTimestampIntervalSelection() {
    EncodedRingBuffer ring(16, 1, 1000);
    ring.SetVideoConfig(VideoConfig(VideoCodec::H264));
    const uint8_t packet[] = {1};
    for (int64_t second = 1; second <= 5; ++second) {
        ring.Push(packet, sizeof(packet), second - 1,
                  second == 1 || second == 4, second * 1000);
    }

    const auto snapshot = ring.TakeSnapshotByTime(2, 5000);
    Check(snapshot.full_history && snapshot.presentation_start_qpc_s == 3.0,
          "AUDIT-042 timestamp boundary remains two seconds before save");
    Check(!snapshot.packets.empty() && snapshot.packets.front().is_keyframe,
          "timestamp selection retains decoder keyframe pre-roll");
}

void ReplayEncoderInterfaceIsPolymorphic() {
    Check(std::has_virtual_destructor_v<fthr::IReplayEncoder>,
          "replay encoder interface has a virtual destructor");
    Check(fthr::IsProductionReplayCodecEnabled(VideoCodec::H264),
          "only existing native H.264 is production enabled");
}

} // namespace

int main() {
    OneMonitorOneAdapter();
    TwoMonitorsSameAdapter();
    SecondaryMonitorSelected();
    TwoAdaptersOneMonitorEach();
    EnumerationOrderChanges();
    PrimaryMonitorChanges();
    SelectedMonitorMissing();
    DevicePathNormalization();
    MatchingLuidMultipleOutputs();
    OutputRemovedAfterResolution();
    StaleTransientMappingRejected();
    NoFallbackToPrimary();
    NoFallbackToOutputZero();
    H264CodecMapsCorrectly();
    HevcConfigExistsWithoutProductionEnablement();
    Av1ConfigExistsWithoutProductionEnablement();
    RingStoresAndSnapshotsGenericConfig();
    PacketTimingAndKeyframeMetadataRemainUnchanged();
    CodecNeutralRingKeepsTimestampIntervalSelection();
    ReplayEncoderInterfaceIsPolymorphic();
    std::cout << "FTHRclips_tests: 20 scenarios passed ("
              << checks << " checks)" << std::endl;
    return 0;
}

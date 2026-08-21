#include "windows_monitor_resolver.h"
#include "encoded_ring_buffer.h"
#include "encoded_video_config.h"
#include "encoded_video_config_ffmpeg.h"
#include "nvenc_codec_config.h"
#include "replay_encoder.h"

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <iterator>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

int RunAmfReplayEncoderTests();

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

bool GuidEquals(const GUID& left, const GUID& right) {
    return std::memcmp(&left, &right, sizeof(GUID)) == 0;
}

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
    config.packet_format = codec == VideoCodec::H264
        ? EncodedPacketFormat::LengthPrefixedNalUnits
        : codec == VideoCodec::HEVC
            ? EncodedPacketFormat::AnnexBNalUnits
            : EncodedPacketFormat::LowOverheadObu;
    return config;
}

void NativeNvencCodecSelectionCoversNvidiaMatrix() {
    const auto* h264 = fthr::GetNvencCodecSelection(VideoCodec::H264);
    const auto* hevc = fthr::GetNvencCodecSelection(VideoCodec::HEVC);
    const auto* av1 = fthr::GetNvencCodecSelection(VideoCodec::AV1);

    Check(h264 && GuidEquals(h264->encode_guid, NV_ENC_CODEC_H264_GUID),
          "H.264 selects the native NVENC H.264 GUID");
    Check(h264 && GuidEquals(h264->profile_guid, NV_ENC_H264_PROFILE_MAIN_GUID),
          "H.264 selects the NVENC main profile");
    Check(hevc && GuidEquals(hevc->encode_guid, NV_ENC_CODEC_HEVC_GUID),
          "HEVC selects the native NVENC HEVC GUID");
    Check(hevc && GuidEquals(hevc->profile_guid, NV_ENC_HEVC_PROFILE_MAIN_GUID),
          "HEVC selects the NVENC main profile");
    Check(av1 && GuidEquals(av1->encode_guid, NV_ENC_CODEC_AV1_GUID),
          "AV1 selects the native NVENC AV1 GUID");
    Check(av1 && GuidEquals(av1->profile_guid, NV_ENC_AV1_PROFILE_MAIN_GUID),
          "AV1 selects the NVENC main profile");
    Check(h264 && std::string(h264->active_codec_name) == "h264_nvenc",
          "H.264 has a truthful active codec name");
    Check(hevc && std::string(hevc->active_codec_name) == "hevc_nvenc",
          "HEVC has a truthful active codec name");
    Check(av1 && std::string(av1->active_codec_name) == "av1_nvenc",
          "AV1 has a truthful active codec name");
}

void UnsupportedNvencCodecIsRejected() {
    const GUID h264_only[] = {NV_ENC_CODEC_H264_GUID};
    Check(fthr::IsNvencCodecSupported(
              VideoCodec::H264, h264_only, std::size(h264_only)),
          "advertised H.264 GUID is accepted");
    Check(!fthr::IsNvencCodecSupported(
              VideoCodec::HEVC, h264_only, std::size(h264_only)),
          "missing HEVC GUID is rejected");
    Check(!fthr::IsNvencCodecSupported(
              VideoCodec::AV1, h264_only, std::size(h264_only)),
          "missing AV1 GUID is rejected without becoming H.264");
    Check(fthr::GetNvencCodecSelection(static_cast<VideoCodec>(99)) == nullptr,
          "unknown codec has no native NVENC selection");

    const NV_ENC_BUFFER_FORMAT formats[] = {NV_ENC_BUFFER_FORMAT_NV12};
    Check(!fthr::IsNvencInputFormatSupported(
              NV_ENC_BUFFER_FORMAT_ARGB, formats, std::size(formats)),
          "missing ARGB support rejects the existing D3D11 frame path");
}

void NativeNvencLowLatencyConfigurationIsCodecSpecific() {
    NV_ENC_CONFIG h264 = {};
    NV_ENC_CONFIG hevc = {};
    NV_ENC_CONFIG av1 = {};

    Check(fthr::ConfigureNvencCodec(VideoCodec::H264, 60, h264),
          "H.264 NVENC config is produced");
    Check(fthr::ConfigureNvencCodec(VideoCodec::HEVC, 60, hevc),
          "HEVC NVENC config is produced");
    Check(fthr::ConfigureNvencCodec(VideoCodec::AV1, 60, av1),
          "AV1 NVENC config is produced");

    Check(h264.frameIntervalP == 1 && h264.gopLength == 240
              && h264.encodeCodecConfig.h264Config.idrPeriod == 240,
          "H.264 preserves no-B-frame four-second GOP behavior");
    Check(hevc.frameIntervalP == 1 && hevc.gopLength == 240
              && hevc.encodeCodecConfig.hevcConfig.idrPeriod == 240,
          "HEVC uses no B-frames and a four-second IDR bound");
    Check(av1.frameIntervalP == 1 && av1.gopLength == 240
              && av1.encodeCodecConfig.av1Config.idrPeriod == 240,
          "AV1 uses no B-frames and a four-second keyframe bound");
    Check(av1.encodeCodecConfig.av1Config.outputAnnexBFormat == 0,
          "AV1 emits MP4-compatible low-overhead OBUs");
    Check(av1.encodeCodecConfig.av1Config.disableSeqHdr == 0
              && av1.encodeCodecConfig.av1Config.repeatSeqHdr == 1,
          "AV1 repeats its sequence header on MP4 keyframes");
}

void NativeNvencVideoConfigsPropagateCodecData() {
    const std::vector<uint8_t> h264_extra{1, 100, 0, 31};
    const std::vector<uint8_t> hevc_extra{0, 0, 0, 1, 64};
    const std::vector<uint8_t> av1_extra{0x0A, 0x02, 0x18, 0x00};

    const auto h264 = fthr::BuildNvencVideoConfig(
        VideoCodec::H264, 1920, 1080, 60, 16000, h264_extra);
    const auto hevc = fthr::BuildNvencVideoConfig(
        VideoCodec::HEVC, 1920, 1080, 60, 16000, hevc_extra);
    const auto av1 = fthr::BuildNvencVideoConfig(
        VideoCodec::AV1, 1920, 1080, 60, 16000, av1_extra);

    Check(h264.codec == VideoCodec::H264
              && h264.packet_format == EncodedPacketFormat::LengthPrefixedNalUnits
              && h264.codec_extradata == h264_extra,
          "H.264 config propagates AVCC extradata");
    Check(hevc.codec == VideoCodec::HEVC
              && hevc.packet_format == EncodedPacketFormat::AnnexBNalUnits
              && hevc.codec_extradata == hevc_extra,
          "HEVC config propagates Annex B VPS/SPS/PPS");
    Check(av1.codec == VideoCodec::AV1
              && av1.packet_format == EncodedPacketFormat::LowOverheadObu
              && av1.codec_extradata == av1_extra,
          "AV1 config propagates its sequence header OBUs");
}

void H264CodecMapsCorrectly() {
    Check(fthr::ToAvCodecId(VideoCodec::H264) == AV_CODEC_ID_H264,
          "H.264 maps to FFmpeg H.264 codec id");
}

void HevcConfigIsProductionEnabled() {
    const auto config = VideoConfig(VideoCodec::HEVC);
    Check(config.codec == VideoCodec::HEVC
              && fthr::ToAvCodecId(config.codec) == AV_CODEC_ID_HEVC,
          "HEVC config and mux mapping exist");
    Check(fthr::IsProductionReplayCodecEnabled(config.codec),
          "HEVC is enabled for the native NVIDIA production backend");
}

void Av1ConfigIsProductionEnabled() {
    const auto config = VideoConfig(VideoCodec::AV1);
    Check(config.codec == VideoCodec::AV1
              && fthr::ToAvCodecId(config.codec) == AV_CODEC_ID_AV1,
          "AV1 config and mux mapping exist");
    Check(fthr::IsProductionReplayCodecEnabled(config.codec),
          "AV1 is enabled for the native NVIDIA production backend");
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

void RingKeepsOneImmutableConfigPerGeneration() {
    EncodedRingBuffer ring(8, 60, 1000);
    const auto h264 = VideoConfig(VideoCodec::H264);
    const auto hevc = VideoConfig(VideoCodec::HEVC);
    Check(ring.SetVideoConfig(h264), "first generation config is accepted");
    const uint8_t packet[] = {1, 2, 3};
    ring.Push(packet, sizeof(packet), 0, true, 1000);
    Check(!ring.SetVideoConfig(hevc),
          "published H.264 generation cannot be relabeled HEVC");
    Check(ring.TakeSnapshotByTime(1, 1000).video_config.codec == VideoCodec::H264,
          "snapshot retains the original generation codec");
}

void ReplayEncoderInterfaceIsPolymorphic() {
    Check(std::has_virtual_destructor_v<fthr::IReplayEncoder>,
          "replay encoder interface has a virtual destructor");
    Check(fthr::IsProductionReplayBackendEnabled(
              fthr::EncoderVendor::Nvidia, VideoCodec::H264)
              && fthr::IsProductionReplayBackendEnabled(
                  fthr::EncoderVendor::Nvidia, VideoCodec::HEVC)
              && fthr::IsProductionReplayBackendEnabled(
                  fthr::EncoderVendor::Nvidia, VideoCodec::AV1),
          "NVIDIA native production backend enables all three codecs");
    Check(fthr::IsProductionReplayBackendEnabled(
              fthr::EncoderVendor::Amd, VideoCodec::H264)
              && fthr::IsProductionReplayBackendEnabled(
                  fthr::EncoderVendor::Amd, VideoCodec::HEVC)
              && fthr::IsProductionReplayBackendEnabled(
                  fthr::EncoderVendor::Amd, VideoCodec::AV1),
          "AMD FFmpeg AMF production backend enables all three codecs");
    Check(!fthr::IsProductionReplayBackendEnabled(
              fthr::EncoderVendor::Intel, VideoCodec::H264),
          "Intel production encoding remains disabled");
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
    NativeNvencCodecSelectionCoversNvidiaMatrix();
    UnsupportedNvencCodecIsRejected();
    NativeNvencLowLatencyConfigurationIsCodecSpecific();
    NativeNvencVideoConfigsPropagateCodecData();
    H264CodecMapsCorrectly();
    HevcConfigIsProductionEnabled();
    Av1ConfigIsProductionEnabled();
    RingStoresAndSnapshotsGenericConfig();
    PacketTimingAndKeyframeMetadataRemainUnchanged();
    CodecNeutralRingKeepsTimestampIntervalSelection();
    RingKeepsOneImmutableConfigPerGeneration();
    ReplayEncoderInterfaceIsPolymorphic();
    const int amf_checks = RunAmfReplayEncoderTests();
    std::cout << "FTHRclips_tests: 49 scenarios passed ("
              << (checks + amf_checks) << " checks total)" << std::endl;
    return 0;
}

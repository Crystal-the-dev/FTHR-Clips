#include "encoded_ring_buffer.h"
#include "encoded_video_config_ffmpeg.h"
#include "ffmpeg_amf_replay_encoder.h"
#include "replay_encoder.h"
#include "transactional_save.h"

#include <windows.h>
#include <d3d11.h>

#include <cstdlib>
#include <deque>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using fthr::AmfCodecSelection;
using fthr::EncodedPacketFormat;
using fthr::EncodedRingBuffer;
using fthr::EncodedVideoConfig;
using fthr::EncoderConfig;
using fthr::EncoderVendor;
using fthr::FfmpegAmfReplayEncoder;
using fthr::ReplayEncoderBackend;
using fthr::VideoCodec;
using fthr::detail::AmfPacketView;
using fthr::detail::AmfReceiveStatus;
using fthr::detail::AmfSubmitStatus;
using fthr::detail::IAmfCodecSession;

int amf_checks = 0;

void CheckAmf(bool condition, const char* message) {
    ++amf_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

EncodedVideoConfig ConfigFor(VideoCodec codec) {
    return fthr::BuildAmfVideoConfig(
        codec, 1920, 1080, 60, 16000, {1, 2, 3, 4});
}

class FakeAmfSession final : public IAmfCodecSession {
public:
    bool initialize_result = true;
    std::string initialize_error;
    bool return_wrong_codec = false;
    bool submit_error = false;
    bool flush_error = false;
    bool emit_packet = false;
    bool emit_keyframe = false;
    bool prepare_gpu_result = true;
    std::string prepare_gpu_error;
    bool flush_called = false;
    uint32_t input_subresource = 0;
    int64_t submitted_pts = -1;
    bool submitted_force_keyframe = false;

    bool Initialize(
        const EncoderConfig& config,
        ID3D11Device*,
        ID3D11DeviceContext*,
        const AmfCodecSelection& selection,
        EncodedVideoConfig& video_config,
        std::string& error) override {
        if (!initialize_result) {
            error = initialize_error;
            return false;
        }
        const uint32_t width = config.enc_width > 0
            ? config.enc_width : config.src_width;
        const uint32_t height = config.enc_height > 0
            ? config.enc_height : config.src_height;
        video_config = fthr::BuildAmfVideoConfig(
            return_wrong_codec ? VideoCodec::H264 : selection.codec,
            width,
            height,
            config.fps,
            config.bitrate_kbps,
            {1, 2, 3, 4});
        return true;
    }

    bool PrepareGpuFrame(
        ID3D11Texture2D*,
        uint32_t,
        std::string& error) override {
        if (!prepare_gpu_result) {
            error = prepare_gpu_error;
            return false;
        }
        return true;
    }

    ID3D11Texture2D* GetCurrentInputTexture() const noexcept override {
        return reinterpret_cast<ID3D11Texture2D*>(1);
    }

    uint32_t GetCurrentInputSubresource() const noexcept override {
        return input_subresource;
    }

    AmfSubmitStatus SubmitFrame(
        int64_t pts, bool force_keyframe, std::string& error) override {
        submitted_pts = pts;
        submitted_force_keyframe = force_keyframe;
        if (submit_error) {
            error = "simulated AMF submit failure";
            return AmfSubmitStatus::Error;
        }
        if (emit_packet) {
            packets_.push_back({pts, emit_keyframe});
        }
        return AmfSubmitStatus::Accepted;
    }

    AmfReceiveStatus ReceivePacket(
        AmfPacketView& packet, std::string&) override {
        if (packets_.empty()) {
            return flush_called
                ? AmfReceiveStatus::EndOfStream
                : AmfReceiveStatus::NeedMoreInput;
        }
        current_ = packets_.front();
        packets_.pop_front();
        packet.data = packet_bytes_;
        packet.size = sizeof(packet_bytes_);
        packet.pts = current_.first;
        packet.dts = current_.first;
        packet.keyframe = current_.second;
        return AmfReceiveStatus::Packet;
    }

    AmfSubmitStatus SendEndOfStream(std::string& error) override {
        flush_called = true;
        if (flush_error) {
            error = "simulated AMF flush failure";
            return AmfSubmitStatus::Error;
        }
        return AmfSubmitStatus::Accepted;
    }

    void Reset() noexcept override {
        packets_.clear();
    }

private:
    uint8_t packet_bytes_[3]{4, 5, 6};
    std::deque<std::pair<int64_t, bool>> packets_;
    std::pair<int64_t, bool> current_{};
};

EncoderConfig TestEncoderConfig() {
    EncoderConfig config;
    config.src_width = 1920;
    config.src_height = 1080;
    config.enc_width = 1920;
    config.enc_height = 1080;
    config.fps = 60;
    config.bitrate_kbps = 16000;
    return config;
}

bool InitializeWithFailure(VideoCodec codec, const char* error_fragment) {
    auto session = std::make_unique<FakeAmfSession>();
    session->initialize_result = false;
    session->initialize_error = error_fragment;
    FfmpegAmfReplayEncoder encoder(codec, std::move(session));
    const bool initialized = encoder.Initialize(
        TestEncoderConfig(),
        reinterpret_cast<ID3D11Device*>(1),
        reinterpret_cast<ID3D11DeviceContext*>(1),
        [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
        false);
    return !initialized
        && encoder.GetLastError().find(error_fragment) != std::string::npos;
}

void AmdAdapterSelectsAmf() {
    CheckAmf(fthr::SelectProductionReplayBackend(
                  EncoderVendor::Amd, VideoCodec::H264)
              == ReplayEncoderBackend::FfmpegAmf,
        "AMD adapter selects FFmpeg AMF");
}

void NvidiaStillSelectsNativeNvenc() {
    CheckAmf(fthr::SelectProductionReplayBackend(
                  EncoderVendor::Nvidia, VideoCodec::AV1)
              == ReplayEncoderBackend::NativeNvenc,
        "NVIDIA adapter remains on native NVENC");
}

void IntelStageDoesNotAffectAmdPolicy() {
    CheckAmf(fthr::SelectProductionReplayBackend(
                  EncoderVendor::Intel, VideoCodec::H264)
              == ReplayEncoderBackend::FfmpegQsv,
        "Intel now selects QSV without changing AMD AMF policy");
}

void RequestedCodecsMapToExactAmfNames() {
    CheckAmf(std::string(fthr::GetAmfCodecSelection(VideoCodec::H264)->encoder_name)
                  == "h264_amf",
        "H.264 maps to h264_amf");
    CheckAmf(std::string(fthr::GetAmfCodecSelection(VideoCodec::HEVC)->encoder_name)
                  == "hevc_amf",
        "HEVC maps to hevc_amf");
    CheckAmf(std::string(fthr::GetAmfCodecSelection(VideoCodec::AV1)->encoder_name)
                  == "av1_amf",
        "AV1 maps to av1_amf");
}

void MissingEncodersFailHonestly() {
    CheckAmf(InitializeWithFailure(VideoCodec::H264, "h264_amf not found"),
        "missing h264_amf fails honestly");
    CheckAmf(InitializeWithFailure(VideoCodec::HEVC, "hevc_amf not found"),
        "missing hevc_amf fails honestly");
    CheckAmf(InitializeWithFailure(VideoCodec::AV1, "av1_amf not found"),
        "missing av1_amf fails honestly");
}

void InitializationFailuresPropagate() {
    CheckAmf(InitializeWithFailure(VideoCodec::H264, "encoder open failed"),
        "encoder open failure propagates");
    CheckAmf(InitializeWithFailure(VideoCodec::HEVC, "hevc_amf open failed"),
        "HEVC encoder initialization failure propagates");
    CheckAmf(InitializeWithFailure(VideoCodec::AV1, "AV1 unavailable on adapter"),
        "AV1 hardware capability failure propagates");
    CheckAmf(InitializeWithFailure(VideoCodec::H264, "D3D11 hw context failed"),
        "D3D11 hw context failure propagates");
    ID3D11Device* warp_device = nullptr;
    ID3D11DeviceContext* warp_context = nullptr;
    D3D_FEATURE_LEVEL feature_level{};
    const HRESULT device_result = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_WARP,
        nullptr,
        0,
        nullptr,
        0,
        D3D11_SDK_VERSION,
        &warp_device,
        &feature_level,
        &warp_context);
    FfmpegAmfReplayEncoder real_encoder(VideoCodec::H264);
    const bool wrong_device_rejected = SUCCEEDED(device_result)
        && !real_encoder.Initialize(
            TestEncoderConfig(),
            warp_device,
            warp_context,
            [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
            false)
        && real_encoder.GetLastError().find("not AMD") != std::string::npos;
    if (warp_context) warp_context->Release();
    if (warp_device) warp_device->Release();
    CheckAmf(wrong_device_rejected,
        "real AMF session rejects a non-AMD D3D11 device");
}

void SilentCodecFallbackIsRejected() {
    auto session = std::make_unique<FakeAmfSession>();
    session->return_wrong_codec = true;
    FfmpegAmfReplayEncoder encoder(VideoCodec::AV1, std::move(session));
    CheckAmf(!encoder.Initialize(
                  TestEncoderConfig(),
                  reinterpret_cast<ID3D11Device*>(1),
                  reinterpret_cast<ID3D11DeviceContext*>(1),
                  [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
                  false),
        "requested AV1 cannot silently become H.264");
}

void AmfVideoConfigsAreCodecCorrect() {
    const auto h264 = ConfigFor(VideoCodec::H264);
    const auto hevc = ConfigFor(VideoCodec::HEVC);
    const auto av1 = ConfigFor(VideoCodec::AV1);
    CheckAmf(h264.codec == VideoCodec::H264
            && h264.packet_format == EncodedPacketFormat::LengthPrefixedNalUnits,
        "AMF H.264 config is AVCC-compatible");
    CheckAmf(hevc.codec == VideoCodec::HEVC
            && hevc.packet_format == EncodedPacketFormat::AnnexBNalUnits,
        "AMF HEVC config preserves Annex B");
    CheckAmf(av1.codec == VideoCodec::AV1
            && av1.packet_format == EncodedPacketFormat::LowOverheadObu,
        "AMF AV1 config preserves low-overhead OBUs");
}

void RingAndMuxRemainCodecNeutral() {
    EncodedRingBuffer ring(8, 60, 1000);
    const auto config = ConfigFor(VideoCodec::HEVC);
    CheckAmf(ring.SetVideoConfig(config)
            && ring.TakeSnapshotByTime(1, 1000).video_config == config,
        "ring preserves AMF video config");
    CheckAmf(fthr::ToAvCodecId(VideoCodec::AV1) == AV_CODEC_ID_AV1,
        "mux mapping preserves requested AMF codec");

    bool cleanup_called = false;
    bool rename_called = false;
    bool writer_saw_partial = false;
    fthr::transactional_save::FileOps operations;
    operations.exists = [](const std::filesystem::path&, std::error_code&) {
        return false;
    };
    operations.remove = [&](const std::filesystem::path&, std::error_code&) {
        cleanup_called = true;
        return true;
    };
    operations.rename_no_replace = [&](
        const std::filesystem::path&,
        const std::filesystem::path&,
        std::error_code&) {
        rename_called = true;
        return true;
    };
    const auto failed_mux = fthr::transactional_save::Run(
        L"amf-fault.mp4",
        [&](const std::filesystem::path& temporary_path, std::string& error) {
            writer_saw_partial = temporary_path.extension() == L".partial";
            error = "simulated AMF mux failure";
            return false;
        },
        operations);
    CheckAmf(!failed_mux.success
            && failed_mux.failure == fthr::transactional_save::Failure::Writer
            && writer_saw_partial
            && cleanup_called
            && !rename_called,
        "AMF mux failure cleans the partial file and never publishes success");
}

void PacketMetadataPropagates() {
    auto session = std::make_unique<FakeAmfSession>();
    auto* fake = session.get();
    fake->emit_packet = true;
    fake->emit_keyframe = true;
    fake->input_subresource = 3;
    int64_t callback_pts = -1;
    bool callback_keyframe = false;
    FfmpegAmfReplayEncoder encoder(VideoCodec::H264, std::move(session));
    CheckAmf(encoder.Initialize(
                  TestEncoderConfig(),
                  reinterpret_cast<ID3D11Device*>(1),
                  reinterpret_cast<ID3D11DeviceContext*>(1),
                  [&](const uint8_t*, uint32_t, int64_t pts, bool keyframe, int64_t) {
                      callback_pts = pts;
                      callback_keyframe = keyframe;
                  },
                   false)
            && encoder.PrepareGpuFrame(nullptr, 0)
            && encoder.EncodeFrame(1)
            && callback_pts == fake->submitted_pts
            && encoder.GetCurrentInputSubresource() == 3,
        "AMF packet timestamp propagates through wrapper");
    CheckAmf(callback_keyframe,
        "AMF keyframe flag propagates through wrapper");
    CheckAmf(encoder.PrepareGpuFrame(nullptr, 0) && encoder.EncodeFrame(2)
            && !fake->submitted_force_keyframe,
        "normal following frame does not force an IDR");
    encoder.RequestKeyframe();
    CheckAmf(encoder.PrepareGpuFrame(nullptr, 0) && encoder.EncodeFrame(3)
            && fake->submitted_force_keyframe,
        "recording start requests an immediate random-access frame");
    CheckAmf(encoder.PrepareGpuFrame(nullptr, 0) && encoder.EncodeFrame(4)
            && !fake->submitted_force_keyframe,
        "keyframe request is consumed exactly once");

}

void ScaledAmfConfigUsesEncoderDimensions() {
    auto session = std::make_unique<FakeAmfSession>();
    FfmpegAmfReplayEncoder encoder(VideoCodec::H264, std::move(session));
    EncoderConfig config = TestEncoderConfig();
    config.src_width = 2560;
    config.src_height = 1440;
    config.enc_width = 1920;
    config.enc_height = 1080;
    config.scaling_mode = 1;
    CheckAmf(encoder.Initialize(
                  config,
                  reinterpret_cast<ID3D11Device*>(1),
                  reinterpret_cast<ID3D11DeviceContext*>(1),
                  [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
                  false),
        "AMF fake accepts a scaled encoder configuration");
    const auto active = encoder.GetVideoConfig();
    CheckAmf(active.width == 1920 && active.height == 1080,
        "AMF active config reports encoder dimensions");
    CheckAmf(encoder.PrepareGpuFrame(nullptr, 0),
        "AMF scaled frame preparation reaches the backend seam");
}

void AmfGpuPreparationFailurePropagates() {
    auto session = std::make_unique<FakeAmfSession>();
    auto* fake = session.get();
    fake->prepare_gpu_result = false;
    fake->prepare_gpu_error = "simulated AMF PrepareGpuFrame failure";
    FfmpegAmfReplayEncoder encoder(VideoCodec::H264, std::move(session));
    CheckAmf(encoder.Initialize(
                  TestEncoderConfig(),
                  reinterpret_cast<ID3D11Device*>(1),
                  reinterpret_cast<ID3D11DeviceContext*>(1),
                  [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
                  false)
            && !encoder.PrepareGpuFrame(nullptr, 0)
            && encoder.GetLastError().find("PrepareGpuFrame failure")
                != std::string::npos,
        "AMF GPU preparation failure propagates without fallback");
}

void AmfNoPacketOutputIsObservableAtTheSeam() {
    auto session = std::make_unique<FakeAmfSession>();
    auto* fake = session.get();
    fake->emit_packet = false;
    FfmpegAmfReplayEncoder encoder(VideoCodec::AV1, std::move(session));
    CheckAmf(encoder.Initialize(
                  TestEncoderConfig(),
                  reinterpret_cast<ID3D11Device*>(1),
                  reinterpret_cast<ID3D11DeviceContext*>(1),
                  [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
                  false)
            && encoder.PrepareGpuFrame(nullptr, 0)
            && encoder.EncodeFrame(1)
            && fake->submitted_pts >= 0,
        "AMF output-without-packet remains observable to the stall watchdog seam");
}

void GenerationConfigCannotChange() {
    EncodedRingBuffer ring(8, 60, 1000);
    CheckAmf(ring.SetVideoConfig(ConfigFor(VideoCodec::H264))
            && !ring.SetVideoConfig(ConfigFor(VideoCodec::AV1)),
        "AMF generation rejects a codec/config change");
}

void FlushAndErrorsAreObservable() {
    auto session = std::make_unique<FakeAmfSession>();
    auto* fake = session.get();
    FfmpegAmfReplayEncoder encoder(VideoCodec::HEVC, std::move(session));
    CheckAmf(encoder.Initialize(
                  TestEncoderConfig(),
                  reinterpret_cast<ID3D11Device*>(1),
                  reinterpret_cast<ID3D11DeviceContext*>(1),
                  [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
                  false),
        "AMF fake session initializes for flush test");
    encoder.Shutdown();
    CheckAmf(fake->flush_called,
        "AMF shutdown flushes the codec session");

    auto failing_session = std::make_unique<FakeAmfSession>();
    failing_session->submit_error = true;
    FfmpegAmfReplayEncoder failing(VideoCodec::AV1, std::move(failing_session));
    CheckAmf(failing.Initialize(
                  TestEncoderConfig(),
                  reinterpret_cast<ID3D11Device*>(1),
                  reinterpret_cast<ID3D11DeviceContext*>(1),
                  [](const uint8_t*, uint32_t, int64_t, bool, int64_t) {},
                   false)
            && failing.PrepareGpuFrame(nullptr, 0)
            && !failing.EncodeFrame(1)
            && failing.GetLastError().find("submit failure") != std::string::npos,
        "AMF encode errors propagate without fallback");
}

void SuccessfulAmfUsesCompressedReplay() {
    CheckAmf(!fthr::ShouldAllocateRawReplayPool(
                  ReplayEncoderBackend::FfmpegAmf, true),
        "successful AMF selection skips the raw replay pool");
}

} // namespace

int RunAmfReplayEncoderTests() {
    AmdAdapterSelectsAmf();
    NvidiaStillSelectsNativeNvenc();
    IntelStageDoesNotAffectAmdPolicy();
    RequestedCodecsMapToExactAmfNames();
    MissingEncodersFailHonestly();
    InitializationFailuresPropagate();
    SilentCodecFallbackIsRejected();
    AmfVideoConfigsAreCodecCorrect();
    RingAndMuxRemainCodecNeutral();
    PacketMetadataPropagates();
    ScaledAmfConfigUsesEncoderDimensions();
    AmfGpuPreparationFailurePropagates();
    AmfNoPacketOutputIsObservableAtTheSeam();
    GenerationConfigCannotChange();
    FlushAndErrorsAreObservable();
    SuccessfulAmfUsesCompressedReplay();
    std::cout << "FTHRclips AMF tests completed ("
              << amf_checks << " checks)" << std::endl;
    return amf_checks;
}

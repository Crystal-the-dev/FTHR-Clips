#include "nvenc_codec_config.h"

#include <cstring>

namespace fthr {
namespace {

bool GuidEquals(const GUID& left, const GUID& right) noexcept {
    return std::memcmp(&left, &right, sizeof(GUID)) == 0;
}

const NvencCodecSelection kH264{
    VideoCodec::H264,
    NV_ENC_CODEC_H264_GUID,
    NV_ENC_H264_PROFILE_MAIN_GUID,
    EncodedPacketFormat::LengthPrefixedNalUnits,
    "h264_nvenc"};

const NvencCodecSelection kHevc{
    VideoCodec::HEVC,
    NV_ENC_CODEC_HEVC_GUID,
    NV_ENC_HEVC_PROFILE_MAIN_GUID,
    EncodedPacketFormat::AnnexBNalUnits,
    "hevc_nvenc"};

const NvencCodecSelection kAv1{
    VideoCodec::AV1,
    NV_ENC_CODEC_AV1_GUID,
    NV_ENC_AV1_PROFILE_MAIN_GUID,
    EncodedPacketFormat::LowOverheadObu,
    "av1_nvenc"};

} // namespace

const NvencCodecSelection* GetNvencCodecSelection(
    VideoCodec codec) noexcept {
    switch (codec) {
    case VideoCodec::H264: return &kH264;
    case VideoCodec::HEVC: return &kHevc;
    case VideoCodec::AV1: return &kAv1;
    }
    return nullptr;
}

bool IsNvencCodecSupported(
    VideoCodec codec,
    const GUID* supported_guids,
    size_t supported_guid_count) noexcept {
    const auto* selection = GetNvencCodecSelection(codec);
    if (!selection || (!supported_guids && supported_guid_count != 0)) {
        return false;
    }
    for (size_t i = 0; i < supported_guid_count; ++i) {
        if (GuidEquals(selection->encode_guid, supported_guids[i])) return true;
    }
    return false;
}

bool IsNvencInputFormatSupported(
    NV_ENC_BUFFER_FORMAT required_format,
    const NV_ENC_BUFFER_FORMAT* supported_formats,
    size_t supported_format_count) noexcept {
    if (!supported_formats && supported_format_count != 0) return false;
    for (size_t i = 0; i < supported_format_count; ++i) {
        if (supported_formats[i] == required_format) return true;
    }
    return false;
}

bool ConfigureNvencCodec(
    VideoCodec codec,
    uint32_t fps,
    NV_ENC_CONFIG& config) noexcept {
    const auto* selection = GetNvencCodecSelection(codec);
    if (!selection || fps == 0) return false;

    const uint32_t keyframe_interval = fps * 4;
    config.profileGUID = selection->profile_guid;
    config.frameIntervalP = 1;
    config.gopLength = keyframe_interval;

    switch (codec) {
    case VideoCodec::H264: {
        auto& h264 = config.encodeCodecConfig.h264Config;
        h264.idrPeriod = keyframe_interval;
        h264.chromaFormatIDC = 1;
        h264.level = NV_ENC_LEVEL_AUTOSELECT;
        h264.enableVFR = 0;
        h264.outputPictureTimingSEI = 0;
        h264.outputBufferingPeriodSEI = 0;
        h264.h264VUIParameters.timingInfoPresentFlag = 0;
        h264.inputBitDepth = NV_ENC_BIT_DEPTH_8;
        h264.outputBitDepth = NV_ENC_BIT_DEPTH_8;
        return true;
    }
    case VideoCodec::HEVC: {
        auto& hevc = config.encodeCodecConfig.hevcConfig;
        hevc.idrPeriod = keyframe_interval;
        hevc.chromaFormatIDC = 1;
        hevc.level = NV_ENC_LEVEL_AUTOSELECT;
        hevc.outputPictureTimingSEI = 0;
        hevc.outputBufferingPeriodSEI = 0;
        hevc.hevcVUIParameters.timingInfoPresentFlag = 0;
        hevc.disableSPSPPS = 1;
        hevc.repeatSPSPPS = 0;
        hevc.inputBitDepth = NV_ENC_BIT_DEPTH_8;
        hevc.outputBitDepth = NV_ENC_BIT_DEPTH_8;
        return true;
    }
    case VideoCodec::AV1: {
        auto& av1 = config.encodeCodecConfig.av1Config;
        av1.idrPeriod = keyframe_interval;
        av1.chromaFormatIDC = 1;
        av1.level = NV_ENC_LEVEL_AV1_AUTOSELECT;
        av1.outputAnnexBFormat = 0;
        av1.enableTimingInfo = 0;
        av1.enableDecoderModelInfo = 0;
        // ISO BMFF AV1 requires the sequence header on every keyframe. This
        // matches the pinned FFmpeg NVENC wrapper and lets movenc filter the
        // low-overhead OBUs into valid MP4 samples.
        av1.disableSeqHdr = 0;
        av1.repeatSeqHdr = 1;
        av1.inputBitDepth = NV_ENC_BIT_DEPTH_8;
        av1.outputBitDepth = NV_ENC_BIT_DEPTH_8;
        return true;
    }
    }
    return false;
}

EncodedVideoConfig BuildNvencVideoConfig(
    VideoCodec codec,
    uint32_t width,
    uint32_t height,
    uint32_t fps,
    uint32_t bitrate_kbps,
    const std::vector<uint8_t>& codec_extradata) {
    EncodedVideoConfig config;
    const auto* selection = GetNvencCodecSelection(codec);
    config.codec = codec;
    config.width = width;
    config.height = height;
    config.frame_rate = {static_cast<int32_t>(fps), 1};
    config.time_base = {1, static_cast<int32_t>(fps)};
    config.bitrate_kbps = bitrate_kbps;
    config.max_keyframe_interval_frames = fps * 4;
    config.max_b_frames = 0;
    if (selection) config.packet_format = selection->packet_format;
    config.codec_extradata = codec_extradata;
    return config;
}

} // namespace fthr

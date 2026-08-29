#include "encoded_video_config.h"
#include "encoded_video_config_ffmpeg.h"

namespace fthr {

const char* VideoCodecName(VideoCodec codec) noexcept {
    switch (codec) {
    case VideoCodec::H264: return "H.264";
    case VideoCodec::HEVC: return "HEVC";
    case VideoCodec::AV1: return "AV1";
    }
    return "Unknown";
}

bool IsMp4PacketFormatCompatible(const EncodedVideoConfig& config) noexcept {
    switch (config.codec) {
    case VideoCodec::H264:
        return config.packet_format
            == EncodedPacketFormat::LengthPrefixedNalUnits;
    case VideoCodec::HEVC:
        return config.packet_format == EncodedPacketFormat::AnnexBNalUnits;
    case VideoCodec::AV1:
        return config.packet_format == EncodedPacketFormat::LowOverheadObu;
    }
    return false;
}

bool IsValidEncodedVideoConfig(const EncodedVideoConfig& config) noexcept {
    return config.width > 0
        && config.height > 0
        && config.frame_rate.numerator > 0
        && config.frame_rate.denominator > 0
        && config.time_base.numerator > 0
        && config.time_base.denominator > 0
        && IsMp4PacketFormatCompatible(config);
}

int64_t DurationInVideoTicks(
    const EncodedVideoConfig& config, uint32_t seconds) noexcept {
    if (config.time_base.numerator <= 0
        || config.time_base.denominator <= 0) {
        return 0;
    }
    const int64_t numerator = static_cast<int64_t>(seconds)
        * static_cast<int64_t>(config.time_base.denominator);
    return numerator / static_cast<int64_t>(config.time_base.numerator);
}

AVCodecID ToAvCodecId(VideoCodec codec) noexcept {
    switch (codec) {
    case VideoCodec::H264: return AV_CODEC_ID_H264;
    case VideoCodec::HEVC: return AV_CODEC_ID_HEVC;
    case VideoCodec::AV1: return AV_CODEC_ID_AV1;
    }
    return AV_CODEC_ID_NONE;
}

void ApplySdrBt709ColorMetadata(AVCodecParameters* parameters) noexcept {
    if (!parameters) return;
    parameters->color_range = AVCOL_RANGE_MPEG;
    parameters->color_primaries = AVCOL_PRI_BT709;
    parameters->color_trc = AVCOL_TRC_BT709;
    parameters->color_space = AVCOL_SPC_BT709;
    parameters->chroma_location = AVCHROMA_LOC_LEFT;
}

} // namespace fthr

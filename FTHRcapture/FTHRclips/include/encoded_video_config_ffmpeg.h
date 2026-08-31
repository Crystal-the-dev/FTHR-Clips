#pragma once
#ifndef FTHR_ENCODED_VIDEO_CONFIG_FFMPEG_H
#define FTHR_ENCODED_VIDEO_CONFIG_FFMPEG_H

#include "encoded_video_config.h"

#ifdef _MSC_VER
#pragma warning(push, 0)
#endif
extern "C" {
#include <libavcodec/codec_id.h>
#include <libavcodec/codec_par.h>
}
#ifdef _MSC_VER
#pragma warning(pop)
#endif

struct AVStream;
struct AVFormatContext;

namespace fthr {

AVCodecID ToAvCodecId(VideoCodec codec) noexcept;

// Captured desktop frames are SDR full-range BGRA. Every replay backend
// converts them to studio-range BT.709 YUV; publish that contract in the MP4
// stream so players do not guess full range and lift the picture/exposure.
void ApplySdrBt709ColorMetadata(AVCodecParameters* parameters) noexcept;

// Publish the configured capture values instead of leaving FFmpeg/MP4 readers
// to infer them from packet timing and payload size. The packet stream remains
// untouched; this sets the stream declarations and records global MP4 metadata
// that survives later remuxes.
void ApplyConfiguredVideoMetadata(
    AVFormatContext* format_context,
    AVStream* stream,
    const EncodedVideoConfig& config) noexcept;

} // namespace fthr

#endif // FTHR_ENCODED_VIDEO_CONFIG_FFMPEG_H

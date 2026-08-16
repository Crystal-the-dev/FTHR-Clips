#pragma once
#ifndef FTHR_ENCODED_VIDEO_CONFIG_FFMPEG_H
#define FTHR_ENCODED_VIDEO_CONFIG_FFMPEG_H

#include "encoded_video_config.h"

#ifdef _MSC_VER
#pragma warning(push, 0)
#endif
extern "C" {
#include <libavcodec/codec_id.h>
}
#ifdef _MSC_VER
#pragma warning(pop)
#endif

namespace fthr {

AVCodecID ToAvCodecId(VideoCodec codec) noexcept;

} // namespace fthr

#endif // FTHR_ENCODED_VIDEO_CONFIG_FFMPEG_H

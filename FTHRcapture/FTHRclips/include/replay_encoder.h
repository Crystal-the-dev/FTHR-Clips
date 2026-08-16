// replay_encoder.h
// Small Windows replay-encoder seam; Stage 2 keeps native NVENC H.264 only.

#pragma once
#ifndef FTHR_REPLAY_ENCODER_H
#define FTHR_REPLAY_ENCODER_H

#include "encoded_video_config.h"
#include "video_encoder.h"

#include <cstdint>
#include <functional>
#include <string>

struct ID3D11Device;
struct ID3D11DeviceContext;
struct ID3D11Texture2D;

namespace fthr {

enum class EncoderVendor : uint32_t {
    Nvidia,
    Amd,
    Intel,
    Software,
};

enum class ReplayEncoderBackend : uint32_t {
    NativeNvenc,
    FfmpegAmf,
    FfmpegQsv,
    Software,
};

struct ActiveEncoderInfo {
    EncoderVendor vendor = EncoderVendor::Software;
    ReplayEncoderBackend backend = ReplayEncoderBackend::Software;
    VideoCodec codec = VideoCodec::H264;
    bool hardware = false;
    std::string name;
};

class IReplayEncoder {
public:
    using PacketCallback = std::function<void(
        const uint8_t* encoded_data,
        uint32_t size,
        int64_t pts,
        bool is_keyframe,
        int64_t wall_qpc)>;

    virtual ~IReplayEncoder() = default;

    virtual bool Initialize(
        const EncoderConfig& config,
        ID3D11Device* shared_device,
        ID3D11DeviceContext* shared_context,
        PacketCallback callback,
        bool cpu_input_mode) = 0;
    virtual bool EncodeFrame(int64_t present_qpc = 0) = 0;
    virtual bool EncodeFrameCPU(
        const uint8_t* bgra_data,
        uint32_t source_stride,
        int64_t present_qpc = 0) = 0;
    virtual ID3D11Texture2D* GetCurrentInputTexture() const noexcept = 0;

    // Flushes pending packets and releases encoder resources. The current
    // native encoder's Finalize operation already has exactly these semantics.
    virtual void Shutdown() = 0;
    virtual EncodedVideoConfig GetVideoConfig() const = 0;
    virtual ActiveEncoderInfo GetActiveEncoderInfo() const = 0;
    virtual bool GetEncodeEpoch(
        int64_t& start_qpc, int64_t& qpc_frequency) const = 0;
    virtual bool IsInitialized() const = 0;
};

} // namespace fthr

#endif // FTHR_REPLAY_ENCODER_H

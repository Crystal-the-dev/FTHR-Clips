// replay_encoder.h
// Small Windows replay-encoder seam for codec-neutral hardware replay encoding.

#pragma once
#ifndef FTHR_REPLAY_ENCODER_H
#define FTHR_REPLAY_ENCODER_H

#include "encoded_video_config.h"
#include "video_encoder.h"

#include <cstdint>
#include <functional>
#include <memory>
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

constexpr ReplayEncoderBackend SelectProductionReplayBackend(
    EncoderVendor vendor,
    VideoCodec codec) noexcept {
    if (!IsProductionReplayCodecEnabled(codec)) {
        return ReplayEncoderBackend::Software;
    }
    switch (vendor) {
    case EncoderVendor::Nvidia: return ReplayEncoderBackend::NativeNvenc;
    case EncoderVendor::Amd: return ReplayEncoderBackend::FfmpegAmf;
    case EncoderVendor::Intel:
    case EncoderVendor::Software:
        return ReplayEncoderBackend::Software;
    }
    return ReplayEncoderBackend::Software;
}

constexpr bool IsProductionReplayBackendEnabled(
    EncoderVendor vendor,
    VideoCodec codec) noexcept {
    return SelectProductionReplayBackend(vendor, codec)
        != ReplayEncoderBackend::Software;
}

constexpr EncoderVendor EncoderVendorFromPciVendorId(
    uint32_t vendor_id) noexcept {
    switch (vendor_id) {
    case 0x10DE: return EncoderVendor::Nvidia;
    case 0x1002: return EncoderVendor::Amd;
    case 0x8086: return EncoderVendor::Intel;
    default: return EncoderVendor::Software;
    }
}

constexpr bool ShouldAllocateRawReplayPool(
    ReplayEncoderBackend backend,
    bool initialized) noexcept {
    return !initialized || backend == ReplayEncoderBackend::Software;
}

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
    virtual uint32_t GetCurrentInputSubresource() const noexcept { return 0; }

    // Flushes pending packets and releases encoder resources. The current
    // native encoder's Finalize operation already has exactly these semantics.
    virtual void Shutdown() = 0;
    virtual EncodedVideoConfig GetVideoConfig() const = 0;
    virtual ActiveEncoderInfo GetActiveEncoderInfo() const = 0;
    virtual std::string GetLastError() const { return {}; }
    virtual bool GetEncodeEpoch(
        int64_t& start_qpc, int64_t& qpc_frequency) const = 0;
    virtual bool IsInitialized() const = 0;
};

const char* EncoderVendorName(EncoderVendor vendor) noexcept;
EncoderVendor QueryD3D11DeviceVendor(ID3D11Device* device) noexcept;

// The selected monitor's capture adapter is authoritative. NVIDIA stays on
// native NVENC; AMD uses FFmpeg AMF; Intel remains on the current fallback.
std::unique_ptr<IReplayEncoder> CreateProductionReplayEncoder(
    EncoderVendor vendor,
    VideoCodec codec);

} // namespace fthr

#endif // FTHR_REPLAY_ENCODER_H

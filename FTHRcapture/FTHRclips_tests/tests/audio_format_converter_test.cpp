#include "audio_format_converter.h"
#include "audio_encoder.h"

#include <windows.h>
#include <ks.h>
#include <ksmedia.h>

#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <vector>

namespace {

int converter_checks = 0;

void CheckConverter(bool condition, const char* message) {
    ++converter_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

WAVEFORMATEXTENSIBLE Extensible(WORD tag, WORD bits, WORD block_align,
                                WORD channels, DWORD sample_rate,
                                DWORD channel_mask, WORD valid_bits = 0) {
    WAVEFORMATEXTENSIBLE format{};
    format.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
    format.Format.nChannels = channels;
    format.Format.nSamplesPerSec = sample_rate;
    format.Format.wBitsPerSample = bits;
    format.Format.nBlockAlign = block_align;
    format.Format.nAvgBytesPerSec = sample_rate * block_align;
    format.Format.cbSize = sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX);
    format.Samples.wValidBitsPerSample = valid_bits == 0 ? bits : valid_bits;
    format.dwChannelMask = channel_mask;
    format.SubFormat = tag == WAVE_FORMAT_IEEE_FLOAT
        ? KSDATAFORMAT_SUBTYPE_IEEE_FLOAT : KSDATAFORMAT_SUBTYPE_PCM;
    return format;
}

void ConvertsIntegerAndFloatFormatsToCanonicalStereo() {
    const auto float_format = Extensible(WAVE_FORMAT_IEEE_FLOAT, 32, 8, 2,
        48000, SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT);
    fthr::AudioFormatConverter converter;
    CheckConverter(converter.Initialize(&float_format.Format),
        "float32 stereo format initializes");
    const float float_input[] = {0.25f, -0.5f, 0.5f, -1.0f};
    std::vector<float> output;
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(float_input),
        2, &output), "float32 stereo converts");
    CheckConverter(converter.sample_rate() == 48000 && converter.channels() == 2,
        "canonical output format is 48 kHz stereo");
    CheckConverter(output.size() == 4 && std::fabs(output[0] - 0.25f) < 0.001f
        && std::fabs(output[3] + 1.0f) < 0.001f,
        "float32 samples preserve channel values");

    const auto s16_format = Extensible(WAVE_FORMAT_PCM, 16, 2, 1, 48000,
        SPEAKER_FRONT_CENTER);
    CheckConverter(converter.Initialize(&s16_format.Format),
        "PCM16 mono format initializes");
    const int16_t s16_input[] = {16384, -16384};
    output.clear();
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(s16_input),
        2, &output), "PCM16 mono converts");
    CheckConverter(output.size() == 4 && std::fabs(output[0] - 0.5f) < 0.01f
        && std::fabs(output[1] - output[0]) < 0.001f,
        "mono source is duplicated transparently to both channels");

    const auto stereo_format = Extensible(WAVE_FORMAT_PCM, 16, 4, 2, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT);
    CheckConverter(converter.Initialize(&stereo_format.Format),
        "PCM16 stereo format initializes");
    const int16_t stereo_input[] = {16384, -8192};
    output.clear();
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(stereo_input),
        1, &output), "PCM16 stereo converts");
    CheckConverter(output.size() == 2 && std::fabs(output[0] - 0.5f) < 0.01f
        && std::fabs(output[1] + 0.25f) < 0.01f,
        "stereo channels remain transparent at canonical format");
}

void ConvertsPacked24AndEightChannelMasks() {
    WAVEFORMATEX packed24{};
    packed24.wFormatTag = WAVE_FORMAT_PCM;
    packed24.nChannels = 2;
    packed24.nSamplesPerSec = 48000;
    packed24.wBitsPerSample = 24;
    packed24.nBlockAlign = 6;
    packed24.nAvgBytesPerSec = 48000 * 6;
    std::vector<uint8_t> packed = {0x00, 0x00, 0x40, 0x00, 0x00, 0xC0};
    fthr::AudioFormatConverter converter;
    CheckConverter(converter.Initialize(&packed24),
        "packed PCM24 stereo format initializes");
    std::vector<float> output;
    CheckConverter(converter.Convert(packed.data(), 1, &output),
        "packed PCM24 converts");
    CheckConverter(output.size() == 2 && output[0] > 0.49f
        && output[1] < -0.49f, "packed PCM24 sign and scale are preserved");

    const auto valid20 = Extensible(WAVE_FORMAT_PCM, 24, 6, 2, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT, 20);
    CheckConverter(converter.Initialize(&valid20.Format),
        "20 valid bits in a packed 24-bit container initializes");
    output.clear();
    CheckConverter(converter.Convert(packed.data(), 1, &output),
        "valid-20-in-24 PCM converts");
    CheckConverter(output.size() == 2 && output[0] > 0.49f
        && output[1] < -0.49f,
        "valid-20-in-24 samples preserve left-aligned sign and scale");

    const auto valid24 = Extensible(WAVE_FORMAT_PCM, 32, 8, 2, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT, 24);
    CheckConverter(converter.Initialize(&valid24.Format),
        "24 valid bits in a 32-bit extensible container initializes");
    const int32_t valid24_input[] = {0x40000000, static_cast<int32_t>(0xC0000000)};
    output.clear();
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(valid24_input),
        1, &output), "valid-24-in-32 PCM converts");
    CheckConverter(output.size() == 2 && output[0] > 0.49f
        && output[1] < -0.49f,
        "valid-24-in-32 samples preserve left-aligned sign and scale");

    const auto s32_format = Extensible(WAVE_FORMAT_PCM, 32, 8, 2, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT);
    CheckConverter(converter.Initialize(&s32_format.Format),
        "PCM32 stereo format initializes");
    const int32_t s32_input[] = {0x40000000, static_cast<int32_t>(0xC0000000)};
    output.clear();
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(s32_input),
        1, &output), "PCM32 stereo converts");
    CheckConverter(output.size() == 2 && output[0] > 0.49f
        && output[1] < -0.49f, "PCM32 sign and scale are preserved");

    const auto eight = Extensible(WAVE_FORMAT_PCM, 16, 16, 8, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT | SPEAKER_FRONT_CENTER
        | SPEAKER_LOW_FREQUENCY | SPEAKER_BACK_LEFT | SPEAKER_BACK_RIGHT
        | SPEAKER_SIDE_LEFT | SPEAKER_SIDE_RIGHT);
    CheckConverter(converter.Initialize(&eight.Format),
        "8-channel PCM16 format initializes");
    std::vector<int16_t> input(8, 32767);
    output.clear();
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(input.data()),
        1, &output), "8-channel PCM16 converts");
    CheckConverter(output.size() == 2 && output[0] <= 1.0f && output[1] <= 1.0f,
        "mask-aware eight-channel downmix has bounded headroom");

    const auto five_one = Extensible(WAVE_FORMAT_PCM, 16, 12, 6, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT | SPEAKER_FRONT_CENTER
        | SPEAKER_LOW_FREQUENCY | SPEAKER_BACK_LEFT | SPEAKER_BACK_RIGHT);
    CheckConverter(converter.Initialize(&five_one.Format),
        "5.1 PCM16 format initializes");
    input.assign(6, 32767);
    output.clear();
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(input.data()),
        1, &output), "5.1 PCM16 converts");
    CheckConverter(output.size() == 2 && std::fabs(output[0]) <= 0.951f
        && std::fabs(output[1]) <= 0.951f,
        "5.1 downmix stays below the documented headroom ceiling");

    const auto seven_one = Extensible(WAVE_FORMAT_PCM, 16, 16, 8, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT | SPEAKER_FRONT_CENTER
        | SPEAKER_LOW_FREQUENCY | SPEAKER_BACK_LEFT | SPEAKER_BACK_RIGHT
        | SPEAKER_SIDE_LEFT | SPEAKER_SIDE_RIGHT);
    CheckConverter(converter.Initialize(&seven_one.Format),
        "7.1 PCM16 format initializes");
    input.assign(8, 32767);
    output.clear();
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(input.data()),
        1, &output), "7.1 PCM16 converts");
    CheckConverter(output.size() == 2 && std::fabs(output[0]) <= 0.951f
        && std::fabs(output[1]) <= 0.951f,
        "7.1 downmix stays below the documented headroom ceiling");

    const auto sonar_like = Extensible(WAVE_FORMAT_IEEE_FLOAT, 32, 32, 8, 96000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT | SPEAKER_FRONT_CENTER
        | SPEAKER_LOW_FREQUENCY | SPEAKER_BACK_LEFT | SPEAKER_BACK_RIGHT
        | SPEAKER_SIDE_LEFT | SPEAKER_SIDE_RIGHT);
    CheckConverter(converter.Initialize(&sonar_like.Format),
        "96 kHz eight-channel float format initializes");
    std::vector<float> sonar_input(960 * 8, 0.1f);
    output.clear();
    CheckConverter(converter.Convert(
        reinterpret_cast<const uint8_t*>(sonar_input.data()), 960, &output),
        "96 kHz eight-channel float input converts");
    CheckConverter(!output.empty() && output.size() <= 480 * 2,
        "96 kHz eight-channel conversion is downmixed and resampled once");

    WAVEFORMATEX malformed = Extensible(WAVE_FORMAT_PCM, 16, 4, 2, 48000,
        SPEAKER_FRONT_LEFT | SPEAKER_FRONT_RIGHT).Format;
    malformed.nBlockAlign = 8;
    malformed.nAvgBytesPerSec = 48000 * malformed.nBlockAlign;
    CheckConverter(!converter.Initialize(&malformed),
        "malformed padded PCM block alignment is rejected");
}

void ResamplingIsPersistentAndFlushesDelay() {
    WAVEFORMATEX input_format{};
    input_format.wFormatTag = WAVE_FORMAT_PCM;
    input_format.nChannels = 2;
    input_format.nSamplesPerSec = 44100;
    input_format.wBitsPerSample = 32;
    input_format.nBlockAlign = 8;
    input_format.nAvgBytesPerSec = 44100 * 8;
    fthr::AudioFormatConverter converter;
    CheckConverter(converter.Initialize(&input_format),
        "44.1 kHz format initializes");
    std::vector<float> input(44100 * 2, 0.1f);
    std::vector<float> output;
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(input.data()),
        44100, &output), "44.1 kHz input converts");
    const size_t before_flush = output.size();
    CheckConverter(converter.Flush(&output), "resampler flush succeeds");
    CheckConverter(output.size() >= before_flush
        && output.size() >= 48000 * 2 - 128,
        "persistent resampler emits approximately one canonical second");
}

void SilentNativePacketsKeepTheirCanonicalDuration() {
    WAVEFORMATEX input_format{};
    input_format.wFormatTag = WAVE_FORMAT_PCM;
    input_format.nChannels = 2;
    input_format.nSamplesPerSec = 96000;
    input_format.wBitsPerSample = 16;
    input_format.nBlockAlign = 4;
    input_format.nAvgBytesPerSec = 96000 * 4;
    fthr::AudioFormatConverter converter;
    CheckConverter(converter.Initialize(&input_format),
        "96 kHz format initializes");
    std::vector<int16_t> input(96000 * 2, 0);
    std::vector<float> output;
    CheckConverter(converter.Convert(reinterpret_cast<const uint8_t*>(input.data()),
        96000, &output), "96 kHz silent input converts");
    CheckConverter(converter.Flush(&output), "96 kHz silent resampler flushes");
    CheckConverter(output.size() >= (48000u * 2 - 256)
        && output.size() <= (48000u * 2 + 256),
        "96 kHz silent packet retains one canonical second of duration");
}

void EncoderReportsSubmissionAndFlushResults() {
    fthr::AudioEncoder encoder;
    uint32_t packets = 0;
    CheckConverter(encoder.Initialize(48000, 2, 96,
        [&packets](const uint8_t*, uint32_t size, int64_t) {
            if (size > 0) ++packets;
        }), "AAC encoder initializes for observability test");
    std::vector<float> input(1024 * 2, 0.0f);
    CheckConverter(!encoder.EncodeSamples(input.data(), 3),
        "AAC encoder rejects incomplete interleaved samples");
    CheckConverter(encoder.EncodeSamples(input.data(),
        static_cast<uint32_t>(input.size())), "AAC frame submission succeeds");
    CheckConverter(encoder.stats().frames_submitted == 1,
        "AAC encoder counts submitted frames");
    CheckConverter(encoder.Finalize(), "AAC encoder flush succeeds");
    CheckConverter(encoder.stats().packets_emitted == packets,
        "AAC encoder packet count is observable");
}

}  // namespace

int RunAudioFormatConverterTests() {
    ConvertsIntegerAndFloatFormatsToCanonicalStereo();
    ConvertsPacked24AndEightChannelMasks();
    ResamplingIsPersistentAndFlushesDelay();
    SilentNativePacketsKeepTheirCanonicalDuration();
    EncoderReportsSubmissionAndFlushResults();
    std::cout << "AUDIT-050 audio format converter tests: "
              << converter_checks << " checks passed" << std::endl;
    return converter_checks;
}

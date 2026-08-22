// AUDIT-050 isolated native AAC replay-ring benchmark.
//
// One process owns one persistent fthr::AudioEncoder per synthetic source.
// No FFmpeg subprocess is started per source. The benchmark intentionally
// measures encoding and packet-snapshot costs, not production capture wiring.

#include "audio_encoder.h"

#include <windows.h>
#include <psapi.h>

#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

namespace {

constexpr uint32_t kRate = 48'000;
constexpr uint32_t kChannels = 2;
constexpr uint32_t kBlockFrames = 480; // 10 ms

struct Packet {
    std::vector<uint8_t> bytes;
    int64_t pts = 0;
};

struct SourceRing {
    std::vector<Packet> packets;
    uint64_t encoded_bytes = 0;
};

uint64_t FileTime100ns(const FILETIME& value) {
    ULARGE_INTEGER time{};
    time.LowPart = value.dwLowDateTime;
    time.HighPart = value.dwHighDateTime;
    return time.QuadPart;
}

uint64_t ProcessCpu100ns() {
    FILETIME create{}, exit{}, kernel{}, user{};
    if (!GetProcessTimes(GetCurrentProcess(), &create, &exit, &kernel, &user)) {
        return 0;
    }
    return FileTime100ns(kernel) + FileTime100ns(user);
}

size_t WorkingSetBytes() {
    PROCESS_MEMORY_COUNTERS counters{};
    counters.cb = sizeof(counters);
    return GetProcessMemoryInfo(GetCurrentProcess(), &counters,
                                sizeof(counters))
        ? static_cast<size_t>(counters.WorkingSetSize)
        : 0;
}

double FrequencyHz() {
    LARGE_INTEGER frequency{};
    QueryPerformanceFrequency(&frequency);
    return static_cast<double>(frequency.QuadPart);
}

double NowTicks() {
    LARGE_INTEGER value{};
    QueryPerformanceCounter(&value);
    return static_cast<double>(value.QuadPart);
}

std::vector<Packet> Snapshot(const SourceRing& source) {
    std::vector<Packet> copy;
    copy.reserve(source.packets.size());
    for (const Packet& packet : source.packets) {
        copy.push_back(packet);
    }
    return copy;
}

bool RunCase(uint32_t sources, uint32_t seconds, std::ofstream& report) {
    std::vector<SourceRing> rings(sources);
    std::vector<std::unique_ptr<fthr::AudioEncoder>> encoders;
    encoders.reserve(sources);
    for (uint32_t source = 0; source < sources; ++source) {
        auto encoder = std::make_unique<fthr::AudioEncoder>();
        const bool initialized = encoder->Initialize(
            kRate, kChannels, 128,
            [&ring = rings[source]](const uint8_t* data, uint32_t size,
                                    int64_t pts) {
                Packet packet;
                packet.bytes.assign(data, data + size);
                packet.pts = pts;
                ring.encoded_bytes += size;
                ring.packets.push_back(std::move(packet));
            });
        if (!initialized) {
            std::cerr << "AAC_INITIALIZE_FAILED source=" << source << "\n";
            return false;
        }
        encoders.push_back(std::move(encoder));
    }

    std::vector<float> pcm(kBlockFrames * kChannels);
    std::vector<double> phases(sources, 0.0);
    const double phase_step = 2.0 * 3.14159265358979323846
        / static_cast<double>(kRate);
    const double frequency = FrequencyHz();
    const double start = NowTicks();
    const uint64_t cpu_start = ProcessCpu100ns();
    for (uint64_t block = 0;
         block < static_cast<uint64_t>(seconds) * kRate / kBlockFrames;
         ++block) {
        for (uint32_t source = 0; source < sources; ++source) {
            const double tone = 440.0 + static_cast<double>(source) * 37.0;
            for (uint32_t frame = 0; frame < kBlockFrames; ++frame) {
                const float sample = static_cast<float>(
                    0.15 * std::sin(phases[source] * tone));
                pcm[frame * kChannels] = sample;
                pcm[frame * kChannels + 1] = sample;
                phases[source] += phase_step;
            }
            encoders[source]->EncodeSamples(
                pcm.data(), static_cast<uint32_t>(pcm.size()));
        }
    }
    for (auto& encoder : encoders) encoder->Finalize();
    const double encode_ms = (NowTicks() - start) * 1000.0 / frequency;
    const uint64_t cpu_end = ProcessCpu100ns();

    size_t packet_count = 0;
    size_t encoded_bytes = 0;
    int64_t first_pts = 0;
    int64_t last_pts = 0;
    bool first = true;
    for (const auto& ring : rings) {
        packet_count += ring.packets.size();
        encoded_bytes += ring.encoded_bytes;
        if (!ring.packets.empty()) {
            if (first) first_pts = ring.packets.front().pts;
            last_pts = ring.packets.back().pts;
            first = false;
        }
    }

    const double snapshot_start = NowTicks();
    size_t snapshot_bytes = 0;
    size_t snapshot_packets = 0;
    for (const auto& ring : rings) {
        auto snapshot = Snapshot(ring);
        snapshot_packets += snapshot.size();
        for (const auto& packet : snapshot) snapshot_bytes += packet.bytes.size();
    }
    const double snapshot_ms = (NowTicks() - snapshot_start)
        * 1000.0 / frequency;

    const double cpu_ms = static_cast<double>(cpu_end - cpu_start) / 10'000.0;
    const size_t working_set = WorkingSetBytes();
    report << sources << ',' << seconds << ',' << packet_count << ','
           << encoded_bytes << ',' << std::fixed << std::setprecision(3)
           << encode_ms << ',' << cpu_ms << ',' << snapshot_packets << ','
           << snapshot_bytes << ',' << snapshot_ms << ',' << working_set
           << ',' << first_pts << ',' << last_pts << '\n';
    std::cout << "AAC_CASE sources=" << sources << " seconds=" << seconds
              << " packets=" << packet_count
              << " encoded_bytes=" << encoded_bytes
              << " encode_ms=" << encode_ms
              << " cpu_ms=" << cpu_ms
              << " snapshot_ms=" << snapshot_ms
              << " working_set_bytes=" << working_set << '\n';
    return true;
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    const std::wstring output = argc > 1
        ? argv[1] : L"audit050-aac-ring.csv";
    std::ofstream report(output);
    if (!report) {
        std::wcerr << L"cannot open report: " << output << L"\n";
        return 64;
    }
    report << "sources,seconds,packet_count,encoded_bytes,encode_ms,cpu_ms,"
              "snapshot_packets,snapshot_bytes,snapshot_ms,working_set_bytes,"
              "first_pts,last_pts\n";
    for (uint32_t sources : {1u, 4u, 8u}) {
        for (uint32_t seconds : {30u, 60u, 300u}) {
            if (!RunCase(sources, seconds, report)) return 2;
        }
    }
    return 0;
}

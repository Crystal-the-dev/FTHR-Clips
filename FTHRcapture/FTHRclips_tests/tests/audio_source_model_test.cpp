#include "audio_packet_ring.h"
#include "audio_source_model.h"

#include <cstdlib>
#include <iostream>

namespace {

int audio_source_checks = 0;

void CheckAudio(bool condition, const char* message) {
    ++audio_source_checks;
    if (!condition) {
        std::cerr << "FAILED: " << message << std::endl;
        std::exit(1);
    }
}

fthr::AudioSourceMetadata Source(
    const char* uuid, const char* display_name,
    fthr::AudioSourceType type = fthr::AudioSourceType::Application) {
    fthr::AudioSourceMetadata source;
    source.identity.id.value = uuid;
    source.identity.type = type;
    source.identity.persistent_identity = display_name;
    source.identity.display_name = display_name;
    source.identity.icon_reference = "generic-app";
    source.format = {48000, type == fthr::AudioSourceType::Microphone ? 1u : 2u, "fltp"};
    return source;
}

void ActivityGatingAndIntervalSelectionAreDeterministic() {
    fthr::AudioSourceRegistry registry(7);
    const auto valorant = Source(
        "11111111-1111-4111-8111-111111111111", "VALORANT");
    const auto silent = Source(
        "22222222-2222-4222-8222-222222222222", "Firefox");
    CheckAudio(registry.Discover(valorant), "an application source is discovered");
    CheckAudio(registry.Discover(silent), "a second source is discovered");

    CheckAudio(registry.ObserveActivity(valorant.identity.id, 0.011f, 100)
                   == fthr::AudioSourceAdmission::RejectedAtCapacity,
        "one loud block does not beat hysteresis");
    CheckAudio(registry.ObserveActivity(valorant.identity.id, 0.011f, 200)
                   == fthr::AudioSourceAdmission::Accepted,
        "two loud blocks admit a meaningful source");
    CheckAudio(registry.SourcesForInterval(0, 250).size() == 1,
        "active app appears in its clip interval");
    CheckAudio(registry.SourcesForInterval(300, 400).empty(),
        "source outside saved interval is excluded");
    CheckAudio(registry.SourcesForInterval(0, 400).front().identity.display_name == "VALORANT",
        "silent discovered browser is not fabricated into a clip");
}

void EndedHistorySurvivesAndGenerationDoesNotMix() {
    fthr::AudioSourceRegistry first_generation(7);
    const auto discord = Source(
        "33333333-3333-4333-8333-333333333333", "Discord");
    CheckAudio(first_generation.Discover(discord), "Discord source discovered");
    first_generation.ObserveActivity(discord.identity.id, 0.020f, 1000);
    first_generation.ObserveActivity(discord.identity.id, 0.020f, 1100);
    first_generation.MarkEnded(discord.identity.id, 1200);
    CheckAudio(first_generation.SourcesForInterval(900, 1250).size() == 1,
        "exited source remains available for its replay interval");

    fthr::AudioSourceRegistry next_generation(8);
    CheckAudio(next_generation.SourcesForInterval(900, 1250).empty(),
        "new capture generation cannot inherit old source history");
}

void SourceLimitAndSanitizationStaySafe() {
    fthr::AudioSourceRegistry registry(1, 1);
    const auto one = Source("44444444-4444-4444-8444-444444444444", "One");
    const auto two = Source("55555555-5555-4555-8555-555555555555", "Two");
    CheckAudio(registry.Discover(one) && registry.Discover(two),
        "sources are discovered before expensive admission");
    registry.ObserveActivity(one.identity.id, 0.02f, 100);
    CheckAudio(registry.ObserveActivity(one.identity.id, 0.02f, 200)
                   == fthr::AudioSourceAdmission::Accepted,
        "first source is admitted at capacity one");
    registry.ObserveActivity(two.identity.id, 0.02f, 300);
    CheckAudio(registry.ObserveActivity(two.identity.id, 0.02f, 400)
                   == fthr::AudioSourceAdmission::RejectedAtCapacity,
        "second audible source is deterministically reported at limit");
    CheckAudio(fthr::SanitizeAudioSourceText("C:/Users/Tom/Discord?token=x", 128)
                   == "CUsersTomDiscordtokenx",
        "manifest text removes path separators and sensitive punctuation");
}

void EncodedPacketRingIsBoundedAndSnapshotsOverlap() {
    fthr::AudioSourceId id{"66666666-6666-4666-8666-666666666666"};
    fthr::EncodedAudioPacketRing ring(id, 9, {48000, 2, "fltp"}, 1);
    ring.SetCodecExtradata({0x12, 0x10});
    for (int64_t pts = 0; pts < 48000 * 3; pts += 1024) {
        CheckAudio(ring.Push({std::vector<uint8_t>(32, 0xA5), pts, 1024}),
            "monotonic AAC packet is accepted");
    }
    CheckAudio(ring.packet_count() <= 48,
        "one-second replay ring retains a bounded packet count");
    const auto snapshot = ring.TakeSnapshot(48000 * 2, 48000 * 3);
    CheckAudio(snapshot.valid() && snapshot.generation == 9
                   && snapshot.source_id == id,
        "snapshot preserves source and generation identity");
    CheckAudio(snapshot.codec_extradata.size() == 2
                   && snapshot.first_pts_samples < 48000 * 3,
        "snapshot carries codec configuration and overlapping packets");
}

}  // namespace

int RunAudioSourceModelTests() {
    ActivityGatingAndIntervalSelectionAreDeterministic();
    EndedHistorySurvivesAndGenerationDoesNotMix();
    SourceLimitAndSanitizationStaySafe();
    EncodedPacketRingIsBoundedAndSnapshotsOverlap();
    std::cout << "AUDIT-050 audio source model tests: " << audio_source_checks
              << " checks passed" << std::endl;
    return audio_source_checks;
}

#include "audio_packet_ring.h"
#include "audio_source_model.h"
#include "clip_audio_manifest.h"
#include "transactional_save.h"

#include <cstdlib>
#include <fstream>
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

void PairedPublicationProtectsTheManifestContract() {
    std::vector<std::pair<std::filesystem::path, std::filesystem::path>> renames;
    fthr::transactional_save::FileOps operations;
    operations.exists = [](const std::filesystem::path&, std::error_code&) {
        return false;
    };
    operations.remove = [](const std::filesystem::path&, std::error_code&) {
        return true;
    };
    operations.rename_no_replace = [&](const std::filesystem::path& from,
                                        const std::filesystem::path& to,
                                        std::error_code&) {
        renames.emplace_back(from, to);
        return true;
    };
    bool writer_saw_paired_partials = false;
    const auto result = fthr::transactional_save::RunPair(
        L"capture_clip_from_20260823_02-30-00.mp4",
        L"capture_clip_from_20260823_02-30-00.mp4.fthr-audio.json",
        [&](const std::filesystem::path& media_partial,
            const std::filesystem::path& manifest_partial,
            std::string&) {
            writer_saw_paired_partials = media_partial.extension() == L".partial"
                && manifest_partial.extension() == L".partial";
            return true;
        },
        operations);
    CheckAudio(result.success && writer_saw_paired_partials,
        "paired transaction gives the writer both unpublished paths");
    CheckAudio(renames.size() == 2
                   && renames[0].second == result.manifest_final_path
                   && renames[1].second == result.media_final_path,
        "manifest is published before the completed media artifact");

    int rename_attempts = 0;
    bool orphan_manifest_removed = false;
    operations.rename_no_replace = [&](const std::filesystem::path&,
                                        const std::filesystem::path&,
                                        std::error_code& error) {
        ++rename_attempts;
        if (rename_attempts == 2) {
            error = std::make_error_code(std::errc::io_error);
            return false;
        }
        return true;
    };
    operations.remove = [&](const std::filesystem::path& path, std::error_code&) {
        if (path.extension() == L".json") orphan_manifest_removed = true;
        return true;
    };
    const auto failed_media_publish = fthr::transactional_save::RunPair(
        L"capture_clip_from_20260823_02-30-01.mp4",
        L"capture_clip_from_20260823_02-30-01.mp4.fthr-audio.json",
        [](const std::filesystem::path&, const std::filesystem::path&, std::string&) {
            return true;
        },
        operations);
    CheckAudio(!failed_media_publish.success
                   && failed_media_publish.failure
                       == fthr::transactional_save::PairFailure::PublishMedia
                   && orphan_manifest_removed,
        "failed media publication removes its already-published manifest");
}

void NativeManifestBindsTheTemporaryMediaWithoutPrivatePaths() {
    const auto directory = std::filesystem::temp_directory_path()
        / "fthr-audit050-native-manifest";
    const auto media_final = directory / "desktop_clip_from_20260823_02-45-00.mp4";
    const auto media_partial = std::filesystem::path(media_final.wstring() + L".partial");
    const auto manifest_partial = std::filesystem::path(
        fthr::AudioManifestPathFor(media_final).wstring() + L".partial");
    std::error_code filesystem_error;
    std::filesystem::create_directories(directory, filesystem_error);
    {
        std::ofstream media(media_partial, std::ios::binary | std::ios::trunc);
        media << "fixed media bytes for native manifest binding";
    }
    auto source = Source("77777777-7777-4777-8777-777777777777", "Default Mix",
                         fthr::AudioSourceType::System);
    source.identity.persistent_identity = "default-mix";
    source.identity.icon_reference = "system-audio";
    source.state.admitted = true;
    source.state.active_in_generation = true;
    source.state.first_active_100ns = 100;
    source.state.last_active_100ns = 200;
    fthr::EncodedAudioTrack track;
    track.source = source;
    track.snapshot.source_id = source.identity.id;
    track.snapshot.format = source.format;
    track.snapshot.codec_extradata = {0x12, 0x10};
    track.snapshot.packets.push_back({{0x11, 0x22}, 0, 1024});
    track.presentation_start_pts_samples = 0;

    auto application_track = track;
    application_track.source = Source("88888888-8888-4888-8888-888888888888", "Discord");
    application_track.source.identity.persistent_identity = "discord";
    application_track.source.identity.icon_reference = "windows-app-icon";
    application_track.source.state.admitted = true;
    application_track.source.state.active_in_generation = false;
    application_track.source.state.first_active_100ns = 150;
    application_track.source.state.last_active_100ns = 250;
    application_track.snapshot.source_id = application_track.source.identity.id;

    std::string transaction_id;
    std::string error;
    const bool made_transaction = fthr::CreateAudioManifestTransactionId(
        &transaction_id, &error);
    const bool wrote = made_transaction && fthr::WriteClipAudioManifest(
        media_final, media_partial, manifest_partial, transaction_id,
        {track, application_track}, &error);
    std::ifstream manifest(manifest_partial, std::ios::binary);
    const std::string content((std::istreambuf_iterator<char>(manifest)),
                              std::istreambuf_iterator<char>());
    CheckAudio(wrote && fthr::AudioSourceId{transaction_id}.IsValid(),
        "native manifest uses a fresh valid transaction UUID");
    CheckAudio(content.find("desktop_clip_from_20260823_02-45-00.mp4") != std::string::npos
                   && content.find("Default Mix") != std::string::npos
                   && content.find("\"stream_index\":2") != std::string::npos
                   && content.find("\"source_type\":\"application\"") != std::string::npos
                   && content.find("\"display_name\":\"Discord\"") != std::string::npos
                   && content.find("0f63f6d6210c6220135cdf185527ccc70a3cd26eca1a8b8b68e8ca8c4ba11aa9")
                       != std::string::npos
                   && content.find("C:\\\\") == std::string::npos,
        "native manifest maps Default Mix plus an actual application stem without private paths");
    std::filesystem::remove(media_partial, filesystem_error);
    std::filesystem::remove(manifest_partial, filesystem_error);
    std::filesystem::remove(directory, filesystem_error);
}

}  // namespace

int RunAudioSourceModelTests() {
    ActivityGatingAndIntervalSelectionAreDeterministic();
    EndedHistorySurvivesAndGenerationDoesNotMix();
    SourceLimitAndSanitizationStaySafe();
    EncodedPacketRingIsBoundedAndSnapshotsOverlap();
    PairedPublicationProtectsTheManifestContract();
    NativeManifestBindsTheTemporaryMediaWithoutPrivatePaths();
    std::cout << "AUDIT-050 audio source model tests: " << audio_source_checks
              << " checks passed" << std::endl;
    return audio_source_checks;
}

#pragma once

#include <exception>
#include <filesystem>
#include <functional>
#include <string>
#include <system_error>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#elif defined(__linux__)
#include <cerrno>
#include <fcntl.h>
#include <linux/fs.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

namespace fthr::transactional_save {

namespace fs = std::filesystem;

inline constexpr char kPartialSuffix[] = ".partial";

enum class Failure {
    None,
    InspectDestination,
    DestinationExists,
    InspectTemporary,
    TemporaryExists,
    Writer,
    Rename,
};

struct FileOps {
    std::function<bool(const fs::path&, std::error_code&)> exists;
    std::function<bool(const fs::path&, const fs::path&, std::error_code&)>
        rename_no_replace;
    std::function<bool(const fs::path&, std::error_code&)> remove;
};

struct Result {
    bool success = false;
    Failure failure = Failure::None;
    fs::path final_path;
    fs::path temporary_path;
    std::string writer_error;
    std::error_code operation_error;
    std::error_code cleanup_error;
};

using Writer = std::function<bool(const fs::path&, std::string&)>;

inline fs::path TemporaryPathFor(const fs::path& final_path) {
    fs::path temporary_path = final_path;
    temporary_path += kPartialSuffix;
    return temporary_path;
}

inline std::string Utf8Path(const fs::path& path) {
    const auto encoded = path.u8string();
    std::string text;
    text.reserve(encoded.size());
    for (const auto character : encoded)
        text.push_back(static_cast<char>(character));
    return text;
}

inline FileOps DefaultFileOps() {
    FileOps ops;
    ops.exists = [](const fs::path& path, std::error_code& error) {
        return fs::exists(path, error);
    };
    ops.remove = [](const fs::path& path, std::error_code& error) {
        (void)fs::remove(path, error);
        return !error;
    };
    ops.rename_no_replace = [](
        const fs::path& temporary_path,
        const fs::path& final_path,
        std::error_code& error) {
#if defined(_WIN32)
        if (MoveFileExW(
                temporary_path.c_str(), final_path.c_str(), MOVEFILE_WRITE_THROUGH)) {
            return true;
        }
        error = std::error_code(
            static_cast<int>(GetLastError()), std::system_category());
        return false;
#elif defined(__linux__) && defined(SYS_renameat2)
        const long result = syscall(
            SYS_renameat2,
            AT_FDCWD,
            temporary_path.c_str(),
            AT_FDCWD,
            final_path.c_str(),
            RENAME_NOREPLACE);
        if (result == 0) return true;
        error = std::error_code(errno, std::generic_category());
        return false;
#else
        (void)temporary_path;
        (void)final_path;
        error = std::make_error_code(std::errc::operation_not_supported);
        return false;
#endif
    };
    return ops;
}

inline void CleanupTemporary(Result& result, const FileOps& ops) {
    std::error_code error;
    if (!ops.remove(result.temporary_path, error)) {
        if (!error) error = std::make_error_code(std::errc::io_error);
        result.cleanup_error = error;
    }
}

inline Result Run(
    const fs::path& final_path,
    const Writer& writer,
    const FileOps& ops = DefaultFileOps()) {
    Result result;
    result.final_path = final_path;
    result.temporary_path = TemporaryPathFor(final_path);

    std::error_code error;
    if (ops.exists(result.final_path, error)) {
        result.failure = Failure::DestinationExists;
        return result;
    }
    if (error) {
        result.failure = Failure::InspectDestination;
        result.operation_error = error;
        return result;
    }

    error.clear();
    if (ops.exists(result.temporary_path, error)) {
        result.failure = Failure::TemporaryExists;
        return result;
    }
    if (error) {
        result.failure = Failure::InspectTemporary;
        result.operation_error = error;
        return result;
    }

    bool writer_succeeded = false;
    try {
        writer_succeeded = writer(result.temporary_path, result.writer_error);
    }
    catch (const std::exception& exception) {
        result.writer_error = exception.what();
    }
    catch (...) {
        result.writer_error = "Media writer raised an unknown exception";
    }

    if (!writer_succeeded) {
        result.failure = Failure::Writer;
        CleanupTemporary(result, ops);
        return result;
    }

    error.clear();
    if (!ops.rename_no_replace(result.temporary_path, result.final_path, error)) {
        result.failure = Failure::Rename;
        result.operation_error = error
            ? error
            : std::make_error_code(std::errc::io_error);
        CleanupTemporary(result, ops);
        return result;
    }

    result.success = true;
    result.failure = Failure::None;
    return result;
}

inline std::string DescribeFailure(const Result& result) {
    const std::string final_path = Utf8Path(result.final_path);
    const std::string temporary_path = Utf8Path(result.temporary_path);
    switch (result.failure) {
    case Failure::InspectDestination:
        return "Could not inspect final clip path '" + final_path + "': "
            + result.operation_error.message();
    case Failure::DestinationExists:
        return "Final clip already exists and was not overwritten: '" + final_path + "'";
    case Failure::InspectTemporary:
        return "Could not inspect temporary clip path '" + temporary_path + "': "
            + result.operation_error.message();
    case Failure::TemporaryExists:
        return "Temporary clip already exists and was not overwritten: '"
            + temporary_path + "'";
    case Failure::Writer:
        return result.writer_error.empty()
            ? "Media writer failed while creating temporary clip '" + temporary_path + "'"
            : result.writer_error;
    case Failure::Rename:
        return "Could not atomically finalize clip '" + final_path + "' from '"
            + temporary_path + "': " + result.operation_error.message();
    case Failure::None:
        break;
    }
    return {};
}

} // namespace fthr::transactional_save

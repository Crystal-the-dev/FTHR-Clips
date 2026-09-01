// windows_native_error.h
// Lossless, structured Win32/HRESULT diagnostics for startup failures.

#pragma once
#ifndef FTHR_WINDOWS_NATIVE_ERROR_H
#define FTHR_WINDOWS_NATIVE_ERROR_H

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>

#include <cstdint>
#include <iomanip>
#include <iterator>
#include <sstream>
#include <string>

namespace fthr::diagnostics {

inline std::string JsonEscape(const std::string& value) {
    std::ostringstream escaped;
    for (const unsigned char character : value) {
        switch (character) {
        case '\"': escaped << "\\\""; break;
        case '\\': escaped << "\\\\"; break;
        case '\b': escaped << "\\b"; break;
        case '\f': escaped << "\\f"; break;
        case '\n': escaped << "\\n"; break;
        case '\r': escaped << "\\r"; break;
        case '\t': escaped << "\\t"; break;
        default:
            if (character < 0x20) {
                escaped << "\\u" << std::hex << std::setw(4)
                        << std::setfill('0') << static_cast<unsigned>(character)
                        << std::dec << std::setfill(' ');
            } else {
                escaped << static_cast<char>(character);
            }
        }
    }
    return escaped.str();
}

inline std::string WideToUtf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
        static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0) return {};
    std::string output(static_cast<size_t>(size), '\0');
    if (WideCharToMultiByte(
            CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
            static_cast<int>(value.size()), output.data(), size,
            nullptr, nullptr) <= 0) {
        return {};
    }
    return output;
}

inline std::string WindowsSystemMessage(DWORD error) {
    wchar_t buffer[2048]{};
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, error, 0, buffer, static_cast<DWORD>(std::size(buffer)), nullptr);
    if (length == 0) return {};
    std::wstring message(buffer, length);
    while (!message.empty()
           && (message.back() == L'\r' || message.back() == L'\n'
               || message.back() == L' ' || message.back() == L'\t')) {
        message.pop_back();
    }
    return WideToUtf8(message);
}

inline const char* WindowsSymbolicErrorName(DWORD error) noexcept {
#ifdef ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION
    if (error == ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION) {
        return "ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION";
    }
#endif
    return nullptr;
}

inline std::string Hex32(uint32_t value) {
    std::ostringstream text;
    text << "0x" << std::uppercase << std::hex << std::setw(8)
         << std::setfill('0') << value;
    return text.str();
}

inline std::string FormatWin32Failure(const char* api_call, DWORD error) {
    const char* symbolic = WindowsSymbolicErrorName(error);
    const std::string message = WindowsSystemMessage(error);
    std::ostringstream text;
    text << "native_failure={\"api_call\":\""
         << JsonEscape(api_call ? api_call : "unknown")
         << "\",\"error_domain\":\"win32\""
         << ",\"native_error_signed\":" << static_cast<int32_t>(error)
         << ",\"native_error_unsigned\":" << static_cast<uint32_t>(error)
         << ",\"native_error_hex\":\"" << Hex32(error) << "\""
         << ",\"win32_error_decimal\":" << static_cast<uint32_t>(error)
         << ",\"symbolic_error\":";
    if (symbolic) text << '\"' << symbolic << '\"';
    else text << "null";
    text << ",\"system_message\":";
    if (!message.empty()) text << '\"' << JsonEscape(message) << '\"';
    else text << "null";
    text << '}';
    return text.str();
}

inline std::string HResultFailureJson(const char* api_call, HRESULT result) {
    const uint32_t raw = static_cast<uint32_t>(result);
    const bool from_win32 = HRESULT_FACILITY(result) == FACILITY_WIN32;
    const DWORD win32_error = from_win32 ? HRESULT_CODE(result) : 0;
    const char* symbolic = from_win32
        ? WindowsSymbolicErrorName(win32_error) : nullptr;
    const std::string message = WindowsSystemMessage(
        from_win32 ? win32_error : raw);
    std::ostringstream text;
    text << "{\"api_call\":\""
         << JsonEscape(api_call ? api_call : "unknown")
         << "\",\"error_domain\":\"hresult\""
         << ",\"native_error_signed\":" << static_cast<int32_t>(raw)
         << ",\"native_error_unsigned\":" << raw
         << ",\"native_error_hex\":\"" << Hex32(raw) << "\""
         << ",\"win32_error_decimal\":";
    if (from_win32) text << win32_error;
    else text << "null";
    text << ",\"symbolic_error\":";
    if (symbolic) text << '\"' << symbolic << '\"';
    else text << "null";
    text << ",\"system_message\":";
    if (!message.empty()) text << '\"' << JsonEscape(message) << '\"';
    else text << "null";
    text << '}';
    return text.str();
}

inline std::string FormatHResultFailure(const char* api_call, HRESULT result) {
    return "native_failure=" + HResultFailureJson(api_call, result);
}

} // namespace fthr::diagnostics

#endif // FTHR_WINDOWS_NATIVE_ERROR_H

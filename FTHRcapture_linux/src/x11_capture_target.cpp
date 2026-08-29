#include "x11_capture_target.h"

#include <array>
#include <charconv>
#include <limits>

namespace fthr {

bool ParseX11CaptureTarget(std::string_view value, X11CaptureTarget& target) {
    constexpr std::string_view prefix{"@x11:"};
    if (!value.starts_with(prefix)) return false;
    value.remove_prefix(prefix.size());

    std::array<uint32_t, 4> fields{};
    for (size_t index = 0; index < fields.size(); ++index) {
        const size_t comma = value.find(',');
        const bool last = index + 1 == fields.size();
        if ((last && comma != std::string_view::npos) ||
            (!last && comma == std::string_view::npos)) {
            return false;
        }
        const std::string_view token =
            last ? value : value.substr(0, comma);
        if (token.empty() || token.front() == '-' || token.front() == '+')
            return false;
        uint32_t parsed = 0;
        const auto result = std::from_chars(
            token.data(), token.data() + token.size(), parsed);
        if (result.ec != std::errc{} ||
            result.ptr != token.data() + token.size()) {
            return false;
        }
        fields[index] = parsed;
        if (!last) value.remove_prefix(comma + 1);
    }

    const uint64_t right = static_cast<uint64_t>(fields[0]) + fields[2];
    const uint64_t bottom = static_cast<uint64_t>(fields[1]) + fields[3];
    if (fields[2] == 0 || fields[3] == 0 ||
        right > static_cast<uint64_t>(std::numeric_limits<int>::max()) ||
        bottom > static_cast<uint64_t>(std::numeric_limits<int>::max())) {
        return false;
    }

    target = X11CaptureTarget{fields[0], fields[1], fields[2], fields[3]};
    return true;
}

} // namespace fthr

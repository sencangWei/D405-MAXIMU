#pragma once

#include <cstdint>

namespace as5047p {

constexpr uint16_t kDataMask = 0x3FFFU;
constexpr uint16_t kErrorMask = 0x4000U;
constexpr uint16_t kParityMask = 0x8000U;
constexpr uint16_t kReadMask = 0x4000U;
constexpr uint16_t kErrorFlagsAddress = 0x0001U;
constexpr uint16_t kAngleUncompensatedAddress = 0x3FFEU;

enum ErrorFlag : uint8_t {
    kFramingError = 1U << 0U,
    kInvalidCommand = 1U << 1U,
    kParityError = 1U << 2U,
};

inline bool hasEvenParity(uint16_t value) {
    bool odd = false;
    while (value != 0U) {
        odd = !odd;
        value = static_cast<uint16_t>(value & (value - 1U));
    }
    return !odd;
}

inline uint16_t withEvenParity(uint16_t value) {
    value = static_cast<uint16_t>(value & ~kParityMask);
    if (!hasEvenParity(value)) {
        value = static_cast<uint16_t>(value | kParityMask);
    }
    return value;
}

inline uint16_t makeReadCommand(uint16_t address) {
    return withEvenParity(
        static_cast<uint16_t>(kReadMask | (address & kDataMask)));
}

inline uint16_t makeNopCommand() {
    return withEvenParity(0U);
}

constexpr bool hasError(uint16_t response) {
    return (response & kErrorMask) != 0U;
}

inline bool isValidResponse(uint16_t response) {
    return hasEvenParity(response) && !hasError(response);
}

constexpr uint16_t angleRaw(uint16_t response) {
    return static_cast<uint16_t>(response & kDataMask);
}

constexpr uint8_t errorFlags(uint16_t response) {
    return static_cast<uint8_t>(response & 0x0007U);
}

}  // namespace as5047p

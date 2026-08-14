#pragma once

#include <cstdint>

namespace as5047p {

constexpr uint16_t kDataMask = 0x3FFF;
constexpr uint16_t kErrorMask = 0x4000;
constexpr uint16_t kParityMask = 0x8000;
constexpr uint16_t kReadMask = 0x4000;
constexpr uint16_t kAngleUncompensatedAddress = 0x3FFE;

inline bool hasEvenParity(uint16_t value) {
    bool odd = false;
    while (value != 0) {
        odd = !odd;
        value &= static_cast<uint16_t>(value - 1U);
    }
    return !odd;
}

inline uint16_t makeReadCommand(uint16_t address) {
    uint16_t command = static_cast<uint16_t>(kReadMask | (address & kDataMask));
    if (!hasEvenParity(command)) {
        command = static_cast<uint16_t>(command | kParityMask);
    }
    return command;
}

constexpr bool hasError(uint16_t response) {
    return (response & kErrorMask) != 0;
}

constexpr uint16_t data(uint16_t response) {
    return static_cast<uint16_t>(response & kDataMask);
}

}  // namespace as5047p

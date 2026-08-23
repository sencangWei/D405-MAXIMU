#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "kt_ex9_protocol.h"

namespace combined {

constexpr size_t kPacketSize = 63U;
constexpr size_t kCrcOffset = 61U;
constexpr uint8_t kSync0 = 0xA5U;
constexpr uint8_t kSync1 = 0x5AU;
constexpr uint8_t kVersion = 1U;

enum Flag : uint16_t {
    kImuValid = 1U << 0U,
    kEncoderValid = 1U << 1U,
    kEncoderError = 1U << 2U,
    kEncoderParityError = 1U << 3U,
    kImuCounterGap = 1U << 4U,
    kImuQueueOverflow = 1U << 5U,
    kPcTxQueueOverflow = 1U << 6U,
};

struct Sample {
    uint16_t flags = 0U;
    uint32_t sequence = 0U;
    uint32_t imu_first_byte_rx_us = 0U;
    uint32_t encoder_read_us = 0U;
    uint32_t imu_counter = 0U;
    uint16_t encoder_response = 0U;
    std::array<uint8_t, kt_ex9::kFrameSize> imu_frame{};
};

inline uint16_t readLe16(const uint8_t* data) {
    return static_cast<uint16_t>(data[0]) |
           static_cast<uint16_t>(static_cast<uint16_t>(data[1]) << 8U);
}

inline uint32_t readLe32(const uint8_t* data) {
    return static_cast<uint32_t>(data[0]) |
           (static_cast<uint32_t>(data[1]) << 8U) |
           (static_cast<uint32_t>(data[2]) << 16U) |
           (static_cast<uint32_t>(data[3]) << 24U);
}

inline void writeLe16(uint8_t* data, uint16_t value) {
    data[0] = static_cast<uint8_t>(value);
    data[1] = static_cast<uint8_t>(value >> 8U);
}

inline void writeLe32(uint8_t* data, uint32_t value) {
    data[0] = static_cast<uint8_t>(value);
    data[1] = static_cast<uint8_t>(value >> 8U);
    data[2] = static_cast<uint8_t>(value >> 16U);
    data[3] = static_cast<uint8_t>(value >> 24U);
}

inline uint16_t crc16CcittFalse(const uint8_t* data, size_t size) {
    uint16_t crc = 0xFFFFU;
    for (size_t i = 0U; i < size; ++i) {
        crc = static_cast<uint16_t>(crc ^
                                    (static_cast<uint16_t>(data[i]) << 8U));
        for (uint8_t bit = 0U; bit < 8U; ++bit) {
            crc = (crc & 0x8000U) != 0U
                      ? static_cast<uint16_t>((crc << 1U) ^ 0x1021U)
                      : static_cast<uint16_t>(crc << 1U);
        }
    }
    return crc;
}

inline std::array<uint8_t, kPacketSize> encode(const Sample& sample) {
    std::array<uint8_t, kPacketSize> packet{};
    packet[0] = kSync0;
    packet[1] = kSync1;
    packet[2] = kVersion;
    packet[3] = static_cast<uint8_t>(kPacketSize);
    writeLe16(packet.data() + 4U, sample.flags);
    writeLe32(packet.data() + 6U, sample.sequence);
    writeLe32(packet.data() + 10U, sample.imu_first_byte_rx_us);
    writeLe32(packet.data() + 14U, sample.encoder_read_us);
    writeLe32(packet.data() + 18U, sample.imu_counter);
    writeLe16(packet.data() + 22U, sample.encoder_response);
    for (size_t i = 0U; i < sample.imu_frame.size(); ++i) {
        packet[24U + i] = sample.imu_frame[i];
    }
    writeLe16(packet.data() + kCrcOffset,
              crc16CcittFalse(packet.data(), kCrcOffset));
    return packet;
}

}  // namespace combined

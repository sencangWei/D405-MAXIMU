#pragma once

#include <cstddef>
#include <cstdint>

namespace board_config {

constexpr int kImuRxPin = 8;
constexpr int kImuTxPin = 9;
constexpr uint32_t kImuBaud = 921600;
constexpr size_t kImuRxBufferSize = 4096;

constexpr int kEncoderCsPin = 10;
constexpr int kEncoderMosiPin = 15;
constexpr int kEncoderMisoPin = 16;
constexpr int kEncoderSckPin = 17;
constexpr uint32_t kEncoderSpiHz = 1000000;

constexpr uint32_t kUsbBaud = 115200;
constexpr uint32_t kPrintEveryFrames = 40;
constexpr uint64_t kWaitingIntervalUs = 1000000ULL;
constexpr float kDegreesPerEncoderCount = 360.0F / 16384.0F;

}  // namespace board_config

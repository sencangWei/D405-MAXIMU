#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace kt_ex9 {

constexpr size_t kFrameSize = 37U;
constexpr uint8_t kHeader0 = 0xEBU;
constexpr uint8_t kHeader1 = 0x90U;
constexpr uint8_t kExpectedLength = 0x22U;

struct Frame {
    std::array<uint8_t, kFrameSize> raw{};
    uint32_t counter = 0U;
    uint32_t first_byte_rx_us = 0U;
};

enum class ParseResult : uint8_t {
    None,
    HeaderConfirmed,
    FrameReady,
    BadChecksum,
};

inline uint32_t readLe32(const uint8_t* data) {
    return static_cast<uint32_t>(data[0]) |
           (static_cast<uint32_t>(data[1]) << 8U) |
           (static_cast<uint32_t>(data[2]) << 16U) |
           (static_cast<uint32_t>(data[3]) << 24U);
}

inline bool verifyChecksum(const uint8_t* frame, size_t size) {
    if (frame == nullptr || size != kFrameSize) {
        return false;
    }

    uint32_t sum = 0U;
    for (size_t i = 0U; i < kFrameSize - 1U; ++i) {
        sum += frame[i];
    }
    return static_cast<uint8_t>(sum) == frame[kFrameSize - 1U];
}

inline bool isCounterDiscontinuity(uint32_t previous, uint32_t current) {
    return current != previous + 1U && current != 1U;
}

class StreamParser {
public:
    ParseResult feed(uint8_t byte, uint32_t rx_us, Frame& output) {
        if (size_ == 0U) {
            if (byte != kHeader0) {
                ++resyncs_;
                return ParseResult::None;
            }
            buffer_[0] = byte;
            first_byte_rx_us_ = rx_us;
            size_ = 1U;
            return ParseResult::None;
        }

        if (size_ == 1U && byte != kHeader1) {
            restartCandidate(byte, rx_us);
            return ParseResult::None;
        }

        if (size_ == 2U && byte != kExpectedLength) {
            restartCandidate(byte, rx_us);
            return ParseResult::None;
        }

        buffer_[size_++] = byte;
        if (size_ == 3U) {
            return ParseResult::HeaderConfirmed;
        }
        if (size_ < kFrameSize) {
            return ParseResult::None;
        }

        if (!verifyChecksum(buffer_.data(), buffer_.size())) {
            size_ = 0U;
            ++frames_bad_;
            ++resyncs_;
            return ParseResult::BadChecksum;
        }

        output.raw = buffer_;
        output.counter = readLe32(buffer_.data() + 32U);
        output.first_byte_rx_us = first_byte_rx_us_;
        size_ = 0U;
        ++frames_ok_;
        return ParseResult::FrameReady;
    }

    uint32_t framesOk() const { return frames_ok_; }
    uint32_t framesBad() const { return frames_bad_; }
    uint32_t resyncs() const { return resyncs_; }
    void reset() { size_ = 0U; }

private:
    void restartCandidate(uint8_t byte, uint32_t rx_us) {
        ++resyncs_;
        if (byte == kHeader0) {
            buffer_[0] = byte;
            first_byte_rx_us_ = rx_us;
            size_ = 1U;
        } else {
            size_ = 0U;
        }
    }

    std::array<uint8_t, kFrameSize> buffer_{};
    size_t size_ = 0U;
    uint32_t first_byte_rx_us_ = 0U;
    uint32_t frames_ok_ = 0U;
    uint32_t frames_bad_ = 0U;
    uint32_t resyncs_ = 0U;
};

}  // namespace kt_ex9

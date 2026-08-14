#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace kt_ex9 {

constexpr size_t kFrameSize = 37;
constexpr uint8_t kHeader0 = 0xEB;
constexpr uint8_t kHeader1 = 0x90;
constexpr uint8_t kExpectedLength = 0x22;

struct Sample {
    float gx = 0.0F;
    float gy = 0.0F;
    float gz = 0.0F;
    float ax = 0.0F;
    float ay = 0.0F;
    float az = 0.0F;
    float temperature = 0.0F;
    uint32_t counter = 0;
};

inline bool hasHeader(const uint8_t* frame, size_t size) {
    return size >= 3 && frame[0] == kHeader0 && frame[1] == kHeader1 &&
           frame[2] == kExpectedLength;
}

inline bool verifyChecksum(const uint8_t* frame, size_t size) {
    if (frame == nullptr || size < kFrameSize) {
        return false;
    }

    uint32_t sum = 0;
    for (size_t i = 0; i < kFrameSize - 1; ++i) {
        sum += frame[i];
    }
    return static_cast<uint8_t>(sum & 0xFFU) == frame[kFrameSize - 1];
}

inline uint32_t readLe32(const uint8_t* data) {
    return static_cast<uint32_t>(data[0]) |
           (static_cast<uint32_t>(data[1]) << 8U) |
           (static_cast<uint32_t>(data[2]) << 16U) |
           (static_cast<uint32_t>(data[3]) << 24U);
}

inline float readLeFloat(const uint8_t* data) {
    const uint32_t bits = readLe32(data);
    float value = 0.0F;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

inline bool parseFrame(const uint8_t* frame, size_t size, Sample& sample) {
    if (frame == nullptr || size < kFrameSize || !hasHeader(frame, size) ||
        !verifyChecksum(frame, size)) {
        return false;
    }

    sample.gx = readLeFloat(frame + 4);
    sample.gy = readLeFloat(frame + 8);
    sample.gz = readLeFloat(frame + 12);
    sample.ax = readLeFloat(frame + 16);
    sample.ay = readLeFloat(frame + 20);
    sample.az = readLeFloat(frame + 24);
    sample.temperature = readLeFloat(frame + 28);
    sample.counter = readLe32(frame + 32);
    return true;
}

inline bool isCounterDiscontinuity(uint32_t previous, uint32_t current) {
    return current != previous + 1U && current != 1U;
}

enum class ParseResult {
    None,
    Frame,
    BadChecksum,
};

class StreamParser {
public:
    ParseResult feed(uint8_t byte, Sample& sample) {
        if (size_ < kFrameSize) {
            buffer_[size_++] = byte;
        }

        discardNoise();
        if (size_ < kFrameSize) {
            return ParseResult::None;
        }

        if (parseFrame(buffer_.data(), size_, sample)) {
            size_ = 0;
            ++frames_ok_;
            return ParseResult::Frame;
        }

        ++frames_bad_;
        dropFirst();
        ++resyncs_;
        return ParseResult::BadChecksum;
    }

    uint32_t framesOk() const {
        return frames_ok_;
    }

    uint32_t framesBad() const {
        return frames_bad_;
    }

    uint32_t resyncs() const {
        return resyncs_;
    }

private:
    void discardNoise() {
        while (size_ >= 3 && !hasHeader(buffer_.data(), size_)) {
            dropFirst();
            ++resyncs_;
        }
    }

    void dropFirst() {
        if (size_ == 0) {
            return;
        }
        std::memmove(buffer_.data(), buffer_.data() + 1, size_ - 1);
        --size_;
    }

    std::array<uint8_t, kFrameSize> buffer_{};
    size_t size_ = 0;
    uint32_t frames_ok_ = 0;
    uint32_t frames_bad_ = 0;
    uint32_t resyncs_ = 0;
};

}  // namespace kt_ex9

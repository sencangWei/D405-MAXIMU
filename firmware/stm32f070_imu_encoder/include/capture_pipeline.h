#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "as5047p_protocol.h"
#include "combined_packet.h"
#include "kt_ex9_protocol.h"

namespace capture {

constexpr size_t kQueueCapacity = 4U;

inline void queueMemoryBarrier() {
#if defined(__arm__) || defined(__thumb__)
    __asm volatile("dmb" ::: "memory");
#else
    std::atomic_signal_fence(std::memory_order_seq_cst);
#endif
}

enum class PipelineEvent : uint8_t {
    None,
    EncoderReadRequested,
    BadImuFrame,
    SampleReady,
};

struct ImuRxByte {
    uint8_t value = 0U;
    uint32_t rx_us = 0U;
    uint32_t generation = 0U;
};

template <typename T, size_t Capacity>
class SpscQueue {
public:
    static_assert(Capacity > 0U, "queue capacity must be positive");
    static_assert(Capacity < 255U, "queue capacity exceeds uint8_t index");

    bool push(const T& value) {
        const uint8_t next = increment(head_);
        const uint8_t tail = tail_;
        queueMemoryBarrier();
        if (next == tail) {
            return false;
        }
        storage_[head_] = value;
        queueMemoryBarrier();
        head_ = next;
        return true;
    }

    bool pop(T& value) {
        if (tail_ == head_) {
            return false;
        }
        queueMemoryBarrier();
        value = storage_[tail_];
        queueMemoryBarrier();
        tail_ = increment(tail_);
        return true;
    }

    bool empty() const {
        return tail_ == head_;
    }

private:
    static constexpr uint8_t kStorageSize =
        static_cast<uint8_t>(Capacity + 1U);

    static uint8_t increment(uint8_t index) {
        ++index;
        return index == kStorageSize ? 0U : index;
    }

    std::array<T, Capacity + 1U> storage_{};
    volatile uint8_t head_ = 0U;
    volatile uint8_t tail_ = 0U;
};

template <size_t Capacity>
class ImuRxBuffer {
public:
    bool push(ImuRxByte value) {
        value.generation = generation_;
        if (!queue_.push(value)) {
            ++generation_;
            ++overflows_;
            return false;
        }
        return true;
    }

    bool pop(ImuRxByte& value) { return queue_.pop(value); }
    bool empty() const { return queue_.empty(); }
    uint32_t overflows() const { return overflows_; }
    void noteDiscontinuity() {
        ++generation_;
        ++overflows_;
    }

private:
    SpscQueue<ImuRxByte, Capacity> queue_{};
    volatile uint32_t generation_ = 0U;
    volatile uint32_t overflows_ = 0U;
};

class Pipeline {
public:
    PipelineEvent onImuByte(uint8_t byte, uint32_t rx_us) {
        kt_ex9::Frame frame{};
        const auto result = parser_.feed(byte, rx_us, frame);
        if (result == kt_ex9::ParseResult::HeaderConfirmed) {
            pending_encoder_valid_ = false;
            awaiting_encoder_ = true;
            return PipelineEvent::EncoderReadRequested;
        }
        if (result == kt_ex9::ParseResult::BadChecksum) {
            pending_encoder_valid_ = false;
            awaiting_encoder_ = false;
            return PipelineEvent::BadImuFrame;
        }
        if (result == kt_ex9::ParseResult::FrameReady) {
            emitFrame(frame);
            awaiting_encoder_ = false;
            return PipelineEvent::SampleReady;
        }
        return PipelineEvent::None;
    }

    void storePendingEncoder(uint16_t response, uint32_t encoder_read_us) {
        if (!awaiting_encoder_) {
            return;
        }
        pending_encoder_response_ = response;
        pending_encoder_read_us_ = encoder_read_us;
        pending_encoder_valid_ = true;
    }

    void noteImuQueueOverflow(uint32_t count = 1U) {
        if (count == 0U) {
            return;
        }
        imu_queue_overflows_ += count;
        imu_overflow_sticky_ = true;
    }

    void abortImuFrame() {
        parser_.reset();
        awaiting_encoder_ = false;
        pending_encoder_valid_ = false;
    }

    bool popOutput(combined::Sample& sample) {
        return output_queue_.pop(sample);
    }

    bool hasOutput() const { return !output_queue_.empty(); }

    uint32_t imuQueueOverflows() const { return imu_queue_overflows_; }
    uint32_t outputQueueOverflows() const { return output_queue_overflows_; }
    uint32_t framesOk() const { return parser_.framesOk(); }
    uint32_t framesBad() const { return parser_.framesBad(); }

private:
    void emitFrame(const kt_ex9::Frame& frame) {
        combined::Sample sample{};
        sample.flags = combined::kImuValid;
        if (have_previous_counter_ &&
            kt_ex9::isCounterDiscontinuity(previous_counter_, frame.counter)) {
            sample.flags = static_cast<uint16_t>(
                sample.flags | combined::kImuCounterGap);
        }
        previous_counter_ = frame.counter;
        have_previous_counter_ = true;

        if (imu_overflow_sticky_) {
            sample.flags = static_cast<uint16_t>(
                sample.flags | combined::kImuQueueOverflow);
        }
        if (output_overflow_sticky_) {
            sample.flags = static_cast<uint16_t>(
                sample.flags | combined::kPcTxQueueOverflow);
        }

        if (pending_encoder_valid_) {
            sample.encoder_response = pending_encoder_response_;
            sample.encoder_read_us = pending_encoder_read_us_;
            if (!as5047p::hasEvenParity(pending_encoder_response_)) {
                sample.flags = static_cast<uint16_t>(
                    sample.flags | combined::kEncoderParityError);
            }
            if (as5047p::hasError(pending_encoder_response_)) {
                sample.flags = static_cast<uint16_t>(
                    sample.flags | combined::kEncoderError);
            }
            if (as5047p::isValidResponse(pending_encoder_response_)) {
                sample.flags = static_cast<uint16_t>(
                    sample.flags | combined::kEncoderValid);
            }
        }

        sample.sequence = sequence_++;
        sample.imu_first_byte_rx_us = frame.first_byte_rx_us;
        sample.imu_counter = frame.counter;
        sample.imu_frame = frame.raw;
        pending_encoder_valid_ = false;

        if (!output_queue_.push(sample)) {
            output_overflow_sticky_ = true;
            ++output_queue_overflows_;
        }
    }

    kt_ex9::StreamParser parser_{};
    SpscQueue<combined::Sample, kQueueCapacity> output_queue_{};
    uint32_t previous_counter_ = 0U;
    uint32_t sequence_ = 0U;
    uint32_t imu_queue_overflows_ = 0U;
    uint32_t output_queue_overflows_ = 0U;
    uint32_t pending_encoder_read_us_ = 0U;
    uint16_t pending_encoder_response_ = 0U;
    bool have_previous_counter_ = false;
    bool awaiting_encoder_ = false;
    bool pending_encoder_valid_ = false;
    bool imu_overflow_sticky_ = false;
    bool output_overflow_sticky_ = false;
};

}  // namespace capture

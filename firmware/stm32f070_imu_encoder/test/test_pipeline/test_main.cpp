#include <array>
#include <cstddef>
#include <cstdint>

#include <unity.h>

#include "capture_pipeline.h"

void setUp() {}
void tearDown() {}

namespace {

std::array<uint8_t, kt_ex9::kFrameSize> makeImuFrame(uint32_t counter) {
    std::array<uint8_t, kt_ex9::kFrameSize> frame{};
    frame[0] = kt_ex9::kHeader0;
    frame[1] = kt_ex9::kHeader1;
    frame[2] = kt_ex9::kExpectedLength;
    frame[32] = static_cast<uint8_t>(counter);
    frame[33] = static_cast<uint8_t>(counter >> 8U);
    frame[34] = static_cast<uint8_t>(counter >> 16U);
    frame[35] = static_cast<uint8_t>(counter >> 24U);
    uint32_t sum = 0U;
    for (size_t i = 0U; i < frame.size() - 1U; ++i) {
        sum += frame[i];
    }
    frame.back() = static_cast<uint8_t>(sum);
    return frame;
}

capture::PipelineEvent feedFrame(capture::Pipeline& pipeline,
                                 const std::array<uint8_t,
                                                  kt_ex9::kFrameSize>& frame,
                                 uint32_t first_us,
                                 bool capture_encoder = false,
                                 uint16_t encoder_response = 0U,
                                 uint32_t encoder_us = 0U) {
    capture::PipelineEvent final_event = capture::PipelineEvent::None;
    for (size_t i = 0U; i < frame.size(); ++i) {
        const auto event = pipeline.onImuByte(
            frame[i], first_us + static_cast<uint32_t>(i * 11U));
        if (i == 2U) {
            TEST_ASSERT_EQUAL_INT(
                static_cast<int>(capture::PipelineEvent::EncoderReadRequested),
                static_cast<int>(event));
            if (capture_encoder) {
                pipeline.storePendingEncoder(encoder_response, encoder_us);
            }
        }
        final_event = event;
    }
    return final_event;
}

combined::Sample popOne(capture::Pipeline& pipeline) {
    combined::Sample output{};
    TEST_ASSERT_TRUE(pipeline.popOutput(output));
    return output;
}

void test_parser_reports_confirmed_header_on_third_byte() {
    kt_ex9::StreamParser parser;
    kt_ex9::Frame output{};

    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(kt_ex9::ParseResult::None),
        static_cast<int>(parser.feed(0xEBU, 1000U, output)));
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(kt_ex9::ParseResult::None),
        static_cast<int>(parser.feed(0x90U, 1011U, output)));
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(kt_ex9::ParseResult::HeaderConfirmed),
        static_cast<int>(parser.feed(0x22U, 1022U, output)));

    const auto frame = makeImuFrame(7U);
    for (size_t i = 3U; i < frame.size() - 1U; ++i) {
        TEST_ASSERT_EQUAL_INT(
            static_cast<int>(kt_ex9::ParseResult::None),
            static_cast<int>(parser.feed(
                frame[i], 1000U + static_cast<uint32_t>(i * 11U), output)));
    }
    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(kt_ex9::ParseResult::FrameReady),
        static_cast<int>(parser.feed(frame.back(), 1396U, output)));
    TEST_ASSERT_EQUAL_UINT32(1000U, output.first_byte_rx_us);
    TEST_ASSERT_EQUAL_UINT32(7U, output.counter);
}

void test_valid_frame_uses_encoder_captured_at_header() {
    capture::Pipeline pipeline;
    const auto frame = makeImuFrame(10U);

    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(capture::PipelineEvent::SampleReady),
        static_cast<int>(feedFrame(
            pipeline, frame, 1000U, true, 0x9234U, 1044U)));
    const combined::Sample output = popOne(pipeline);

    TEST_ASSERT_EQUAL_UINT32(0U, output.sequence);
    TEST_ASSERT_EQUAL_UINT32(1000U, output.imu_first_byte_rx_us);
    TEST_ASSERT_EQUAL_UINT32(1044U, output.encoder_read_us);
    TEST_ASSERT_EQUAL_UINT32(10U, output.imu_counter);
    TEST_ASSERT_EQUAL_HEX16(0x9234U, output.encoder_response);
    TEST_ASSERT_BITS_HIGH(combined::kImuValid | combined::kEncoderValid,
                          output.flags);
}

void test_bad_frame_discards_pending_encoder_sample() {
    capture::Pipeline pipeline;
    auto bad = makeImuFrame(10U);
    bad.back() ^= 0x01U;

    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(capture::PipelineEvent::BadImuFrame),
        static_cast<int>(feedFrame(
            pipeline, bad, 1000U, true, 0x9234U, 1044U)));
    combined::Sample output{};
    TEST_ASSERT_FALSE(pipeline.popOutput(output));

    TEST_ASSERT_EQUAL_INT(
        static_cast<int>(capture::PipelineEvent::SampleReady),
        static_cast<int>(feedFrame(pipeline, makeImuFrame(11U), 2000U)));
    output = popOne(pipeline);
    TEST_ASSERT_BITS_HIGH(combined::kImuValid, output.flags);
    TEST_ASSERT_BITS_LOW(combined::kEncoderValid, output.flags);
    TEST_ASSERT_EQUAL_HEX16(0U, output.encoder_response);
    TEST_ASSERT_EQUAL_UINT32(0U, output.encoder_read_us);
}

void test_encoder_error_still_emits_imu_sample() {
    capture::Pipeline pipeline;
    feedFrame(pipeline, makeImuFrame(1U), 2000U, true, 0xFFFFU, 2044U);

    const combined::Sample output = popOne(pipeline);
    TEST_ASSERT_BITS_HIGH(combined::kImuValid | combined::kEncoderError,
                          output.flags);
    TEST_ASSERT_BITS_LOW(combined::kEncoderValid, output.flags);
    TEST_ASSERT_EQUAL_UINT32(1U, output.imu_counter);
}

void test_valid_frame_without_encoder_still_emits_imu() {
    capture::Pipeline pipeline;
    feedFrame(pipeline, makeImuFrame(3U), 3000U);

    const combined::Sample output = popOne(pipeline);
    TEST_ASSERT_BITS_HIGH(combined::kImuValid, output.flags);
    TEST_ASSERT_BITS_LOW(combined::kEncoderValid |
                             combined::kEncoderError |
                             combined::kEncoderParityError,
                         output.flags);
    TEST_ASSERT_EQUAL_UINT32(3U, output.imu_counter);
}

void test_counter_gap_is_reported_on_new_sample() {
    capture::Pipeline pipeline;
    feedFrame(pipeline, makeImuFrame(40U), 100U);
    popOne(pipeline);
    feedFrame(pipeline, makeImuFrame(42U), 200U);

    const combined::Sample output = popOne(pipeline);
    TEST_ASSERT_BITS_HIGH(combined::kImuCounterGap, output.flags);
}

void test_rx_buffer_reports_overflow_and_preserves_fifo() {
    capture::ImuRxBuffer<2U> buffer;
    TEST_ASSERT_TRUE(buffer.push({0xEBU, 100U}));
    TEST_ASSERT_TRUE(buffer.push({0x90U, 110U}));
    TEST_ASSERT_FALSE(buffer.push({0x22U, 120U}));
    TEST_ASSERT_EQUAL_UINT32(1U, buffer.overflows());

    capture::ImuRxByte value{};
    TEST_ASSERT_TRUE(buffer.pop(value));
    TEST_ASSERT_EQUAL_HEX8(0xEBU, value.value);
    TEST_ASSERT_EQUAL_UINT32(100U, value.rx_us);
    TEST_ASSERT_TRUE(buffer.pop(value));
    TEST_ASSERT_EQUAL_HEX8(0x90U, value.value);
    TEST_ASSERT_EQUAL_UINT32(110U, value.rx_us);
    TEST_ASSERT_FALSE(buffer.pop(value));

    TEST_ASSERT_TRUE(buffer.push({0x55U, 130U}));
    TEST_ASSERT_TRUE(buffer.pop(value));
    TEST_ASSERT_EQUAL_UINT32(1U, value.generation);
}

void test_receive_discontinuity_discards_partial_frame_and_encoder() {
    capture::Pipeline pipeline;
    const auto interrupted = makeImuFrame(10U);
    for (size_t i = 0U; i < 12U; ++i) {
        const auto event = pipeline.onImuByte(
            interrupted[i], 1000U + static_cast<uint32_t>(i * 11U));
        if (event == capture::PipelineEvent::EncoderReadRequested) {
            pipeline.storePendingEncoder(0x9234U, 1044U);
        }
    }

    pipeline.abortImuFrame();
    for (size_t i = 12U; i < interrupted.size(); ++i) {
        pipeline.onImuByte(
            interrupted[i], 1000U + static_cast<uint32_t>(i * 11U));
    }
    combined::Sample output{};
    TEST_ASSERT_FALSE(pipeline.popOutput(output));

    feedFrame(pipeline, makeImuFrame(11U), 2000U, true, 0x8000U, 2044U);
    output = popOne(pipeline);
    TEST_ASSERT_EQUAL_UINT32(11U, output.imu_counter);
    TEST_ASSERT_EQUAL_HEX16(0x8000U, output.encoder_response);
    TEST_ASSERT_NOT_EQUAL(0x9234U, output.encoder_response);
}

void test_explicit_rx_error_advances_buffer_generation() {
    capture::ImuRxBuffer<4U> buffer;
    TEST_ASSERT_TRUE(buffer.push({0xEBU, 100U}));
    buffer.noteDiscontinuity();
    TEST_ASSERT_TRUE(buffer.push({0x90U, 110U}));
    TEST_ASSERT_EQUAL_UINT32(1U, buffer.overflows());

    capture::ImuRxByte value{};
    TEST_ASSERT_TRUE(buffer.pop(value));
    TEST_ASSERT_EQUAL_UINT32(0U, value.generation);
    TEST_ASSERT_TRUE(buffer.pop(value));
    TEST_ASSERT_EQUAL_UINT32(1U, value.generation);
}

void test_mixed_generation_drain_restarts_before_fresh_frame() {
    capture::ImuRxBuffer<96U> buffer;
    capture::Pipeline pipeline;
    const auto interrupted = makeImuFrame(10U);
    const auto fresh = makeImuFrame(11U);

    for (size_t i = 0U; i < 12U; ++i) {
        TEST_ASSERT_TRUE(buffer.push(
            {interrupted[i], 1000U + static_cast<uint32_t>(i * 11U)}));
    }
    buffer.noteDiscontinuity();
    for (size_t i = 12U; i < interrupted.size(); ++i) {
        TEST_ASSERT_TRUE(buffer.push(
            {interrupted[i], 1000U + static_cast<uint32_t>(i * 11U)}));
    }
    for (size_t i = 0U; i < fresh.size(); ++i) {
        TEST_ASSERT_TRUE(buffer.push(
            {fresh[i], 2000U + static_cast<uint32_t>(i * 11U)}));
    }

    uint32_t consumed_generation = 0U;
    capture::ImuRxByte input{};
    while (buffer.pop(input)) {
        if (input.generation != consumed_generation) {
            pipeline.abortImuFrame();
            pipeline.noteImuQueueOverflow(
                input.generation - consumed_generation);
            consumed_generation = input.generation;
        }
        const auto event = pipeline.onImuByte(input.value, input.rx_us);
        if (event == capture::PipelineEvent::EncoderReadRequested) {
            const uint16_t response =
                input.generation == 0U ? 0x9234U : 0x8000U;
            pipeline.storePendingEncoder(response, input.rx_us + 22U);
        }
    }

    combined::Sample output = popOne(pipeline);
    TEST_ASSERT_EQUAL_UINT32(11U, output.imu_counter);
    TEST_ASSERT_EQUAL_HEX16(0x8000U, output.encoder_response);
    TEST_ASSERT_BITS_HIGH(combined::kImuQueueOverflow, output.flags);
    TEST_ASSERT_FALSE(pipeline.popOutput(output));
}

void test_rx_overflow_flag_is_sticky_on_next_sample() {
    capture::Pipeline pipeline;
    pipeline.noteImuQueueOverflow();
    TEST_ASSERT_EQUAL_UINT32(1U, pipeline.imuQueueOverflows());

    feedFrame(pipeline, makeImuFrame(1U), 100U);
    const combined::Sample output = popOne(pipeline);
    TEST_ASSERT_BITS_HIGH(combined::kImuQueueOverflow, output.flags);
}

void test_output_queue_overflow_is_reported_on_later_sample() {
    capture::Pipeline pipeline;
    for (uint32_t counter = 1U; counter <= 5U; ++counter) {
        feedFrame(pipeline, makeImuFrame(counter), counter * 100U);
    }
    TEST_ASSERT_EQUAL_UINT32(1U, pipeline.outputQueueOverflows());

    combined::Sample discarded{};
    TEST_ASSERT_TRUE(pipeline.popOutput(discarded));
    feedFrame(pipeline, makeImuFrame(6U), 600U);

    combined::Sample output{};
    while (pipeline.popOutput(output)) {
        if (output.imu_counter == 6U) {
            TEST_ASSERT_BITS_HIGH(combined::kPcTxQueueOverflow, output.flags);
            return;
        }
    }
    TEST_FAIL_MESSAGE("counter 6 was not emitted");
}

void test_buffered_drain_preserves_timestamp_and_encoder_association() {
    capture::ImuRxBuffer<64U> buffer;
    capture::Pipeline pipeline;
    const auto frame = makeImuFrame(77U);
    for (size_t i = 0U; i < frame.size(); ++i) {
        TEST_ASSERT_TRUE(buffer.push(
            {frame[i], 5000U + static_cast<uint32_t>(i * 11U)}));
    }

    capture::ImuRxByte input{};
    while (buffer.pop(input)) {
        const auto event = pipeline.onImuByte(input.value, input.rx_us);
        if (event == capture::PipelineEvent::EncoderReadRequested) {
            pipeline.storePendingEncoder(0x9234U, 5044U);
        }
    }

    const combined::Sample output = popOne(pipeline);
    TEST_ASSERT_EQUAL_UINT32(5000U, output.imu_first_byte_rx_us);
    TEST_ASSERT_EQUAL_UINT32(5044U, output.encoder_read_us);
    TEST_ASSERT_EQUAL_UINT32(77U, output.imu_counter);
    TEST_ASSERT_EQUAL_HEX16(0x9234U, output.encoder_response);
}

}  // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_parser_reports_confirmed_header_on_third_byte);
    RUN_TEST(test_valid_frame_uses_encoder_captured_at_header);
    RUN_TEST(test_bad_frame_discards_pending_encoder_sample);
    RUN_TEST(test_encoder_error_still_emits_imu_sample);
    RUN_TEST(test_valid_frame_without_encoder_still_emits_imu);
    RUN_TEST(test_counter_gap_is_reported_on_new_sample);
    RUN_TEST(test_rx_buffer_reports_overflow_and_preserves_fifo);
    RUN_TEST(test_receive_discontinuity_discards_partial_frame_and_encoder);
    RUN_TEST(test_explicit_rx_error_advances_buffer_generation);
    RUN_TEST(test_mixed_generation_drain_restarts_before_fresh_frame);
    RUN_TEST(test_rx_overflow_flag_is_sticky_on_next_sample);
    RUN_TEST(test_output_queue_overflow_is_reported_on_later_sample);
    RUN_TEST(test_buffered_drain_preserves_timestamp_and_encoder_association);
    return UNITY_END();
}

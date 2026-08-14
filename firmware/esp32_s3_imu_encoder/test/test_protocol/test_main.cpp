#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>

#include <unity.h>

#include "as5047p_protocol.h"
#include "kt_ex9_protocol.h"

namespace {

std::array<uint8_t, kt_ex9::kFrameSize> makeValidImuFrame() {
    std::array<uint8_t, kt_ex9::kFrameSize> frame{};
    frame[0] = kt_ex9::kHeader0;
    frame[1] = kt_ex9::kHeader1;
    frame[2] = kt_ex9::kExpectedLength;
    frame[3] = 0x01;

    const float values[7] = {1.0F, -2.0F, 3.5F, 0.1F, -0.2F, 0.3F, 26.5F};
    std::memcpy(frame.data() + 4, values, sizeof(values));

    const uint32_t counter = 399;
    frame[32] = static_cast<uint8_t>(counter & 0xFFU);
    frame[33] = static_cast<uint8_t>((counter >> 8U) & 0xFFU);
    frame[34] = static_cast<uint8_t>((counter >> 16U) & 0xFFU);
    frame[35] = static_cast<uint8_t>((counter >> 24U) & 0xFFU);

    uint32_t sum = 0;
    for (size_t i = 0; i < kt_ex9::kFrameSize - 1; ++i) {
        sum += frame[i];
    }
    frame[36] = static_cast<uint8_t>(sum & 0xFFU);
    return frame;
}

void test_as5047p_read_command_has_expected_bits() {
    TEST_ASSERT_EQUAL_HEX16(0x7FFE, as5047p::makeReadCommand(0x3FFE));
}

void test_as5047p_response_validation() {
    TEST_ASSERT_TRUE(as5047p::hasEvenParity(0x0000));
    TEST_ASSERT_FALSE(as5047p::hasEvenParity(0x0001));
    TEST_ASSERT_TRUE(as5047p::hasError(0x4123));
    TEST_ASSERT_EQUAL_HEX16(0x0123, as5047p::data(0x4123));
}

void test_imu_valid_frame_decodes_payload() {
    const auto frame = makeValidImuFrame();
    kt_ex9::Sample sample{};

    TEST_ASSERT_TRUE(kt_ex9::verifyChecksum(frame.data(), frame.size()));
    TEST_ASSERT_TRUE(kt_ex9::parseFrame(frame.data(), frame.size(), sample));
    TEST_ASSERT_FLOAT_WITHIN(0.0001F, 1.0F, sample.gx);
    TEST_ASSERT_FLOAT_WITHIN(0.0001F, -2.0F, sample.gy);
    TEST_ASSERT_FLOAT_WITHIN(0.0001F, 3.5F, sample.gz);
    TEST_ASSERT_FLOAT_WITHIN(0.0001F, 0.1F, sample.ax);
    TEST_ASSERT_FLOAT_WITHIN(0.0001F, -0.2F, sample.ay);
    TEST_ASSERT_FLOAT_WITHIN(0.0001F, 0.3F, sample.az);
    TEST_ASSERT_FLOAT_WITHIN(0.0001F, 26.5F, sample.temperature);
    TEST_ASSERT_EQUAL_UINT32(399, sample.counter);
}

void test_imu_bad_checksum_is_rejected() {
    auto frame = makeValidImuFrame();
    frame[36] ^= 0x01U;
    kt_ex9::Sample sample{};

    TEST_ASSERT_FALSE(kt_ex9::verifyChecksum(frame.data(), frame.size()));
    TEST_ASSERT_FALSE(kt_ex9::parseFrame(frame.data(), frame.size(), sample));
}

void test_stream_parser_resynchronizes_after_noise() {
    const auto frame = makeValidImuFrame();
    kt_ex9::StreamParser parser;
    kt_ex9::Sample sample{};

    TEST_ASSERT_EQUAL_INT(static_cast<int>(kt_ex9::ParseResult::None),
                          static_cast<int>(parser.feed(0x00, sample)));
    TEST_ASSERT_EQUAL_INT(static_cast<int>(kt_ex9::ParseResult::None),
                          static_cast<int>(parser.feed(0x55, sample)));

    kt_ex9::ParseResult result = kt_ex9::ParseResult::None;
    for (const uint8_t byte : frame) {
        result = parser.feed(byte, sample);
    }

    TEST_ASSERT_EQUAL_INT(static_cast<int>(kt_ex9::ParseResult::Frame),
                          static_cast<int>(result));
    TEST_ASSERT_EQUAL_UINT32(1, parser.framesOk());
    TEST_ASSERT_EQUAL_UINT32(2, parser.resyncs());
    TEST_ASSERT_EQUAL_UINT32(399, sample.counter);
}

void test_stream_parser_counts_bad_checksum() {
    auto frame = makeValidImuFrame();
    frame[36] ^= 0x01U;
    kt_ex9::StreamParser parser;
    kt_ex9::Sample sample{};
    kt_ex9::ParseResult result = kt_ex9::ParseResult::None;

    for (const uint8_t byte : frame) {
        result = parser.feed(byte, sample);
    }

    TEST_ASSERT_EQUAL_INT(static_cast<int>(kt_ex9::ParseResult::BadChecksum),
                          static_cast<int>(result));
    TEST_ASSERT_EQUAL_UINT32(1, parser.framesBad());
}

void test_counter_discontinuity_allows_increment_and_wrap_to_one() {
    TEST_ASSERT_FALSE(kt_ex9::isCounterDiscontinuity(39, 40));
    TEST_ASSERT_FALSE(kt_ex9::isCounterDiscontinuity(400, 1));
    TEST_ASSERT_TRUE(kt_ex9::isCounterDiscontinuity(40, 42));
}

}  // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_as5047p_read_command_has_expected_bits);
    RUN_TEST(test_as5047p_response_validation);
    RUN_TEST(test_imu_valid_frame_decodes_payload);
    RUN_TEST(test_imu_bad_checksum_is_rejected);
    RUN_TEST(test_stream_parser_resynchronizes_after_noise);
    RUN_TEST(test_stream_parser_counts_bad_checksum);
    RUN_TEST(test_counter_discontinuity_allows_increment_and_wrap_to_one);
    return UNITY_END();
}

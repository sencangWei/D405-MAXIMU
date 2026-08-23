#include <array>
#include <cstddef>
#include <cstdint>

#include <unity.h>

#include "as5047p_protocol.h"
#include "combined_packet.h"
#include "kt_ex9_protocol.h"

void setUp() {}
void tearDown() {}

namespace {

std::array<uint8_t, kt_ex9::kFrameSize> makeImuFrame(uint32_t counter = 399U) {
    std::array<uint8_t, kt_ex9::kFrameSize> frame{};
    frame[0] = 0xEB;
    frame[1] = 0x90;
    frame[2] = 0x22;
    frame[3] = 0x01;
    frame[32] = static_cast<uint8_t>(counter);
    frame[33] = static_cast<uint8_t>(counter >> 8U);
    frame[34] = static_cast<uint8_t>(counter >> 16U);
    frame[35] = static_cast<uint8_t>(counter >> 24U);

    uint32_t sum = 0;
    for (size_t i = 0; i < frame.size() - 1U; ++i) {
        sum += frame[i];
    }
    frame.back() = static_cast<uint8_t>(sum);
    return frame;
}

void test_imu_parser_keeps_first_byte_timestamp() {
    const auto bytes = makeImuFrame();
    kt_ex9::StreamParser parser;
    kt_ex9::Frame parsed{};
    kt_ex9::ParseResult result = kt_ex9::ParseResult::None;

    for (size_t i = 0; i < bytes.size(); ++i) {
        result = parser.feed(bytes[i], 123456U + static_cast<uint32_t>(i), parsed);
    }

    TEST_ASSERT_EQUAL_INT(static_cast<int>(kt_ex9::ParseResult::FrameReady),
                          static_cast<int>(result));
    TEST_ASSERT_EQUAL_UINT32(123456U, parsed.first_byte_rx_us);
    TEST_ASSERT_EQUAL_UINT32(399U, parsed.counter);
    TEST_ASSERT_EQUAL_MEMORY(bytes.data(), parsed.raw.data(), bytes.size());
}

void test_imu_parser_rejects_bad_checksum_and_resynchronizes_after_noise() {
    auto bad = makeImuFrame();
    bad.back() ^= 0x01U;
    const auto good = makeImuFrame(400U);
    kt_ex9::StreamParser parser;
    kt_ex9::Frame parsed{};

    parser.feed(0x00, 1U, parsed);
    parser.feed(0x55, 2U, parsed);
    for (size_t i = 0; i < bad.size(); ++i) {
        parser.feed(bad[i], 10U + static_cast<uint32_t>(i), parsed);
    }

    kt_ex9::ParseResult result = kt_ex9::ParseResult::None;
    for (size_t i = 0; i < good.size(); ++i) {
        result = parser.feed(good[i], 100U + static_cast<uint32_t>(i), parsed);
    }

    TEST_ASSERT_EQUAL_INT(static_cast<int>(kt_ex9::ParseResult::FrameReady),
                          static_cast<int>(result));
    TEST_ASSERT_EQUAL_UINT32(100U, parsed.first_byte_rx_us);
    TEST_ASSERT_EQUAL_UINT32(400U, parsed.counter);
    TEST_ASSERT_EQUAL_UINT32(1U, parser.framesBad());
    TEST_ASSERT_TRUE(parser.resyncs() >= 2U);
}

void test_as5047p_commands_and_response_validation() {
    TEST_ASSERT_EQUAL_HEX16(0x7FFEU, as5047p::makeReadCommand(0x3FFEU));
    TEST_ASSERT_EQUAL_HEX16(0x0000U, as5047p::makeNopCommand());
    TEST_ASSERT_TRUE(as5047p::isValidResponse(0x0000U));
    TEST_ASSERT_FALSE(as5047p::isValidResponse(0x0001U));
    TEST_ASSERT_FALSE(as5047p::isValidResponse(0x4000U));
    TEST_ASSERT_EQUAL_UINT16(0x1234U, as5047p::angleRaw(0x1234U));
}

void test_crc_matches_ccitt_false_reference_vector() {
    constexpr uint8_t kReference[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    TEST_ASSERT_EQUAL_HEX16(
        0x29B1U,
        combined::crc16CcittFalse(kReference, sizeof(kReference)));
}

void test_combined_packet_has_exact_layout_and_crc() {
    combined::Sample sample{};
    sample.flags = static_cast<uint16_t>(combined::kImuValid |
                                         combined::kEncoderValid);
    sample.sequence = 0x11223344U;
    sample.imu_first_byte_rx_us = 0x55667788U;
    sample.encoder_read_us = 0x99AABBCCU;
    sample.imu_counter = 0x01020304U;
    sample.encoder_response = 0x1234U;
    sample.imu_frame = makeImuFrame(sample.imu_counter);

    const auto packet = combined::encode(sample);

    TEST_ASSERT_EQUAL_UINT32(63U, packet.size());
    TEST_ASSERT_EQUAL_HEX8(0xA5U, packet[0]);
    TEST_ASSERT_EQUAL_HEX8(0x5AU, packet[1]);
    TEST_ASSERT_EQUAL_UINT8(1U, packet[2]);
    TEST_ASSERT_EQUAL_UINT8(63U, packet[3]);
    TEST_ASSERT_EQUAL_HEX16(sample.flags, combined::readLe16(packet.data() + 4));
    TEST_ASSERT_EQUAL_HEX32(sample.sequence, combined::readLe32(packet.data() + 6));
    TEST_ASSERT_EQUAL_HEX32(sample.imu_first_byte_rx_us,
                            combined::readLe32(packet.data() + 10));
    TEST_ASSERT_EQUAL_HEX32(sample.encoder_read_us,
                            combined::readLe32(packet.data() + 14));
    TEST_ASSERT_EQUAL_HEX32(sample.imu_counter,
                            combined::readLe32(packet.data() + 18));
    TEST_ASSERT_EQUAL_HEX16(sample.encoder_response,
                            combined::readLe16(packet.data() + 22));
    TEST_ASSERT_EQUAL_MEMORY(sample.imu_frame.data(), packet.data() + 24,
                             sample.imu_frame.size());
    TEST_ASSERT_EQUAL_HEX16(
        combined::crc16CcittFalse(packet.data(), 61U),
        combined::readLe16(packet.data() + 61));
}

}  // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_imu_parser_keeps_first_byte_timestamp);
    RUN_TEST(test_imu_parser_rejects_bad_checksum_and_resynchronizes_after_noise);
    RUN_TEST(test_as5047p_commands_and_response_validation);
    RUN_TEST(test_crc_matches_ccitt_false_reference_vector);
    RUN_TEST(test_combined_packet_has_exact_layout_and_crc);
    return UNITY_END();
}

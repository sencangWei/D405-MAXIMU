#include <Arduino.h>
#include <SPI.h>
#include <esp_timer.h>

#include "as5047p_protocol.h"
#include "board_config.h"
#include "kt_ex9_protocol.h"

namespace {

HardwareSerial imu_serial(1);
SPISettings encoder_spi(board_config::kEncoderSpiHz, MSBFIRST, SPI_MODE1);
kt_ex9::StreamParser imu_parser;

struct EncoderReading {
    uint16_t frame = 0;
    uint16_t raw = 0;
    float degrees = 0.0F;
    bool valid = false;
};

uint64_t first_frame_us = 0;
uint64_t last_frame_us = 0;
uint64_t last_waiting_print_us = 0;
uint32_t previous_counter = 0;
uint32_t dropped_frames = 0;
bool have_previous_counter = false;

uint16_t transferEncoderFrame(uint16_t frame) {
    SPI.beginTransaction(encoder_spi);
    digitalWrite(board_config::kEncoderCsPin, LOW);
    const uint16_t response = SPI.transfer16(frame);
    digitalWrite(board_config::kEncoderCsPin, HIGH);
    SPI.endTransaction();
    delayMicroseconds(1);
    return response;
}

EncoderReading readEncoder() {
    const uint16_t command =
        as5047p::makeReadCommand(as5047p::kAngleUncompensatedAddress);
    transferEncoderFrame(command);
    const uint16_t frame = transferEncoderFrame(command);

    EncoderReading reading;
    reading.frame = frame;
    reading.raw = as5047p::data(frame);
    reading.degrees =
        static_cast<float>(reading.raw) * board_config::kDegreesPerEncoderCount;
    reading.valid = !as5047p::hasError(frame) && as5047p::hasEvenParity(frame);
    return reading;
}

double observedImuRate(uint64_t now_us) {
    const uint32_t frame_count = imu_parser.framesOk();
    if (frame_count < 2 || now_us <= first_frame_us) {
        return 0.0;
    }
    return static_cast<double>(frame_count - 1U) * 1000000.0 /
           static_cast<double>(now_us - first_frame_us);
}

void updateCounterStats(uint32_t counter) {
    if (have_previous_counter &&
        kt_ex9::isCounterDiscontinuity(previous_counter, counter)) {
        ++dropped_frames;
    }
    previous_counter = counter;
    have_previous_counter = true;
}

void printCombinedSample(uint64_t timestamp_us,
                         const kt_ex9::Sample& imu,
                         const EncoderReading& encoder) {
    Serial.printf(
        "t_us=%llu rate=%.2fHz cnt=%lu "
        "gyro=[%.4f,%.4f,%.4f]dps accel=[%.4f,%.4f,%.4f]g "
        "temp=%.2fC enc_raw=%u enc=%.2fdeg enc_ok=%u frame=0x%04X "
        "ok=%lu bad=%lu resync=%lu drops=%lu\n",
        static_cast<unsigned long long>(timestamp_us),
        observedImuRate(timestamp_us),
        static_cast<unsigned long>(imu.counter),
        imu.gx,
        imu.gy,
        imu.gz,
        imu.ax,
        imu.ay,
        imu.az,
        imu.temperature,
        encoder.raw,
        encoder.degrees,
        encoder.valid ? 1U : 0U,
        encoder.frame,
        static_cast<unsigned long>(imu_parser.framesOk()),
        static_cast<unsigned long>(imu_parser.framesBad()),
        static_cast<unsigned long>(imu_parser.resyncs()),
        static_cast<unsigned long>(dropped_frames));
}

void handleImuFrame(const kt_ex9::Sample& imu) {
    const uint64_t timestamp_us = static_cast<uint64_t>(esp_timer_get_time());
    if (first_frame_us == 0) {
        first_frame_us = timestamp_us;
    }
    last_frame_us = timestamp_us;

    updateCounterStats(imu.counter);
    const EncoderReading encoder = readEncoder();

    if ((imu_parser.framesOk() % board_config::kPrintEveryFrames) == 0U) {
        printCombinedSample(timestamp_us, imu, encoder);
    }
}

void printWaitingIfNeeded(uint64_t now_us) {
    const bool imu_missing = last_frame_us == 0 ||
                             now_us - last_frame_us >=
                                 board_config::kWaitingIntervalUs;
    if (!imu_missing ||
        now_us - last_waiting_print_us < board_config::kWaitingIntervalUs) {
        return;
    }

    last_waiting_print_us = now_us;
    Serial.printf("WAITING_IMU ok=%lu bad=%lu resync=%lu drops=%lu\n",
                  static_cast<unsigned long>(imu_parser.framesOk()),
                  static_cast<unsigned long>(imu_parser.framesBad()),
                  static_cast<unsigned long>(imu_parser.resyncs()),
                  static_cast<unsigned long>(dropped_frames));
}

}  // namespace

void setup() {
    Serial.begin(board_config::kUsbBaud);
    const uint32_t wait_start_ms = millis();
    while (!Serial && millis() - wait_start_ms < 2000U) {
        delay(10);
    }

    pinMode(board_config::kEncoderCsPin, OUTPUT);
    digitalWrite(board_config::kEncoderCsPin, HIGH);
    SPI.begin(board_config::kEncoderSckPin,
              board_config::kEncoderMisoPin,
              board_config::kEncoderMosiPin,
              -1);

    imu_serial.setRxBufferSize(board_config::kImuRxBufferSize);
    imu_serial.begin(board_config::kImuBaud,
                     SERIAL_8N1,
                     board_config::kImuRxPin,
                     board_config::kImuTxPin);

    Serial.println();
    Serial.println("ESP32-S3 KT-EX9 + AS5047P test firmware");
    Serial.println("IMU: 921600 8N1, RX=GPIO8 TX=GPIO9, expected 400Hz");
    Serial.println("ENC: CS=10 MOSI=15 MISO=16 SCK=17, Mode1, 1MHz");
    Serial.println("POWER: IMU and encoder require regulated 3.3V; all grounds common");
}

void loop() {
    while (imu_serial.available() > 0) {
        const int value = imu_serial.read();
        if (value < 0) {
            break;
        }

        kt_ex9::Sample sample;
        const kt_ex9::ParseResult result =
            imu_parser.feed(static_cast<uint8_t>(value), sample);
        if (result == kt_ex9::ParseResult::Frame) {
            handleImuFrame(sample);
        }
    }

    printWaitingIfNeeded(static_cast<uint64_t>(esp_timer_get_time()));
}

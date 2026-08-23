from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path
from typing import BinaryIO, Iterable, TextIO

try:
    from .imu_encoder_protocol import (
        PACKET_SIZE,
        CombinedSample,
        PacketParser,
        TimerUnwrapper,
        crc16_ccitt_false,
        decode_packet,
        delta_u32,
    )
except ImportError:
    from imu_encoder_protocol import (
        PACKET_SIZE,
        CombinedSample,
        PacketParser,
        TimerUnwrapper,
        crc16_ccitt_false,
        decode_packet,
        delta_u32,
    )


CombinedPacket = CombinedSample


CSV_HEADER = (
    "pc_unix_ns",
    "mcu_imu_us",
    "mcu_encoder_us",
    "sensor_gap_us",
    "sequence",
    "imu_counter",
    "flags",
    "gx",
    "gy",
    "gz",
    "ax",
    "ay",
    "az",
    "temperature",
    "encoder_response",
    "encoder_raw",
    "encoder_degrees",
)


def write_csv_row(
    writer: csv.writer,
    packet: CombinedPacket,
    pc_unix_ns: int,
    imu_extended_us: int,
    encoder_extended_us: int,
) -> None:
    writer.writerow(
        (
            pc_unix_ns,
            imu_extended_us,
            encoder_extended_us,
            packet.sensor_gap_us,
            packet.sequence,
            packet.imu_counter,
            f"0x{packet.flags:04X}",
            *packet.imu_values,
            f"0x{packet.encoder_response:04X}",
            packet.encoder_raw,
            packet.encoder_degrees,
        )
    )


def capture_stream(
    serial_port: object,
    csv_file: TextIO,
    raw_file: BinaryIO,
    report_interval_s: float = 1.0,
) -> None:
    parser = PacketParser()
    timer = TimerUnwrapper()
    writer = csv.writer(csv_file)
    writer.writerow(CSV_HEADER)
    total = 0
    interval_frames = 0
    interval_start = time.monotonic()
    gap_min: int | None = None
    gap_max: int | None = None

    while True:
        chunk = serial_port.read(4096)
        for packet in parser.feed(chunk):
            imu_extended = timer.extend(packet.imu_first_byte_rx_us)
            encoder_extended = timer.extend(packet.encoder_read_us)
            raw_file.write(packet.raw_packet)
            write_csv_row(
                writer,
                packet,
                packet.pc_unix_ns,
                imu_extended,
                encoder_extended,
            )
            total += 1
            interval_frames += 1
            gap_min = packet.sensor_gap_us if gap_min is None else min(gap_min, packet.sensor_gap_us)
            gap_max = packet.sensor_gap_us if gap_max is None else max(gap_max, packet.sensor_gap_us)

        now = time.monotonic()
        elapsed = now - interval_start
        if elapsed >= report_interval_s:
            rate = interval_frames / elapsed
            print(
                f"frames={total} rate={rate:.2f}Hz crc_errors={parser.crc_errors} "
                f"discarded={parser.discarded_bytes} gap_us=[{gap_min},{gap_max}]",
                flush=True,
            )
            csv_file.flush()
            raw_file.flush()
            interval_frames = 0
            interval_start = now
            gap_min = None
            gap_max = None


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture STM32 IMU + encoder packets")
    parser.add_argument("--port", required=True, help="serial port, for example COM5")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--csv", type=Path, default=Path("capture.csv"))
    parser.add_argument("--raw", type=Path, default=Path("capture.bin"))
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required: pip install pyserial") from exc

    with serial.Serial(args.port, args.baud, timeout=0.1) as port:
        with args.csv.open("w", newline="", encoding="utf-8") as csv_file:
            with args.raw.open("wb") as raw_file:
                try:
                    capture_stream(port, csv_file, raw_file)
                except KeyboardInterrupt:
                    print("capture stopped")


if __name__ == "__main__":
    main()

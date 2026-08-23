from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

try:
    from .imu_encoder_client import ImuEncoderClient, ImuEncoderClientError
except ImportError:
    from imu_encoder_client import ImuEncoderClient, ImuEncoderClientError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read paired KT-EX9 IMU and AS5047P samples from an "
            "IMU and AS5047P acquisition device"
        )
    )
    parser.add_argument(
        "--port",
        required=True,
        help="serial or native USB port, for example COM6",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=921600,
        help="serial baud rate (default: 921600)",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    last_print = 0.0
    try:
        with ImuEncoderClient(args.port, baudrate=args.baud) as client:
            print(f"connected: {args.port} @ {args.baud}, press Ctrl+C to stop")
            while True:
                sample = client.read(timeout=1.0)
                if sample is None:
                    continue
                now = time.monotonic()
                if now - last_print < 0.1:
                    continue
                last_print = now
                angle = (
                    f"{sample.encoder_angle_deg:9.4f} deg"
                    if sample.encoder_valid
                    else "  invalid encoder"
                )
                print(
                    f"seq={sample.sequence:10d} imu_count={sample.imu_counter:10d} "
                    f"angle={angle} gap={sample.sensor_gap_us:5d}us "
                    f"gyro=({sample.imu.gx:.5f},{sample.imu.gy:.5f},{sample.imu.gz:.5f}) "
                    f"accel=({sample.imu.ax:.5f},{sample.imu.ay:.5f},{sample.imu.az:.5f}) "
                    f"flags=0x{int(sample.flags):04X}",
                    flush=True,
                )
    except KeyboardInterrupt:
        return 0
    except ImuEncoderClientError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())

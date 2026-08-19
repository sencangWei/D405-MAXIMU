#!/usr/bin/env python3
"""CLI for comparing two Kalibr runs against the 2026-08-08 golden runs."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from product_calibration.compare_camera_imu import compare_runs


ROOT = Path(__file__).resolve().parent
DEFAULT_BASELINE = ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml"


def main() -> int:
    parser = argparse.ArgumentParser(description="相机-IMU两次独立Kalibr结果与历史金样A/B")
    parser.add_argument("--run1", type=Path, required=True)
    parser.add_argument("--run2", type=Path, required=True)
    parser.add_argument("--golden-baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = compare_runs(args.run1, args.run2, args.golden_baseline)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"{report['result']}: {args.output}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

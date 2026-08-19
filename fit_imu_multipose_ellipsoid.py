#!/usr/bin/env python3
"""Fit product accelerometer calibration from arbitrary stationary poses."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from product_calibration.imu_ellipsoid import fit_and_validate, load_pose_means


def main() -> int:
    parser = argparse.ArgumentParser(description="任意多姿态加速度计椭球标定")
    parser.add_argument("--input", type=Path, required=True, help="pose CSV")
    parser.add_argument("--output", type=Path, required=True, help="report YAML")
    parser.add_argument("--gravity", type=float, default=9.80665)
    args = parser.parse_args()
    try:
        fit, validation = load_pose_means(args.input)
        report = fit_and_validate(fit, validation, gravity=args.gravity)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"{report['result']}: {args.output}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

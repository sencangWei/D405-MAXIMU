#!/usr/bin/env python3
"""Aggregate independent robot/UMI clock-offset estimates.

This produces an evaluator input, not a VINS camera--IMU calibration.  Runs
with weak or flat motion correlation are excluded explicitly and every input
and decision is retained in the output provenance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def aggregate_reports(
    paths: list[Path],
    *,
    min_correlation: float = 0.90,
    max_peak_width_ms: float = 80.0,
    max_abs_offset_ms: float = 500.0,
) -> dict:
    if not paths:
        raise ValueError("至少需要一个偏移报告")
    accepted: list[dict] = []
    rejected: list[dict] = []
    for raw_path in paths:
        path = raw_path.resolve()
        value = json.loads(path.read_text(encoding="utf-8"))
        reasons: list[str] = []
        if value.get("schema") != "robot_umi_clock_offset_report_v1":
            reasons.append("schema")
        try:
            offset = float(value["robot_query_offset_ms"])
            correlation = float(value["correlation"])
            width = float(value["peak_width_at_delta_corr_0p005_ms"])
        except (KeyError, TypeError, ValueError):
            reasons.append("missing_numeric_fields")
            offset = correlation = width = float("nan")
        if not np.isfinite(offset) or abs(offset) > max_abs_offset_ms:
            reasons.append("offset_out_of_range")
        if not np.isfinite(correlation) or correlation < min_correlation:
            reasons.append("weak_correlation")
        if not np.isfinite(width) or width > max_peak_width_ms:
            reasons.append("ambiguous_peak")
        entry = {
            "path": str(path),
            "offset_ms": offset,
            "correlation": correlation,
            "peak_width_ms": width,
        }
        if reasons:
            entry["reasons"] = reasons
            rejected.append(entry)
        else:
            accepted.append(entry)
    if len(accepted) < 3:
        raise ValueError(
            f"有效偏移报告不足: {len(accepted)}；至少需要3组独立运动数据"
        )
    offsets = np.asarray([item["offset_ms"] for item in accepted], dtype=float)
    median = float(np.median(offsets))
    mad = float(np.median(np.abs(offsets - median)))
    return {
        "schema": "robot_umi_clock_offset_calibration_v1",
        "robot_query_offset_ms": median,
        "estimator": "median_of_independent_speed_correlation_reports",
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted_offsets_ms": offsets.tolist(),
        "median_absolute_deviation_ms": mad,
        "robust_spread_p95_ms": float(np.percentile(np.abs(offsets - median), 95)),
        "quality_policy": {
            "min_correlation": min_correlation,
            "max_peak_width_ms": max_peak_width_ms,
            "max_abs_offset_ms": max_abs_offset_ms,
        },
        "accepted_reports": accepted,
        "rejected_reports": rejected,
        "contract": "query robot pose at umi_t + robot_query_offset_ms; this is not VINS td",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="聚合多组UMI/机械臂设备时间偏移报告")
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-correlation", type=float, default=0.90)
    parser.add_argument("--max-peak-width-ms", type=float, default=80.0)
    args = parser.parse_args()
    result = aggregate_reports(
        args.report,
        min_correlation=args.min_correlation,
        max_peak_width_ms=args.max_peak_width_ms,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

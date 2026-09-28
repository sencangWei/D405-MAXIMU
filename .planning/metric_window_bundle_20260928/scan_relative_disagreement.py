#!/usr/bin/env python3
"""Find same-schedule relative-odometry residual changes without external GT."""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "reports/metric_window_bundle_20260928/native_solver_trace_v2"
OUTPUT = ROOT / "reports/metric_window_bundle_20260928/native_relative_residual_scan_v1.json"
CASES = ("dev1", "dev2", "fresh1", "fresh2", "fresh3", "fresh4",
         "heldout1", "heldout2", "heldout3", "heldout4")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scan(rows, start_min=100, start_max=1120, step=5):
    if not rows:
        return []
    midpoint = np.asarray([0.5 * (row["first"] + row["second"]) for row in rows])
    residual = np.linalg.norm(np.asarray([row["residual_mm"] for row in rows], float), axis=1)
    if not np.isfinite(residual).all():
        raise ValueError("non-finite relative-motion residual")
    windows = []
    for first in range(start_min, start_max + 1, step):
        baseline = residual[(midpoint >= first - 53) & (midpoint < first)]
        current = residual[(midpoint >= first) & (midpoint <= first + 25)]
        if len(baseline) < 5 or len(current) < 4:
            continue
        base_median, current_median = np.median(baseline), np.median(current)
        windows.append({"start": first, "baseline_edges": len(baseline),
                        "current_edges": len(current),
                        "baseline_median_mm": float(base_median),
                        "current_median_mm": float(current_median),
                        "ratio": float(current_median / max(base_median, 1e-12))})
    return windows


def main():
    if OUTPUT.exists():
        raise FileExistsError("refusing to overwrite fixed scan")
    source_hashes = {str(Path(__file__).resolve()): digest(__file__)}
    result = {}
    for case in CASES:
        path = TRACE / case / "solver_trace.json"
        data = json.loads(path.read_text())
        if data["case"] != case or data["external_ground_truth_used"] is not False:
            raise ValueError("trace provenance mismatch")
        result[case] = scan(data["relative_motion_edges"])
        source_hashes[str(path)] = digest(path)
    if any(digest(path) != value for path, value in source_hashes.items()):
        raise ValueError("trace changed during scan")
    with OUTPUT.open("x") as stream:
        json.dump({"external_ground_truth_used": False,
                   "window_schedule": "start=100..1120 step=5; prior53/current26 raw input frames",
                   "limitations": ["physical motion differs between recordings and windows",
                                   "ratio can rise when baseline residual is small",
                                   "same source factor fitted by the graph, not independent truth"],
                   "source_and_input_sha256": source_hashes, "cases": result},
                  stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()

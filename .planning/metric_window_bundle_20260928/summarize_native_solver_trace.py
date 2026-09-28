#!/usr/bin/env python3
"""Fixed-window, UMI-only factor census from byte-identical native replays."""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "reports/metric_window_bundle_20260928/native_solver_trace_v2"
FROZEN = ROOT / "reports/metric_window_bundle_20260928/seam_graph_full_ten_v1/joint"
OUTPUT = ROOT / "reports/metric_window_bundle_20260928/native_solver_factor_census_v1.json"
CASES = ("dev1", "dev2", "fresh1", "fresh2", "fresh3", "fresh4",
         "heldout1", "heldout2", "heldout3", "heldout4")
WINDOWS = {"before": (1000, 1052), "middle": (1053, 1078),
           "after": (1079, 1120)}
FAMILIES = {"imu_position": ("imu_edges", "position_residual_mm"),
            "imu_velocity": ("imu_edges", "velocity_residual_mps"),
            "relative_motion": ("relative_motion_edges", "residual_mm"),
            "stereo": ("stereo_edges", "residual_mm")}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def factor_stats(rows, vector_key, first, last):
    selected = [row for row in rows if first <= 0.5 * (row["first"] + row["second"]) <= last]
    if not selected:
        return {"count": 0}
    residuals = np.asarray([row[vector_key] for row in selected], dtype=float)
    if residuals.shape != (len(selected), 3) or not np.isfinite(residuals).all():
        raise ValueError("invalid signed factor residuals")
    norms = np.linalg.norm(residuals, axis=1)
    return {"count": len(selected), "median_norm": float(np.median(norms)),
            "p95_norm": float(np.percentile(norms, 95)),
            "mean_vector": np.mean(residuals, axis=0).tolist()}


def main():
    if OUTPUT.exists():
        raise FileExistsError("refusing to overwrite frozen factor census")
    hashes = {str(Path(__file__).resolve()): digest(__file__)}
    results = {}
    for case in CASES:
        folder = TRACE / case
        trace_path = folder / "solver_trace.json"
        trace = json.loads(trace_path.read_text())
        if trace["case"] != case or trace["external_ground_truth_used"] is not False:
            raise ValueError("trace provenance mismatch")
        if trace["source_and_output_sha256"][str(
                ROOT / ".planning/metric_window_bundle_20260928/trace_native_solver_state.py")] != digest(
                    ROOT / ".planning/metric_window_bundle_20260928/trace_native_solver_state.py"):
            raise ValueError("trace adapter source changed")
        original = FROZEN / case / "trajectory_graph.csv"
        replay = folder / "trajectory_graph.csv"
        if digest(original) != digest(replay) or not trace["frozen_trajectory_byte_identical"]:
            raise ValueError("native trace altered frozen trajectory")
        hashes[str(trace_path)] = digest(trace_path)
        hashes[str(original)] = digest(original)
        results[case] = {name: {
            window: factor_stats(trace[rows], key, first, last)
            for window, (first, last) in WINDOWS.items()}
            for name, (rows, key) in FAMILIES.items()}
        results[case]["accepted_factor_counts"] = {
            family: len(trace[rows]) for family, (rows, _) in FAMILIES.items()}
    if any(digest(path) != value for path, value in hashes.items()):
        raise ValueError("diagnostic inputs changed during analysis")
    with OUTPUT.open("x") as stream:
        json.dump({"external_ground_truth_used": False,
                   "producer_trajectories_byte_identical": True,
                   "factor_residuals": "signed native final-solution residuals; weights recorded after last IRLS update",
                   "units": {"imu_velocity": "m/s", "other_families": "mm"},
                   "same_index_different_motions_across_cases": True,
                   "source_and_input_sha256": hashes, "cases": results},
                  stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()

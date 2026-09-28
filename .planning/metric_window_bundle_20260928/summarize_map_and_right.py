#!/usr/bin/env python3
"""Pure fixed-scope census summary; no reference poses or estimator selection."""
import hashlib
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
SCOPES = ((1000, 1120), (1000, 1052), (1053, 1078), (1079, 1120))


def distribution(values):
    values = np.asarray(values, float)
    if not len(values):
        return dict(status="UNKNOWN", count=0)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite summary values")
    return dict(status="OK", count=len(values), min=float(values.min()),
                median=float(np.median(values)), max=float(values.max()))


def summarize(rows):
    result = dict(frame_count=len(rows))
    for metric in ("fb_closure_norm_px_stats", "forward_vs_expected_norm_px_stats"):
        values = [r["right_temporal"][metric]["median"] for r in rows
                  if r["right_temporal"].get(metric, {}).get("status") == "OK"]
        result[metric] = distribution(values)
        result[metric]["unknown_frames"] = len(rows)-len(values)
    for label, key in (("forward_support_fraction", "usable_expected_comparison_count"),
                       ("backward_support_fraction", "usable_forward_backward_count")):
        values = [r["right_temporal"].get(key, 0)/r["right_temporal"]["native_bounds_count"]
                  for r in rows if r["right_temporal"]["native_bounds_count"] > 0]
        result[label] = distribution(values)
    updates = [r["map_update"] for r in rows if r["map_update"]["status"] == "OK"]
    for metric in ("centered_log_depth_shape_before", "centered_log_depth_shape_after"):
        result[metric] = distribution([u[metric]["median"] for u in updates])
    result["fixed_pre_scale_point_update_frame_p95_mm"] = distribution(
        [u["fixed_pre_scale_update_norm_mm"]["p95"] for u in updates])
    continuity = [r["continuity"] for r in rows if r["continuity"]["status"] == "OK"]
    for metric in ("absolute_z_delta_learned_units", "xyz_delta_learned_units"):
        result[metric] = distribution([u[metric]["max"] for u in continuity])
    return result


def validate(census):
    if (census.get("diagnostic_only") is not True or census.get("external_ground_truth_used") is not False
            or census.get("estimator_changed") is not False or census.get("gpu_replay_used") is not False
            or census.get("fixed_input_scope") != [1000, 1120]
            or set(census["cases"]) != {"fresh4", "fresh1", "heldout1"}):
        raise ValueError("unexpected diagnostic scope or estimator semantics")
    for case in census["cases"].values():
        if [r["frame_id"] for r in case["rows"]] != list(range(1000, 1121)):
            raise ValueError("incomplete or duplicate frame scope")
        for row in case["rows"]:
            path = Path(row["raw_diagnostic_path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != row["raw_diagnostic_sha256"]:
                raise ValueError("raw right-LK diagnostic changed")


def main():
    base = ROOT/"reports/metric_window_bundle_20260928"
    output = base/"map_and_right_chain_v1/comparison.json"
    if output.exists():
        raise FileExistsError("refusing to overwrite comparison")
    paths = [base/name/"census.json" for name in ("map_and_right_probe_v1", "map_and_right_chain_v1")]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [*paths, Path(__file__)]}
    result = dict(diagnostic_only=True, external_ground_truth_used=False, estimator_changed=False,
                  source_and_census_sha256=hashes, variants={},
                  limitations=["framewise median summaries, not pooled independent landmarks",
                               "same index ranges are not matched physical actions",
                               "LK/stereo inconsistencies are not absolute trajectory errors",
                               "adjacent LK survival is a diagnostic subset, not deleted trajectory frames",
                               "point-map updates remove uniform scale only for shape statistics"])
    for adjacent, path in zip((False, True), paths):
        census = json.loads(path.read_text())
        validate(census)
        if census.get("adjacent_temporal_chain", False) is not adjacent:
            raise ValueError("direct/adjacent census mislabeled")
        result["variants"][path.parent.name] = {
            case: {f"{lo}..{hi}": summarize([r for r in value["rows"] if lo <= r["frame_id"] <= hi])
                   for lo, hi in SCOPES} for case, value in census["cases"].items()}
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()):
        raise ValueError("source or census changed during analysis")
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(output)


if __name__ == "__main__":
    main()

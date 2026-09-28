#!/usr/bin/env python3
"""Compare raw native update captures on fixed failed/passing controls, no GT."""
import hashlib
import json
from pathlib import Path

import numpy as np

from keyframe_update_diagnostics import summarize_update
from summarize_frontend_geometry_probe import CASES as ORIGINAL_CASES, validate_trace


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/"reports/metric_window_bundle_20260928/keyframe_update_probe_v1"
SCOPES = ((1000, 1120), (1000, 1052), (1053, 1078), (1079, 1120))
ORIGINAL_FIELDS = ("Xf", "Xk", "K", "pixel_current", "pixel_keyframe", "depth_current_m",
                   "depth_keyframe_m", "T_pre", "T_post", "T_final", "Xk_after",
                   "keyframe_pixel_ids", "current_pixel_ids", "full_optimize_valid_count")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_new_trace(trace):
    if (trace.get("diagnostic_only") is not True or trace.get("external_ground_truth_used") is not False
            or trace.get("estimator_changed") is not False or trace.get("errors") != []
            or trace.get("fixed_input_scope") != [1000, 1120]
            or [r["frame_id"] for r in trace["rows"]] != list(range(1000, 1121))
            or not all(r.get("captured") is True for r in trace["rows"])):
        raise ValueError("incomplete or altered native update probe")
    identities = trace.get("trajectory_identity", {})
    if set(identities) != {"trajectory_frames.csv", "trajectory_online_frames.csv"}:
        raise ValueError("both output trajectory identities required")
    if not all(i["rows"] == 1199 and i["byte_identical"] is True and i["array_identical"] is True
               and i["max_component_delta"] == 0 for i in identities.values()):
        raise ValueError("update capture changed full/online trajectory")
    adapter = Path(__file__).with_name("probe_keyframe_update.py").resolve()
    if trace.get("source_input_sha256", {}).get(str(adapter)) != digest(adapter):
        raise ValueError("native keyframe-update capture adapter hash missing or stale")
    paths = [Path(r["sample_path"]).resolve() for r in trace["rows"]]
    if len(set(paths)) != 121 or any(p.stem != f'{r["frame_id"]:04d}' for p, r in zip(paths, trace["rows"])):
        raise ValueError("raw paths not uniquely bound to frames")


def distribution(values):
    values = np.asarray(values, float)
    if not len(values):
        return dict(status="UNKNOWN", count=0)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite frame summary")
    return dict(status="OK", count=len(values), min=float(values.min()),
                median=float(np.median(values)), max=float(values.max()))


def summary(rows):
    d = [r["update_diagnostics"] for r in rows]
    result = dict(frames=len(rows), native_sample_counts=distribution([r["count"] for r in d]),
                  weighted_formula_inconsistent_frames=sum(not r["weighted_formula_consistent"] for r in d),
                  derived_pose_does_not_bind_proposal_frames=sum(not r["derived_pose_binds_actual_proposal"] for r in d),
                  update_N_before=distribution([r["update_N_before"] for r in d]))
    for metric in ("confidence_incoming_fraction", "proposal_minus_old_learned_units", "after_minus_old_learned_units",
                   "working_minus_match_xyz_learned_units", "weighted_xyz_formula_error_learned_units",
                   "derived_pose_proposal_error_learned_units"):
        result[metric] = distribution([r[metric]["median"] for r in d])
    for label in ("old", "proposal", "after"):
        result[f"{label}_raw_ray_frame_median_px"] = distribution(
            [r["raw_keyframe_ray_pixel_disagreement"][label]["median"] for r in d
             if r["raw_keyframe_ray_pixel_disagreement"][label]["status"] == "OK"])
    metric_rows = [r["stereo_consistency"] for r in d if r["stereo_consistency"]["status"] == "OK"]
    result["stereo_unknown_frames"] = len(d)-len(metric_rows)
    for label in ("old", "proposal", "after"):
        result[f"{label}_centered_depth_shape_frame_median"] = distribution(
            [r[f"{label}_centered_log_depth_shape"]["median"] for r in metric_rows])
    for label in ("proposal_innovation", "actual_update"):
        result[f"same_old_units_{label}_frame_median_mm"] = distribution(
            [r[f"same_old_units_{label}_norm_mm"]["median"] for r in metric_rows])
    return result


def main():
    output = BASE/"comparison.json"
    if output.exists():
        raise FileExistsError("refusing to overwrite raw-update comparison")
    hashes = {str(p): digest(p) for p in (Path(__file__).resolve(), Path(__file__).with_name("keyframe_update_diagnostics.py"),
                                        Path(__file__).with_name("summarize_frontend_geometry_probe.py"),
                                        Path(__file__).with_name("probe_keyframe_update.py"))}
    cases = {}
    for case, original_path in ORIGINAL_CASES.items():
        path = BASE/(case+"_mast3r")/"geometry_trace.json"
        hashes[str(path)], hashes[str(original_path)] = digest(path), digest(original_path)
        trace, original = json.loads(path.read_text()), json.loads(original_path.read_text())
        validate_new_trace(trace)
        validate_trace(case, original)
        original_capture = original
        if case == "fresh4":
            original_capture_path = Path(original["original_capture_trace"])
            hashes[str(original_capture_path)] = digest(original_capture_path)
            if hashes[str(original_capture_path)] != original["original_capture_sha256"]:
                raise ValueError("recovered probe no longer binds original capture")
            original_capture = json.loads(original_capture_path.read_text())
        if (trace["producer_identity"] != original_capture["producer_identity"]
                or trace["dataset"] != original_capture["dataset"]):
            raise ValueError("producer or prepared dataset differs from previous capture")
        for row, old in zip(trace["rows"], original["rows"]):
            if row["keyframe_id"] != old["keyframe_id"]:
                raise ValueError("keyframe assignment changed")
            raw, previous = Path(row["sample_path"]), Path(old["sample_path"])
            hashes[str(raw)], hashes[str(previous)] = digest(raw), digest(previous)
            if hashes[str(raw)] != row["sample_sha256"] or hashes[str(previous)] != old["sample_sha256"]:
                raise ValueError("raw capture hash differs")
            with np.load(raw, allow_pickle=False) as new, np.load(previous, allow_pickle=False) as before:
                for key in ORIGINAL_FIELDS:
                    if not np.array_equal(new[key], before[key], equal_nan=True):
                        raise ValueError(f"original geometry changed: {case}/{row['frame_id']}/{key}")
                row["update_diagnostics"] = summarize_update(new)
        cases[case] = dict(rows=trace["rows"], original_geometry_array_identical=True,
                          scopes={f"{lo}..{hi}": summary([r for r in trace["rows"] if lo <= r["frame_id"] <= hi])
                                  for lo, hi in SCOPES})
    if any(digest(p) != value for p, value in hashes.items()):
        raise ValueError("source or captured input changed during summary")
    with output.open("x") as stream:
        json.dump(dict(diagnostic_only=True, estimator_changed=False, external_ground_truth_read=False,
                       source_and_input_sha256=hashes, cases=cases,
                       limitations=["same index ranges are not same physical motions", "sampled dense rays are correlated",
                                    "metric innovations are not trajectory ATE", "derived boundary pose is checked, not assumed tracker-local",
                                    "previous diagnostic geometry must be array-identical"]), stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(output)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compare fixed diagnostic captures, without pose estimation or GT inputs."""
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/"reports/metric_window_bundle_20260928/frontend_geometry_probe_v1"
CASES = {
    "fresh4": BASE/"fresh4_mast3r/geometry_trace_recovered_v1.json",
    "fresh1": BASE/"fresh1_mast3r/geometry_trace.json",
    "heldout1": BASE/"heldout1_mast3r/geometry_trace.json",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def distribution(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return dict(status="UNKNOWN", count=0)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite diagnostic distribution")
    return dict(status="OK", count=len(values), min=float(values.min()),
                median=float(np.median(values)), max=float(values.max()))


def summarize(rows):
    result = dict(frame_count=len(rows), keyframe_count=len(set(r["keyframe_id"] for r in rows)),
                  keyframe_age_frames=distribution([r["frame_id"]-r["keyframe_id"] for r in rows]),
                  full_optimize_valid_count=distribution([r["full_optimize_valid_count"] for r in rows]),
                  common_stereo_count=distribution([r["diagnostics"]["common_metric_geometry"]["count"] for r in rows]))
    for key in ("common_keyframe_depth_m", "gyro_relative_rotation_deg", "post_to_final_rotation_deg"):
        values = [r["saved_sample_checks"][key] for r in rows]
        result[key] = distribution([v for v in values if v is not None])
        result[key]["unknown_frames"] = sum(v is None for v in values)
    metric = [r["diagnostics"]["common_metric_geometry"] for r in rows
              if r["diagnostics"]["common_metric_geometry"]["status"] == "OK"]
    for label in ("pre", "post", "final"):
        visual = [r["diagnostics"]["visual_residuals"][label] for r in rows]
        result[label] = dict(
            framewise_pixel_median_px=distribution([v["pixel_residual_norm_px"]["median"] for v in visual if v["status"] == "OK"]),
            relative_rotation_vs_integrated_gyro_deg=distribution([r["diagnostics"]["relative_rotation_vs_imu"][label]["visual_vs_imu_deg"] for r in rows]),
            framewise_common_stereo_residual_median_mm=distribution([1000*m["by_transform"][label]["fixed_rotation_translation_residual_norm_m"]["median"] for m in metric]),
            learned_minus_stereo_translation_mm=distribution([1000*m["by_transform"][label]["learned_minus_stereo_median_norm_m"] for m in metric]),
            sim3_metric_scale_consistency=distribution([m["by_transform"][label]["sim3_metric_scale_consistency"] for m in metric]))
    return result


def validate_trace(name, trace):
    if (trace.get("diagnostic_only") is not True or trace.get("external_ground_truth_used") is not False
            or trace.get("estimator_changed") is not False or trace.get("errors") != []
            or trace.get("fixed_input_scope") != [1000, 1120]
            or [r["frame_id"] for r in trace["rows"]] != list(range(1000, 1121))):
        raise ValueError("incomplete or altered-estimator diagnostic")
    if name == "fresh4":
        if (trace.get("gpu_or_pose_replay_rerun") is not False
                or trace.get("recovered_pure_analysis") is not True
                or trace.get("original_capture_status") != "FAILED_DIAGNOSTIC_MATH"):
            raise ValueError("fresh4 must retain failed-capture recovery semantics")
    elif trace.get("recovered_pure_analysis", False) is not False:
        raise ValueError("control must be a normal successful capture")
    identities = trace.get("trajectory_identity", {})
    if set(identities) != {"trajectory_frames.csv", "trajectory_online_frames.csv"}:
        raise ValueError("full/online trajectory proof missing")
    if not all(v["rows"] == 1199 and v["byte_identical"] is True and v["array_identical"] is True
               and v["max_component_delta"] == 0 for v in identities.values()):
        raise ValueError("replay trajectory identity failure")
    paths = [Path(r["sample_path"]).resolve() for r in trace["rows"]]
    if len(set(paths)) != 121 or any(p.stem != f'{r["frame_id"]:04d}' for p, r in zip(paths, trace["rows"])):
        raise ValueError("raw sample paths not unique or frame-bound")


def main():
    output = BASE/"geometry_comparison_v1.json"
    if output.exists():
        raise FileExistsError("refusing to overwrite frozen comparison")
    source_hash = digest(__file__)
    hashes, cases = {}, {}
    for name, path in CASES.items():
        hashes[str(path)] = digest(path)
        trace = json.loads(path.read_text())
        validate_trace(name, trace)
        for row in trace["rows"]:
            if digest(row["sample_path"]) != row["sample_sha256"]:
                raise ValueError("raw captured sample changed")
            with np.load(row["sample_path"], allow_pickle=False) as sample:
                zf, zk = sample["depth_current_m"], sample["depth_keyframe_m"]
                common = np.isfinite(zf) & np.isfinite(zk) & (zf > 0) & (zk > 0)
                gyro = Rotation.from_quat(sample["quat_keyframe_xyzw"]).inv()*Rotation.from_quat(sample["quat_current_xyzw"])
                post_final = Rotation.from_quat(sample["T_post"][3:7]).inv()*Rotation.from_quat(sample["T_final"][3:7])
                row["saved_sample_checks"] = dict(common_keyframe_depth_m=float(np.median(zk[common])) if common.any() else None,
                                                   gyro_relative_rotation_deg=float(np.rad2deg(gyro.magnitude())),
                                                   post_to_final_rotation_deg=float(np.rad2deg(post_final.magnitude())))
            if digest(row["sample_path"]) != row["sample_sha256"]:
                raise ValueError("raw sample changed during analysis")
        cases[name] = {f"{lo}..{hi}": summarize([r for r in trace["rows"] if lo <= r["frame_id"] <= hi])
                       for lo, hi in ((1000, 1120), (1000, 1052), (1053, 1078), (1079, 1120))}
    if digest(__file__) != source_hash or any(digest(path) != value for path, value in hashes.items()):
        raise ValueError("diagnostic source/input changed")
    result = dict(diagnostic_only=True, trajectory_modified=False, external_ground_truth_read=False,
                  source_sha256=source_hash, input_sha256=hashes, cases=cases,
                  limitations=["same input-index intervals are not matched physical motions across recordings",
                               "min/median/max summarize per-frame statistics, not pooled independent landmarks",
                               "stereo consistency includes depth and correspondence errors; not absolute accuracy",
                               "fresh4 is saved-NPZ recovery, not a second successful GPU replay"])
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(dict(output=str(output), cases=list(cases))))


if __name__ == "__main__":
    main()

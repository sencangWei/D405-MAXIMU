#!/usr/bin/env python3
"""Evaluation-only comparison of frozen metric stages at identical timestamps."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_slam_ground_truth as official  # noqa: E402

REPORTS = ROOT / "reports"
JOINT = REPORTS / "metric_window_bundle_20260928/seam_graph_full_ten_v1/joint"
OUTPUT = REPORTS / "metric_window_bundle_20260928/keyframe_update_probe_v1/metric_boundary_comparison.json"
CALIBRATION = Path(
    "/home/robot/umi_docker2_product_1.0.0-20260829/"
    "docker2_release/formal_runtime_calibration/vins_config.yaml"
)
BASES = {
    "fresh4": REPORTS / "joint_scale_independent_four_20260927/take4/fusion/baseline/mast3r",
    "fresh1": REPORTS / "joint_scale_independent_four_20260927/take1/fusion/baseline/mast3r",
    "heldout1": REPORTS / "steamvr_fusion_heldout_20260927/run_frozen_four/take1/fusion/baseline/mast3r",
}
WINDOWS = {"before": (1000, 1052), "previously_over_10mm": (1053, 1078), "after": (1079, 1120)}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def window_stats(times, errors_mm, raw_times, first, last):
    """Use absolute exposure times; product rows may start later than raw rows."""
    times = np.asarray(times, float)
    errors_mm = np.asarray(errors_mm, float)
    raw_times = np.asarray(raw_times, float)
    if (times.shape != errors_mm.shape or times.ndim != 1 or raw_times.ndim != 1
            or not 0 <= first <= last < len(raw_times) or not np.isfinite(errors_mm).all()):
        raise ValueError("invalid timestamp-aligned diagnostic inputs")
    chosen = (times >= raw_times[first] - 1e-6) & (times <= raw_times[last] + 1e-6)
    values = errors_mm[chosen]
    if not len(values):
        raise ValueError("no matched poses in diagnostic window")
    return {"count": int(len(values)), "median_mm": float(np.median(values)),
            "max_mm": float(np.max(values))}


def local_displacement(times, positions, quaternions, first_time, last_time):
    ids = [int(np.argmin(np.abs(times - target))) for target in (first_time, last_time)]
    if ids[0] >= ids[1] or max(abs(times[ids[0]] - first_time), abs(times[ids[1]] - last_time)) > 0.005:
        raise ValueError("local displacement endpoints not timestamp-matched")
    world_delta = positions[ids[1]] - positions[ids[0]]
    return Rotation.from_quat(quaternions[ids[0]]).inv().apply(world_delta)


def score_stage(path, camera_frame, raw_times, gt, body_t_camera):
    times, positions, quaternions = official.load_trajectory(path)
    if camera_frame:
        positions, quaternions = official.camera_trajectory_to_body(
            positions, quaternions, body_t_camera
        )
    gt_times, gt_positions, gt_quaternions = gt
    inside, valid, reference, reference_quaternions = official.interpolate_ground_truth(
        times, gt_times, gt_positions, gt_quaternions, 0.05
    )
    indices = np.flatnonzero(inside)[valid]
    if len(indices) < 30:
        raise ValueError("insufficient official timestamp overlap")
    times, positions, quaternions = times[indices], positions[indices], quaternions[indices]
    target = reference[:, 1:]
    rotation, translation = official.rigid_align(positions, target)
    errors_mm = 1000 * np.linalg.norm(positions @ rotation.T + translation - target, axis=1)
    windows = {name: window_stats(times, errors_mm, raw_times, *limits)
               for name, limits in WINDOWS.items()}
    # Same timestamps and body-local coordinate convention for all stages.
    delta = local_displacement(times, positions, quaternions,
                               raw_times[1053], raw_times[1078])
    gt_delta = local_displacement(reference[:, 0], target, reference_quaternions,
                                  raw_times[1053], raw_times[1078])
    return {"path": str(path), "matched_samples": int(len(times)), "windows": windows,
            "local_displacement_body_mm": (1000 * delta).tolist(),
            "local_displacement_error_mm": float(1000 * np.linalg.norm(delta - gt_delta))}


def main():
    if OUTPUT.exists():
        raise FileExistsError("refusing to overwrite evaluation-only comparison")
    body_t_camera = official.load_opencv_matrix(CALIBRATION, "body_T_cam0")
    hashes = {str(path): digest(path) for path in
              (Path(__file__).resolve(), Path(official.__file__).resolve(), CALIBRATION)}
    result = {}
    for name, base in BASES.items():
        joint = JOINT / name
        raw_path = base / "trajectory_frames.csv"
        gt_path = joint / "official_score/steamvr_body_reference.csv"
        raw_times, _, _ = official.load_trajectory(raw_path)
        gt = official.load_trajectory(gt_path)
        paths = {
            "stereo_dense": (base / "trajectory_stereo_dense10hz.csv", True),
            "imu_metric": (base / "trajectory_imu_metric.csv", True),
            "joint_graph": (joint / "trajectory_graph.csv", True),
            "fused": (joint / "trajectory_fused.csv", False),
        }
        hashes.update({str(path): digest(path) for path in
                       (raw_path, gt_path, *(entry[0] for entry in paths.values()))})
        stages = {stage: score_stage(path, camera_frame, raw_times, gt, body_t_camera)
                  for stage, (path, camera_frame) in paths.items()}
        if any(stage["windows"]["previously_over_10mm"]["count"] != 26
               for stage in stages.values()):
            raise ValueError(f"bad block timestamp coverage changed: {name}")
        result[name] = stages
    if any(digest(path) != value for path, value in hashes.items()):
        raise ValueError("evaluation input changed during calculation")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("x") as stream:
        json.dump({"evaluation_only": True, "external_ground_truth_used_in_estimation": False,
                   "alignment": "official_global_SE3_no_scale_per_stage",
                   "time_binding": "raw_MASt3R_exposure_timestamp_not_product_row_index",
                   "source_and_input_sha256": hashes, "cases": result,
                   "limitations": ["same frame indices across recordings are different motions",
                                   "each stage has its own global SE3 fit",
                                   "GT is used only for post-freeze localization, not factor selection"]},
                  stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()

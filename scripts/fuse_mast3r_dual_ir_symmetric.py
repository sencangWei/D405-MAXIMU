#!/usr/bin/env python3
"""Experimental symmetric left/right learned-motion graph; onboard inputs only.

The VINS body timeline/world defines the gauge and short-motion regularization.
Neither IR trajectory is an absolute position anchor. External references are
not accepted; score the frozen output separately.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fuse_mast3r_stereo_imu as fusion
from ego_vio.vio.symmetric_ir_factors import build_symmetric_ir_factors


VINS_CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml")
IMU_CONFIG = ROOT / "config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml"
CORRECTION_CAP_MODES = {"global", "per-frame", "per-node"}
EYE_POLICIES = {"both", "left", "right"}
STEREO_WEIGHT_POLICIES = {"observation", "residual-aware"}
OPTIONAL_STEREO_POLICIES = {"strict", "reject_window"}


def parse_optional_correction_mm(value: str) -> float | None:
    if value.lower() == "none":
        return None
    try:
        millimeters = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--max-correction-mm must be 'none' or a finite positive number"
        ) from exc
    if not np.isfinite(millimeters) or millimeters <= 0.0:
        raise argparse.ArgumentTypeError(
            "--max-correction-mm must be 'none' or a finite positive number"
        )
    return millimeters / 1000.0


def symmetric_policy(args) -> dict:
    max_correction_m = getattr(args, "max_correction_m", 1.0)
    cap_mode = getattr(args, "correction_cap_mode", "global")
    eyes = getattr(args, "eyes", "both")
    stereo_weight_policy = getattr(args, "stereo_weight_policy", "observation")
    optional_stereo_policy = getattr(args, "optional_stereo_policy", "strict")
    disable_learned_motion = bool(getattr(args, "disable_learned_motion", False))
    consistency_limit_m = getattr(args, "learned_motion_consistency_limit_m", None)
    if max_correction_m is not None and (
        not np.isfinite(max_correction_m) or max_correction_m <= 0.0
    ):
        raise ValueError("--max-correction-mm must be none or finite positive")
    if cap_mode not in CORRECTION_CAP_MODES:
        raise ValueError(f"unsupported correction cap mode: {cap_mode}")
    if eyes not in EYE_POLICIES:
        raise ValueError(f"unsupported eye policy: {eyes}")
    if stereo_weight_policy not in STEREO_WEIGHT_POLICIES:
        raise ValueError(f"unsupported stereo weight policy: {stereo_weight_policy}")
    if optional_stereo_policy not in OPTIONAL_STEREO_POLICIES:
        raise ValueError(f"unsupported optional stereo policy: {optional_stereo_policy}")
    if consistency_limit_m is not None and (not np.isfinite(consistency_limit_m) or consistency_limit_m <= 0):
        raise ValueError("learned motion consistency limit must be none or finite positive")
    policy = {
        "max_correction_m": max_correction_m,
        "max_correction_mm": (
            None if max_correction_m is None else 1000.0 * max_correction_m
        ),
        "correction_cap_mode": cap_mode,
        "eyes": eyes,
        "stereo_weight_policy": stereo_weight_policy,
        "disable_learned_motion": disable_learned_motion,
        "learned_motion_consistency_limit_m": consistency_limit_m,
    }
    if optional_stereo_policy != "strict":
        policy["optional_stereo_policy"] = optional_stereo_policy
    return policy


def validate_factory_calibration(left, right):
    # Older left reports omit the right-eye rotation; the right report records
    # it explicitly from the same DB3. All other calibration fields must match.
    left_shared = {key: value for key, value in left.items() if key != "right_rotation_from_left"}
    right_shared = {key: value for key, value in right.items() if key != "right_rotation_from_left"}
    if left_shared != right_shared:
        raise ValueError("the two eyes have different factory calibration")
    right_rotation = np.asarray(right.get("right_rotation_from_left"), dtype=float)
    if (right_rotation.shape != (3, 3) or not np.all(np.isfinite(right_rotation))
            or not np.allclose(right_rotation.T @ right_rotation, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(right_rotation), 1.0, atol=1e-6)):
        raise ValueError("right factory rotation is invalid")
    if "right_rotation_from_left" in left and left["right_rotation_from_left"] != right["right_rotation_from_left"]:
        raise ValueError("the two eyes have different factory rotation")


def bind_body_reference(frame_csv, times, positions, rotations, rows):
    frames = list(csv.DictReader(frame_csv.open(newline="", encoding="utf-8")))
    camera_times = np.asarray([float(row["infrared_left_device_ms"]) / 1000 for row in frames])
    camera_mono = np.asarray([float(row["infrared_left_mono"]) for row in frames])
    if (not np.all(np.isfinite(camera_times)) or not np.all(np.isfinite(camera_mono))
            or np.any(np.diff(camera_times) <= 0) or np.any(np.diff(camera_mono) <= 0)):
        raise ValueError("camera timestamp table must be finite and strictly increasing")
    insertions = np.clip(np.searchsorted(camera_times, times), 0, len(camera_times) - 1)
    previous = np.maximum(insertions - 1, 0)
    closest = np.where(abs(camera_times[previous] - times) < abs(camera_times[insertions] - times), previous, insertions)
    errors = abs(camera_times[closest] - times)
    keep = errors <= 0.010
    if np.count_nonzero(keep) < 4 or np.any(np.diff(closest[keep]) <= 0):
        raise ValueError("VINS poses do not uniquely bind recorded camera frames")
    bound_rows = [dict(row, t_sec=f"{camera_times[index]:.9f}")
                  for row, index, valid in zip(rows, closest, keep) if valid]
    quality = {
        "raw_samples": len(times), "bound_samples": int(np.count_nonzero(keep)),
        "unbound_samples": int(np.count_nonzero(~keep)),
        "unbound_timestamps": times[~keep].tolist(),
        "maximum_binding_error_s": float(np.max(errors[keep])),
        "policy": "nearest_recorded_camera_frame_within_10ms_no_extrapolation",
    }
    return (camera_times[closest[keep]], positions[keep], rotations[keep],
            bound_rows, camera_mono[closest[keep]], quality)


def load_eye(
    directory, eye, session, config, imu_times, gyro,
    optional_stereo_policy="strict",
):
    if eye == "left":
        trajectory = directory / "trajectory_imu_metric.csv"
        names = ["stereo_scale_bidirectional_report.json"] + [
            f"stereo_scale_{kind}_report.json"
            for kind in ("long_hops", "dense10hz", "multisecond")
        ]
    else:
        trajectory = directory / "imu_metric_trajectory.csv"
        names = ["stereo_scale_right_report.json"] + [
            f"stereo_scale_{kind}_right_report.json"
            for kind in ("long_hops", "dense10hz", "multisecond")
        ]
    paths = [directory / name for name in names]
    reports = []
    for index, path in enumerate(paths):
        report = fusion.load_json_report(path)
        if optional_stereo_policy == "strict" or index == 0:
            fusion.validate_onboard_report(
                report, path, "umi_mast3r_stereo_scale_v2"
            )
        if Path(report.get("session", "")).resolve() != session.resolve():
            raise ValueError("eye report uses a different recording")
        if report.get("observation_frame") != f"infrared_{eye}_camera_i":
            raise ValueError("eye report uses the wrong camera frame")
        report["report_path"] = str(path.resolve())
        reports.append(report)
    stereo = fusion.merge_stereo_reports(
        reports[0], reports[1:], optional_policy=optional_stereo_policy
    )
    imu_path = directory / "imu_scale_report.json"
    imu_report = fusion.load_json_report(imu_path)
    fusion.validate_onboard_report(imu_report, imu_path, "umi_mast3r_imu_scale_v1")
    fusion.validate_imu_scale_report_binding(imu_report, trajectory, False)
    if Path(stereo.get("trajectory", "")).resolve() != Path(imu_report.get("trajectory", "")).resolve():
        raise ValueError("eye stereo and IMU reports have different frontend sources")
    times, positions, rotations, _ = fusion.load_trajectory(trajectory)
    scales = [float(imu_report["scale"]), float(stereo["scale_m_per_mast3r_unit"])]
    if not all(np.isfinite(scale) and scale > 0 for scale in scales):
        raise ValueError("eye scales must be finite and positive")
    quality = fusion.scale_consistency(*scales)
    ratio = quality["joint_scale_m_per_mast3r_unit"] / scales[0]
    positions = positions[0] + ratio * (positions - positions[0])
    extrinsic = fusion.body_t_trajectory_camera_from_stereo_report(config["body_T_camera"], stereo)
    mono = fusion.camera_epoch_to_monotonic(session / "d405_frames.csv", f"infrared_{eye}", times)
    rotations, orientation_report = fusion.refine_orientations(
        rotations, fusion.regular_node_indices(len(times), 10), mono,
        imu_times, gyro, Rotation.from_matrix(extrinsic[:3, :3]),
        config["td_s"], stereo.get("observations", []),
    )
    observations = stereo.get("observations", [])
    track = {
        "eye": eye, "times": times, "metric_camera_positions": positions,
        "camera_rotations": rotations.as_matrix(), "body_t_camera": extrinsic,
        "observations": observations,
        "observation_confidences": [fusion.stereo_observation_confidence(
            observation, scales[1]
        ) for observation in observations],
    }
    metadata = {
        "metric_scale_consistency": quality,
        "orientation_refinement": orientation_report,
        "effective_body_T_camera": extrinsic.tolist(),
        "factory_stereo_calibration": stereo["factory_stereo_calibration"],
    }
    if optional_stereo_policy != "strict":
        metadata["optional_stereo_policy"] = optional_stereo_policy
        metadata["optional_report_rejections"] = stereo.get(
            "optional_report_rejections", []
        )
    return track, metadata, [trajectory, imu_path, *paths]


def run(args):
    policy = symmetric_policy(args)
    output = args.output_dir.resolve()
    if output.exists() or args.output_dir.is_symlink():
        raise ValueError("output directory must be new; existing products are preserved")
    output.relative_to(ROOT)
    config = fusion.load_vins_config(VINS_CONFIG, -0.009109323)
    imu_times, gyro, accel, imu_info = fusion.load_calibrated_imu(
        args.session / "external_imu/imu.bin", IMU_CONFIG
    )
    vins_path = args.vins_dir / "vio_corrected_stream.csv"
    times, positions, rotations, rows = fusion.load_trajectory(vins_path)
    vins_report_path = args.vins_dir / "run_acceptance.json"
    source_quality = fusion.validate_relative_motion_report(
        fusion.load_json_report(vins_report_path), vins_report_path, vins_path,
        args.session, len(rows),
    )
    times, positions, rotations, rows, mono, binding = bind_body_reference(
        args.session / "d405_frames.csv", times, positions, rotations, rows
    )
    all_tracks, eye_reports, inputs = {}, {}, []
    for eye, directory in (("left", args.left_dir), ("right", args.right_dir)):
        track, report, paths = load_eye(
            directory, eye, args.session, config, imu_times, gyro,
            policy.get("optional_stereo_policy", "strict"),
        )
        all_tracks[eye] = track
        eye_reports[eye] = report
        inputs.extend(paths)
    validate_factory_calibration(
        eye_reports["left"]["factory_stereo_calibration"],
        eye_reports["right"]["factory_stereo_calibration"],
    )
    selected_eyes = ("left", "right") if policy["eyes"] == "both" else (policy["eyes"],)
    tracks = [all_tracks[eye] for eye in selected_eyes]
    motion_factors, stereo_observations, complementarity = build_symmetric_ir_factors(
        times, rotations.as_matrix(), tracks,
        stereo_weight_policy=policy["stereo_weight_policy"],
        learned_motion_consistency_limit_m=policy["learned_motion_consistency_limit_m"],
    )
    graph_motion_factors = None if policy["disable_learned_motion"] else motion_factors
    inputs.extend([
        vins_path, vins_report_path, VINS_CONFIG, IMU_CONFIG,
        args.session / "d405_frames.csv", args.session / "external_imu/imu.bin",
        Path(__file__), ROOT / "ego_vio/vio/symmetric_ir_factors.py",
        ROOT / "ego_vio/vio/dual_ir_factors.py", ROOT / "scripts/fuse_mast3r_stereo_imu.py",
    ])
    output.mkdir(parents=True)
    manifest = {
        "schema": "umi_dual_ir_symmetric_experiment_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED", "accepted": False,
        "external_ground_truth_used": False, "slam_supervision": False,
        "session": str(args.session.resolve()),
        "input_sha256": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
        "eye_reports": eye_reports, "vins_source_validation": source_quality,
        "reference_time_binding": binding,
        "shared_gauge": "VINS_body_world_first_node_only",
        "primary_eye": None, "shared_scale_state": False,
        "body_t_camera_in_solver": np.eye(4).tolist(),
        "policy_arguments": {
            "max_correction_mm": policy["max_correction_mm"],
            "correction_cap_mode": policy["correction_cap_mode"],
            "eyes": policy["eyes"],
            "stereo_weight_policy": policy["stereo_weight_policy"],
            "disable_learned_motion": policy["disable_learned_motion"],
            "learned_motion_consistency_limit_m": policy["learned_motion_consistency_limit_m"],
        },
        "imu": imu_info,
    }
    if policy.get("optional_stereo_policy", "strict") != "strict":
        manifest["policy_arguments"]["optional_stereo_policy"] = policy[
            "optional_stereo_policy"
        ]
    (output / "candidate_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "local_motion_factors.json").write_text(json.dumps(motion_factors, indent=2) + "\n")
    (output / "shared_stereo_observations.json").write_text(json.dumps(stereo_observations, indent=2) + "\n")
    positions, graph_report = fusion.refine_positions_visual_inertial(
        positions, rotations, stereo_observations, mono, imu_times, gyro, accel,
        np.eye(4), config["td_s"], node_stride=1,
        max_correction_m=policy["max_correction_m"],
        relative_motion_positions_body=positions,
        relative_motion_valid=np.ones(len(times), dtype=bool),
        secondary_visual_factors=graph_motion_factors,
        use_visual_position_prior=False, solve_metric_scale=False,
        correction_cap_mode=policy["correction_cap_mode"],
        stereo_factor_confidences=np.asarray([
            observation["pnp_inlier_ratio"] for observation in stereo_observations
        ]),
    )
    report = {
        "schema": "umi_dual_ir_symmetric_graph_diagnostic_v1",
        "status": "EXPERIMENTAL_NOT_ACCEPTED", "accepted": False,
        "external_ground_truth_used": False, "slam_supervision": False,
        "output_frame": "body_imu_origin", "output_samples": len(rows),
        "td_s": config["td_s"], "complementarity": complementarity,
        "policy_arguments": manifest["policy_arguments"],
        "joint_position_solver": graph_report,
    }
    fusion.write_trajectory(output / "body_trajectory_fused.csv", rows, positions, rotations)
    (output / "graph_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), "samples": len(rows), "status": report["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("session", "left-dir", "right-dir", "vins-dir", "output-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument(
        "--max-correction-mm",
        dest="max_correction_m",
        type=parse_optional_correction_mm,
        default=1.0,
        help="post-solve correction cap in mm, or 'none' to disable it; default 1000",
    )
    parser.add_argument(
        "--correction-cap-mode",
        choices=sorted(CORRECTION_CAP_MODES),
        default="global",
    )
    parser.add_argument("--eyes", choices=sorted(EYE_POLICIES), default="both")
    parser.add_argument(
        "--stereo-weight-policy",
        choices=sorted(STEREO_WEIGHT_POLICIES),
        default="observation",
    )
    parser.add_argument(
        "--optional-stereo-policy",
        choices=sorted(OPTIONAL_STEREO_POLICIES),
        default="strict",
        help=(
            "strict keeps every stereo report mandatory; reject_window keeps "
            "identity/GT errors fatal but records optional failed or scale-"
            "inconsistent windows as rejected diagnostics"
        ),
    )
    parser.add_argument("--disable-learned-motion", action="store_true")
    parser.add_argument("--learned-motion-consistency-mm", dest="learned_motion_consistency_limit_m",
                        type=parse_optional_correction_mm, default=None,
                        help="experimental learned-edge consistency bound in mm, or none (default)")
    run(parser.parse_args())

#!/usr/bin/env python3
"""Read-only census of independent stereo PnP rotations in baseline adapters v2.

The census uses only onboard source artifacts already referenced by each
candidate manifest.  It does not read scores, ground truth, point
correspondences, run frontend/GPU work, or solve a graph.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import fuse_mast3r_stereo_imu as fusion  # noqa: E402


CORPUS = ROOT / ".planning" / "dual_ir_regression_25_20261002"
SOURCE_ROOT = CORPUS / "batch_adapters_v2"
OUT = Path(__file__).resolve().parent / "independent_stereo_rotation_census_v1.json"
TIMESTAMP_TOLERANCE_S = 0.010

REPORT_ORDER = {
    "left": [
        "stereo_scale_bidirectional_report.json",
        "stereo_scale_long_hops_report.json",
        "stereo_scale_dense10hz_report.json",
        "stereo_scale_multisecond_report.json",
    ],
    "right": [
        "stereo_scale_right_report.json",
        "stereo_scale_long_hops_right_report.json",
        "stereo_scale_dense10hz_right_report.json",
        "stereo_scale_multisecond_right_report.json",
    ],
}

POINT_CORRESPONDENCE_KEYS = {
    "object_points",
    "image_points",
    "matches",
    "correspondences",
    "point_correspondences",
    "inlier_object_points",
    "inlier_image_points",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50_deg": None, "p90_deg": None, "p95_deg": None, "max_deg": None}
    array = np.asarray(values, dtype=float)
    return {
        "n": int(array.size),
        "p50_deg": float(np.percentile(array, 50)),
        "p90_deg": float(np.percentile(array, 90)),
        "p95_deg": float(np.percentile(array, 95)),
        "max_deg": float(np.max(array)),
    }


def load_body_rotations(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    quats = np.asarray(
        [[float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])] for row in rows],
        dtype=float,
    )
    return times, Rotation.from_quat(quats).as_matrix()


def nearest_index(times: np.ndarray, value: float) -> int | None:
    insertion = int(np.searchsorted(times, value))
    candidates = []
    if insertion < times.size:
        candidates.append(insertion)
    if insertion > 0:
        candidates.append(insertion - 1)
    if not candidates:
        return None
    best = min(candidates, key=lambda index: abs(float(times[index]) - value))
    if abs(float(times[best]) - value) > TIMESTAMP_TOLERANCE_S:
        return None
    return best


def rotation_angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.degrees((Rotation.from_matrix(a).inv() * Rotation.from_matrix(b)).magnitude()))


def finite_rotation_from_quat(value: Any) -> np.ndarray | None:
    try:
        quat = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None
    if quat.shape != (4,) or not np.all(np.isfinite(quat)):
        return None
    norm = float(np.linalg.norm(quat))
    if not np.isfinite(norm) or norm == 0.0:
        return None
    return Rotation.from_quat(quat / norm).as_matrix()


def report_paths(input_paths: list[Path], eye: str) -> list[Path]:
    by_name = {path.name: path for path in input_paths if path.name in REPORT_ORDER[eye]}
    missing = [name for name in REPORT_ORDER[eye] if name not in by_name]
    if missing:
        raise ValueError(f"{eye} missing stereo reports: {missing}")
    return [by_name[name] for name in REPORT_ORDER[eye]]


def load_merged_stereo(input_paths: list[Path], eye: str, policy: str) -> dict[str, Any]:
    reports = []
    for path in report_paths(input_paths, eye):
        report = fusion.load_json_report(path)
        report["report_path"] = str(path.resolve())
        reports.append(report)
    return fusion.merge_stereo_reports(reports[0], reports[1:], optional_policy=policy)


def classify_rotation(observation: dict[str, Any]) -> str:
    mode = observation.get("pnp_rotation_mode")
    constrained = observation.get("pnp_rotation_constrained")
    rotation = finite_rotation_from_quat(observation.get("pnp_rotation_quaternion_xyzw"))
    if mode == "free" and constrained is False and rotation is not None:
        return "free_independent"
    if constrained is True or mode == "trajectory-fixed":
        return "trajectory_or_imu_constrained"
    if rotation is None:
        return "missing_or_invalid_quaternion"
    return "other_nonfree"


def has_saved_point_correspondences(observation: dict[str, Any]) -> bool:
    return any(key in observation for key in POINT_CORRESPONDENCE_KEYS)


def audit_record(directory: Path) -> dict[str, Any]:
    record_id = directory.parent.name
    manifest_path = directory / "candidate_manifest.json"
    body_path = directory / "body_trajectory_fused.csv"
    manifest = json.loads(manifest_path.read_text())
    input_paths = [Path(path) for path in manifest["input_sha256"]]
    vins_paths = [path for path in input_paths if path.name == "vio_corrected_stream.csv"]
    if len(vins_paths) != 1:
        raise ValueError(f"{record_id} expected one VINS reference CSV, got {len(vins_paths)}")
    reference_times, body_rotations = load_body_rotations(body_path)
    policy = manifest.get("policy_arguments", {})
    optional_policy = policy.get("optional_stereo_policy", "strict")
    per_eye: dict[str, Any] = {}
    vins_errors: list[float] = []
    lr_by_pair: dict[tuple[int, int], dict[str, list[np.ndarray]]] = defaultdict(lambda: {"left": [], "right": []})
    point_correspondence_count = 0
    total_accepted = 0
    total_mapped = 0
    total_free = 0
    total_constrained = 0
    class_counts = Counter()
    mode_counts = Counter()
    for eye in ("left", "right"):
        merged = load_merged_stereo(input_paths, eye, optional_policy)
        body_t_camera = np.asarray(
            manifest["eye_reports"][eye]["effective_body_T_camera"], dtype=float
        )
        rbc = body_t_camera[:3, :3]
        eye_vins_errors: list[float] = []
        eye_lr_candidates = 0
        accepted = 0
        mapped = 0
        unbound = 0
        nonordered = 0
        eye_class_counts = Counter()
        eye_mode_counts = Counter()
        for observation in merged.get("observations", []):
            if observation.get("accepted") is not True:
                continue
            accepted += 1
            total_accepted += 1
            point_correspondence_count += int(has_saved_point_correspondences(observation))
            mode = str(observation.get("pnp_rotation_mode"))
            eye_mode_counts[mode] += 1
            mode_counts[mode] += 1
            first = nearest_index(reference_times, float(observation["first_t_sec"]))
            second = nearest_index(reference_times, float(observation["second_t_sec"]))
            if first is None or second is None:
                unbound += 1
                continue
            if second <= first:
                nonordered += 1
                continue
            mapped += 1
            total_mapped += 1
            classification = classify_rotation(observation)
            eye_class_counts[classification] += 1
            class_counts[classification] += 1
            if classification == "trajectory_or_imu_constrained":
                total_constrained += 1
            if classification != "free_independent":
                continue
            total_free += 1
            pnp_camera = finite_rotation_from_quat(observation["pnp_rotation_quaternion_xyzw"])
            expected_camera = rbc.T @ body_rotations[second].T @ body_rotations[first] @ rbc
            error_deg = rotation_angle_deg(expected_camera, pnp_camera)
            eye_vins_errors.append(error_deg)
            vins_errors.append(error_deg)
            body_relative = rbc @ pnp_camera @ rbc.T
            lr_by_pair[(first, second)][eye].append(body_relative)
            eye_lr_candidates += 1
        per_eye[eye] = {
            "accepted_observations": accepted,
            "mapped_observations": mapped,
            "unbound_timestamp_observations": unbound,
            "nonordered_mapped_observations": nonordered,
            "rotation_class_counts": dict(sorted(eye_class_counts.items())),
            "pnp_rotation_mode_counts": dict(sorted(eye_mode_counts.items())),
            "free_vins_expected_error_deg": stats(eye_vins_errors),
            "free_lr_candidate_rotations": eye_lr_candidates,
            "merged_report_count": int(merged.get("merged_report_count", 0)),
            "optional_report_rejections": list(merged.get("optional_report_rejections", [])),
        }
    lr_errors = []
    lr_pair_count = 0
    lr_combo_count = 0
    for pair, rotations_by_eye in lr_by_pair.items():
        left = rotations_by_eye["left"]
        right = rotations_by_eye["right"]
        if not left or not right:
            continue
        lr_pair_count += 1
        for left_rotation, right_rotation in product(left, right):
            lr_errors.append(rotation_angle_deg(left_rotation, right_rotation))
            lr_combo_count += 1
    return {
        "id": record_id,
        "candidate_dir": str(directory),
        "policy_arguments": policy,
        "inputs": {
            "candidate_manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
            "body_trajectory_fused": {"path": str(body_path), "sha256": sha256(body_path)},
            "vins_reference_csv": {"path": str(vins_paths[0]), "sha256": sha256(vins_paths[0])},
        },
        "external_ground_truth_used": manifest.get("external_ground_truth_used"),
        "slam_supervision": manifest.get("slam_supervision"),
        "total_accepted_observations": total_accepted,
        "total_mapped_observations": total_mapped,
        "free_independent_rotation_observations": total_free,
        "trajectory_or_imu_constrained_observations": total_constrained,
        "rotation_class_counts": dict(sorted(class_counts.items())),
        "pnp_rotation_mode_counts": dict(sorted(mode_counts.items())),
        "saved_point_correspondence_observations": point_correspondence_count,
        "per_eye": per_eye,
        "free_vins_expected_error_deg": stats(vins_errors),
        "free_lr_rotation_agreement_deg": stats(lr_errors),
        "free_lr_pair_count": lr_pair_count,
        "free_lr_rotation_combination_count": lr_combo_count,
        "_free_vins_errors_deg": vins_errors,
        "_free_lr_errors_deg": lr_errors,
    }


def main() -> None:
    directories = sorted(path for path in SOURCE_ROOT.glob("*/both") if (path / "candidate_manifest.json").exists())
    records = [audit_record(directory) for directory in directories]
    all_vins: list[float] = []
    all_lr: list[float] = []
    for record in records:
        all_vins.extend(record.pop("_free_vins_errors_deg"))
        all_lr.extend(record.pop("_free_lr_errors_deg"))
    totals = {
        "record_count": len(records),
        "total_accepted_observations": int(sum(r["total_accepted_observations"] for r in records)),
        "total_mapped_observations": int(sum(r["total_mapped_observations"] for r in records)),
        "free_independent_rotation_observations": int(sum(r["free_independent_rotation_observations"] for r in records)),
        "trajectory_or_imu_constrained_observations": int(sum(r["trajectory_or_imu_constrained_observations"] for r in records)),
        "rotation_class_counts": dict(sorted(sum((Counter(r["rotation_class_counts"]) for r in records), Counter()).items())),
        "pnp_rotation_mode_counts": dict(sorted(sum((Counter(r["pnp_rotation_mode_counts"]) for r in records), Counter()).items())),
        "records_with_unbound_accepted_observations": {
            r["id"]: int(r["total_accepted_observations"] - r["total_mapped_observations"])
            for r in records
            if r["total_accepted_observations"] != r["total_mapped_observations"]
        },
        "records_with_free_rotations": [r["id"] for r in records if r["free_independent_rotation_observations"]],
        "records_with_constrained_rotations": [r["id"] for r in records if r["trajectory_or_imu_constrained_observations"]],
        "records_with_saved_point_correspondences": [
            r["id"] for r in records if r["saved_point_correspondence_observations"]
        ],
        "total_free_lr_pair_count": int(sum(r["free_lr_pair_count"] for r in records)),
        "total_free_lr_rotation_combination_count": int(sum(r["free_lr_rotation_combination_count"] for r in records)),
        "all_free_vins_expected_error_deg": stats(all_vins),
        "all_free_lr_rotation_agreement_deg": stats(all_lr),
    }
    result = {
        "schema": "independent_stereo_rotation_census_v1",
        "source": "batch_adapters_v2 manifests/input_sha256 only",
        "rule": (
            "FREE means accepted stereo observation with pnp_rotation_mode='free', "
            "pnp_rotation_constrained is false, and finite pnp_rotation_quaternion_xyzw. "
            "Trajectory-fixed/constrained rotations are diagnostics only, not independent factors."
        ),
        "timestamp_binding_tolerance_s": TIMESTAMP_TOLERANCE_S,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "records": records,
        "summary": totals,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(totals, indent=2, sort_keys=True))
    print(OUT)


if __name__ == "__main__":
    main()

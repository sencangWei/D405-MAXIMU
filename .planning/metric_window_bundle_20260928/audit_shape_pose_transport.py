#!/usr/bin/env python3
"""Read-only pose-transport audit for frozen nine-center shape diagnostics.

This script does not run any estimator, graph optimizer, selector, threshold
sweep, recording, or ground-truth evaluation. It compares the already-frozen
shape profiles with the already-frozen native camera graph poses and verifies
the pure profile algebra is invariant to one deterministic world rigid transform.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports/metric_window_bundle_20260928"
DEFAULT_SHAPE_SUMMARY = REPORT_ROOT / "shape_full_ten_v1/summary.json"
DEFAULT_GRAPH_ROOT = REPORT_ROOT / "seam_graph_full_ten_v1/joint"
DEFAULT_SEAM_BATCH_STATUS = REPORT_ROOT / "seam_graph_full_ten_v1/batch_status.json"
DEFAULT_OUTPUT = REPORT_ROOT / "shape_pose_transport_audit_v3.json"

EXPECTED_CASES = {
    "dev1",
    "dev2",
    "heldout1",
    "heldout2",
    "heldout3",
    "heldout4",
    "fresh1",
    "fresh2",
    "fresh3",
    "fresh4",
}
EXPECTED_CASE_COUNT = 10
EXPECTED_PAIRS_PER_CASE = 29
EXPECTED_WINDOWS_PER_CASE = 58
EXPECTED_TOTAL_PAIRS = 290
EXPECTED_ACCEPTED_GROUPS = 253
EXPECTED_REFUSED_GROUPS = 37
EXPECTED_ENDPOINT_ROWS = 580
EXPECTED_ACCEPTED_ENDPOINTS = 506
EXPECTED_SOURCE_HASHES = 14
EXPECTED_RAW_COUNTS = {"dev2": 1200}
EXPECTED_DEFAULT_RAW_COUNT = 1199
EXPECTED_TD_S = -0.009109323
EXPECTED_TRAJECTORY_FRAME = "infrared_left_camera_i"


@dataclass(frozen=True)
class Trajectory:
    times: np.ndarray
    positions: np.ndarray
    rotations: Rotation
    sha256: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_helper(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json_no_overwrite(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing audit output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_trajectory(path: Path) -> Trajectory:
    rows = list(csv.DictReader(path.open("r", encoding="utf-8")))
    required = {"t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"}
    if not rows or set(rows[0]) != required:
        raise ValueError(f"trajectory header mismatch: {path}")
    times, positions, xyzw = [], [], []
    for row in rows:
        times.append(float(row["t_sec"]))
        positions.append([float(row["x"]), float(row["y"]), float(row["z"])])
        xyzw.append([float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])])
    times_array = np.asarray(times, dtype=float)
    positions_array = np.asarray(positions, dtype=float)
    xyzw_array = np.asarray(xyzw, dtype=float)
    if (
        times_array.ndim != 1
        or positions_array.shape != (len(times_array), 3)
        or xyzw_array.shape != (len(times_array), 4)
        or not np.all(np.isfinite(times_array))
        or not np.all(np.isfinite(positions_array))
        or not np.all(np.isfinite(xyzw_array))
        or np.any(np.diff(times_array) <= 0.0)
    ):
        raise ValueError(f"trajectory values malformed: {path}")
    return Trajectory(
        times=times_array,
        positions=positions_array,
        rotations=Rotation.from_quat(xyzw_array),
        sha256=sha256_file(path),
    )


def strict_indices(values: Any, expected_len: int) -> list[int]:
    if not isinstance(values, list) or len(values) != expected_len:
        raise ValueError("shape indices length mismatch")
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("shape indices must be integer raw frame ids")
        result.append(value)
    if any(value < 0 for value in result) or any(b <= a for a, b in zip(result, result[1:])):
        raise ValueError("shape indices must be strictly increasing")
    return result


def strict_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a literal bool")
    return value


def finite_array(values: Any, shape: tuple[int, ...], name: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} malformed")
    return array


def local_vector(positions: np.ndarray, rotations: Rotation) -> np.ndarray:
    return rotations[0].inv().apply(positions[1:] - positions[0]).reshape(-1)


def relative_rotations(rotations: Rotation) -> Rotation:
    return rotations[0].inv() * rotations


def profile_residual(factor: dict[str, Any], positions: np.ndarray, rotations: Rotation) -> np.ndarray:
    reference = finite_array(factor.get("reference_center_vector_m"), (24,), "factor reference")
    sensitivity = np.asarray(factor.get("compact_sqrt_sensitivity"), dtype=float)
    if sensitivity.ndim != 2 or sensitivity.shape[1] != 24 or not np.all(np.isfinite(sensitivity)):
        raise ValueError("factor sensitivity malformed")
    affine = finite_array(factor.get("affine_offset"), (sensitivity.shape[0],), "factor affine")
    return affine + sensitivity @ (local_vector(positions, rotations) - reference)


def deterministic_world_transform() -> tuple[Rotation, np.ndarray]:
    rotation = Rotation.from_rotvec(np.array([0.31, -0.17, 0.23], dtype=float))
    translation = np.array([0.42, -0.31, 0.27], dtype=float)
    return rotation, translation


def transform_world(
    positions: np.ndarray, rotations: Rotation, transform: Rotation, translation: np.ndarray
) -> tuple[np.ndarray, Rotation]:
    return transform.apply(positions) + translation, transform * rotations


def summarize(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    array = np.asarray(values, dtype=float)
    return {
        "count": int(array.size),
        "min": float(np.min(array)),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
    }


def validate_factor(factor: Any) -> dict[str, Any]:
    if not isinstance(factor, dict):
        raise ValueError("missing shape factor")
    if factor.get("diagnostic_only") is not True or factor.get("available") is not True:
        raise ValueError("shape factor availability flags mismatch")
    if factor.get("solver_accepted") is not True or factor.get("not_admissible_for_graph") is not True:
        raise ValueError("shape factor solver/graph flags mismatch")
    if factor.get("frames") != 9 or factor.get("gauge") != "all_relative_centers_mapped_through_first_camera_rotation":
        raise ValueError("shape factor frame/gauge mismatch")
    metadata = factor.get("metadata", {})
    if (
        metadata.get("calibrated_covariance") is not False
        or metadata.get("statistical_independence_claimed") is not False
        or metadata.get("available_for_graph") is not False
    ):
        raise ValueError("shape factor metadata must remain diagnostic/nonproduction")
    finite_array(factor.get("reference_center_vector_m"), (24,), "factor reference")
    finite_array(factor.get("optimized_centers_m"), (9, 3), "factor optimized centers")
    finite_array(factor.get("optimized_rotvecs_camera_to_window"), (9, 3), "factor optimized rotations")
    return factor


def endpoint_factor(row: dict[str, Any]) -> dict[str, Any] | None:
    return row.get("diagnostics", {}).get("stereo_window_shape_factor")


def expected_pair_indices(pair_index: int) -> list[int]:
    start = 40 * pair_index
    return list(range(start, start + 41, 5))


def expected_endpoint_indices(pair_index: int, part: int) -> list[int]:
    start = 40 * pair_index + 20 * part
    return list(range(start, start + 21, 5))


def validate_pair_binding(case: dict[str, Any], pair_index: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    pair = case["pairs"][pair_index]
    first = case["windows"][2 * pair_index]
    second = case["windows"][2 * pair_index + 1]
    indices = strict_indices(pair.get("indices"), 9)
    if indices != expected_pair_indices(pair_index):
        raise ValueError("pair indices do not match literal 0..1120 stride-40 schedule")
    if strict_indices(first.get("indices"), 5) != expected_endpoint_indices(pair_index, 0):
        raise ValueError("first endpoint indices do not match pair")
    if strict_indices(second.get("indices"), 5) != expected_endpoint_indices(pair_index, 1):
        raise ValueError("second endpoint indices do not match pair")
    expected_pair_id = pair_index + 1
    if first.get("joint_pair") != expected_pair_id or second.get("joint_pair") != expected_pair_id:
        raise ValueError("endpoint joint_pair mismatch")
    pair_accepted = strict_bool(pair.get("accepted"), "pair accepted")
    first_accepted = strict_bool(first.get("accepted"), "first endpoint accepted")
    second_accepted = strict_bool(second.get("accepted"), "second endpoint accepted")
    if pair_accepted != first_accepted or pair_accepted != second_accepted:
        raise ValueError("pair/endpoint acceptance mismatch")
    return pair, first, second


def audit_pair(
    case_name: str,
    pair_index: int,
    pair: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
    trajectory: Trajectory,
    rigid_transform: tuple[Rotation, np.ndarray],
) -> dict[str, Any]:
    indices = strict_indices(pair.get("indices"), 9)
    if max(indices) >= len(trajectory.times):
        raise ValueError(f"{case_name} pair {pair_index + 1} exceeds trajectory length")
    expected_elapsed = trajectory.times[indices] - trajectory.times[0]
    first_elapsed = finite_array(first.get("elapsed_s"), (5,), "first elapsed")
    second_elapsed = finite_array(second.get("elapsed_s"), (5,), "second elapsed")
    if np.max(np.abs(first_elapsed - expected_elapsed[:5])) > 1e-9:
        raise ValueError(f"{case_name} pair {pair_index + 1} first timestamp binding mismatch")
    if np.max(np.abs(second_elapsed - expected_elapsed[4:])) > 1e-9:
        raise ValueError(f"{case_name} pair {pair_index + 1} second timestamp binding mismatch")

    base = {
        "case": case_name,
        "pair": pair_index + 1,
        "indices": indices,
        "accepted": strict_bool(pair.get("accepted"), "pair accepted"),
        "reason": pair.get("reason"),
    }
    if not pair.get("accepted"):
        if endpoint_factor(first) is not None or endpoint_factor(second) is not None:
            raise ValueError(f"{case_name} refused pair {pair_index + 1} unexpectedly has factor")
        return base

    factor_a = validate_factor(endpoint_factor(first))
    factor_b = validate_factor(endpoint_factor(second))
    if factor_a != factor_b:
        raise ValueError(f"{case_name} pair {pair_index + 1} endpoint factors differ")

    positions = trajectory.positions[indices]
    rotations = trajectory.rotations[indices]
    ba_rotations = Rotation.from_rotvec(finite_array(factor_a["optimized_rotvecs_camera_to_window"], (9, 3), "ba rotations"))
    native_rel = relative_rotations(rotations)
    ba_rel = relative_rotations(ba_rotations)
    angle_deg = np.degrees((native_rel.inv() * ba_rel).magnitude())

    native_local = local_vector(positions, rotations)
    reference = finite_array(factor_a["reference_center_vector_m"], (24,), "factor reference")
    local_delta = native_local - reference
    local_delta_norms_mm = np.linalg.norm(local_delta.reshape(8, 3), axis=1) * 1000.0

    residual = profile_residual(factor_a, positions, rotations)
    transformed_positions, transformed_rotations = transform_world(
        positions, rotations, rigid_transform[0], rigid_transform[1]
    )
    transformed_residual = profile_residual(factor_a, transformed_positions, transformed_rotations)
    invariant_delta = float(np.max(np.abs(transformed_residual - residual))) if residual.size else 0.0

    return {
        **base,
        "native_vs_ba_relative_rotation_deg": {
            "median": float(np.median(angle_deg)),
            "max": float(np.max(angle_deg)),
            "per_node": angle_deg.tolist(),
        },
        "native_local_translation_delta_from_ba_reference_mm": {
            "median_node_norm": float(np.median(local_delta_norms_mm)),
            "max_node_norm": float(np.max(local_delta_norms_mm)),
            "per_nonanchor_node_norm": local_delta_norms_mm.tolist(),
        },
        "profile_residual_norm_at_native_graph": float(np.linalg.norm(residual)),
        "profile_residual_norm_at_ba_reference": float(np.linalg.norm(finite_array(factor_a["affine_offset"], (len(residual),), "affine"))),
        "rigid_world_transform_profile_residual_max_abs_delta": invariant_delta,
        "conditional_rank": int(factor_a.get("conditional_rank")),
        "solver_optimality": float(factor_a.get("solver_optimality")),
    }


def validate_case_shape(case: dict[str, Any]) -> None:
    if not isinstance(case.get("case"), str):
        raise ValueError("case name malformed")
    if len(case.get("pairs", [])) != EXPECTED_PAIRS_PER_CASE:
        raise ValueError(f"{case['case']} pair count mismatch")
    if len(case.get("windows", [])) != EXPECTED_WINDOWS_PER_CASE:
        raise ValueError(f"{case['case']} endpoint count mismatch")
    if len(case.get("independent_windows", [])) != EXPECTED_WINDOWS_PER_CASE:
        raise ValueError(f"{case['case']} independent endpoint count mismatch")
    if not isinstance(case.get("input_sha256"), dict) or not case["input_sha256"]:
        raise ValueError(f"{case['case']} missing input hash map")
    if not isinstance(case.get("decoded_grayscale_frame_sha256"), dict) or not case["decoded_grayscale_frame_sha256"]:
        raise ValueError(f"{case['case']} missing decoded-frame hash map")


def verify_hash_map(hashes: dict[str, str], *, label: str) -> dict[str, str]:
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError(f"{label} hash map empty")
    actuals: dict[str, str] = {}
    for raw, expected in hashes.items():
        if not isinstance(raw, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"{label} hash entry malformed: {raw}")
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"{label} hash mismatch: {raw}")
        actuals[raw] = actual
    return actuals


def command_value(command: list[str], flag: str) -> Path:
    if flag not in command:
        raise ValueError(f"command missing {flag}")
    return Path(command[command.index(flag) + 1])


def command_string(command: list[str], flag: str) -> str:
    if flag not in command:
        raise ValueError(f"command missing {flag}")
    return str(command[command.index(flag) + 1])


def load_csv_times(path: Path) -> np.ndarray:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"trajectory CSV has no header: {path}")
        time_key = "t_sec" if "t_sec" in reader.fieldnames else reader.fieldnames[0]
        return np.asarray([float(row[time_key]) for row in reader], dtype=float)


def verify_same_times(source: Path, output: Path) -> None:
    source_times = load_csv_times(source)
    output_times = load_csv_times(output)
    if source_times.shape != output_times.shape or not np.allclose(source_times, output_times, atol=1e-9, rtol=0.0):
        raise ValueError("graph output trajectory rows/timestamps changed")


def validate_graph_report(report: dict[str, Any], *, case_name: str, graph_command: list[str], trajectory_path: Path) -> None:
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError(f"{case_name} graph report must be UMI-only")
    alignment = report.get("time_alignment", {})
    if alignment.get("estimate_td") != 0 or abs(float(alignment.get("td_s")) - EXPECTED_TD_S) > 1e-12:
        raise ValueError(f"{case_name} graph report formal td mismatch")
    camera = report.get("camera_extrinsics", {})
    if camera.get("trajectory_observation_frame") != EXPECTED_TRAJECTORY_FRAME:
        raise ValueError(f"{case_name} graph report trajectory frame mismatch")
    inputs = report.get("inputs", {})
    if inputs.get("external_ground_truth_used") is not False:
        raise ValueError(f"{case_name} graph inputs must not use GT")
    if Path(inputs.get("session", "")) != command_value(graph_command, "--session"):
        raise ValueError(f"{case_name} graph session binding mismatch")
    if Path(inputs.get("vins_spatiotemporal_calibration", "")) != command_value(graph_command, "--vins-config"):
        raise ValueError(f"{case_name} graph formal calibration binding mismatch")
    if Path(inputs.get("trajectory", "")) != command_value(graph_command, "--trajectory"):
        raise ValueError(f"{case_name} graph source trajectory binding mismatch")
    if command_string(graph_command, "--stream") != "infrared_left":
        raise ValueError(f"{case_name} graph stream mismatch")
    if abs(float(command_string(graph_command, "--expected-td-s")) - EXPECTED_TD_S) > 1e-12:
        raise ValueError(f"{case_name} graph command td mismatch")
    if Path(report.get("output", "")) != trajectory_path:
        raise ValueError(f"{case_name} graph output trajectory binding mismatch")


def validate_fusion_report(report: dict[str, Any], *, case_name: str, graph_command: list[str], trajectory_path: Path) -> None:
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError(f"{case_name} fusion report must be UMI-only")
    if report.get("output_frame") != "body_imu_origin":
        raise ValueError(f"{case_name} fusion report output frame mismatch")
    inputs = report.get("inputs", {})
    if Path(inputs.get("mast3r_camera_trajectory", "")) != trajectory_path:
        raise ValueError(f"{case_name} fusion report camera trajectory binding mismatch")
    if Path(inputs.get("body_camera_calibration", "")) != command_value(graph_command, "--vins-config"):
        raise ValueError(f"{case_name} fusion report formal calibration binding mismatch")


def load_frozen_joint_records(batch_status_path: Path, graph_root: Path) -> dict[str, dict[str, Any]]:
    batch = read_json(batch_status_path)
    if batch.get("gt_scoring_started_after_all_graphs") is not True:
        raise ValueError("seam batch did not freeze all graphs before scoring")
    records = {}
    for record in batch.get("cases", []):
        if record.get("variant") != "joint":
            continue
        case_name = record.get("case")
        if case_name in records:
            raise ValueError(f"duplicate joint batch case: {case_name}")
        if record.get("completed") is not True or record.get("graph_completed") is not True or record.get("failure_stage"):
            raise ValueError(f"{case_name} frozen joint graph did not complete cleanly")
        target = graph_root / str(case_name)
        manifest_path = target / "manifest.json"
        manifest = read_json(manifest_path)
        stable_fields = (
            "case",
            "variant",
            "baseline",
            "controls",
            "commands",
            "input_sha256",
            "source_sha256",
            "external_reference_used_in_optimization",
            "fixed_penalty_m",
            "fixed_penalty_role",
            "calibrated_covariance",
            "statistical_independence_claimed",
            "experiment_status",
        )
        if any(manifest.get(field) != record.get(field) for field in stable_fields):
            raise ValueError(f"{case_name} target manifest stable fields differ from batch status")
        records[case_name] = record
    if set(records) != EXPECTED_CASES:
        raise ValueError("frozen joint batch case set mismatch")
    return records


def validate_frozen_case_graph(
    case_name: str,
    trajectory_path: Path,
    trajectory: Trajectory,
    graph_root: Path,
    frozen_record: dict[str, Any] | None,
) -> dict[str, Any]:
    expected_count = EXPECTED_RAW_COUNTS.get(case_name, EXPECTED_DEFAULT_RAW_COUNT)
    if len(trajectory.times) != expected_count:
        raise ValueError(f"{case_name} camera trajectory raw count {len(trajectory.times)} != {expected_count}")
    if frozen_record is None:
        return {
            "trajectory_rows": int(len(trajectory.times)),
            "raw_count_expected": expected_count,
            "frozen_manifest_bound": False,
        }
    target = graph_root / case_name
    graph_command = next(command for stage, command in frozen_record["commands"] if stage == "graph")
    complementary_command = next(command for stage, command in frozen_record["commands"] if stage == "complementary")
    graph_report_path = command_value(graph_command, "--report")
    fusion_report_path = command_value(complementary_command, "--report")
    if command_value(graph_command, "--output") != trajectory_path:
        raise ValueError(f"{case_name} manifest graph output path mismatch")
    if graph_report_path != target / "graph_fusion_report.json":
        raise ValueError(f"{case_name} graph report path mismatch")
    if fusion_report_path != target / "fusion_report.json":
        raise ValueError(f"{case_name} fusion report path mismatch")
    frozen_input_actuals = verify_hash_map(frozen_record["input_sha256"], label=f"{case_name} frozen graph input")
    frozen_source_actuals = verify_hash_map(frozen_record["source_sha256"], label=f"{case_name} frozen graph source")
    verify_same_times(command_value(graph_command, "--trajectory"), trajectory_path)
    graph_report = read_json(graph_report_path)
    fusion_report = read_json(fusion_report_path)
    validate_graph_report(graph_report, case_name=case_name, graph_command=graph_command, trajectory_path=trajectory_path)
    validate_fusion_report(fusion_report, case_name=case_name, graph_command=graph_command, trajectory_path=trajectory_path)
    return {
        "trajectory_rows": int(len(trajectory.times)),
        "raw_count_expected": expected_count,
        "frozen_manifest_bound": True,
        "manifest_sha256": sha256_file(target / "manifest.json"),
        "graph_fusion_report_sha256": sha256_file(graph_report_path),
        "fusion_report_sha256": sha256_file(fusion_report_path),
        "trajectory_graph_sha256": trajectory.sha256,
        "source_trajectory": str(command_value(graph_command, "--trajectory")),
        "source_trajectory_sha256": sha256_file(command_value(graph_command, "--trajectory")),
        "frozen_input_files_verified": len(frozen_input_actuals),
        "frozen_source_files_verified": len(frozen_source_actuals),
        "verified_file_paths": sorted(
            {
                str(target / "manifest.json"),
                str(graph_report_path),
                str(fusion_report_path),
                str(trajectory_path),
                str(command_value(graph_command, "--trajectory")),
                *frozen_input_actuals.keys(),
                *frozen_source_actuals.keys(),
            }
        ),
        "session": command_string(graph_command, "--session"),
        "formal_calibration": command_string(graph_command, "--vins-config"),
        "stream": command_string(graph_command, "--stream"),
        "expected_td_s": float(command_string(graph_command, "--expected-td-s")),
        "trajectory_observation_frame": graph_report["camera_extrinsics"]["trajectory_observation_frame"],
    }


def audit(
    shape_summary_path: Path = DEFAULT_SHAPE_SUMMARY,
    graph_root: Path = DEFAULT_GRAPH_ROOT,
    seam_batch_status_path: Path | None = DEFAULT_SEAM_BATCH_STATUS,
) -> dict[str, Any]:
    before_shape_sha = sha256_file(shape_summary_path)
    summary = read_json(shape_summary_path)
    source_hashes = summary.get("source_sha256", {})
    cases = summary.get("cases", [])
    if len(source_hashes) != EXPECTED_SOURCE_HASHES:
        raise ValueError("shape source hash closure is not the expected 14 files")
    if len(cases) != EXPECTED_CASE_COUNT or {case.get("case") for case in cases} != EXPECTED_CASES:
        raise ValueError("case set mismatch")
    if summary.get("external_reference_used") is not False or summary.get("production_modified") is not False:
        raise ValueError("shape summary must be UMI-only and nonproduction")
    source_hashes_before = verify_hash_map(source_hashes, label="shape source")

    expected_full_shape_inputs: dict[str, str] | None = None
    full_shape_input_actuals_before: dict[str, str] | None = None
    if shape_summary_path.resolve() == DEFAULT_SHAPE_SUMMARY.resolve():
        full_shape = load_helper(ROOT / ".planning/metric_window_bundle_20260928/run_full_shape_controls.py", "full_shape_controls")
        expected_full_shape_inputs = full_shape.preflight_inputs()
        full_shape.verify_input_hashes(expected_full_shape_inputs)
        full_shape_input_actuals_before = verify_hash_map(expected_full_shape_inputs, label="full-shape preflight input")
    use_frozen_records = (
        seam_batch_status_path is not None
        and seam_batch_status_path.is_file()
        and (
            graph_root.resolve() == DEFAULT_GRAPH_ROOT.resolve()
            or seam_batch_status_path.resolve() != DEFAULT_SEAM_BATCH_STATUS.resolve()
        )
    )
    frozen_records = (
        load_frozen_joint_records(seam_batch_status_path, graph_root)
        if use_frozen_records
        else None
    )

    transform = deterministic_world_transform()
    pair_records = []
    trajectory_hashes_before = {}
    trajectory_hashes_after = {}
    input_map_hashes_before = {}
    input_map_hashes_after = {}
    actual_case_input_hashes_before = {}
    actual_case_input_hashes_after = {}
    graph_binding_before = {}
    graph_binding_after = {}
    verified_file_paths = {str(Path(path).resolve()) for path in source_hashes_before}
    if full_shape_input_actuals_before is not None:
        verified_file_paths.update(str(Path(path).resolve()) for path in full_shape_input_actuals_before)
    accepted = refused = accepted_endpoint_rows = endpoint_rows = 0
    rotation_max_values = []
    rotation_median_values = []
    translation_max_values = []
    profile_native_values = []
    profile_ba_values = []
    invariant_values = []
    case_summaries = []

    for case in sorted(cases, key=lambda row: row["case"]):
        validate_case_shape(case)
        name = case["case"]
        input_map_hashes_before[name] = {
            "input_sha256_map_sha256": sha256_json(case["input_sha256"]),
            "decoded_grayscale_frame_sha256_map_sha256": sha256_json(case["decoded_grayscale_frame_sha256"]),
        }
        actual_case_input_hashes_before[name] = verify_hash_map(case["input_sha256"], label=f"{name} case input")
        verified_file_paths.update(str(Path(path).resolve()) for path in actual_case_input_hashes_before[name])
        trajectory_path = graph_root / name / "trajectory_graph.csv"
        if not trajectory_path.is_file():
            raise FileNotFoundError(trajectory_path)
        trajectory = load_trajectory(trajectory_path)
        trajectory_hashes_before[name] = trajectory.sha256
        graph_binding_before[name] = validate_frozen_case_graph(
            name,
            trajectory_path,
            trajectory,
            graph_root,
            None if frozen_records is None else frozen_records[name],
        )
        verified_file_paths.update(str(Path(path).resolve()) for path in graph_binding_before[name].get("verified_file_paths", []))
        case_accepted = 0
        case_refused = 0
        for pair_index in range(EXPECTED_PAIRS_PER_CASE):
            pair, first, second = validate_pair_binding(case, pair_index)
            endpoint_rows += 2
            if strict_bool(pair.get("accepted"), "pair accepted"):
                accepted += 1
                case_accepted += 1
                accepted_endpoint_rows += 2
            else:
                refused += 1
                case_refused += 1
            record = audit_pair(name, pair_index, pair, first, second, trajectory, transform)
            pair_records.append(record)
            if record["accepted"]:
                rotation_max_values.append(record["native_vs_ba_relative_rotation_deg"]["max"])
                rotation_median_values.append(record["native_vs_ba_relative_rotation_deg"]["median"])
                translation_max_values.append(record["native_local_translation_delta_from_ba_reference_mm"]["max_node_norm"])
                profile_native_values.append(record["profile_residual_norm_at_native_graph"])
                profile_ba_values.append(record["profile_residual_norm_at_ba_reference"])
                invariant_values.append(record["rigid_world_transform_profile_residual_max_abs_delta"])
        case_summaries.append(
            {
                "case": name,
                "pairs": EXPECTED_PAIRS_PER_CASE,
                "accepted_groups": case_accepted,
                "refused_groups": case_refused,
                "trajectory_rows": int(len(trajectory.times)),
                "trajectory_graph_sha256": trajectory.sha256,
            }
        )
        trajectory_hashes_after[name] = sha256_file(trajectory_path)
        input_map_hashes_after[name] = {
            "input_sha256_map_sha256": sha256_json(case["input_sha256"]),
            "decoded_grayscale_frame_sha256_map_sha256": sha256_json(case["decoded_grayscale_frame_sha256"]),
        }
        actual_case_input_hashes_after[name] = verify_hash_map(case["input_sha256"], label=f"{name} case input")
        graph_binding_after[name] = validate_frozen_case_graph(
            name,
            trajectory_path,
            load_trajectory(trajectory_path),
            graph_root,
            None if frozen_records is None else frozen_records[name],
        )

    if accepted != EXPECTED_ACCEPTED_GROUPS or refused != EXPECTED_REFUSED_GROUPS:
        raise ValueError("accepted/refused group count mismatch")
    if endpoint_rows != EXPECTED_ENDPOINT_ROWS or accepted_endpoint_rows != EXPECTED_ACCEPTED_ENDPOINTS:
        raise ValueError("endpoint count mismatch")
    if before_shape_sha != sha256_file(shape_summary_path):
        raise ValueError("shape summary changed during audit")
    if trajectory_hashes_before != trajectory_hashes_after:
        raise ValueError("trajectory graph inputs changed during audit")
    if input_map_hashes_before != input_map_hashes_after:
        raise ValueError("case input maps changed during audit")
    source_hashes_after = {source: sha256_file(Path(source)) for source in source_hashes}
    if source_hashes_before != source_hashes_after:
        raise ValueError("source files changed during audit")
    if actual_case_input_hashes_before != actual_case_input_hashes_after:
        raise ValueError("case input files changed during audit")
    if graph_binding_before != graph_binding_after:
        raise ValueError("graph/report binding files changed during audit")
    full_shape_input_actuals_after = None
    if expected_full_shape_inputs is not None:
        full_shape_input_actuals_after = verify_hash_map(expected_full_shape_inputs, label="full-shape preflight input")
        if full_shape_input_actuals_before != full_shape_input_actuals_after:
            raise ValueError("full-shape preflight inputs changed during audit")

    max_invariance_delta = max(invariant_values) if invariant_values else 0.0
    result = {
        "diagnostic_only": True,
        "umi_only": True,
        "external_reference_used": False,
        "ground_truth_used": False,
        "optimizer_run": False,
        "selector_or_threshold_change": False,
        "production_modified": False,
        "interpretation": (
            "Pose-consistency audit only. This is not ATE, covariance, confidence, "
            "production admission, graph optimization, or a GT-selected correction."
        ),
        "inputs": {
            "shape_summary": str(shape_summary_path),
            "shape_summary_sha256_before": before_shape_sha,
            "shape_summary_sha256_after": sha256_file(shape_summary_path),
            "shape_source_sha256_count": len(source_hashes),
            "shape_source_sha256": source_hashes,
            "shape_source_sha256_before": source_hashes_before,
            "shape_source_sha256_after": source_hashes_after,
            "case_input_map_sha256_before": input_map_hashes_before,
            "case_input_map_sha256_after": input_map_hashes_after,
            "case_input_file_sha256_before": actual_case_input_hashes_before,
            "case_input_file_sha256_after": actual_case_input_hashes_after,
            "full_shape_preflight_input_sha256_before": full_shape_input_actuals_before,
            "full_shape_preflight_input_sha256_after": full_shape_input_actuals_after,
            "graph_root": str(graph_root),
            "trajectory_graph_sha256_before": trajectory_hashes_before,
            "trajectory_graph_sha256_after": trajectory_hashes_after,
            "frozen_graph_report_bindings_before": graph_binding_before,
            "frozen_graph_report_bindings_after": graph_binding_after,
        },
        "counts": {
            "cases": len(cases),
            "pairs_total": accepted + refused,
            "accepted_groups": accepted,
            "refused_groups": refused,
            "endpoint_rows": endpoint_rows,
            "accepted_endpoint_rows": accepted_endpoint_rows,
            "refused_endpoint_rows": endpoint_rows - accepted_endpoint_rows,
            "actual_case_input_files_verified": int(sum(len(v) for v in actual_case_input_hashes_before.values())),
            "full_shape_preflight_input_files_verified": (
                None if full_shape_input_actuals_before is None else int(len(full_shape_input_actuals_before))
            ),
            "frozen_graph_bound_cases": int(sum(1 for binding in graph_binding_before.values() if binding.get("frozen_manifest_bound"))),
            "frozen_graph_binding_files_verified": int(
                len(
                    {
                        str(Path(path).resolve())
                        for binding in graph_binding_before.values()
                        for path in binding.get("verified_file_paths", [])
                    }
                )
            ),
            "unique_actual_files_verified": int(len(verified_file_paths)),
        },
        "summary": {
            "native_vs_ba_relative_rotation_deg_max": summarize(rotation_max_values),
            "native_vs_ba_relative_rotation_deg_median_per_group": summarize(rotation_median_values),
            "native_local_translation_delta_from_ba_reference_mm_max_node": summarize(translation_max_values),
            "profile_residual_norm_at_native_graph": summarize(profile_native_values),
            "profile_residual_norm_at_ba_reference": summarize(profile_ba_values),
            "rigid_world_transform_profile_residual_max_abs_delta": summarize(invariant_values),
            "rigid_invariance_passed_1e_9": bool(max_invariance_delta <= 1e-9),
        },
        "case_summaries": case_summaries,
        "pair_records": pair_records,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shape-summary", type=Path, default=DEFAULT_SHAPE_SUMMARY)
    parser.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    parser.add_argument("--seam-batch-status", type=Path, default=DEFAULT_SEAM_BATCH_STATUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = audit(args.shape_summary, args.graph_root, args.seam_batch_status)
    write_json_no_overwrite(args.output, result)
    print(
        f"wrote {args.output} accepted_groups={result['counts']['accepted_groups']} "
        f"refused_groups={result['counts']['refused_groups']} "
        f"rigid_invariance={result['summary']['rigid_invariance_passed_1e_9']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

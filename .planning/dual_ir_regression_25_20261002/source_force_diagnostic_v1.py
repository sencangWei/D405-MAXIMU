#!/usr/bin/env python3
"""Read-only source-force diagnostic for physical+constant dual-IR artifacts.

This script replays the current full visual-inertial position solver in memory
and captures the fourth LSQR design/target/solution actually used by the solve.
It then decomposes fixed 1 s, 3 mm triangular position perturbations by source
row block.  It does not read ground truth, score trajectories, run frontends, or
write anything except the requested JSON summary.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
ARTIFACT_ROOT = (
    ROOT
    / ".planning/dual_ir_regression_25_20261002/gauge_physical_combined_batch_v1"
)
MANIFEST = ROOT / "config/dual_ir_regression_25_20261002_recovery.json"
DEFAULT_OUTPUT = (
    ROOT / ".planning/dual_ir_regression_25_20261002/source_force_diagnostic_v2.json"
)
MAX_REPLAY_POSITION_DELTA_M = 1e-6
MAX_FINITE_DIFFERENCE_ERROR = 1e-9
RECORD_IDS = (
    "20260930_take06",
    "20260927_heldout2",
    "20260927_heldout4",
    "20260927_ind2",
)
AXES = {
    "x": np.array([0.003, 0.0, 0.0]),
    "y": np.array([0.0, 0.003, 0.0]),
    "z": np.array([0.0, 0.0, 0.003]),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode())
    digest.update(str(contiguous.shape).encode())
    digest.update(contiguous.view(np.uint8))
    return digest.hexdigest()


def sha256_csr(matrix: Any) -> str:
    csr = matrix.tocsr()
    digest = hashlib.sha256()
    digest.update(str(csr.shape).encode())
    for array in (csr.data, csr.indices, csr.indptr):
        contiguous = np.ascontiguousarray(array)
        digest.update(str(contiguous.dtype).encode())
        digest.update(str(contiguous.shape).encode())
        digest.update(contiguous.view(np.uint8))
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def validate_candidate_input_hashes(candidate: dict[str, Any]) -> dict[str, Any]:
    input_hashes = candidate.get("input_sha256")
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("candidate input_sha256 is missing")
    checked = []
    for source, expected in sorted(input_hashes.items()):
        source_path = Path(source)
        if not source_path.is_file():
            raise ValueError(f"candidate input path missing: {source}")
        actual = sha256_file(source_path)
        if actual != expected:
            raise ValueError(f"candidate input hash changed: {source}")
        checked.append(str(source_path.resolve()))
    return {
        "checked_count": len(checked),
        "checked_paths": checked,
        "all_match": True,
    }


def unique_paths(paths: list[Path]) -> list[Path]:
    result: dict[str, Path] = {}
    for path in paths:
        resolved = path.resolve()
        result[str(resolved)] = resolved
    return [result[key] for key in sorted(result)]


def hash_paths(paths: list[Path]) -> dict[str, str]:
    hashes = {}
    for path in unique_paths(paths):
        if not path.is_file():
            raise ValueError(f"consumed path is missing: {path}")
        hashes[str(path)] = sha256_file(path)
    return hashes


def consumed_source_paths(fusion: Any, base: Any) -> list[Path]:
    return [
        SCRIPT,
        MANIFEST,
        Path(fusion.__file__),
        Path(base.__file__),
        Path(base.symmetric.__file__),
        ROOT / "ego_vio/vio/symmetric_ir_factors.py",
        ROOT / "ego_vio/vio/dual_ir_factors.py",
        Path(base.symmetric.VINS_CONFIG),
        Path(base.symmetric.IMU_CONFIG),
    ]


def consumed_record_paths(record_id: str, record: dict[str, Any]) -> list[Path]:
    variant_dir = ARTIFACT_ROOT / record_id / "physical_stereo_constant_gauge"
    candidate_path = variant_dir / "candidate_manifest.json"
    paths = [
        variant_dir / "shared_stereo_observations.json",
        variant_dir / "local_motion_factors.json",
        variant_dir / "body_trajectory_fused.csv",
        variant_dir / "graph_report.json",
        candidate_path,
        Path(record["session"]) / "d405_frames.csv",
        Path(record["session"]) / "external_imu/imu.bin",
        Path(record["vins_dir"]) / "vio_corrected_stream.csv",
        Path(record["vins_dir"]) / "run_acceptance.json",
    ]
    candidate = read_json(candidate_path)
    paths.extend(Path(source) for source in candidate.get("input_sha256", {}))
    return paths


def make_debug_refine(fusion: Any) -> Any:
    """Return an in-memory copy exposing the last LSQR system."""
    source = inspect.getsource(fusion.refine_positions_visual_inertial)
    lsqr_needle = """        solution = lsqr(
            design, target, atol=1e-10, btol=1e-10, iter_lim=5000
        )[0]
"""
    lsqr_patch = """        solution = lsqr(
            design, target, atol=1e-10, btol=1e-10, iter_lim=5000
        )[0]
        _debug_last_design = design.copy()
        _debug_last_target = target.copy()
        _debug_last_solution = solution.copy()
        _debug_last_weights = {
            "stereo_weights": stereo_weights.copy(),
            "secondary_robust_weights": secondary_robust_weights.copy(),
            "relative_motion_weights": relative_motion_weights.copy(),
            "imu_position_weights": imu_position_weights.copy(),
            "imu_velocity_weights": imu_velocity_weights.copy(),
        }
"""
    if lsqr_needle not in source:
        raise RuntimeError("refine_positions_visual_inertial LSQR call changed")
    source = source.replace(lsqr_needle, lsqr_patch)
    return_needle = "    return refined, {\n        **secondary_quality,"
    return_patch = """    _debug_state = {
        "node_indices": node_indices.copy(),
        "design": _debug_last_design,
        "target": _debug_last_target,
        "solution": _debug_last_solution.copy(),
        "weights_used_in_last_solve": _debug_last_weights,
        "unknowns": int(unknowns),
        "accepted_count": len(accepted),
        "secondary_count": len(secondary_factors),
        "relative_count": len(relative_motion_weights),
        "static_pair_count": len(static_pairs),
        "use_visual_position_prior": bool(use_visual_position_prior),
        "scale_active": bool(scale_active),
    }
    return refined, {"_debug_state": _debug_state, **secondary_quality,"""
    if return_needle not in source:
        raise RuntimeError("refine_positions_visual_inertial return block changed")
    namespace = dict(fusion.__dict__)
    exec(source.replace(return_needle, return_patch), namespace)
    return namespace["refine_positions_visual_inertial"]


def triangular_alpha(times: np.ndarray, start_s: int) -> np.ndarray:
    relative = times - (times[0] + float(start_s))
    alpha = np.zeros(len(times), dtype=float)
    mask = (relative >= 0.0) & (relative <= 1.0)
    alpha[mask] = 1.0 - np.abs(relative[mask] - 0.5) / 0.5
    return alpha


def build_row_blocks(debug: dict[str, Any], factors: list[dict[str, Any]]) -> tuple[list[tuple[str, int, int]], int]:
    if debug["use_visual_position_prior"]:
        raise ValueError("row order diagnostic expects visual priors disabled")
    if debug["scale_active"]:
        raise ValueError("row order diagnostic expects metric scale inactive")
    if len(factors) != int(debug["secondary_count"]):
        raise ValueError("secondary factor count does not match captured solver state")
    node_count = int(debug["node_indices"].size)
    relative_count = int(debug["relative_count"])
    secondary_count = int(debug["secondary_count"])
    accepted_count = int(debug["accepted_count"])
    row = 0
    blocks: list[tuple[str, int, int]] = []
    blocks.append(("anchor", row, row + 3))
    row += 3
    blocks.append(("vins_relative", row, row + 3 * relative_count))
    row += 3 * relative_count
    secondary_start = row
    for index, factor in enumerate(factors):
        label = "learned_" + str(factor.get("eye", "unknown"))
        blocks.append((label, secondary_start + 3 * index, secondary_start + 3 * index + 3))
    row += 3 * secondary_count
    for _ in range(node_count - 1):
        blocks.append(("imu_position", row, row + 3))
        row += 3
        blocks.append(("imu_velocity", row, row + 3))
        row += 3
    blocks.append(("stereo", row, row + 3 * accepted_count))
    row += 3 * accepted_count
    blocks.append(("gravity_prior", row, row + 3))
    row += 3
    blocks.append(("bias_prior", row, row + 3))
    row += 3
    static_pair_count = int(debug["static_pair_count"])
    if static_pair_count:
        blocks.append(("static_pairs", row, row + 3 * static_pair_count))
        row += 3 * static_pair_count
    return blocks, row


def evaluate_perturbation(
    times: np.ndarray,
    debug: dict[str, Any],
    blocks: list[tuple[str, int, int]],
    start_s: int,
    axis_vector: np.ndarray,
) -> dict[str, Any]:
    design = debug["design"]
    target = debug["target"]
    solution = debug["solution"]
    residual = design @ solution - target
    alpha = triangular_alpha(times, start_s)
    delta_solution = np.zeros_like(solution)
    for index, value in enumerate(alpha):
        if value != 0.0:
            delta_solution[3 * index : 3 * index + 3] = value * axis_vector
    design_delta = design @ delta_solution
    linear = 2.0 * float(residual @ design_delta)
    quadratic = float(design_delta @ design_delta)
    base_cost = float(residual @ residual)
    plus_residual = design @ (solution + delta_solution) - target
    finite_difference = float(plus_residual @ plus_residual - base_cost)
    classes: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for label, first_row, last_row in blocks:
        row_residual = residual[first_row:last_row]
        row_delta = design_delta[first_row:last_row]
        class_linear = 2.0 * float(row_residual @ row_delta)
        class_quadratic = float(row_delta @ row_delta)
        classes[label][0] += class_linear
        classes[label][1] += class_quadratic
        classes[label][2] += class_linear + class_quadratic
    return {
        "linear": linear,
        "quadratic": quadratic,
        "linear_plus_quadratic": linear + quadratic,
        "finite_difference": finite_difference,
        "finite_difference_error": finite_difference - (linear + quadratic),
        "classes": {
            label: {
                "linear": values[0],
                "quadratic": values[1],
                "linear_plus_quadratic": values[2],
            }
            for label, values in sorted(classes.items())
            if abs(values[0]) + abs(values[1]) > 1e-15
        },
    }


def residual_norm_stats(values: list[float]) -> dict[str, float | int]:
    array = np.asarray(values, dtype=float)
    return {
        "count": int(array.size),
        "median_m": float(np.median(array)),
        "p95_m": float(np.percentile(array, 95)),
        "max_m": float(np.max(array)),
    }


def source_residual_stats(
    state: Any,
    saved_positions: np.ndarray,
    factors: list[dict[str, Any]],
    shared: list[dict[str, Any]],
    debug: dict[str, Any],
) -> dict[str, dict[str, float | int]]:
    stats: dict[str, list[float]] = defaultdict(list)
    node_indices = debug["node_indices"]
    for node, (first, second) in enumerate(zip(node_indices[:-1], node_indices[1:])):
        residual = (
            state.positions[second]
            - state.positions[first]
            - (saved_positions[second] - saved_positions[first])
        )
        stats["vins_relative"].append(float(np.linalg.norm(residual)))
    for index, factor in enumerate(factors):
        first = int(factor["first_index"])
        second = int(factor["second_index"])
        residual = np.asarray(factor["metric_displacement_world_m"], dtype=float) - (
            saved_positions[second] - saved_positions[first]
        )
        stats["learned_" + str(factor.get("eye", "unknown"))].append(
            float(np.linalg.norm(residual))
        )
    for observation in shared:
        first = int(observation["first_index"])
        second = int(observation["second_index"])
        target = state.rotations[first].apply(
            np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
        )
        residual = target - (saved_positions[second] - saved_positions[first])
        stats["stereo"].append(float(np.linalg.norm(residual)))
    # IMU-position residuals are already whitened into the captured design; keep
    # the unwhitened residual summary out rather than re-deriving with hidden
    # local variables.  Their signed force is still exactly represented by rows.
    return {label: residual_norm_stats(values) for label, values in sorted(stats.items())}


def analyze_record(record_id: str, record: dict[str, Any], debug_refine: Any, fusion: Any, base: Any) -> dict[str, Any]:
    variant_dir = ARTIFACT_ROOT / record_id / "physical_stereo_constant_gauge"
    shared_path = variant_dir / "shared_stereo_observations.json"
    factors_path = variant_dir / "local_motion_factors.json"
    trajectory_path = variant_dir / "body_trajectory_fused.csv"
    graph_path = variant_dir / "graph_report.json"
    candidate_path = variant_dir / "candidate_manifest.json"
    candidate = read_json(candidate_path)
    candidate_input_check = validate_candidate_input_hashes(candidate)
    shared = read_json(shared_path)
    factors = read_json(factors_path)
    state = base.load_bound_reference(record)
    _, saved_positions, _, _ = fusion.load_trajectory(trajectory_path)
    refined, report = debug_refine(
        state.positions,
        state.rotations,
        shared,
        state.mono,
        state.imu_times,
        state.gyro,
        state.accel,
        np.eye(4),
        state.config["td_s"],
        node_stride=1,
        max_correction_m=None,
        relative_motion_positions_body=state.positions,
        relative_motion_valid=np.ones(len(state.times), dtype=bool),
        secondary_visual_factors=factors,
        use_visual_position_prior=False,
        solve_metric_scale=False,
        correction_cap_mode="global",
        stereo_factor_confidences=np.asarray(
            [float(observation.get("pnp_inlier_ratio", 0.5)) for observation in shared]
        ),
    )
    debug = report["_debug_state"]
    if not np.array_equal(debug["node_indices"], np.arange(len(state.times))):
        raise ValueError("captured solver node indices are not all-frame arange")
    blocks, block_rows = build_row_blocks(debug, factors)
    design = debug["design"]
    if block_rows != design.shape[0]:
        raise ValueError("source row-order block count does not match design rows")
    target = debug["target"]
    solution = debug["solution"]
    residual = design @ solution - target
    gradient = design.T @ residual
    duration_s = float(state.times[-1] - state.times[0])
    windows: list[dict[str, Any]] = []
    finite_errors: list[float] = []
    for start_s in range(int(np.floor(duration_s))):
        axes: dict[str, Any] = {}
        for axis_name, axis_vector in AXES.items():
            result = evaluate_perturbation(state.times, debug, blocks, start_s, axis_vector)
            finite_errors.append(abs(float(result["finite_difference_error"])))
            axes[axis_name] = result
        windows.append({"start_s": start_s, "axes": axes})
    max_finite_error = float(max(finite_errors))
    if max_finite_error > MAX_FINITE_DIFFERENCE_ERROR:
        raise ValueError(
            "finite-difference self-check failed: "
            f"{max_finite_error:.3e} > {MAX_FINITE_DIFFERENCE_ERROR:.3e}"
        )
    replay_norms = np.linalg.norm(refined - saved_positions, axis=1)
    max_replay_delta = float(np.max(replay_norms))
    if max_replay_delta > MAX_REPLAY_POSITION_DELTA_M:
        raise ValueError(
            "trajectory replay self-check failed: "
            f"{max_replay_delta:.3e} > {MAX_REPLAY_POSITION_DELTA_M:.3e}"
        )
    graph = read_json(graph_path)
    return {
        "id": record_id,
        "artifact_dir": str(variant_dir.resolve()),
        "candidate_input_sha256_check": candidate_input_check,
        "input_hashes": {
            str(path.resolve()): sha256_file(path)
            for path in (
                shared_path,
                factors_path,
                trajectory_path,
                graph_path,
                candidate_path,
                Path(record["session"]) / "d405_frames.csv",
                Path(record["session"]) / "external_imu/imu.bin",
                Path(record["vins_dir"]) / "vio_corrected_stream.csv",
                Path(record["vins_dir"]) / "run_acceptance.json",
            )
        },
        "samples": int(len(state.times)),
        "duration_s": duration_s,
        "row_order": [
            {"source": label, "first_row": int(first), "last_row": int(last)}
            for label, first, last in blocks
        ],
        "row_order_check": {
            "block_rows": int(block_rows),
            "design_rows": int(design.shape[0]),
            "ok": bool(block_rows == design.shape[0]),
            "design_cols": int(design.shape[1]),
        },
        "captured_fourth_lsqr_state": {
            "design_csr_sha256": sha256_csr(design),
            "target_sha256": sha256_array(target),
            "solution_sha256": sha256_array(solution),
            "weights_sha256": {
                name: sha256_array(values)
                for name, values in debug["weights_used_in_last_solve"].items()
            },
            "cost": float(residual @ residual),
            "gradient_inf_norm": float(np.max(np.abs(gradient))),
            "gradient_l2_norm": float(np.linalg.norm(gradient)),
        },
        "trajectory_replay": {
            "max_position_delta_m": max_replay_delta,
            "p95_position_delta_m": float(np.percentile(replay_norms, 95)),
            "consistency_threshold_m": MAX_REPLAY_POSITION_DELTA_M,
        },
        "graph_counts": {
            key: graph.get("joint_position_solver", {}).get(key)
            for key in (
                "stereo_edges",
                "relative_motion_edges",
                "robust_relative_motion_inliers",
                "robust_imu_position_inliers",
                "robust_imu_velocity_inliers",
            )
        },
        "source_residual_norms": source_residual_stats(
            state, saved_positions, factors, shared, debug
        ),
        "finite_difference_verification": {
            "max_abs_error": max_finite_error,
            "checked_window_count": int(len(windows) * len(AXES)),
            "objective": "fourth_lsqr_actual_design_target_solution_cost",
            "consistency_threshold": MAX_FINITE_DIFFERENCE_ERROR,
        },
        "windows_1s_3mm_triangular": windows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    import sys

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))
    import fuse_mast3r_stereo_imu as fusion  # noqa: PLC0415
    import run_learned_segment_probe as base  # noqa: PLC0415

    manifest = read_json(MANIFEST)
    records = {record["id"]: record for record in manifest["records"]}
    missing = [record_id for record_id in RECORD_IDS if record_id not in records]
    if missing:
        raise ValueError(f"missing records in manifest: {missing}")
    consumed_paths = consumed_source_paths(fusion, base)
    for record_id in RECORD_IDS:
        consumed_paths.extend(consumed_record_paths(record_id, records[record_id]))
    before_hashes = hash_paths(consumed_paths)
    debug_refine = make_debug_refine(fusion)
    record_reports = [
        analyze_record(record_id, records[record_id], debug_refine, fusion, base)
        for record_id in RECORD_IDS
    ]
    after_hashes = hash_paths(consumed_paths)
    if before_hashes != after_hashes:
        changed = [
            path for path, digest in before_hashes.items()
            if after_hashes.get(path) != digest
        ]
        raise ValueError(f"consumed input/source changed during diagnostic: {changed[:5]}")
    output = {
        "schema": "umi_source_force_diagnostic_v1",
        "status": "READ_ONLY_DIAGNOSTIC",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "description": (
            "Source-class decomposition of fixed 1s 3mm triangular position "
            "perturbations using the actual fourth-LSQR design/target/solution."
        ),
        "command": f"rtk proxy python3 {SCRIPT} --output {args.output}",
        "script": str(SCRIPT),
        "script_sha256": sha256_file(SCRIPT),
        "repo_root": str(ROOT),
        "manifest": str(MANIFEST),
        "manifest_sha256": sha256_file(MANIFEST),
        "artifact_root": str(ARTIFACT_ROOT),
        "consumed_source_and_input_hashes": before_hashes,
        "hash_guard": {
            "before_after_match": True,
            "checked_path_count": len(before_hashes),
            "excludes_output_json_self_hash": True,
        },
        "records": record_reports,
    }
    write_json(args.output, output)
    print(json.dumps({
        "output": str(args.output),
        "records": [record["id"] for record in output["records"]],
        "script_sha256": output["script_sha256"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

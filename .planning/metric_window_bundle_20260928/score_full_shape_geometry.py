"""Evaluate frozen full-shape local geometry diagnostics after census freeze.

This is a post-freeze evaluator only: it scores existing diagnostic 9-state BA
shape factors against the unchanged official reference. It is not full ATE, not
graph promotion, and not estimator supervision.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import score_shape_geometry as geom
import run_full_shape_controls as full_shape


def _add_source_closure(hashes: dict[str, str], joint: dict, independent: dict) -> None:
    source_map = joint.get("source_sha256")
    if not isinstance(source_map, dict) or not source_map:
        raise ValueError("full-shape source hash map required")
    if independent.get("source_sha256") != source_map:
        raise ValueError("joint/independent source hash maps differ")
    required = {str(Path(path).resolve()) for path in full_shape.source_paths()}
    missing = required - set(source_map)
    if missing:
        raise ValueError(f"full-shape source hash map missing required source: {sorted(missing)[0]}")
    geom.add_hashes(hashes, source_map)


def _add_case_inputs(hashes: dict[str, str], joint: dict, independent: dict) -> None:
    independent_by_name = {case["case"]: case for case in independent.get("cases", [])}
    for joint_case in joint.get("cases", []):
        independent_case = independent_by_name[joint_case["case"]]
        if joint_case.get("input_sha256") != independent_case.get("input_sha256"):
            raise ValueError(f"{joint_case.get('case')} joint/independent input hashes differ")
        input_map = joint_case.get("input_sha256")
        if not isinstance(input_map, dict) or not input_map:
            raise ValueError(f"{joint_case.get('case')} input hash map required")
        geom.add_hashes(hashes, input_map)


def _validate_adapter(summary: dict, *, independent: bool) -> None:
    adapter = summary.get("full_shape_window_adapter")
    if not isinstance(adapter, dict):
        raise ValueError("full-shape adapter metadata required")
    required_false = [
        "external_ground_truth_used",
        "used_for_graph_or_selection",
        "available_for_graph",
        "calibrated_covariance",
        "statistical_independence_claimed",
    ]
    if (
        adapter.get("diagnostic_only") is not True
        or any(adapter.get(key) is not False for key in required_false)
        or adapter.get("joint_pairs_per_case") != 29
        or adapter.get("endpoint_rows_per_case") != 58
        or adapter.get("independent_summary") is not independent
    ):
        raise ValueError("full-shape diagnostic-only metadata contract invalid")


def _validate_completion(joint: dict, independent: dict) -> None:
    case_counts = full_shape._validate_pair_summaries(joint, independent)
    for name, row in case_counts.items():
        expected = full_shape.EXPECTED_RAW_COUNTS[name]
        if (
            row.get("recording_raw_frame_count") != expected
            or row.get("pair_count") != 29
            or row.get("joint_window_count") != 58
            or row.get("independent_window_count") != 58
            or row.get("last_endpoint_index") != 1160
            or row.get("uncovered_tail_frames_after_last_endpoint") != expected - 1 - 1160
        ):
            raise ValueError(f"{name} full-shape completion metadata inconsistent")
    for summary, flag in ((joint, False), (independent, True)):
        if summary.get("external_reference_used") is not False or summary.get("production_modified") is not False:
            raise ValueError("full-shape summary must be frozen UMI-only output")
        _validate_adapter(summary, independent=flag)
        stored = summary["full_shape_window_adapter"].get("case_counts")
        if stored != case_counts:
            raise ValueError("full-shape adapter persisted case_counts mismatch")


def _validate_time_alignment(name: str, graph: dict, original: dict, precision: dict) -> None:
    if graph.get("time_alignment") != original.get("time_alignment"):
        raise ValueError(f"{name} time alignment differs from bound original")
    alignment = graph.get("time_alignment", {})
    if alignment.get("estimate_td") != 0 or not np.isclose(float(alignment.get("td_s")), -0.009109323, atol=1e-12, rtol=0):
        raise ValueError(f"{name} formal time alignment mismatch")
    if (
        not np.isclose(float(precision.get("max_interpolation_gap_s")), 0.05, atol=1e-12, rtol=0)
        or precision.get("estimate_frame") != "as_recorded"
        or precision.get("ground_truth_frame") != "as_recorded"
    ):
        raise ValueError(f"{name} official precision contract mismatch")


def validate_full_controls(controls_dir: Path) -> tuple[dict, dict, dict[str, str]]:
    hashes = {
        str(Path(__file__).resolve()): geom.digest(Path(__file__).resolve()),
        str(Path(geom.__file__).resolve()): geom.digest(Path(geom.__file__).resolve()),
        str(Path(geom.evaluation.__file__).resolve()): geom.digest(Path(geom.evaluation.__file__).resolve()),
        str(Path(full_shape.__file__).resolve()): geom.digest(Path(full_shape.__file__).resolve()),
    }
    joint = geom.json_snapshot(Path(controls_dir) / "summary.json", hashes)
    independent = geom.json_snapshot(Path(controls_dir) / "independent_summary.json", hashes)
    _validate_completion(joint, independent)
    _add_source_closure(hashes, joint, independent)
    _add_case_inputs(hashes, joint, independent)
    for case in joint["cases"]:
        for number, pair in enumerate(case["pairs"]):
            geom.pair_factor(pair, case["windows"][2 * number:2 * number + 2])
    geom.verify_hashes(hashes)
    return joint, independent, hashes


def _original_graph_path(case: dict) -> Path:
    paths = [Path(path) for path in case["input_sha256"] if Path(path).name == "graph_fusion_report.json"]
    if len(paths) != 1:
        raise ValueError(f"{case.get('case')} single bound original graph required")
    return paths[0]


def _check_elapsed(times: np.ndarray, case: dict) -> None:
    for row in case["windows"]:
        indices = np.asarray(row["indices"], dtype=int)
        elapsed = np.asarray(row["elapsed_s"], dtype=float)
        if indices.shape != (5,) or elapsed.shape != (5,) or not np.allclose(times[indices] - times[0], elapsed, atol=1e-9, rtol=0):
            raise ValueError(f"{case.get('case')} window elapsed times differ from official estimate")


def _score_case(case: dict, graph_root: Path, hashes: dict[str, str]) -> dict:
    folder = Path(graph_root) / case["case"]
    graph = geom.json_snapshot(folder / "graph_fusion_report.json", hashes)
    precision = geom.json_snapshot(folder / "official_score" / "precision.json", hashes)
    original = geom.json_snapshot(_original_graph_path(case), hashes)
    extrinsic = np.asarray(graph.get("camera_extrinsics", {}).get("effective_body_T_trajectory_camera"), dtype=float)
    original_extrinsic = np.asarray(original.get("camera_extrinsics", {}).get("effective_body_T_trajectory_camera"), dtype=float)
    estimate_path = Path(precision.get("estimate", ""))
    if (
        graph.get("inputs", {}).get("session") != original.get("inputs", {}).get("session")
        or extrinsic.shape != (4, 4)
        or not np.array_equal(extrinsic, original_extrinsic)
        or graph.get("camera_extrinsics", {}).get("trajectory_observation_frame") != "infrared_left_camera_i"
        or precision.get("alignment") != "SE3_estimate_to_external_ground_truth_no_scale"
        or estimate_path.resolve() != (folder / "trajectory_fused.csv").resolve()
    ):
        raise ValueError(f"{case['case']} official contract mismatch")
    _validate_time_alignment(case["case"], graph, original, precision)
    times, positions, quats = geom.pose_snapshot(estimate_path, hashes)
    expected_count = full_shape.EXPECTED_RAW_COUNTS[case["case"]]
    if len(times) != expected_count:
        raise ValueError(f"{case['case']} official estimate raw pose count differs from census")
    stereo = geom.json_snapshot(Path(original["inputs"]["stereo_report"]), hashes)
    input_times, _, _ = geom.pose_snapshot(Path(stereo["trajectory"]), hashes)
    if not np.array_equal(times, input_times):
        raise ValueError(f"{case['case']} official estimate timestamps differ from census input")
    _check_elapsed(times, case)
    gt_times, gt_pos, gt_quats = geom.pose_snapshot(Path(precision["ground_truth"]), hashes)
    inside, valid, reference, attitudes = geom.evaluation.interpolate_ground_truth(
        times, gt_times, gt_pos, gt_quats, precision["max_interpolation_gap_s"]
    )
    mapping = np.full(len(times), -1, dtype=int)
    mapping[np.flatnonzero(inside)[valid]] = np.arange(len(reference))
    camera_pos, camera_rot = geom.camera_reference(reference[:, 1:], Rotation.from_quat(attitudes), extrinsic)
    estimate_pos, estimate_rot = geom.camera_reference(positions, Rotation.from_quat(quats), extrinsic)
    rows, ba_all, estimate_all = [], [], []
    for number, pair in enumerate(case["pairs"]):
        row = {"pair": number + 1, "indices": pair["indices"], "accepted": pair["accepted"], "reason": pair["reason"], "scored": False}
        windows = case["windows"][2 * number:2 * number + 2]
        if not pair.get("accepted", False):
            row["score_reason"] = "pair_refused"
        else:
            factor = geom.pair_factor(pair, windows)
            if factor is None:
                row["score_reason"] = "shape_diagnostic_missing_or_unavailable"
            else:
                indices = np.asarray(pair["indices"], dtype=int)
                selected = mapping[indices]
                if np.any(selected < 0):
                    row["score_reason"] = "outside_unchanged_reference_coverage"
                else:
                    truth, truth_rot = camera_pos[selected], camera_rot[selected]
                    centers = np.asarray(factor["optimized_centers_m"])
                    rotations = Rotation.from_rotvec(factor["optimized_rotvecs_camera_to_window"])
                    ba = geom.relative_errors(centers, rotations, truth, truth_rot)
                    estimate = geom.relative_errors(estimate_pos[indices], estimate_rot[indices], truth, truth_rot)
                    row.update(
                        scored=True,
                        ba_local_node_errors_mm=ba.tolist(),
                        current_official_estimate_local_node_errors_mm=estimate.tolist(),
                    )
                    ba_all.extend(ba.tolist())
                    estimate_all.extend(estimate.tolist())
        rows.append(row)
    return {"case": case["case"], "pairs": rows, "_ba": ba_all, "_estimate": estimate_all}


def score(controls_dir: Path, graph_root: Path) -> dict:
    joint, _independent, hashes = validate_full_controls(Path(controls_dir))
    result, ba_all, estimate_all = [], [], []
    for case in joint["cases"]:
        row = _score_case(case, Path(graph_root), hashes)
        ba_all.extend(row.pop("_ba"))
        estimate_all.extend(row.pop("_estimate"))
        result.append(row)
    geom.verify_hashes(hashes)
    return {
        "cases": result,
        "ba_local_geometry": geom.stats(ba_all),
        "current_official_estimate_local_geometry": geom.stats(estimate_all),
        "provenance_sha256": hashes,
        "independent_summary_identity": "validated_by_case_name_not_array_order",
        "estimate_origin": "body_converted_once_to_left_IR_camera",
        "reference_origin": "body_converted_once_to_left_IR_camera",
        "external_reference_used_in_estimation": False,
        "external_reference_used_in_evaluation": True,
        "used_for_selection_or_graph": False,
        "calibrated_covariance": False,
        "warning": "All eight non-anchor local relative centers per accepted group; NOT full trajectory ATE; no fit, selector, graph promotion, or window weighting.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("refuse to overwrite full-shape geometry evaluation")
    report = score(args.controls, args.graphs)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("ba_local_geometry", "current_official_estimate_local_geometry")}, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

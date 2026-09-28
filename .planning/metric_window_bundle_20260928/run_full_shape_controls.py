"""Full 29-pair seam controls with diagnostic shape-factor instrumentation.

This adapter only changes the seam control schedule and the joint 9-node solver
hook for an offline UMI diagnostic census. It does not read GT, score, select,
modify production trajectories, or create graph factors.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_full_seam_controls as full_seam
import run_seam_window_controls as seam
import run_shape_window_controls as shape_adapter


EXPECTED_CASES = shape_adapter.EXPECTED_CASES
EXPECTED_RAW_COUNTS = shape_adapter.EXPECTED_RAW_COUNTS


def expected_pair_schedule() -> list[list[int]]:
    return [list(range(start, start + 41, 5)) for start in range(0, 1160, 40)]


def source_paths() -> list[Path]:
    paths = [
        Path(__file__).resolve(),
        Path(full_seam.__file__).resolve(),
        Path(shape_adapter.__file__).resolve(),
    ]
    paths.extend(shape_adapter.source_paths())
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = Path(path).resolve()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def hash_sources() -> dict[str, str]:
    return {str(path): shape_adapter.digest(path) for path in source_paths()}


def verify_hashes(hashes: dict[str, str]) -> None:
    shape_adapter.verify_hashes(hashes)


def case_graphs() -> list[tuple[str, Path]]:
    registry = ROOT / ".planning/joint_metric_scale_20260927/run_cached_regression.py"
    cases = list(seam.previous.base.load_module("full_shape_case_sources", registry).CASES)
    cases += [
        (f"fresh{i}", f"joint_scale_independent_four_20260927/take{i}/fusion/{'rescue' if i == 2 else 'baseline'}", "")
        for i in range(1, 5)
    ]
    if len(cases) != 10 or len({case[0] for case in cases}) != 10:
        raise ValueError("full-shape preflight requires exact ten unique registry cases")
    graphs = [(name, ROOT / "reports" / report_path / "mast3r/graph_fusion_report.json") for name, report_path, _ in cases]
    if {name for name, _ in graphs} != EXPECTED_CASES:
        raise ValueError("full-shape preflight requires exact ten known registry cases")
    return graphs


def _case_input_paths(graph_path: Path) -> list[Path]:
    graph = json.loads(graph_path.read_text())
    inputs = graph["inputs"]
    report_path = Path(inputs["stereo_report"])
    report = json.loads(report_path.read_text())
    trajectory = Path(report["trajectory"])
    session = Path(inputs["session"])
    return [
        graph_path,
        report_path,
        trajectory,
        Path(inputs["imu_calibration"]),
        Path(inputs["vins_spatiotemporal_calibration"]),
        session / "d405_frames.csv",
        session / "external_imu/imu.bin",
        trajectory.parent / "dataset/frames.csv",
    ]


def preflight_inputs() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, graph_path in case_graphs():
        graph = json.loads(graph_path.read_text())
        report = json.loads(Path(graph["inputs"]["stereo_report"]).read_text())
        timestamps = np.asarray(seam.previous.base.stereo.load_trajectory(Path(report["trajectory"]))[0], dtype=float)
        if timestamps.ndim != 1 or len(timestamps) == 0 or not np.all(np.isfinite(timestamps)):
            raise ValueError(f"{name} trajectory timestamps invalid")
        if np.any(np.diff(timestamps) <= 0.0):
            raise ValueError(f"{name} trajectory timestamps are not strictly monotonic")
        expected_count = EXPECTED_RAW_COUNTS[name]
        if len(timestamps) != expected_count:
            raise ValueError(f"{name} recording raw frame count {len(timestamps)} != expected {expected_count}")
        actual_schedule = [[int(v) for v in window] for window in full_seam.pair_windows(len(timestamps))]
        if actual_schedule != expected_pair_schedule():
            raise ValueError(f"{name} full schedule does not match literal 0..1160 stride-40 windows")
        for path in _case_input_paths(graph_path):
            if not path.is_file():
                raise ValueError(f"full-shape preflight input missing: {path}")
            digest = shape_adapter.digest(path)
            key = str(path)
            if key in hashes and hashes[key] != digest:
                raise ValueError(f"full-shape preflight input hash conflict: {path}")
            hashes[key] = digest
    if not hashes:
        raise ValueError("full-shape preflight input hash map empty")
    return hashes


def verify_input_hashes(hashes: dict[str, str]) -> None:
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("full-shape preflight input hash map empty")
    for raw, expected in hashes.items():
        path = Path(raw)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"full-shape preflight input hash malformed: {raw}")
        if not path.is_file():
            raise ValueError(f"full-shape preflight input missing: {path}")
        if shape_adapter.digest(path) != expected:
            raise ValueError(f"full-shape preflight input changed: {path}")


def _case_count(name: str, case: dict) -> dict:
    expected_count = EXPECTED_RAW_COUNTS.get(name)
    if expected_count is None:
        raise ValueError(f"{name} unknown fixed full-shape case")
    pairs = case.get("pairs")
    windows = case.get("windows")
    independent = case.get("independent_windows", case.get("windows"))
    if len(pairs) != 29:
        raise ValueError(f"{name} must contain exactly 29 seam pairs")
    if len(windows) != 58 or len(independent) != 58:
        raise ValueError(f"{name} must contain exactly 58 endpoint rows")
    expected_pairs = [[int(v) for v in window] for window in full_seam.pair_windows(expected_count)]
    if [pair.get("indices") for pair in pairs] != expected_pairs:
        raise ValueError(f"{name} pair schedule does not match full 40-frame stride")
    for pair_index, (pair, expected) in enumerate(zip(pairs, expected_pairs), 1):
        if pair.get("pair", pair_index) != pair_index:
            raise ValueError(f"{name} pair ids are not exact 1..29")
        if pair.get("raw_frame_indices") != list(range(expected[0], expected[-1] + 1)):
            raise ValueError(f"{name} raw frame indices are not exact 41-frame spans")
        for part, endpoint_indices in enumerate((expected[:5], expected[4:])):
            row_index = 2 * (pair_index - 1) + part
            joint_row = windows[row_index]
            independent_row = independent[row_index]
            expected_window = row_index + 1
            if joint_row.get("window") != expected_window or independent_row.get("window") != expected_window:
                raise ValueError(f"{name} window ids are not exact 1..58")
            if joint_row.get("joint_pair") != pair_index:
                raise ValueError(f"{name} joint_pair ids are not exact 1..29")
            if "joint_pair" in independent_row and independent_row.get("joint_pair") != pair_index:
                raise ValueError(f"{name} independent joint_pair ids are not exact 1..29")
            if joint_row.get("indices") != endpoint_indices or independent_row.get("indices") != endpoint_indices:
                raise ValueError(f"{name} endpoint indices do not match fixed full schedule")
    last_endpoint = expected_pairs[-1][-1]
    return {
        "recording_raw_frame_count": int(expected_count),
        "pair_count": 29,
        "joint_window_count": 58,
        "independent_window_count": 58,
        "last_endpoint_index": int(last_endpoint),
        "uncovered_tail_frames_after_last_endpoint": int(expected_count - 1 - last_endpoint),
    }


def _validate_pair_summaries(joint: dict, independent: dict) -> dict[str, dict]:
    for label, summary in (("joint", joint), ("independent", independent)):
        if summary.get("external_reference_used") is not False:
            raise ValueError(f"{label} summary used external reference")
        if summary.get("production_modified") is not False:
            raise ValueError(f"{label} summary production_modified flag invalid")
    joint_rows = joint.get("cases")
    independent_rows = independent.get("cases")
    if not isinstance(joint_rows, list) or not isinstance(independent_rows, list):
        raise ValueError("full-shape summaries require case arrays")
    joint_names = [case.get("case") for case in joint_rows]
    independent_names = [case.get("case") for case in independent_rows]
    if (
        len(joint_rows) != 10
        or len(independent_rows) != 10
        or any(not isinstance(name, str) for name in joint_names + independent_names)
        or len(set(joint_names)) != 10
        or len(set(independent_names)) != 10
    ):
        raise ValueError("full-shape summaries require exact ten unique case rows")
    joint_cases = {case["case"]: case for case in joint_rows}
    independent_cases = {case["case"]: case for case in independent_rows}
    if set(joint_cases) != EXPECTED_CASES or set(independent_cases) != EXPECTED_CASES:
        raise ValueError("full-shape summaries require exact ten known cases")
    counts: dict[str, dict] = {}
    for name in sorted(EXPECTED_CASES):
        joint_case = joint_cases[name]
        independent_case = independent_cases[name]
        counts[name] = _case_count(name, joint_case)
        if independent_case.get("windows") != joint_case.get("independent_windows"):
            raise ValueError(f"{name} independent summary windows differ from original independent_windows")
        if joint_case.get("input_sha256") != independent_case.get("input_sha256"):
            raise ValueError(f"{name} input provenance differs between summaries")
        if joint_case.get("decoded_grayscale_frame_sha256") != independent_case.get("decoded_grayscale_frame_sha256"):
            raise ValueError(f"{name} decoded image provenance differs between summaries")
    return counts


def annotate(output: Path, source_hashes: dict[str, str]) -> None:
    joint_path = output / "summary.json"
    independent_path = output / "independent_summary.json"
    joint = json.loads(joint_path.read_text())
    independent = json.loads(independent_path.read_text())
    case_counts = _validate_pair_summaries(joint, independent)
    shape_adapter._verify_case_input_hashes(joint)
    shape_adapter._verify_case_input_hashes(independent)
    shape_adapter._check_source_hash_conflicts([joint, independent], source_hashes)
    shape_counts = shape_adapter._shape_factor_counts(joint)
    metadata = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "used_for_graph_or_selection": False,
        "available_for_graph": False,
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "production_modified": False,
        "instrumented_solver": "stereo_window_shape_factor.solve_with_shape_factor",
        "joint_pairs_per_case": 29,
        "endpoint_rows_per_case": 58,
        "policy": "full 29 paired-40 seam groups per known UMI case; no GT/scoring/selector/graph integration",
        "leftover_tail_policy": "explicitly_uncovered_no_duplicate_partial_edge_no_tail_fallback",
        "schedule": {
            "raw_interval_frames": 40,
            "ba_node_offsets": [0, 5, 10, 15, 20, 25, 30, 35, 40],
            "start_indices": list(range(0, 1160, 40)),
            "last_endpoint_index": 1160,
        },
        "case_counts": case_counts,
        "joint_independent_input_decoded_identity_verified": True,
        **shape_counts,
        "source_sha256": {
            str(Path(__file__).resolve()): source_hashes[str(Path(__file__).resolve())],
            str(Path(full_seam.__file__).resolve()): source_hashes[str(Path(full_seam.__file__).resolve())],
            str(Path(shape_adapter.__file__).resolve()): source_hashes[str(Path(shape_adapter.__file__).resolve())],
        },
    }
    for summary, path, independent_flag in (
        (joint, joint_path, False),
        (independent, independent_path, True),
    ):
        shape_adapter._merge_source_hashes(summary, source_hashes)
        summary["full_shape_window_adapter"] = {**metadata, "independent_summary": independent_flag}
        summary["candidate_policy"] = "full 29 paired-40 seam shape diagnostics on all ten cases; no GT/scoring/selector/graph integration"
        path.write_text(json.dumps(summary, indent=2) + "\n")
    joint_after = json.loads(joint_path.read_text())
    independent_after = json.loads(independent_path.read_text())
    shape_adapter._verify_case_input_hashes(joint_after)
    shape_adapter._verify_case_input_hashes(independent_after)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("refuse to overwrite full-shape controls")
    source_hashes = hash_sources()
    input_hashes = preflight_inputs()
    verify_hashes(source_hashes)
    verify_input_hashes(input_hashes)
    old_pair_windows = seam.previous.pair_windows
    old_solve = seam.previous.solve
    old_argv = sys.argv[:]
    try:
        seam.previous.pair_windows = full_seam.pair_windows
        seam.previous.solve = shape_adapter.solve_with_shape_factor
        sys.argv = [str(Path(seam.__file__).resolve()), "--output", str(args.output)]
        result = seam.main()
    finally:
        seam.previous.pair_windows = old_pair_windows
        seam.previous.solve = old_solve
        sys.argv = old_argv
    verify_hashes(source_hashes)
    verify_input_hashes(input_hashes)
    annotate(args.output, source_hashes)
    verify_input_hashes(input_hashes)
    verify_hashes(source_hashes)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

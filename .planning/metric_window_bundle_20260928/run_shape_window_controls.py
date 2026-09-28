"""Five-pair seam controls with diagnostic shape-factor instrumentation only."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

import run_seam_window_controls as seam
import stereo_window_bundle as bundle
import stereo_window_shape_factor as shape


EXPECTED_CASES = {
    "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4",
    "fresh1", "fresh2", "fresh3", "fresh4",
}
EXPECTED_RAW_COUNTS = {name: 1199 for name in EXPECTED_CASES}
EXPECTED_RAW_COUNTS["dev2"] = 1200


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_paths() -> list[Path]:
    registry = ROOT / ".planning/joint_metric_scale_20260927/run_cached_regression.py"
    return [
        Path(__file__).resolve(),
        Path(shape.__file__).resolve(),
        Path(seam.__file__).resolve(),
        Path(seam.previous.__file__).resolve(),
        Path(seam.previous.base.__file__).resolve(),
        registry,
        ROOT / "scripts/join_stereo_windows.py",
        ROOT / "scripts/prepare_seam_stereo_observations.py",
        ROOT / "scripts/stereo_window_bundle.py",
        ROOT / "scripts/prepare_stereo_window_observations.py",
        ROOT / "scripts/align_mast3r_scale_with_stereo.py",
        ROOT / "scripts/fuse_mast3r_stereo_imu.py",
    ]


def hash_sources() -> dict[str, str]:
    return {str(path): digest(path) for path in source_paths()}


def verify_hashes(hashes: dict[str, str]) -> None:
    for raw, expected in hashes.items():
        if digest(Path(raw)) != expected:
            raise ValueError(f"source changed during shape controls: {raw}")


def solve_with_shape_factor(data, calibration, times, deltas, jacobians):
    train = seam.previous.base.training_support(
        data["train"], data["admitted_train"], data["initial_points"]
    )
    return shape.solve_with_shape_factor(
        bundle,
        data["observations"][:, train],
        data["admitted_train"][:, train],
        times - times[0],
        calibration["left_intrinsics"],
        calibration["right_intrinsics"],
        calibration["baseline_m"],
        data["initial_points"][train],
        data["initial_centers"],
        data["initial_rotations"],
        deltas,
        gyro_noise_density=0.00103,
        gyro_bias_sigma=0.01 / 3.0,
        gyro_bias_jacobians=jacobians,
    )


def _expected_windows_for_count(count: int) -> list[list[int]]:
    return [selection.tolist() for selection in seam.previous.pair_windows(count)]


def _validate_case(case: dict) -> None:
    pairs = case.get("pairs", [])
    windows = case.get("windows", [])
    independent = case.get("independent_windows", case.get("windows", []))
    if len(pairs) != 5 or len(windows) != 10 or len(independent) != 10:
        raise ValueError(f"{case.get('case')} must contain exactly five seam pairs / ten endpoint rows")
    count = EXPECTED_RAW_COUNTS.get(case.get("case"))
    if count is None:
        raise ValueError(f"{case.get('case')} unknown fixed schedule case")
    expected = _expected_windows_for_count(count)
    if [pair.get("indices") for pair in pairs] != expected:
        raise ValueError(f"{case.get('case')} pair schedule is not fixed five time-stratified seam pairs")
    for index, selection in enumerate(expected):
        halves = [selection[:5], selection[4:]]
        for part, expected_indices in enumerate(halves):
            row_index = 2 * index + part
            if windows[row_index].get("window") != row_index + 1:
                raise ValueError(f"{case.get('case')} window ids are not exact 1..10")
            if independent[row_index].get("window") != row_index + 1:
                raise ValueError(f"{case.get('case')} independent window ids are not exact 1..10")
            if windows[row_index].get("joint_pair") != index + 1:
                raise ValueError(f"{case.get('case')} joint_pair ids are not exact 1..5")
            if independent[row_index].get("joint_pair") != index + 1:
                raise ValueError(f"{case.get('case')} independent joint_pair ids are not exact 1..5")
            if windows[row_index].get("indices") != expected_indices:
                raise ValueError(f"{case.get('case')} joint endpoint indices mismatch")
            if independent[row_index].get("indices") != expected_indices:
                raise ValueError(f"{case.get('case')} independent endpoint indices mismatch")


def _validate_pair_summaries(joint: dict, independent: dict) -> None:
    for label, summary in (("joint", joint), ("independent", independent)):
        if summary.get("external_reference_used") is not False:
            raise ValueError(f"{label} summary used external reference")
        if summary.get("production_modified") is not False:
            raise ValueError(f"{label} summary production_modified flag invalid")
    joint_raw = joint.get("cases", [])
    independent_raw = independent.get("cases", [])
    joint_names = [case.get("case") for case in joint_raw]
    independent_names = [case.get("case") for case in independent_raw]
    if (
        len(joint_raw) != 10
        or len(independent_raw) != 10
        or any(not isinstance(name, str) for name in joint_names + independent_names)
        or len(set(joint_names)) != 10
        or len(set(independent_names)) != 10
    ):
        raise ValueError("shape controls require exact ten unique case rows")
    joint_cases = {case["case"]: case for case in joint_raw}
    independent_cases = {case["case"]: case for case in independent_raw}
    if set(joint_cases) != EXPECTED_CASES or set(independent_cases) != EXPECTED_CASES:
        raise ValueError("shape controls require exact ten known cases")
    for name in EXPECTED_CASES:
        joint_case = joint_cases[name]
        independent_case = independent_cases[name]
        _validate_case(joint_case)
        if independent_case.get("windows") != joint_case.get("independent_windows"):
            raise ValueError(f"{name} independent summary windows differ from original independent_windows")
        if joint_case.get("input_sha256") != independent_case.get("input_sha256"):
            raise ValueError(f"{name} input provenance differs between summaries")
        if joint_case.get("decoded_grayscale_frame_sha256") != independent_case.get("decoded_grayscale_frame_sha256"):
            raise ValueError(f"{name} decoded image provenance differs between summaries")


def _verify_case_input_hashes(summary: dict) -> None:
    for case in summary.get("cases", []):
        input_hashes = case.get("input_sha256")
        decoded_hashes = case.get("decoded_grayscale_frame_sha256")
        if not isinstance(input_hashes, dict) or not input_hashes:
            raise ValueError(f"{case.get('case')} input_sha256 must be nonempty")
        if not isinstance(decoded_hashes, dict) or not decoded_hashes:
            raise ValueError(f"{case.get('case')} decoded_grayscale_frame_sha256 must be nonempty")
        for raw, expected in input_hashes.items():
            if not isinstance(expected, str) or len(expected) != 64:
                raise ValueError(f"{case.get('case')} input hash malformed: {raw}")
            path = Path(raw)
            if not path.is_file():
                raise ValueError(f"case input missing during shape controls: {path}")
            if digest(path) != expected:
                raise ValueError(f"case input changed during shape controls: {path}")


def _merge_source_hashes(summary: dict, source_hashes: dict[str, str]) -> None:
    current = summary.setdefault("source_sha256", {})
    for raw, expected in source_hashes.items():
        if raw in current and current[raw] != expected:
            raise ValueError(f"source hash conflict for {raw}")
        current[raw] = expected


def _check_source_hash_conflicts(summaries: list[dict], source_hashes: dict[str, str]) -> None:
    for summary in summaries:
        current = summary.get("source_sha256", {})
        if not isinstance(current, dict):
            raise ValueError("source_sha256 must be a dictionary")
        for raw, expected in source_hashes.items():
            if raw in current and current[raw] != expected:
                raise ValueError(f"source hash conflict for {raw}")


def _empty_factor_counts() -> dict:
    return {
        "available": 0,
        "unavailable": 0,
        "missing": 0,
        "refused": 0,
        "solver_accepted_true": 0,
        "not_admissible_for_graph": 0,
        "rank_deficient": 0,
        "rank_histogram": {},
    }


def _count_factor(row: dict, counts: dict) -> None:
    if not row.get("accepted", False):
        counts["refused"] += 1
    factor = row.get("diagnostics", {}).get("stereo_window_shape_factor")
    if not isinstance(factor, dict):
        counts["missing"] += 1
        return
    if factor.get("available", True) is False:
        counts["unavailable"] += 1
    else:
        counts["available"] += 1
    if factor.get("solver_accepted") is True:
        counts["solver_accepted_true"] += 1
    if factor.get("not_admissible_for_graph") is True:
        counts["not_admissible_for_graph"] += 1
    rank = factor.get("conditional_rank")
    if isinstance(rank, int) and not isinstance(rank, bool):
        key = str(rank)
        counts["rank_histogram"][key] = counts["rank_histogram"].get(key, 0) + 1
    if factor.get("rank_deficient") is True:
        counts["rank_deficient"] += 1


def _shape_factor_counts(joint: dict) -> dict:
    endpoint_counts = _empty_factor_counts()
    pair_counts = _empty_factor_counts()
    for case in joint.get("cases", []):
        windows = case.get("windows", [])
        for row in windows:
            _count_factor(row, endpoint_counts)
        for index in range(0, len(windows), 2):
            group = windows[index:index + 2]
            if len(group) != 2:
                pair_counts["missing"] += 1
                continue
            factors = [
                row.get("diagnostics", {}).get("stereo_window_shape_factor")
                for row in group
            ]
            if not all(row.get("accepted", False) for row in group):
                pair_counts["refused"] += 1
            elif all(isinstance(factor, dict) for factor in factors):
                if factors[0] != factors[1]:
                    raise ValueError(f"{case.get('case')} accepted pair endpoints have different shape diagnostics")
                _count_factor(group[0], pair_counts)
            elif any(isinstance(factor, dict) for factor in factors):
                raise ValueError(f"{case.get('case')} accepted pair has partial shape diagnostic")
            else:
                pair_counts["missing"] += 1
    return {
        "endpoint_diagnostic_counts": endpoint_counts,
        "pair_group_counts": pair_counts,
    }


def annotate(output: Path, source_hashes: dict[str, str]) -> None:
    joint_path = output / "summary.json"
    independent_path = output / "independent_summary.json"
    joint = json.loads(joint_path.read_text())
    independent = json.loads(independent_path.read_text())
    _validate_pair_summaries(joint, independent)
    _verify_case_input_hashes(joint)
    _verify_case_input_hashes(independent)
    _check_source_hash_conflicts([joint, independent], source_hashes)
    shape_counts = _shape_factor_counts(joint)
    metadata = {
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "used_for_graph_or_selection": False,
        "available_for_graph": False,
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "instrumented_solver": "stereo_window_shape_factor.solve_with_shape_factor",
        "joint_pairs_per_case": 5,
        "endpoint_rows_per_case": 10,
        "policy": "fixed five time-stratified seam pairs on all ten cases; no GT/scoring/production trajectory",
        "joint_independent_input_decoded_identity_verified": True,
        **shape_counts,
        "source_sha256": {
            str(Path(__file__).resolve()): source_hashes[str(Path(__file__).resolve())],
            str(Path(shape.__file__).resolve()): source_hashes[str(Path(shape.__file__).resolve())],
        },
    }
    for summary, path, independent_flag in (
        (joint, joint_path, False),
        (independent, independent_path, True),
    ):
        _merge_source_hashes(summary, source_hashes)
        summary["shape_window_adapter"] = {**metadata, "independent_summary": independent_flag}
        path.write_text(json.dumps(summary, indent=2) + "\n")
    joint_after = json.loads(joint_path.read_text())
    independent_after = json.loads(independent_path.read_text())
    _verify_case_input_hashes(joint_after)
    _verify_case_input_hashes(independent_after)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError("refuse to overwrite shape controls")
    source_hashes = hash_sources()
    old_solve = seam.previous.solve
    old_argv = sys.argv[:]
    try:
        seam.previous.solve = solve_with_shape_factor
        sys.argv = [str(Path(seam.__file__).resolve()), "--output", str(args.output)]
        result = seam.main()
    finally:
        seam.previous.solve = old_solve
        sys.argv = old_argv
    verify_hashes(source_hashes)
    annotate(args.output, source_hashes)
    verify_hashes(source_hashes)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

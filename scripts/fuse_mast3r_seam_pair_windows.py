"""Inject full-seam paired metric factors into native MASt3R fusion."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuse_mast3r_metric_windows as metric
import fuse_mast3r_stereo_imu as native


METRIC_SOURCE = "stereo_window_full_seam_pair_v1"
FIXED_PENALTY_M = 0.004
EXPECTED_CASES = {
    "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4",
    "fresh1", "fresh2", "fresh3", "fresh4",
}
SCHEDULE_OFFSETS = (0, 5, 10, 15, 20)
EXPECTED_RAW_COUNTS = {
    "dev1": 1199,
    "dev2": 1200,
    "heldout1": 1199,
    "heldout2": 1199,
    "heldout3": 1199,
    "heldout4": 1199,
    "fresh1": 1199,
    "fresh2": 1199,
    "fresh3": 1199,
    "fresh4": 1199,
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_nonempty_hashes(hashes: dict, label: str) -> None:
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError(f"{label} hash map must be nonempty")
    metric.verify_hashes(hashes, label)


def validate_summary_contract(summary: dict, *, independent: bool) -> None:
    if summary.get("external_reference_used") is not False:
        raise ValueError("full-seam controls used external reference")
    verify_nonempty_hashes(summary.get("source_sha256", {}), "full-seam source")
    adapter = summary.get("full_seam_adapter", {})
    if not isinstance(adapter, dict) or adapter.get("raw_interval_frames") != 40:
        raise ValueError("full-seam adapter interval40 missing")
    if bool(adapter.get("independent_summary")) != bool(independent):
        raise ValueError("summary independent flag does not match mode")
    if adapter.get("correlated_paired_endpoints") is not True:
        raise ValueError("full-seam correlated paired endpoint flag missing")
    if adapter.get("calibrated_covariance") is not False:
        raise ValueError("full-seam calibrated covariance flag invalid")
    cases = summary.get("cases", [])
    names = [case.get("case") for case in cases]
    if len(cases) != 10 or set(names) != EXPECTED_CASES:
        raise ValueError("full-seam exact ten known cases required")
    counts = adapter.get("case_counts")
    if not isinstance(counts, list):
        raise ValueError("full-seam adapter case_counts exact ten required")
    by_name = {row.get("case"): row for row in counts}
    if len(counts) != 10 or set(by_name) != EXPECTED_CASES:
        raise ValueError("full-seam adapter case_counts exact ten mismatch")
    for name in EXPECTED_CASES:
        row = by_name[name]
        raw_count = EXPECTED_RAW_COUNTS[name]
        if row.get("pair_count") != 29:
            raise ValueError("full-seam adapter case_counts must record 29 pairs")
        if row.get("recording_raw_frame_count") != raw_count:
            raise ValueError("full-seam adapter case_counts raw frame count mismatch")
        if row.get("last_endpoint_index") != 1160:
            raise ValueError("full-seam adapter case_counts last endpoint mismatch")
        if row.get("uncovered_tail_frames_after_last_endpoint") != raw_count - 1 - 1160:
            raise ValueError("full-seam adapter case_counts tail frame mismatch")
        if row.get("joint_window_count", row.get("window_count", 58)) != 58:
            raise ValueError("full-seam adapter case_counts must record 58 windows")


def case_by_name(summary: dict, name: str, *, independent: bool) -> dict:
    validate_summary_contract(summary, independent=independent)
    matches = [case for case in summary.get("cases", []) if case.get("case") == name]
    if len(matches) != 1:
        raise ValueError("full-seam exact ten/case match failed")
    verify_nonempty_hashes(matches[0].get("input_sha256", {}), "full-seam case")
    return matches[0]


def full_schedule_rows(case: dict) -> list[dict]:
    rows = case.get("windows", [])
    if len(rows) != 58:
        raise ValueError("full-seam case must contain 58 endpoint rows")
    for row_index, row in enumerate(rows):
        pair = row_index // 2
        part = row_index % 2
        start = 40 * pair + 20 * part
        expected_indices = [start + offset for offset in SCHEDULE_OFFSETS]
        if row.get("window") != row_index + 1:
            raise ValueError("full-seam window id sequence mismatch")
        if row.get("joint_pair", pair + 1) != pair + 1:
            raise ValueError("full-seam joint_pair sequence mismatch")
        if row.get("indices") != expected_indices:
            raise ValueError("full-seam exact 40/20/5 endpoint schedule mismatch")
    for pair in range(29):
        first, second = rows[2 * pair], rows[2 * pair + 1]
        if first.get("joint_pair", pair + 1) != pair + 1 or second.get("joint_pair", pair + 1) != pair + 1:
            raise ValueError("full-seam joint_pair sequence mismatch")
        if first.get("indices", [None])[-1] != second.get("indices", [None])[0]:
            raise ValueError("full-seam pair endpoints do not share boundary")
    return rows


def accepted_factor(row: dict, times, case_name: str, pair_index: int, part: int, mode: str) -> dict | None:
    factor = metric.validate_accepted_row(row, times)
    if factor is None:
        return None
    factor.update({
        "metric_source": METRIC_SOURCE,
        "metric_displacement_frame": metric.STEREO_FRAME,
        "stereo_sigma_m": FIXED_PENALTY_M,
        "fixed_penalty_m": FIXED_PENALTY_M,
        "fixed_penalty_role": "regularization_not_stochastic_sigma",
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "full_seam_mode": mode,
    })
    if mode == "joint":
        factor.update({
            "correlated_factor_group_id": f"{case_name}:pair:{pair_index}",
            "correlated_factor_group_size": 2,
            "correlated_factor_group_part": part,
        })
    elif mode == "independent":
        factor.update({
            "pair_schedule_group_id": f"{case_name}:pair:{pair_index}",
            "pair_schedule_group_part": part,
            "separate_independent_solves": True,
            "independent_solve_group_size": 1,
        })
    else:
        raise ValueError(f"unsupported full-seam mode: {mode}")
    return factor


def build_pair_factors(case: dict, times, mode: str) -> tuple[list[dict], dict]:
    rows = full_schedule_rows(case)
    factors, partial_refusals = [], 0
    accepted_groups = 0
    independent_partial_pairs = 0
    for pair in range(29):
        pair_rows = rows[2 * pair: 2 * pair + 2]
        accepted = [bool(row.get("accepted")) for row in pair_rows]
        if mode == "joint" and any(accepted) and not all(accepted):
            raise ValueError("partial joint pair accepted/refused mix is invalid")
        if mode == "independent" and any(accepted) and not all(accepted):
            independent_partial_pairs += 1
        if all(accepted):
            accepted_groups += 1
        elif any(accepted):
            partial_refusals += 1
        for part, row in enumerate(pair_rows):
            factor = accepted_factor(row, times, case["case"], pair + 1, part, mode)
            if factor is not None:
                factors.append(factor)
    return factors, {
        "endpoint_factor_count": len(factors),
        "pair_level_group_count": 29,
        "accepted_pair_groups": accepted_groups,
        "partial_pair_groups": partial_refusals,
        "independent_control_partial_pairs": independent_partial_pairs,
    }


def confidence_dispatch(original):
    def confidence(observation: dict, reference_scale: float) -> float:
        if observation.get("metric_source") == METRIC_SOURCE:
            return 1.0
        return original(observation, reference_scale)
    return confidence


def manifest_path(report_path: Path) -> Path:
    return report_path.with_name(f"{report_path.stem}_full_seam_pair_factors.json")


def frozen_wrapper_hashes(controls: Path) -> dict[str, str]:
    return {
        str(controls.resolve()): file_sha256(controls),
        str(Path(__file__).resolve()): file_sha256(Path(__file__).resolve()),
        str(Path(native.__file__).resolve()): file_sha256(Path(native.__file__).resolve()),
        str(Path(metric.__file__).resolve()): file_sha256(Path(metric.__file__).resolve()),
    }


def verify_frozen_hashes(hashes: dict[str, str], label: str) -> None:
    for raw, expected in hashes.items():
        path = Path(raw)
        if file_sha256(path) != expected:
            raise ValueError(f"{label} changed during wrapper run: {path}")


def write_manifest(path: Path, controls: Path, case_name: str, mode: str, factors: list[dict], stats: dict) -> dict:
    payload = {
        "schema": "mast3r_full_seam_pair_factor_manifest_v1",
        "metric_source": METRIC_SOURCE,
        "mode": mode,
        "case": case_name,
        "controls": str(controls.resolve()),
        "controls_sha256": file_sha256(controls),
        "wrapper_sha256": file_sha256(Path(__file__).resolve()),
        "native_fusion_sha256": file_sha256(Path(native.__file__).resolve()),
        "factor_count": len(factors),
        "factors": factors,
        "factors_sha256": metric.canonical_sha256(factors),
        "fixed_penalty_m": FIXED_PENALTY_M,
        "fixed_penalty_role": "regularization_not_stochastic_sigma",
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "external_ground_truth_used": False,
        **stats,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def augment_report(report: dict, manifest: dict, path: Path) -> dict:
    out = dict(report)
    out["full_seam_pair_window_integration"] = {
        "schema": manifest["schema"],
        "metric_source": METRIC_SOURCE,
        "mode": manifest["mode"],
        "case": manifest["case"],
        "factor_count": manifest["factor_count"],
        "pair_level_group_count": manifest["pair_level_group_count"],
        "accepted_pair_groups": manifest["accepted_pair_groups"],
        "factors_sha256": manifest["factors_sha256"],
        "manifest": str(path.resolve()),
        "fixed_penalty_m": FIXED_PENALTY_M,
        "fixed_penalty_role": "regularization_not_stochastic_sigma",
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "external_ground_truth_used": False,
        "experiment_status": "research_fixedcost_not_promoted",
    }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--seam-window-controls", type=Path, required=True)
    parser.add_argument("--seam-window-case", required=True)
    parser.add_argument("--seam-window-mode", choices=("joint", "independent"), required=True)
    wrapper_args, native_argv = parser.parse_known_args(argv)
    native_paths = metric.parse_native_paths(native_argv)
    if native_paths.stream != "infrared_left":
        raise ValueError("full-seam fusion requires --stream infrared_left")
    if native_paths.output.exists() or native_paths.report.exists():
        raise ValueError("refuse to overwrite native outputs")
    sidecar = manifest_path(native_paths.report)
    if sidecar.exists():
        raise ValueError(f"refuse to overwrite full-seam manifest: {sidecar}")

    summary = load_json(wrapper_args.seam_window_controls)
    frozen_hashes = frozen_wrapper_hashes(wrapper_args.seam_window_controls)
    verify_frozen_hashes(frozen_hashes, "full-seam wrapper/source")
    case = case_by_name(summary, wrapper_args.seam_window_case, independent=wrapper_args.seam_window_mode == "independent")
    source_report = metric.source_report_from_case(case)
    times = metric.validate_timestamps(native_paths.trajectory, source_report)
    factors, stats = build_pair_factors(case, times, wrapper_args.seam_window_mode)

    original_merge = native.merge_stereo_reports
    original_confidence = native.stereo_observation_confidence
    original_run = native.run
    original_argv = sys.argv[:]

    def merge_with_seam_pairs(primary: dict, additions: list[dict]) -> dict:
        metric.compare_source_report(primary, source_report)
        merged = original_merge(primary, additions)
        merged["observations"] = list(merged.get("observations", [])) + factors
        return merged

    def run_with_manifest(args):
        report = original_run(args)
        manifest = write_manifest(sidecar, wrapper_args.seam_window_controls, wrapper_args.seam_window_case,
                                  wrapper_args.seam_window_mode, factors, stats)
        augmented = augment_report(report, manifest, sidecar)
        args.report.write_text(json.dumps(augmented, indent=2) + "\n", encoding="utf-8")
        return augmented

    try:
        native.merge_stereo_reports = merge_with_seam_pairs
        native.stereo_observation_confidence = confidence_dispatch(original_confidence)
        native.run = run_with_manifest
        sys.argv = [str(Path(native.__file__).resolve())] + native_argv
        verify_frozen_hashes(frozen_hashes, "full-seam wrapper/source")
        code = native.main()
        verify_nonempty_hashes(summary.get("source_sha256", {}), "full-seam source")
        verify_nonempty_hashes(case.get("input_sha256", {}), "full-seam case")
        verify_frozen_hashes(frozen_hashes, "full-seam wrapper/source")
        return code
    finally:
        native.merge_stereo_reports = original_merge
        native.stereo_observation_confidence = original_confidence
        native.run = original_run
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())

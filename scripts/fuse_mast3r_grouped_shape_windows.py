"""Compose full-seam endpoint factors with diagnostic grouped-shape LSQR rows.

This wrapper is intentionally isolated.  It delegates factor injection and the
native CLI to ``fuse_mast3r_seam_pair_windows`` unchanged, and only wraps one
native ``refine_positions_visual_inertial`` call so
``stereo_window_shape_refiner`` can replace same-source endpoint rows with one
correlated diagnostic group row per accepted 9-node seam pair.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".planning/metric_window_bundle_20260928"))

import fuse_mast3r_seam_pair_windows as seam_wrapper
import stereo_window_shape_refiner as shape_refiner
import run_full_shape_controls as full_shape


SHAPE_GROUP_SOURCE = seam_wrapper.METRIC_SOURCE
MANIFEST_SCHEMA = "mast3r_grouped_shape_window_refinement_v1"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_hash(target: dict[str, str], path: Path, digest: str) -> None:
    key = str(Path(path).resolve())
    if key in target and target[key] != digest:
        raise ValueError(f"grouped-shape hash conflict: {key}")
    target[key] = digest


def _source_hashes(*, shape_controls: Path, independent_controls: Path, seam_controls: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for raw, digest in full_shape.hash_sources().items():
        _merge_hash(hashes, Path(raw), digest)
    for path in (
        Path(__file__).resolve(),
        Path(shape_refiner.__file__).resolve(),
        Path(shape_refiner.shape_graph.__file__).resolve(),
        Path(shape_refiner.shape_system.__file__).resolve(),
        Path(seam_wrapper.__file__).resolve(),
        Path(seam_wrapper.metric.__file__).resolve(),
        Path(seam_wrapper.native.__file__).resolve(),
        shape_controls,
        independent_controls,
        seam_controls,
    ):
        _merge_hash(hashes, path, seam_wrapper.file_sha256(Path(path)))
    return hashes


def _verify_hashes(hashes: dict[str, str]) -> None:
    for raw, expected in hashes.items():
        path = Path(raw)
        if seam_wrapper.file_sha256(path) != expected:
            raise ValueError(f"grouped-shape source/control changed: {path}")


def _validate_shape_summaries(joint: dict, independent: dict) -> dict[str, dict]:
    counts = full_shape._validate_pair_summaries(joint, independent)
    full_shape.shape_adapter._verify_case_input_hashes(joint)
    full_shape.shape_adapter._verify_case_input_hashes(independent)
    expected_sources = full_shape.hash_sources()
    if joint.get("source_sha256") != expected_sources or independent.get("source_sha256") != expected_sources:
        raise ValueError("full-shape source hash closure mismatch")
    full_shape.shape_adapter._check_source_hash_conflicts([joint, independent], expected_sources)
    for summary, independent_flag in ((joint, False), (independent, True)):
        adapter = summary.get("full_shape_window_adapter")
        if not isinstance(adapter, dict):
            raise ValueError("full-shape adapter metadata required")
        required_false = (
            "external_ground_truth_used",
            "used_for_graph_or_selection",
            "available_for_graph",
            "calibrated_covariance",
            "statistical_independence_claimed",
            "production_modified",
        )
        if (
            adapter.get("diagnostic_only") is not True
            or any(adapter.get(key) is not False for key in required_false)
            or adapter.get("joint_pairs_per_case") != 29
            or adapter.get("endpoint_rows_per_case") != 58
            or adapter.get("independent_summary") is not independent_flag
            or adapter.get("case_counts") != counts
        ):
            raise ValueError("full-shape adapter metadata invalid")
    return counts


def _case_by_name(summary: dict, name: str) -> dict:
    matches = [case for case in summary.get("cases", []) if case.get("case") == name]
    if len(matches) != 1:
        raise ValueError(f"grouped-shape case match count is {len(matches)}")
    return matches[0]


def _shape_factor(row: dict, case_name: str, pair: int) -> dict:
    factor = row.get("diagnostics", {}).get("stereo_window_shape_factor")
    if not isinstance(factor, dict):
        raise ValueError(f"{case_name} pair {pair} accepted shape diagnostic missing")
    if factor.get("available") is not True:
        raise ValueError(f"{case_name} pair {pair} accepted shape diagnostic unavailable")
    return factor


def _build_shape_groups(case: dict) -> tuple[list[dict], list[dict]]:
    groups: list[dict] = []
    refusals: list[dict] = []
    windows = case.get("windows", [])
    pairs = case.get("pairs", [])
    if len(windows) != 58 or len(pairs) != 29:
        raise ValueError("full-shape case must contain 29 pairs / 58 windows")
    for pair_index in range(29):
        pair_number = pair_index + 1
        pair_row = pairs[pair_index]
        expected_indices = full_shape.expected_pair_schedule()[pair_index]
        indices9 = pair_row.get("indices")
        if (
            not isinstance(indices9, list)
            or len(indices9) != 9
            or any(isinstance(value, bool) or not isinstance(value, int) for value in indices9)
            or indices9 != expected_indices
        ):
            raise ValueError(f"{case['case']} pair {pair_number} indices9 schedule mismatch")
        pair_accepted = pair_row.get("accepted")
        if not isinstance(pair_accepted, bool):
            raise ValueError(f"{case['case']} pair {pair_number} accepted must be literal bool")
        first, second = windows[2 * pair_index: 2 * pair_index + 2]
        endpoint_accepted = [first.get("accepted"), second.get("accepted")]
        if any(not isinstance(value, bool) for value in endpoint_accepted):
            raise ValueError(f"{case['case']} pair {pair_number} endpoint accepted must be literal bool")
        if first.get("indices") != indices9[:5] or second.get("indices") != indices9[4:]:
            raise ValueError(f"{case['case']} pair {pair_number} endpoint indices do not match schedule")
        if pair_accepted != endpoint_accepted[0] or pair_accepted != endpoint_accepted[1]:
            raise ValueError(f"{case['case']} pair {pair_number} pair/window accepted mismatch")
        if any(endpoint_accepted) and not all(endpoint_accepted):
            raise ValueError(f"{case['case']} pair {pair_number} partial accepted shape group")
        if not pair_accepted:
            refusals.append(
                {
                    "pair": pair_number,
                    "indices": indices9,
                    "endpoint_indices": [first.get("indices"), second.get("indices")],
                    "reasons": [first.get("reason"), second.get("reason")],
                }
            )
            continue
        first_factor = _shape_factor(first, case["case"], pair_number)
        second_factor = _shape_factor(second, case["case"], pair_number)
        if first_factor != second_factor:
            raise ValueError(f"{case['case']} pair {pair_number} accepted endpoints have different shape diagnostics")
        groups.append(
            {
                "group_id": f"{case['case']}:pair:{pair_number}",
                "indices9": indices9,
                "factor": first_factor,
            }
        )
    return groups, refusals


def _compare_old_seam_case(old_case: dict, shape_case: dict) -> None:
    if old_case.get("input_sha256") != shape_case.get("input_sha256"):
        raise ValueError("old seam and full-shape selected input maps differ")
    old_rows = seam_wrapper.full_schedule_rows(old_case)
    shape_rows = shape_case.get("windows", [])
    if len(old_rows) != 58 or len(shape_rows) != 58:
        raise ValueError("old seam and full-shape selected windows must contain 58 rows")
    for index, (old, new) in enumerate(zip(old_rows, shape_rows), 1):
        for key in ("accepted", "reason", "indices", "endpoint_m"):
            if old.get(key) != new.get(key):
                raise ValueError(f"old seam and full-shape row {index} {key} differ")


def _old_wrapper_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--seam-window-controls", type=Path, required=True)
    parser.add_argument("--seam-window-case", required=True)
    parser.add_argument("--seam-window-mode", choices=("joint", "independent"), required=True)
    args, _ = parser.parse_known_args(argv)
    if args.seam_window_mode != "joint":
        raise ValueError("grouped-shape wrapper composes old seam joint mode only")
    return args


def _native_position_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--position-mode", default="full")
    args, _ = parser.parse_known_args(argv)
    if args.position_mode not in {"joint-inertial", "keyframe-graph"}:
        raise ValueError("grouped-shape wrapper requires native --position-mode joint-inertial or keyframe-graph")
    return args


def _manifest_path(report: Path) -> Path:
    return report.with_name(f"{report.stem}_grouped_shape_windows.json")


def _write_manifest(path: Path, controls: Path, independent: Path, seam_controls: Path, case_name: str, mode: str, groups: list[dict], refusals: list[dict], hashes: dict[str, str], call_report: dict) -> dict:
    payload = {
        "schema": MANIFEST_SCHEMA,
        "case": case_name,
        "shape_window_mode": mode,
        "metric_source": SHAPE_GROUP_SOURCE,
        "controls": str(controls.resolve()),
        "independent_controls": str(independent.resolve()),
        "seam_window_controls": str(seam_controls.resolve()),
        "group_count": len(groups),
        "refused_pair_count": len(refusals),
        "refused_pairs": refusals,
        "group_ids": [group["group_id"] for group in groups],
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_modified": False,
        "used_for_selection": False,
        "used_for_production_graph": False,
        "used_for_research_graph": True,
        "available_for_production": False,
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "shape_refinement_scope": "actual_returned_camera_shape_evaluation_only",
        "refiner_call_count": int(call_report.get("count", 0)),
        "source_sha256": hashes,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _augment_report(report_path: Path, manifest_path: Path, manifest: dict) -> None:
    report = load_json(report_path)
    report["grouped_shape_window_refinement"] = {
        "schema": manifest["schema"],
        "case": manifest["case"],
        "shape_window_mode": manifest["shape_window_mode"],
        "metric_source": manifest["metric_source"],
        "group_count": manifest["group_count"],
        "refused_pair_count": manifest["refused_pair_count"],
        "manifest": str(manifest_path.resolve()),
        "diagnostic_only": True,
        "external_ground_truth_used": False,
        "production_modified": False,
        "used_for_selection": False,
        "used_for_production_graph": False,
        "used_for_research_graph": True,
        "available_for_production": False,
        "calibrated_covariance": False,
        "statistical_independence_claimed": False,
        "shape_refinement_scope": "actual_returned_camera_shape_evaluation_only",
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--shape-window-controls", type=Path, required=True)
    parser.add_argument("--shape-window-independent-controls", type=Path, required=True)
    parser.add_argument("--shape-window-mode", choices=(shape_refiner.ENDPOINT_CONTROL_MODE, shape_refiner.GROUPED_SHAPE_MODE), required=True)
    shape_args, old_argv = parser.parse_known_args(argv)
    old_args = _old_wrapper_args(old_argv)
    _native_position_args(old_argv)
    native_paths = seam_wrapper.metric.parse_native_paths(old_argv)
    manifest_path = _manifest_path(native_paths.report)
    if manifest_path.exists():
        raise ValueError(f"refuse to overwrite grouped-shape manifest: {manifest_path}")

    joint = load_json(shape_args.shape_window_controls)
    independent = load_json(shape_args.shape_window_independent_controls)
    _validate_shape_summaries(joint, independent)
    old_summary = load_json(old_args.seam_window_controls)
    shape_case = _case_by_name(joint, old_args.seam_window_case)
    old_case = seam_wrapper.case_by_name(old_summary, old_args.seam_window_case, independent=False)
    _compare_old_seam_case(old_case, shape_case)
    groups, refusals = _build_shape_groups(shape_case)
    if not groups:
        raise ValueError("grouped-shape wrapper requires at least one accepted shape group")

    frozen_hashes = _source_hashes(
        shape_controls=shape_args.shape_window_controls,
        independent_controls=shape_args.shape_window_independent_controls,
        seam_controls=old_args.seam_window_controls,
    )
    _verify_hashes(frozen_hashes)
    full_shape.shape_adapter._verify_case_input_hashes(joint)
    full_shape.shape_adapter._verify_case_input_hashes(independent)
    seam_wrapper.verify_nonempty_hashes(old_case.get("input_sha256", {}), "old seam case")

    native = seam_wrapper.native
    original_refine = native.refine_positions_visual_inertial
    original_argv = sys.argv[:]
    call_report = {"count": 0}
    expected_native_hash = frozen_hashes[str(Path(native.__file__).resolve())]

    def refine_with_shape_hook(*args, **kwargs):
        call_report["count"] += 1
        if call_report["count"] != 1:
            raise ValueError("grouped-shape wrapper must intercept exactly one native refine call")
        native.refine_positions_visual_inertial = original_refine
        try:
            return shape_refiner.refine_positions_visual_inertial_with_shape(
                native,
                groups,
                shape_args.shape_window_mode,
                *args,
                expected_native_sha256=expected_native_hash,
                **kwargs,
            )
        finally:
            native.refine_positions_visual_inertial = refine_with_shape_hook

    try:
        native.refine_positions_visual_inertial = refine_with_shape_hook
        code = seam_wrapper.main(old_argv)
        if call_report["count"] != 1:
            raise ValueError("grouped-shape wrapper must intercept exactly one native refine call")
    finally:
        native.refine_positions_visual_inertial = original_refine
        sys.argv = original_argv

    _verify_hashes(frozen_hashes)
    full_shape.shape_adapter._verify_case_input_hashes(joint)
    full_shape.shape_adapter._verify_case_input_hashes(independent)
    seam_wrapper.verify_nonempty_hashes(old_case.get("input_sha256", {}), "old seam case")
    manifest = _write_manifest(
        manifest_path,
        shape_args.shape_window_controls,
        shape_args.shape_window_independent_controls,
        old_args.seam_window_controls,
        old_args.seam_window_case,
        shape_args.shape_window_mode,
        groups,
        refusals,
        frozen_hashes,
        call_report,
    )
    _augment_report(native_paths.report, manifest_path, manifest)
    _verify_hashes(frozen_hashes)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Corrected read-only audit of the symmetric-IR stereo body lever.

This intentionally reuses the current adapter's stereo merge policy and
confidence function.  It does not read external reference trajectories or score
against ground truth.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import fuse_mast3r_stereo_imu as fusion  # noqa: E402


CORPUS = ROOT / ".planning" / "dual_ir_regression_25_20261002"
OUT = Path(__file__).resolve().parent / "stereo_lever_geometry_audit_v2.json"
CONFIDENCE_ATOL = 1e-12

BATCH_IDS = [
    "20260927_dev1", "20260927_dev2", "20260927_heldout1", "20260927_heldout2",
    "20260927_heldout3", "20260927_heldout4", "20260927_ind1", "20260927_ind3",
    "20260927_ind4", "20260929_take01", "20260929_take03", "20260929_take06",
    "20260929_take07", "20260929_take08", "20260929_take09", "20260930_take02",
    "20260930_take04", "20260930_take05", "20260930_take06",
]
NEW5 = [
    "20260927_ind2", "20260929_take02", "20260929_take04",
    "20260930_take01", "20260930_take03",
]

LEFT_REPORT_NAMES = {
    "stereo_scale_bidirectional_report.json",
    "stereo_scale_long_hops_report.json",
    "stereo_scale_dense10hz_report.json",
    "stereo_scale_multisecond_report.json",
}
RIGHT_REPORT_NAMES = {
    "stereo_scale_right_report.json",
    "stereo_scale_long_hops_right_report.json",
    "stereo_scale_dense10hz_right_report.json",
    "stereo_scale_multisecond_right_report.json",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50_m": None, "p95_m": None, "max_m": None}
    array = np.asarray(values, dtype=float)
    return {
        "n": int(array.size),
        "p50_m": float(np.percentile(array, 50)),
        "p95_m": float(np.percentile(array, 95)),
        "max_m": float(np.max(array)),
    }


def candidate_dirs() -> list[tuple[str, str, Path]]:
    return (
        [("batch_v1", rid, CORPUS / "batch_v1" / rid / "both") for rid in BATCH_IDS]
        + [
            ("adapter_smoke_v2", rid, CORPUS / "adapter_smoke_v2" / rid / "both")
            for rid in NEW5
        ]
    )


def load_body_rotations(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    times = np.asarray([float(row["t_sec"]) for row in rows], dtype=float)
    quat_xyzw = np.asarray(
        [[float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])] for row in rows],
        dtype=float,
    )
    return times, Rotation.from_quat(quat_xyzw).as_matrix()


def nearest_index(times: np.ndarray, value: float) -> int | None:
    index = int(np.searchsorted(times, value))
    candidates = []
    if index < times.size:
        candidates.append(index)
    if index > 0:
        candidates.append(index - 1)
    if not candidates:
        return None
    best = min(candidates, key=lambda item: abs(float(times[item]) - value))
    if abs(float(times[best]) - value) > 0.010:
        return None
    return best


def report_paths(paths: list[Path], eye: str) -> list[Path]:
    names = LEFT_REPORT_NAMES if eye == "left" else RIGHT_REPORT_NAMES
    found = {path.name: path for path in paths if path.name in names}
    missing = sorted(names - set(found))
    if missing:
        raise ValueError(f"{eye} missing stereo reports: {missing}")
    return [found[name] for name in sorted(names)]


def ordered_reports(paths: list[Path], eye: str) -> list[dict[str, Any]]:
    if eye == "left":
        order = [
            "stereo_scale_bidirectional_report.json",
            "stereo_scale_long_hops_report.json",
            "stereo_scale_dense10hz_report.json",
            "stereo_scale_multisecond_report.json",
        ]
    else:
        order = [
            "stereo_scale_right_report.json",
            "stereo_scale_long_hops_right_report.json",
            "stereo_scale_dense10hz_right_report.json",
            "stereo_scale_multisecond_right_report.json",
        ]
    by_name = {path.name: path for path in report_paths(paths, eye)}
    reports = []
    for name in order:
        path = by_name[name]
        report = fusion.load_json_report(path)
        report["report_path"] = str(path.resolve())
        reports.append(report)
    return reports


def load_merged_stereo(
    paths: list[Path], eye: str, optional_policy: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reports = ordered_reports(paths, eye)
    merged = fusion.merge_stereo_reports(
        reports[0], reports[1:], optional_policy=optional_policy
    )
    return merged, reports


def selected_factors(local_factors: list[dict[str, Any]]) -> dict[tuple[int, int, str], dict[str, Any]]:
    selected: dict[tuple[int, int, str], dict[str, Any]] = {}
    for factor in local_factors:
        key = (
            int(factor["first_index"]),
            int(factor["second_index"]),
            str(factor["eye"]),
        )
        if key in selected:
            raise ValueError(f"duplicate stored local factor {key}")
        selected[key] = factor
    return selected


def physical_body_i_delta(
    observation: dict[str, Any],
    body_t_camera: np.ndarray,
    rotations: np.ndarray,
    first_reference: int,
    second_reference: int,
) -> np.ndarray:
    body_r_camera = body_t_camera[:3, :3]
    lever = body_t_camera[:3, 3]
    camera_delta = np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
    return (
        body_r_camera @ camera_delta
        - rotations[first_reference].T @ rotations[second_reference] @ lever
        + lever
    )


def gather_raw_candidates(
    *,
    manifest: dict[str, Any],
    input_paths: list[Path],
    eye: str,
    optional_policy: str,
    reference_times: np.ndarray,
    rotations: np.ndarray,
    selected: dict[tuple[int, int, str], dict[str, Any]],
) -> tuple[dict[tuple[int, int, str], list[dict[str, Any]]], dict[str, Any]]:
    merged, reports = load_merged_stereo(input_paths, eye, optional_policy)
    merged_scale = float(merged["scale_m_per_mast3r_unit"])
    body_t_camera = np.asarray(
        manifest["eye_reports"][eye]["effective_body_T_camera"], dtype=float
    )
    by_factor: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    considered = 0
    accepted = 0
    for observation in merged.get("observations", []):
        considered += 1
        if observation.get("accepted") is not True:
            continue
        accepted += 1
        first_reference = nearest_index(reference_times, float(observation["first_t_sec"]))
        second_reference = nearest_index(reference_times, float(observation["second_t_sec"]))
        if first_reference is None or second_reference is None:
            continue
        key = (first_reference, second_reference, eye)
        factor = selected.get(key)
        if factor is None:
            continue
        confidence = fusion.stereo_observation_confidence(observation, merged_scale)
        if abs(confidence - float(factor["own_observation_confidence"])) > CONFIDENCE_ATOL:
            continue
        by_factor[key].append(
            {
                "confidence": confidence,
                "physical_body_i_delta": physical_body_i_delta(
                    observation, body_t_camera, rotations, first_reference, second_reference
                ),
                "raw_body_i_delta": np.asarray(
                    observation["metric_displacement_camera_i_m"], dtype=float
                ),
            }
        )
    diagnostic = {
        "merged_scale_m_per_mast3r_unit": merged_scale,
        "merged_report_paths": list(merged.get("merged_report_paths", [])),
        "merged_report_count": int(merged.get("merged_report_count", len(reports))),
        "optional_report_rejections": list(merged.get("optional_report_rejections", [])),
        "merged_observation_count": considered,
        "merged_accepted_observation_count": accepted,
    }
    return by_factor, diagnostic


def exact_selected_eye_candidates(
    raw: dict[tuple[int, int, str], list[dict[str, Any]]],
    selected: dict[tuple[int, int, str], dict[str, Any]],
) -> tuple[dict[tuple[int, int, str], dict[str, Any]], list[dict[str, Any]]]:
    exact: dict[tuple[int, int, str], dict[str, Any]] = {}
    mismatches = []
    for key, factor in selected.items():
        matches = raw.get(key, [])
        if not matches:
            mismatches.append({"key": list(key), "reason": "missing_matching_raw_candidate"})
            continue
        deltas = np.asarray([match["physical_body_i_delta"] for match in matches], dtype=float)
        if deltas.ndim != 2 or deltas.shape[1] != 3:
            mismatches.append({"key": list(key), "reason": "invalid_candidate_delta_shape"})
            continue
        if np.max(np.linalg.norm(deltas - deltas[0], axis=1)) > 1e-15:
            mismatches.append(
                {
                    "key": list(key),
                    "reason": "ambiguous_same_confidence_raw_candidates",
                    "candidate_count": len(matches),
                    "factor_own_observation_confidence": factor["own_observation_confidence"],
                }
            )
            continue
        exact[key] = {
            "confidence": float(factor["own_observation_confidence"]),
            "own_confidence": float(factor["own_confidence"]),
            "physical_body_i_delta": deltas[0],
            "raw_match_count": len(matches),
        }
    return exact, mismatches


def choose_shared_physical_delta(
    row: dict[str, Any],
    exact: dict[tuple[int, int, str], dict[str, Any]],
    stereo_weight_policy: str,
) -> tuple[np.ndarray | None, dict[str, Any] | None]:
    pair = (int(row["first_index"]), int(row["second_index"]))
    candidates = [
        (eye, exact[(pair[0], pair[1], eye)])
        for eye in ("left", "right")
        if (pair[0], pair[1], eye) in exact
    ]
    if not candidates:
        return None, {"pair": list(pair), "reason": "missing_selected_eye_candidates"}
    if stereo_weight_policy == "observation":
        key = "confidence"
    elif stereo_weight_policy == "residual-aware":
        key = "own_confidence"
    else:
        raise ValueError(f"unsupported stereo_weight_policy {stereo_weight_policy}")
    best = max(candidate[key] for _, candidate in candidates)
    tied = [
        (eye, candidate)
        for eye, candidate in candidates
        if np.isclose(candidate[key], best, rtol=0.0, atol=0.0)
    ]
    expected_row_confidence = float(row["pnp_inlier_ratio"])
    if abs(expected_row_confidence - best) > CONFIDENCE_ATOL:
        return None, {
            "pair": list(pair),
            "reason": "shared_row_confidence_mismatch",
            "row_pnp_inlier_ratio": expected_row_confidence,
            "reconstructed_best_confidence": float(best),
            "policy": stereo_weight_policy,
            "candidate_eyes": [eye for eye, _ in candidates],
        }
    tied = sorted(tied, key=lambda item: item[0])
    delta = np.mean(
        np.asarray([candidate["physical_body_i_delta"] for _, candidate in tied], dtype=float),
        axis=0,
    )
    return delta, {
        "pair": list(pair),
        "confidence": float(best),
        "tie_count": len(tied),
        "eyes": [eye for eye, _ in tied],
        "raw_match_counts": {
            eye: int(candidate["raw_match_count"]) for eye, candidate in tied
        },
    }


def audit_record(source: str, rid: str, directory: Path) -> dict[str, Any]:
    manifest_path = directory / "candidate_manifest.json"
    shared_path = directory / "shared_stereo_observations.json"
    local_path = directory / "local_motion_factors.json"
    body_path = directory / "body_trajectory_fused.csv"
    manifest = json.loads(manifest_path.read_text())
    input_paths = [Path(path) for path in manifest["input_sha256"]]
    reference_times, rotations = load_body_rotations(body_path)
    shared_rows = json.loads(shared_path.read_text())
    local_factors = json.loads(local_path.read_text())
    policy = manifest.get("policy_arguments", {})
    optional_policy = policy.get("optional_stereo_policy", "strict")
    stereo_weight_policy = policy.get("stereo_weight_policy", "observation")
    selected = selected_factors(local_factors)
    raw_all: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    eye_diagnostics = {}
    for eye in ("left", "right"):
        raw_eye, eye_diag = gather_raw_candidates(
            manifest=manifest,
            input_paths=input_paths,
            eye=eye,
            optional_policy=optional_policy,
            reference_times=reference_times,
            rotations=rotations,
            selected=selected,
        )
        eye_diagnostics[eye] = eye_diag
        for key, candidates in raw_eye.items():
            raw_all[key].extend(candidates)
    exact, exact_mismatches = exact_selected_eye_candidates(raw_all, selected)
    diffs = []
    old_norms = []
    new_norms = []
    row_mismatches = []
    tie_rows = 0
    raw_duplicate_match_rows = 0
    examples = []
    for row in shared_rows:
        new_delta, selection = choose_shared_physical_delta(row, exact, stereo_weight_policy)
        if new_delta is None:
            row_mismatches.append(selection)
            continue
        old_delta = np.asarray(row["metric_displacement_camera_i_m"], dtype=float)
        diff = float(np.linalg.norm(new_delta - old_delta))
        diffs.append(diff)
        old_norms.append(float(np.linalg.norm(old_delta)))
        new_norms.append(float(np.linalg.norm(new_delta)))
        tie_rows += int(selection["tie_count"] > 1)
        raw_duplicate_match_rows += int(max(selection["raw_match_counts"].values()) > 1)
        if len(examples) < 3 and diff > 0.001:
            examples.append(
                {
                    "pair": selection["pair"],
                    "diff_m": diff,
                    "old_body_i_delta": old_delta.tolist(),
                    "physical_vins_relative_body_i_delta": new_delta.tolist(),
                    "selection": selection,
                }
            )
    failure_count = len(exact_mismatches) + len(row_mismatches)
    return {
        "id": rid,
        "source": source,
        "candidate_dir": str(directory),
        "policy_arguments": policy,
        "inputs": {
            "candidate_manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
            "shared_stereo_observations": {"path": str(shared_path), "sha256": sha256(shared_path)},
            "local_motion_factors": {"path": str(local_path), "sha256": sha256(local_path)},
            "body_trajectory_fused": {"path": str(body_path), "sha256": sha256(body_path)},
        },
        "external_ground_truth_used": manifest.get("external_ground_truth_used"),
        "slam_supervision": manifest.get("slam_supervision"),
        "eye_merge_diagnostics": eye_diagnostics,
        "shared_rows": len(shared_rows),
        "local_factor_count": len(local_factors),
        "matched_rows": len(diffs),
        "selection_tie_rows": tie_rows,
        "raw_duplicate_match_rows": raw_duplicate_match_rows,
        "selection_failure_count": failure_count,
        "selection_failures": (exact_mismatches + row_mismatches)[:20],
        "_delta_change_values_m": diffs,
        "delta_change_norm": stats(diffs),
        "old_delta_norm": stats(old_norms),
        "physical_vins_relative_delta_norm": stats(new_norms),
        "examples_over_1mm": examples,
    }


def main() -> None:
    records = [audit_record(source, rid, directory) for source, rid, directory in candidate_dirs()]
    failed = [record["id"] for record in records if record["selection_failure_count"]]
    all_diffs: list[float] = []
    for record in records:
        all_diffs.extend(record.pop("_delta_change_values_m"))
    summary = {
        "records": len(records),
        "records_with_selection_failures": failed,
        "total_shared_rows": int(sum(record["shared_rows"] for record in records)),
        "total_matched_rows": int(sum(record["matched_rows"] for record in records)),
        "all_delta_change_norm": stats(all_diffs),
        "records_with_optional_rejections": [
            record["id"]
            for record in records
            if any(
                record["eye_merge_diagnostics"][eye]["optional_report_rejections"]
                for eye in ("left", "right")
            )
        ],
        "records_with_selection_ties": [
            record["id"] for record in records if record["selection_tie_rows"]
        ],
        "records_with_raw_duplicate_matches": [
            record["id"] for record in records if record["raw_duplicate_match_rows"]
        ],
    }
    result = {
        "schema": "stereo_lever_geometry_audit_v2",
        "rule": (
            "Reconstruct exact current stereo observation selection using "
            "merge_stereo_reports(optional_policy), stereo_observation_confidence(merged_scale), "
            "and stored local factor own-confidence metadata; then compare stored body_i "
            "stereo delta with VINS-relative physical lever recomputation. No GT."
        ),
        "confidence_assertion_atol": CONFIDENCE_ATOL,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "records": records,
        "summary": summary,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(OUT)
    if failed:
        raise SystemExit(f"selection reconstruction failed for {failed}")


if __name__ == "__main__":
    main()

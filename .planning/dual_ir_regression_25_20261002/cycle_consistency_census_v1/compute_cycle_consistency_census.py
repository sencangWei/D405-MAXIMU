#!/usr/bin/env python3
"""Onboard-only cycle-consistency census for existing dual-IR adapter factors.

This script reads frozen ``batch_adapters_v2/*/both`` products only.  It does
not read score/reference files, does not run MASt3R/VINS/GPU, and does not
modify any candidate product.  The only output is the requested census JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))


def metric_stats_m(values_m: list[float]) -> dict[str, Any]:
    values = [float(value) for value in values_m]
    values_mm = [1000.0 * value for value in values]
    return {
        "count": len(values),
        "p50_mm": percentile(values_mm, 50),
        "p90_mm": percentile(values_mm, 90),
        "p95_mm": percentile(values_mm, 95),
        "p99_mm": percentile(values_mm, 99),
        "max_mm": max(values_mm) if values_mm else None,
    }


def make_pair_map(factors: list[dict[str, Any]], eye: str) -> dict[tuple[int, int], np.ndarray]:
    pair_map: dict[tuple[int, int], np.ndarray] = {}
    for factor in factors:
        if factor.get("eye") != eye:
            continue
        first = int(factor["first_index"])
        second = int(factor["second_index"])
        if second <= first:
            raise ValueError(f"non-forward factor for {eye}: {first}->{second}")
        vector = np.asarray(factor["metric_displacement_world_m"], dtype=float)
        if vector.shape != (3,) or not np.all(np.isfinite(vector)):
            raise ValueError(f"invalid displacement for {eye}: {first}->{second}")
        if (first, second) in pair_map:
            raise ValueError(f"duplicate factor for {eye}: {first}->{second}")
        pair_map[(first, second)] = vector
    return pair_map


def cycle_rows(pair_map: dict[tuple[int, int], np.ndarray]) -> list[dict[str, Any]]:
    by_start: dict[int, list[int]] = {}
    for first, second in pair_map:
        by_start.setdefault(first, []).append(second)
    rows: list[dict[str, Any]] = []
    for first in sorted(by_start):
        for middle in sorted(by_start[first]):
            for second in sorted(by_start.get(middle, [])):
                if (first, second) not in pair_map:
                    continue
                residual = (
                    pair_map[(first, middle)]
                    + pair_map[(middle, second)]
                    - pair_map[(first, second)]
                )
                rows.append(
                    {
                        "first_index": first,
                        "middle_index": middle,
                        "second_index": second,
                        "first_hop_frames": middle - first,
                        "second_hop_frames": second - middle,
                        "long_hop_frames": second - first,
                        "closure_error_m": float(np.linalg.norm(residual)),
                        "closure_error_vector_m": [float(value) for value in residual],
                    }
                )
    return rows


def summarize_cycles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "triangles": len(rows),
        "closure_error": metric_stats_m([row["closure_error_m"] for row in rows]),
        "long_hop_frames": {
            "count": len(rows),
            "p50": percentile([float(row["long_hop_frames"]) for row in rows], 50),
            "p95": percentile([float(row["long_hop_frames"]) for row in rows], 95),
            "max": max((int(row["long_hop_frames"]) for row in rows), default=None),
        },
    }


def paired_cycle_rows(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    left_by_key = {
        (row["first_index"], row["middle_index"], row["second_index"]): row
        for row in left_rows
    }
    right_by_key = {
        (row["first_index"], row["middle_index"], row["second_index"]): row
        for row in right_rows
    }
    rows: list[dict[str, Any]] = []
    for key in sorted(left_by_key.keys() & right_by_key.keys()):
        left = left_by_key[key]
        right = right_by_key[key]
        left_vec = np.asarray(left["closure_error_vector_m"], dtype=float)
        right_vec = np.asarray(right["closure_error_vector_m"], dtype=float)
        rows.append(
            {
                "first_index": key[0],
                "middle_index": key[1],
                "second_index": key[2],
                "first_hop_frames": left["first_hop_frames"],
                "second_hop_frames": left["second_hop_frames"],
                "long_hop_frames": left["long_hop_frames"],
                "left_closure_error_m": left["closure_error_m"],
                "right_closure_error_m": right["closure_error_m"],
                "max_eye_closure_error_m": max(
                    left["closure_error_m"], right["closure_error_m"]
                ),
                "lr_closure_vector_disagreement_m": float(
                    np.linalg.norm(left_vec - right_vec)
                ),
            }
        )
    return rows


def summarize_paired(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "paired_triangles": len(rows),
        "left_closure_error": metric_stats_m(
            [row["left_closure_error_m"] for row in rows]
        ),
        "right_closure_error": metric_stats_m(
            [row["right_closure_error_m"] for row in rows]
        ),
        "max_eye_closure_error": metric_stats_m(
            [row["max_eye_closure_error_m"] for row in rows]
        ),
        "lr_closure_vector_disagreement": metric_stats_m(
            [row["lr_closure_vector_disagreement_m"] for row in rows]
        ),
    }


def top_rows(rows: list[dict[str, Any]], key: str, limit: int) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: float(row[key]), reverse=True)[:limit]


def record_report(record_dir: Path, top_limit: int) -> dict[str, Any]:
    both_dir = record_dir / "both"
    required = {
        "candidate_manifest": both_dir / "candidate_manifest.json",
        "graph_report": both_dir / "graph_report.json",
        "local_motion_factors": both_dir / "local_motion_factors.json",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        return {
            "record_id": record_dir.name,
            "status": "SKIPPED_MISSING_BOTH_PRODUCT",
            "missing": missing,
        }

    manifest = load_json(required["candidate_manifest"])
    graph_report = load_json(required["graph_report"])
    factors = load_json(required["local_motion_factors"])
    if not isinstance(factors, list):
        raise ValueError(f"{required['local_motion_factors']} is not a list")

    left_pairs = make_pair_map(factors, "left")
    right_pairs = make_pair_map(factors, "right")
    left_cycles = cycle_rows(left_pairs)
    right_cycles = cycle_rows(right_pairs)
    paired_cycles = paired_cycle_rows(left_cycles, right_cycles)
    graph_solver = graph_report.get("joint_position_solver", {})

    return {
        "record_id": record_dir.name,
        "status": "PASS_CENSUS_COMPUTED",
        "paths": {
            name: str(path.resolve()) for name, path in required.items()
        },
        "product_hashes": {
            name: sha256_file(path) for name, path in required.items()
        },
        "declared_input_sha256": manifest.get("input_sha256", {}),
        "no_supervision_flags": {
            "manifest_external_ground_truth_used": manifest.get(
                "external_ground_truth_used"
            ),
            "manifest_slam_supervision": manifest.get("slam_supervision"),
            "graph_external_ground_truth_used": graph_report.get(
                "external_ground_truth_used"
            ),
            "graph_slam_supervision": graph_report.get("slam_supervision"),
            "joint_metric_scale_external_ground_truth_used": (
                graph_solver.get("joint_metric_scale", {}).get(
                    "external_ground_truth_used"
                )
            ),
            "secondary_visual_external_ground_truth_used": (
                graph_solver.get("secondary_visual_motion", {}).get(
                    "external_ground_truth_used"
                )
            ),
        },
        "policy_arguments": graph_report.get("policy_arguments", {}),
        "factor_counts": {
            "left": len(left_pairs),
            "right": len(right_pairs),
            "total": len(factors),
        },
        "by_eye": {
            "left": summarize_cycles(left_cycles),
            "right": summarize_cycles(right_cycles),
        },
        "paired_cycle_summary": summarize_paired(paired_cycles),
        "paired_cycle_rows": paired_cycles,
        "top_paired_cycles_by_max_eye_closure": top_rows(
            paired_cycles, "max_eye_closure_error_m", top_limit
        ),
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    computed = [record for record in records if record["status"] == "PASS_CENSUS_COMPUTED"]
    skipped = [record for record in records if record["status"] != "PASS_CENSUS_COMPUTED"]
    ranking = []
    for record in computed:
        ranking.append(
            {
                "record_id": record["record_id"],
                "left_p95_mm": record["by_eye"]["left"]["closure_error"]["p95_mm"],
                "left_max_mm": record["by_eye"]["left"]["closure_error"]["max_mm"],
                "right_p95_mm": record["by_eye"]["right"]["closure_error"]["p95_mm"],
                "right_max_mm": record["by_eye"]["right"]["closure_error"]["max_mm"],
                "paired_max_eye_p95_mm": record["paired_cycle_summary"][
                    "max_eye_closure_error"
                ]["p95_mm"],
                "paired_max_eye_max_mm": record["paired_cycle_summary"][
                    "max_eye_closure_error"
                ]["max_mm"],
                "paired_triangles": record["paired_cycle_summary"][
                    "paired_triangles"
                ],
            }
        )
    ranking.sort(
        key=lambda row: (
            row["paired_max_eye_p95_mm"]
            if row["paired_max_eye_p95_mm"] is not None
            else -1.0
        ),
        reverse=True,
    )
    return {
        "computed_records": len(computed),
        "skipped_records": len(skipped),
        "skipped": skipped,
        "ranking_by_paired_max_eye_closure_p95": ranking,
        "no_gt_or_slam_supervision_all_computed": all(
            flags.get("manifest_external_ground_truth_used") is False
            and flags.get("manifest_slam_supervision") is False
            and flags.get("graph_external_ground_truth_used") is False
            and flags.get("graph_slam_supervision") is False
            for flags in (record["no_supervision_flags"] for record in computed)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-root",
        type=Path,
        default=Path(
            "/home/robot/ego_vio_humble/.planning/dual_ir_regression_25_20261002/batch_adapters_v2"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/home/robot/ego_vio_humble/.planning/dual_ir_regression_25_20261002/cycle_consistency_census_v1/cycle_consistency_census.json"
        ),
    )
    parser.add_argument("--top-limit", type=int, default=10)
    args = parser.parse_args()
    if args.top_limit <= 0:
        raise ValueError("--top-limit must be positive")
    records = [
        record_report(record_dir, args.top_limit)
        for record_dir in sorted(path for path in args.batch_root.iterdir() if path.is_dir())
    ]
    report = {
        "schema": "dual_ir_cycle_consistency_census_v1",
        "scope": "existing_batch_adapters_v2_both_local_motion_factors_only",
        "reads_ground_truth_or_score": False,
        "runs_frontend_or_solver": False,
        "batch_root": str(args.batch_root.resolve()),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "records": records,
        "summary": aggregate(records),
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "computed_records": report["summary"]["computed_records"],
                "skipped_records": report["summary"]["skipped_records"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

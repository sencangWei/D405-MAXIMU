#!/usr/bin/env python3
"""Read-only onboard duration reliability census for the dual-IR 25 corpus.

This script intentionally reads only existing adapter artifacts:
candidate_manifest.json, local_motion_factors.json, shared_stereo_observations.json,
and body_trajectory_fused.csv. It does not read precision/GT scoring files and it
does not run any frontend, GPU, fusion, or scoring command.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_ROOT = REPO_ROOT / ".planning" / "dual_ir_regression_25_20261002"
OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_PATH = OUTPUT_DIR / "duration_reliability_census_v1.json"

BATCH_IDS = [
    "20260927_dev1",
    "20260927_dev2",
    "20260927_heldout1",
    "20260927_heldout2",
    "20260927_heldout3",
    "20260927_heldout4",
    "20260927_ind1",
    "20260927_ind3",
    "20260927_ind4",
    "20260929_take01",
    "20260929_take03",
    "20260929_take06",
    "20260929_take07",
    "20260929_take08",
    "20260929_take09",
    "20260930_take02",
    "20260930_take04",
    "20260930_take05",
    "20260930_take06",
]

ADAPTER_SMOKE_NEW_IDS = [
    "20260927_ind2",
    "20260929_take02",
    "20260929_take04",
    "20260930_take01",
    "20260930_take03",
]

RULE = {
    "schema": "duration_reliability_census_v1",
    "supervision": "onboard_only_no_precision_no_gt",
    "candidate_count": 24,
    "formal_confidence_field": "confidence",
    "diagnostic_confidence_field": "own_observation_confidence",
    "duration_layer": "duration_sec >= 1.0",
    "window_membership": "abs(pair_midtime_sec - body_frame_time_sec) <= 1.0",
    "window_half_width_sec": 1.0,
    "min_paired_edges": 8,
    "joint_bad_definition": (
        "left.own_stereo_residual_m > 0.010 and "
        "right.own_stereo_residual_m > 0.010"
    ),
    "joint_bad_fraction_threshold_exclusive": 0.25,
    "weighted_residual_p95_m_threshold_exclusive": 0.015,
    "raw_delta_world_rotation_source": (
        "nearest unchanged body_trajectory_fused.csv quaternion at first_t_sec"
    ),
    "notes": [
        "Formal rule uses final factor confidence because that is the solver input.",
        "own_observation_confidence is emitted only as a diagnostic contrast.",
        "No GT/precision/scoring files are opened by this script.",
    ],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def percentile(values: list[float], pct: float) -> float | None:
    finite = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not finite:
        return None
    x = (len(finite) - 1) * pct / 100.0
    lo = math.floor(x)
    hi = math.ceil(x)
    if lo == hi:
        return finite[lo]
    return finite[lo] * (hi - x) + finite[hi] * (x - lo)


def stats_m(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50_m": None, "p95_m": None, "max_m": None}
    return {
        "n": len(values),
        "p50_m": percentile(values, 50),
        "p95_m": percentile(values, 95),
        "max_m": max(values),
    }


def qrot(q_wxyz: tuple[float, float, float, float], vector: np.ndarray) -> np.ndarray:
    w, x, y, z = q_wxyz
    vx, vy, vz = vector
    t = np.array(
        [
            2.0 * (y * vz - z * vy),
            2.0 * (z * vx - x * vz),
            2.0 * (x * vy - y * vx),
        ],
        dtype=float,
    )
    return vector + w * t + np.cross(np.array([x, y, z], dtype=float), t)


def nearest_quat(
    times: list[float],
    quats: list[tuple[float, float, float, float]],
    query_time: float,
) -> tuple[float, float, float, float]:
    idx = bisect_left(times, query_time)
    if idx <= 0:
        return quats[0]
    if idx >= len(times):
        return quats[-1]
    before = times[idx - 1]
    after = times[idx]
    return quats[idx - 1] if abs(before - query_time) <= abs(after - query_time) else quats[idx]


def weighted_residual_m(
    left_delta: np.ndarray,
    right_delta: np.ndarray,
    raw_world_delta: np.ndarray,
    left_weight: float,
    right_weight: float,
) -> float:
    left_weight = max(float(left_weight), 0.0)
    right_weight = max(float(right_weight), 0.0)
    if left_weight + right_weight > 0.0:
        learned = (left_delta * left_weight + right_delta * right_weight) / (
            left_weight + right_weight
        )
    else:
        learned = (left_delta + right_delta) / 2.0
    return float(np.linalg.norm(learned - raw_world_delta))


def candidate_specs() -> list[dict[str, str]]:
    specs = []
    for record_id in BATCH_IDS:
        specs.append(
            {
                "record_id": record_id,
                "source": "batch_v1",
                "candidate_dir": str(CORPUS_ROOT / "batch_v1" / record_id / "both"),
            }
        )
    for record_id in ADAPTER_SMOKE_NEW_IDS:
        specs.append(
            {
                "record_id": record_id,
                "source": "adapter_smoke_v2_new5",
                "candidate_dir": str(CORPUS_ROOT / "adapter_smoke_v2" / record_id / "both"),
            }
        )
    return specs


def load_body(path: Path) -> tuple[list[float], list[tuple[float, float, float, float]]]:
    times: list[float] = []
    quats: list[tuple[float, float, float, float]] = []
    with path.open() as f:
        for row in csv.DictReader(f):
            times.append(float(row["t_sec"]))
            quats.append(
                (
                    float(row["qw"]),
                    float(row["qx"]),
                    float(row["qy"]),
                    float(row["qz"]),
                )
            )
    return times, quats


def build_edges(
    factors: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    body_times: list[float],
    body_quats: list[tuple[float, float, float, float]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    obs_by_pair = {
        (int(o["first_index"]), int(o["second_index"])): o
        for o in observations
        if o.get("accepted", True)
    }
    factors_by_pair: dict[tuple[int, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    duplicate_pair_eye = 0
    bad_eye = 0
    for factor in factors:
        eye = factor.get("eye")
        if eye not in ("left", "right"):
            bad_eye += 1
            continue
        pair = (int(factor["first_index"]), int(factor["second_index"]))
        if eye in factors_by_pair[pair]:
            duplicate_pair_eye += 1
        factors_by_pair[pair][eye] = factor

    edges = []
    missing_observation = 0
    unpaired_factor = 0
    nonpositive_duration = 0
    for pair, pair_factors in factors_by_pair.items():
        if "left" not in pair_factors or "right" not in pair_factors:
            unpaired_factor += 1
            continue
        if pair not in obs_by_pair:
            missing_observation += 1
            continue
        observation = obs_by_pair[pair]
        first_t = float(observation["first_t_sec"])
        second_t = float(observation["second_t_sec"])
        duration = second_t - first_t
        if not math.isfinite(duration) or duration <= 0.0:
            nonpositive_duration += 1
            continue

        raw_body = np.array(observation["metric_displacement_camera_i_m"], dtype=float)
        raw_world = qrot(nearest_quat(body_times, body_quats, first_t), raw_body)

        left = pair_factors["left"]
        right = pair_factors["right"]
        left_delta = np.array(left["metric_displacement_world_m"], dtype=float)
        right_delta = np.array(right["metric_displacement_world_m"], dtype=float)
        left_own_res = float(left["own_stereo_residual_m"])
        right_own_res = float(right["own_stereo_residual_m"])

        final_residual = weighted_residual_m(
            left_delta,
            right_delta,
            raw_world,
            float(left.get("confidence", 1.0)),
            float(right.get("confidence", 1.0)),
        )
        own_conf_residual = weighted_residual_m(
            left_delta,
            right_delta,
            raw_world,
            float(left.get("own_observation_confidence", left.get("confidence", 1.0))),
            float(right.get("own_observation_confidence", right.get("confidence", 1.0))),
        )
        lr_diff = float(np.linalg.norm(left_delta - right_delta))
        left_norm = float(np.linalg.norm(left_delta))
        right_norm = float(np.linalg.norm(right_delta))
        lr_cos = (
            float(np.dot(left_delta, right_delta) / (left_norm * right_norm))
            if left_norm > 1e-12 and right_norm > 1e-12
            else None
        )
        left_residual_vec = left_delta - raw_world
        right_residual_vec = right_delta - raw_world
        left_residual_norm = float(np.linalg.norm(left_residual_vec))
        right_residual_norm = float(np.linalg.norm(right_residual_vec))
        residual_dir_cos = (
            float(
                np.dot(left_residual_vec, right_residual_vec)
                / (left_residual_norm * right_residual_norm)
            )
            if left_residual_norm > 1e-12 and right_residual_norm > 1e-12
            else None
        )

        edges.append(
            {
                "pair": [pair[0], pair[1]],
                "first_t_sec": first_t,
                "second_t_sec": second_t,
                "mid_t_sec": (first_t + second_t) / 2.0,
                "duration_sec": duration,
                "joint_bad": left_own_res > 0.010 and right_own_res > 0.010,
                "left_own_stereo_residual_m": left_own_res,
                "right_own_stereo_residual_m": right_own_res,
                "own_max_residual_m": max(left_own_res, right_own_res),
                "weighted_residual_final_confidence_m": final_residual,
                "weighted_residual_own_observation_confidence_m": own_conf_residual,
                "lr_diff_m": lr_diff,
                "lr_cos": lr_cos,
                "residual_dir_cos": residual_dir_cos,
                "raw_norm_m": float(np.linalg.norm(raw_world)),
                "left_norm_m": left_norm,
                "right_norm_m": right_norm,
            }
        )

    checks = {
        "observation_pairs": len(obs_by_pair),
        "factor_pairs": len(factors_by_pair),
        "paired_edges": len(edges),
        "duplicate_pair_eye": duplicate_pair_eye,
        "bad_eye": bad_eye,
        "missing_observation": missing_observation,
        "unpaired_factor": unpaired_factor,
        "nonpositive_duration": nonpositive_duration,
    }
    return edges, checks


def window_summary(edges: list[dict[str, Any]], center_time: float, field: str) -> dict[str, Any]:
    members = [
        edge
        for edge in edges
        if edge["duration_sec"] >= 1.0 and abs(edge["mid_t_sec"] - center_time) <= 1.0
    ]
    joint_bad_fraction = (
        sum(1 for edge in members if edge["joint_bad"]) / len(members) if members else None
    )
    weighted_stats = stats_m([edge[field] for edge in members])
    hit = (
        len(members) >= RULE["min_paired_edges"]
        and joint_bad_fraction is not None
        and joint_bad_fraction > RULE["joint_bad_fraction_threshold_exclusive"]
        and weighted_stats["p95_m"] is not None
        and weighted_stats["p95_m"] > RULE["weighted_residual_p95_m_threshold_exclusive"]
    )
    return {
        "center_t_sec": center_time,
        "edge_count": len(members),
        "joint_bad_count": sum(1 for edge in members if edge["joint_bad"]),
        "joint_bad_fraction": joint_bad_fraction,
        "weighted_residual": weighted_stats,
        "own_max_residual": stats_m([edge["own_max_residual_m"] for edge in members]),
        "lr_diff": stats_m([edge["lr_diff_m"] for edge in members]),
        "lr_cos_p05": percentile(
            [edge["lr_cos"] for edge in members if edge["lr_cos"] is not None],
            5,
        ),
        "lr_cos_p50": percentile(
            [edge["lr_cos"] for edge in members if edge["lr_cos"] is not None],
            50,
        ),
        "residual_dir_cos_p50": percentile(
            [
                edge["residual_dir_cos"]
                for edge in members
                if edge["residual_dir_cos"] is not None
            ],
            50,
        ),
        "duration": stats_m([edge["duration_sec"] for edge in members]),
        "hit": hit,
    }


def compact_window(window: dict[str, Any], first_body_t: float) -> dict[str, Any]:
    return {
        "center_rel_sec": window["center_t_sec"] - first_body_t,
        "edge_count": window["edge_count"],
        "joint_bad_count": window["joint_bad_count"],
        "joint_bad_fraction": window["joint_bad_fraction"],
        "weighted_residual_p95_m": window["weighted_residual"]["p95_m"],
        "weighted_residual_p50_m": window["weighted_residual"]["p50_m"],
        "weighted_residual_max_m": window["weighted_residual"]["max_m"],
        "own_max_residual_p95_m": window["own_max_residual"]["p95_m"],
        "lr_diff_p95_m": window["lr_diff"]["p95_m"],
        "lr_cos_p05": window["lr_cos_p05"],
        "lr_cos_p50": window["lr_cos_p50"],
        "residual_dir_cos_p50": window["residual_dir_cos_p50"],
        "duration_p50_sec": window["duration"]["p50_m"],
        "duration_p95_sec": window["duration"]["p95_m"],
    }


def hit_intervals(hit_windows: list[dict[str, Any]], first_body_t: float) -> list[dict[str, float]]:
    if not hit_windows:
        return []
    centers = sorted(window["center_t_sec"] for window in hit_windows)
    intervals = []
    start = prev = centers[0]
    for center in centers[1:]:
        if center - prev > 0.08:
            intervals.append({"start_rel_sec": start - first_body_t, "end_rel_sec": prev - first_body_t})
            start = center
        prev = center
    intervals.append({"start_rel_sec": start - first_body_t, "end_rel_sec": prev - first_body_t})
    return intervals


def analyze_candidate(spec: dict[str, str]) -> dict[str, Any]:
    candidate_dir = Path(spec["candidate_dir"])
    input_files = {
        "candidate_manifest": candidate_dir / "candidate_manifest.json",
        "local_motion_factors": candidate_dir / "local_motion_factors.json",
        "shared_stereo_observations": candidate_dir / "shared_stereo_observations.json",
        "body_trajectory_fused": candidate_dir / "body_trajectory_fused.csv",
    }
    inputs = {
        key: {
            "path": str(path),
            "resolved_path": str(path.resolve()),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for key, path in input_files.items()
    }

    manifest = read_json(input_files["candidate_manifest"])
    factors = read_json(input_files["local_motion_factors"])
    observations = read_json(input_files["shared_stereo_observations"])
    body_times, body_quats = load_body(input_files["body_trajectory_fused"])
    edges, schema_checks = build_edges(factors, observations, body_times, body_quats)
    long_edges = [edge for edge in edges if edge["duration_sec"] >= 1.0]

    final_windows = [
        window_summary(edges, center, "weighted_residual_final_confidence_m")
        for center in body_times
    ]
    own_conf_windows = [
        window_summary(edges, center, "weighted_residual_own_observation_confidence_m")
        for center in body_times
    ]
    final_hits = [window for window in final_windows if window["hit"]]
    own_conf_hits = [window for window in own_conf_windows if window["hit"]]
    final_eligible = [window for window in final_windows if window["edge_count"] >= 8]
    own_conf_eligible = [window for window in own_conf_windows if window["edge_count"] >= 8]

    def max_by(windows: list[dict[str, Any]], key_path: tuple[str, ...]) -> dict[str, Any] | None:
        candidates = [window for window in windows if window["edge_count"] >= 8]
        if not candidates:
            return None

        def get(window: dict[str, Any]) -> float:
            item: Any = window
            for key in key_path:
                item = item[key]
            return float(item) if item is not None else float("-inf")

        return compact_window(max(candidates, key=get), body_times[0])

    final_marked_edges = [
        edge
        for edge in long_edges
        if any(abs(edge["mid_t_sec"] - window["center_t_sec"]) <= 1.0 for window in final_hits)
    ]
    own_marked_edges = [
        edge
        for edge in long_edges
        if any(abs(edge["mid_t_sec"] - window["center_t_sec"]) <= 1.0 for window in own_conf_hits)
    ]

    return {
        "record_id": spec["record_id"],
        "source": spec["source"],
        "candidate_dir": spec["candidate_dir"],
        "inputs": inputs,
        "unsupervised_flags": {
            "external_ground_truth_used": manifest.get("external_ground_truth_used"),
            "slam_supervision": manifest.get("slam_supervision"),
        },
        "session": manifest.get("session"),
        "schema_checks": schema_checks,
        "body_frames": len(body_times),
        "duration_layer_counts": {
            "all_paired_edges": len(edges),
            "duration_lt_0p2": sum(1 for edge in edges if edge["duration_sec"] < 0.2),
            "duration_0p2_to_lt_1": sum(
                1 for edge in edges if 0.2 <= edge["duration_sec"] < 1.0
            ),
            "duration_gte_1": len(long_edges),
        },
        "long_layer_full_run": {
            "joint_bad_fraction": (
                sum(1 for edge in long_edges if edge["joint_bad"]) / len(long_edges)
                if long_edges
                else None
            ),
            "own_max_residual": stats_m([edge["own_max_residual_m"] for edge in long_edges]),
            "lr_diff": stats_m([edge["lr_diff_m"] for edge in long_edges]),
            "weighted_residual_final_confidence": stats_m(
                [edge["weighted_residual_final_confidence_m"] for edge in long_edges]
            ),
            "weighted_residual_own_observation_confidence": stats_m(
                [edge["weighted_residual_own_observation_confidence_m"] for edge in long_edges]
            ),
        },
        "formal_final_confidence": {
            "eligible_window_count": len(final_eligible),
            "hit_window_count": len(final_hits),
            "hit_intervals": hit_intervals(final_hits, body_times[0]),
            "marked_long_edge_count": len(final_marked_edges),
            "marked_long_joint_bad_fraction": (
                sum(1 for edge in final_marked_edges if edge["joint_bad"]) / len(final_marked_edges)
                if final_marked_edges
                else None
            ),
            "marked_weighted_residual": stats_m(
                [edge["weighted_residual_final_confidence_m"] for edge in final_marked_edges]
            ),
            "max_joint_bad_window": max_by(final_windows, ("joint_bad_fraction",)),
            "max_weighted_p95_window": max_by(final_windows, ("weighted_residual", "p95_m")),
            "top_hit_windows": [
                compact_window(window, body_times[0])
                for window in sorted(
                    final_hits,
                    key=lambda w: (
                        w["joint_bad_fraction"] or -1.0,
                        w["weighted_residual"]["p95_m"] or -1.0,
                    ),
                    reverse=True,
                )[:5]
            ],
        },
        "diagnostic_own_observation_confidence": {
            "eligible_window_count": len(own_conf_eligible),
            "hit_window_count": len(own_conf_hits),
            "hit_intervals": hit_intervals(own_conf_hits, body_times[0]),
            "marked_long_edge_count": len(own_marked_edges),
            "marked_long_joint_bad_fraction": (
                sum(1 for edge in own_marked_edges if edge["joint_bad"]) / len(own_marked_edges)
                if own_marked_edges
                else None
            ),
            "marked_weighted_residual": stats_m(
                [
                    edge["weighted_residual_own_observation_confidence_m"]
                    for edge in own_marked_edges
                ]
            ),
            "max_joint_bad_window": max_by(own_conf_windows, ("joint_bad_fraction",)),
            "max_weighted_p95_window": max_by(
                own_conf_windows,
                ("weighted_residual", "p95_m"),
            ),
            "top_hit_windows": [
                compact_window(window, body_times[0])
                for window in sorted(
                    own_conf_hits,
                    key=lambda w: (
                        w["joint_bad_fraction"] or -1.0,
                        w["weighted_residual"]["p95_m"] or -1.0,
                    ),
                    reverse=True,
                )[:5]
            ],
        },
    }


def main() -> None:
    records = [analyze_candidate(spec) for spec in candidate_specs()]
    duplicate_ids = sorted(
        record_id
        for record_id in {record["record_id"] for record in records}
        if sum(1 for record in records if record["record_id"] == record_id) > 1
    )
    formal_hits = [
        record["record_id"]
        for record in records
        if record["formal_final_confidence"]["hit_window_count"] > 0
    ]
    own_conf_hits = [
        record["record_id"]
        for record in records
        if record["diagnostic_own_observation_confidence"]["hit_window_count"] > 0
    ]
    target_ids = ["20260927_heldout2", "20260927_ind1", "20260927_ind3", "20260930_take06"]
    result = {
        "schema": "duration_reliability_census_result_v1",
        "generated_by": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "rule": RULE,
        "corpus": {
            "root": str(CORPUS_ROOT),
            "batch_v1_ids": BATCH_IDS,
            "adapter_smoke_v2_new_ids": ADAPTER_SMOKE_NEW_IDS,
            "record_count": len(records),
            "duplicate_record_ids": duplicate_ids,
        },
        "summary": {
            "formal_final_confidence_hit_record_ids": formal_hits,
            "diagnostic_own_observation_confidence_hit_record_ids": own_conf_hits,
            "target_record_outcomes": {
                record["record_id"]: {
                    "formal_hit_windows": record["formal_final_confidence"]["hit_window_count"],
                    "diagnostic_own_conf_hit_windows": record[
                        "diagnostic_own_observation_confidence"
                    ]["hit_window_count"],
                    "formal_max_joint_bad_window": record["formal_final_confidence"][
                        "max_joint_bad_window"
                    ],
                    "formal_max_weighted_p95_window": record["formal_final_confidence"][
                        "max_weighted_p95_window"
                    ],
                }
                for record in records
                if record["record_id"] in target_ids
            },
            "schema_or_index_error_records": [
                record["record_id"]
                for record in records
                if any(
                    record["schema_checks"][key]
                    for key in (
                        "duplicate_pair_eye",
                        "bad_eye",
                        "missing_observation",
                        "nonpositive_duration",
                    )
                )
            ],
        },
        "records": records,
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    print(f"wrote {RESULT_PATH}")


if __name__ == "__main__":
    main()

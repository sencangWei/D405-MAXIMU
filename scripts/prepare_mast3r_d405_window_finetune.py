#!/usr/bin/env python3
"""Add long temporal pairs solved from D405 stereo edges and onboard IMU.

The source temporal manifest contains independently verified short stereo/IMU
motions.  This tool robustly solves one translation graph per connected motion
segment, then derives 0.8--1.6 second endpoint labels from that graph.  No
Lighthouse, robot, or SLAM trajectory is read.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import sys

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import lsqr
from scipy.spatial.transform import Rotation


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from prepare_mast3r_d405_temporal_finetune import (  # noqa: E402
    compose_camera_rotation,
    temporal_motion_bin,
)


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def connected_components(samples: list[dict]) -> list[list[dict]]:
    parent: dict[int, int] = {}

    def find(node: int) -> int:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(first: int, second: int) -> None:
        root_first = find(first)
        root_second = find(second)
        if root_first != root_second:
            parent[root_second] = root_first

    for sample in samples:
        union(int(sample["first_input_index"]), int(sample["second_input_index"]))
    grouped: dict[int, list[dict]] = defaultdict(list)
    for sample in samples:
        grouped[find(int(sample["first_input_index"]))].append(sample)
    return list(grouped.values())


def rejected_source_sample_ids(
    grouped: dict[str, list[dict]], component_reports: list[dict]
) -> set[int]:
    rejected = {
        (report["session_id"], report["first_input_index"], report["last_input_index"])
        for report in component_reports
        if not report["accepted_for_long_windows"]
    }
    sample_ids = set()
    for session_id, session_samples in grouped.items():
        for component in connected_components(session_samples):
            nodes = [
                int(sample[key])
                for sample in component
                for key in ("first_input_index", "second_input_index")
            ]
            if (session_id, min(nodes), max(nodes)) in rejected:
                sample_ids.update(id(sample) for sample in component)
    return sample_ids


def solve_component_positions(
    samples: list[dict], priors: list[dict[str, str]], iterations: int = 4
) -> tuple[dict[int, np.ndarray], dict]:
    """Solve metric camera positions from local stereo translations."""
    nodes = sorted(
        {
            int(sample[key])
            for sample in samples
            for key in ("first_input_index", "second_input_index")
        }
    )
    if len(nodes) < 4:
        raise ValueError("window component needs at least four nodes")
    anchor = nodes[0]
    variable = {node: index for index, node in enumerate(nodes[1:])}
    anchor_to_node = {
        node: compose_camera_rotation(priors, anchor, node)
        if node != anchor
        else Rotation.identity()
        for node in nodes
    }
    edge_deltas = []
    for sample in samples:
        first = int(sample["first_input_index"])
        pose = np.asarray(sample["camera_pose_second"], dtype=float)
        edge_deltas.append(anchor_to_node[first].apply(pose[:3, 3]))
    edge_deltas = np.asarray(edge_deltas)
    weights = np.ones(len(samples), dtype=float)

    def solve(weight: np.ndarray) -> np.ndarray:
        row_indices = []
        column_indices = []
        values = []
        for row, sample in enumerate(samples):
            first = int(sample["first_input_index"])
            second = int(sample["second_input_index"])
            scale = float(np.sqrt(weight[row]))
            if first != anchor:
                row_indices.append(row)
                column_indices.append(variable[first])
                values.append(-scale)
            if second != anchor:
                row_indices.append(row)
                column_indices.append(variable[second])
                values.append(scale)
        matrix = coo_matrix(
            (values, (row_indices, column_indices)),
            shape=(len(samples), len(nodes) - 1),
        ).tocsr()
        positions = np.zeros((len(nodes), 3), dtype=float)
        for axis in range(3):
            target = edge_deltas[:, axis] * np.sqrt(weight)
            positions[1:, axis] = lsqr(matrix, target, atol=1e-10, btol=1e-10)[0]
        return positions

    positions = solve(weights)
    node_offset = {node: index for index, node in enumerate(nodes)}
    residuals = np.zeros(len(samples), dtype=float)
    for _ in range(iterations):
        for edge, sample in enumerate(samples):
            first = node_offset[int(sample["first_input_index"])]
            second = node_offset[int(sample["second_input_index"])]
            residuals[edge] = np.linalg.norm(
                positions[second] - positions[first] - edge_deltas[edge]
            )
        robust_scale = max(0.001, 1.4826 * float(np.median(residuals)))
        huber = 2.5 * robust_scale
        weights = np.minimum(1.0, huber / np.maximum(residuals, 1e-12))
        positions = solve(weights)

    for edge, sample in enumerate(samples):
        first = node_offset[int(sample["first_input_index"])]
        second = node_offset[int(sample["second_input_index"])]
        residuals[edge] = np.linalg.norm(
            positions[second] - positions[first] - edge_deltas[edge]
        )

    solved = {node: positions[index] for index, node in enumerate(nodes)}
    return solved, {
        "nodes": len(nodes),
        "edges": len(samples),
        "edge_residual_median_m": float(np.median(residuals)),
        "edge_residual_p95_m": float(np.percentile(residuals, 95)),
        "robust_edge_inliers": int(np.count_nonzero(weights >= 0.999)),
    }


def outlier_source_sample_ids(
    grouped: dict[str, list[dict]],
    priors_by_session: dict[str, list[dict[str, str]]],
    maximum_edge_residual_m: float,
) -> set[int]:
    sample_ids = set()
    for session_id, session_samples in grouped.items():
        priors = priors_by_session[session_id]
        for component in connected_components(session_samples):
            if len(component) < 3:
                continue
            try:
                positions, _ = solve_component_positions(component, priors)
            except ValueError:
                continue
            anchor = min(positions)
            anchor_to_node = {anchor: Rotation.identity()}
            for sample in component:
                first = int(sample["first_input_index"])
                second = int(sample["second_input_index"])
                if first not in anchor_to_node:
                    anchor_to_node[first] = compose_camera_rotation(
                        priors, anchor, first
                    )
                delta = anchor_to_node[first].apply(
                    np.asarray(sample["camera_pose_second"], dtype=float)[:3, 3]
                )
                residual = np.linalg.norm(positions[second] - positions[first] - delta)
                if residual > maximum_edge_residual_m:
                    sample_ids.add(id(sample))
    return sample_ids


def endpoint_metadata(samples: list[dict]) -> dict[int, dict]:
    result = {}
    for sample in samples:
        first = int(sample["first_input_index"])
        second = int(sample["second_input_index"])
        result.setdefault(
            first,
            {
                "image": sample["first_image"],
                "depth": sample["depth_first"],
                "timestamp_s": float(sample.get("first_timestamp_s", 0.0)),
                "intrinsics": sample["intrinsics"],
                "split": sample["split"],
            },
        )
        result.setdefault(
            second,
            {
                "image": sample["second_image"],
                "depth": sample["depth_second"],
                "timestamp_s": float(sample.get("second_timestamp_s", 0.0)),
                "intrinsics": sample["intrinsics"],
                "split": sample["split"],
            },
        )
    # Version-1 temporal manifests store only duration, not endpoint times.
    # Recover one consistent relative timeline from every accepted edge.
    known = {node: data["timestamp_s"] for node, data in result.items() if data["timestamp_s"]}
    if not known:
        anchor = min(result)
        result[anchor]["timestamp_s"] = 0.0
    changed = True
    while changed:
        changed = False
        for sample in samples:
            first = int(sample["first_input_index"])
            second = int(sample["second_input_index"])
            duration = float(sample["duration_s"])
            first_time = result[first]["timestamp_s"]
            second_time = result[second]["timestamp_s"]
            if first_time or first == min(result):
                candidate = first_time + duration
                if not second_time:
                    result[second]["timestamp_s"] = candidate
                    changed = True
            elif second_time:
                result[first]["timestamp_s"] = second_time - duration
                changed = True
    return result


def build_long_window_samples(
    source_samples: list[dict],
    priors: list[dict[str, str]],
    minimum_duration_s: float,
    maximum_duration_s: float,
    target_durations_s: tuple[float, ...],
    maximum_pairs: int,
    maximum_component_edge_residual_p95_m: float = 0.005,
) -> tuple[list[dict], list[dict]]:
    long_samples = []
    component_reports = []
    for component in connected_components(source_samples):
        if len(component) < 3:
            continue
        try:
            positions, report = solve_component_positions(component, priors)
        except ValueError:
            continue
        metadata = endpoint_metadata(component)
        nodes = sorted(positions)
        node_times = np.asarray([metadata[node]["timestamp_s"] for node in nodes])
        report["first_input_index"] = nodes[0]
        report["last_input_index"] = nodes[-1]
        report["accepted_for_long_windows"] = bool(
            report["edge_residual_p95_m"]
            <= maximum_component_edge_residual_p95_m
        )
        component_reports.append(report)
        if not report["accepted_for_long_windows"]:
            continue
        candidates = set()
        for first_offset, first in enumerate(nodes[:-1]):
            for target_duration in target_durations_s:
                target_time = node_times[first_offset] + target_duration
                second_offset = int(np.searchsorted(node_times, target_time, side="left"))
                options = [
                    index
                    for index in (second_offset - 1, second_offset)
                    if first_offset < index < len(nodes)
                ]
                if not options:
                    continue
                second_offset = min(
                    options, key=lambda index: abs(node_times[index] - target_time)
                )
                second = nodes[second_offset]
                duration = float(node_times[second_offset] - node_times[first_offset])
                if minimum_duration_s <= duration <= maximum_duration_s:
                    candidates.add((first, second))

        for first, second in sorted(candidates):
            duration = float(metadata[second]["timestamp_s"] - metadata[first]["timestamp_s"])
            supporting = [
                sample
                for sample in component
                if first <= int(sample["first_input_index"])
                and int(sample["second_input_index"]) <= second
            ]
            if len(supporting) < 3:
                continue
            rotation = compose_camera_rotation(priors, first, second)
            anchor_to_first = (
                compose_camera_rotation(priors, nodes[0], first)
                if first != nodes[0]
                else Rotation.identity()
            )
            translation = anchor_to_first.inv().apply(positions[second] - positions[first])
            translation_m = float(np.linalg.norm(translation))
            if not 0.005 <= translation_m <= 0.40:
                continue
            pose = np.eye(4, dtype=float)
            pose[:3, :3] = rotation.as_matrix()
            pose[:3, 3] = translation
            angle_deg = float(np.degrees(rotation.magnitude()))
            tracked_points = int(
                np.median([sample["tracked_depth_points"] for sample in supporting])
            )
            inlier_ratio = float(
                np.median([sample["pnp_inlier_ratio"] for sample in supporting])
            )
            reprojection_p95 = float(
                np.percentile(
                    [sample["reprojection_p95_px"] for sample in supporting], 95
                )
            )
            long_samples.append(
                {
                    "session_id": component[0]["session_id"],
                    "split": metadata[first]["split"],
                    "first_input_index": first,
                    "second_input_index": second,
                    "first_image": metadata[first]["image"],
                    "second_image": metadata[second]["image"],
                    "depth_first": metadata[first]["depth"],
                    "depth_second": metadata[second]["depth"],
                    "intrinsics": metadata[first]["intrinsics"],
                    "camera_pose_second": pose.tolist(),
                    "duration_s": duration,
                    "frame_gap": second - first,
                    "imu_rotation_deg": angle_deg,
                    "angular_speed_deg_s": angle_deg / duration,
                    "motion_bin": temporal_motion_bin(angle_deg, duration),
                    "tracked_depth_points": tracked_points,
                    "pnp_inlier_ratio": inlier_ratio,
                    "reprojection_p95_px": reprojection_p95,
                    "translation_m": translation_m,
                    "label_source": "robust_short_edge_translation_graph",
                    "supporting_edges": len(supporting),
                    "component_edge_residual_p95_m": report[
                        "edge_residual_p95_m"
                    ],
                }
            )
    if 0 < maximum_pairs < len(long_samples):
        selected = np.rint(
            np.linspace(0, len(long_samples) - 1, maximum_pairs)
        ).astype(int)
        long_samples = [long_samples[index] for index in selected]
    return long_samples, component_reports


def build_window_manifest(
    source_manifest_path: Path,
    output: Path,
    minimum_duration_s: float = 0.8,
    maximum_duration_s: float = 1.6,
    target_durations_s: tuple[float, ...] = (0.9, 1.2, 1.5),
    maximum_pairs_per_session: int = 400,
    include_source_pairs: bool = True,
    maximum_component_edge_residual_p95_m: float = 0.005,
    exclude_rejected_source_components: bool = False,
    exclude_outlier_source_edges: bool = False,
) -> dict:
    source = load_json(source_manifest_path)
    if source.get("external_ground_truth_used") is not False:
        raise ValueError("source manifest must exclude external ground truth")
    if source.get("result") != "READY" or "temporal" not in source.get("schema", ""):
        raise ValueError("source temporal manifest is not ready")
    stereo = load_json(Path(source["source_stereo_manifest"]))
    export_roots = {
        item["session_id"]: Path(item["dataset_manifest"]).parent
        for item in stereo["source_exports"]
    }
    grouped: dict[str, list[dict]] = defaultdict(list)
    for sample in source["samples"]:
        grouped[sample["session_id"]].append(sample)

    long_samples = []
    component_reports = []
    priors_by_session = {}
    for session_id, session_samples in grouped.items():
        priors = list(
            csv.DictReader(
                (export_roots[session_id] / "imu_rotation_priors.csv").open(
                    newline="", encoding="utf-8"
                )
            )
        )
        priors_by_session[session_id] = priors
        session_long, reports = build_long_window_samples(
            session_samples,
            priors,
            minimum_duration_s,
            maximum_duration_s,
            target_durations_s,
            maximum_pairs_per_session,
            maximum_component_edge_residual_p95_m,
        )
        long_samples.extend(session_long)
        for report in reports:
            component_reports.append({"session_id": session_id, **report})
    if not long_samples:
        raise ValueError("no robust long-window samples")
    rejected_ids = (
        rejected_source_sample_ids(grouped, component_reports)
        if include_source_pairs and exclude_rejected_source_components
        else set()
    )
    outlier_ids = (
        outlier_source_sample_ids(
            grouped, priors_by_session, maximum_component_edge_residual_p95_m
        )
        if include_source_pairs and exclude_outlier_source_edges
        else set()
    )
    excluded_ids = rejected_ids | outlier_ids
    samples = (
        [sample for sample in source["samples"] if id(sample) not in excluded_ids]
        if include_source_pairs
        else []
    )
    samples.extend(long_samples)
    counts = {
        split: sum(sample["split"] == split for sample in samples)
        for split in ("train", "validation")
    }
    long_counts = {
        split: sum(sample["split"] == split for sample in long_samples)
        for split in ("train", "validation")
    }
    if not counts["train"] or not counts["validation"]:
        raise ValueError(f"empty temporal split: {counts}")
    result = {
        "schema": "umi_mast3r_d405_ir_temporal_window_finetune_v1",
        "result": "READY",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "supervision": [
            "temporal_left_ir_pairs",
            "synchronized_stereo_metric_depth",
            "onboard_400hz_imu_relative_rotation",
            "robust_short_edge_translation_graph",
        ],
        "source_temporal_manifest": str(source_manifest_path.resolve()),
        "source_stereo_manifest": source["source_stereo_manifest"],
        "include_source_pairs": include_source_pairs,
        "target_durations_s": list(target_durations_s),
        "maximum_component_edge_residual_p95_m": (
            maximum_component_edge_residual_p95_m
        ),
        "pair_duration_s": {
            "minimum": minimum_duration_s,
            "maximum": maximum_duration_s,
        },
        "counts": counts,
        "long_window_counts": long_counts,
        "long_window_samples": len(long_samples),
        "component_reports": component_reports,
        "samples": samples,
    }
    if exclude_rejected_source_components:
        result["excluded_rejected_source_pairs"] = len(rejected_ids)
    if exclude_outlier_source_edges:
        result["excluded_outlier_source_pairs"] = len(outlier_ids - rejected_ids)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-temporal-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-duration-s", type=float, default=0.8)
    parser.add_argument("--maximum-duration-s", type=float, default=1.6)
    parser.add_argument("--target-duration-s", type=float, action="append", default=[])
    parser.add_argument("--maximum-pairs-per-session", type=int, default=400)
    parser.add_argument(
        "--maximum-component-edge-residual-p95-m", type=float, default=0.005
    )
    parser.add_argument("--long-window-only", action="store_true")
    parser.add_argument("--exclude-rejected-source-components", action="store_true")
    parser.add_argument("--exclude-outlier-source-edges", action="store_true")
    args = parser.parse_args()
    if not 0.0 < args.minimum_duration_s <= args.maximum_duration_s:
        raise ValueError("window duration bounds are invalid")
    target_durations = tuple(args.target_duration_s or (0.9, 1.2, 1.5))
    if any(
        not args.minimum_duration_s <= duration <= args.maximum_duration_s
        for duration in target_durations
    ):
        raise ValueError("target durations must lie inside window duration bounds")
    report = build_window_manifest(
        args.source_temporal_manifest.resolve(),
        args.output.resolve(),
        args.minimum_duration_s,
        args.maximum_duration_s,
        target_durations,
        args.maximum_pairs_per_session,
        not args.long_window_only,
        args.maximum_component_edge_residual_p95_m,
        args.exclude_rejected_source_components,
        args.exclude_outlier_source_edges,
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "schema",
                    "result",
                    "external_ground_truth_used",
                    "counts",
                    "long_window_counts",
                    "long_window_samples",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

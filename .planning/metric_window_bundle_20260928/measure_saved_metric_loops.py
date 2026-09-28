#!/usr/bin/env python3
"""Read-only metric check of saved MASt3R retrieval matches (no GT)."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from check_backend_match_stereo import check_direction, check_edge
from mast3r_slam.stereo_depth import StereoDepthProvider


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def visual_relative_camera_i(rows, first, second, scale):
    first_row, second_row = rows[first], rows[second]
    position_i = np.array([float(first_row[key]) for key in ("x", "y", "z")])
    position_j = np.array([float(second_row[key]) for key in ("x", "y", "z")])
    rotation_i = Rotation.from_quat(
        [float(first_row[key]) for key in ("qx", "qy", "qz", "qw")]
    )
    return rotation_i.inv().apply((position_j - position_i) * scale)


def visual_relative_rotation(rows, first, second):
    def rotation(row):
        return Rotation.from_quat(
            [float(row[key]) for key in ("qx", "qy", "qz", "qw")]
        )
    return rotation(rows[second]).inv() * rotation(rows[first])


def measured_relative_camera_i(transform):
    # PnP maps source-frame points into target-frame coordinates.
    # The target camera centre in the source frame is -R^T t.
    return -transform[:3, :3].T @ transform[:3, 3]


def run_case(case, matches, report_path, min_gap):
    stereo_report = json.loads(report_path.read_text())
    trajectory = Path(stereo_report["trajectory"])
    dataset = trajectory.parent / "dataset"
    scale = float(stereo_report["scale_m_per_mast3r_unit"])
    with trajectory.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    records = [json.loads(line) for line in (matches / "match_manifest.jsonl").read_text().splitlines()]
    provider = StereoDepthProvider.from_dataset(dataset)
    if provider is None:
        raise ValueError("independent D405 stereo unavailable")
    cache = {}
    results = []
    for record in records:
        first, second = int(record["first_raw"]), int(record["second_raw"])
        if second - first < min_gap:
            continue
        sample_path = Path(record["npz"])
        if sha256(sample_path) != record["sha256"]:
            raise ValueError("saved match hash changed")
        with np.load(sample_path, allow_pickle=False) as sample:
            check = check_edge(sample, provider, dataset, cache)
            if not check["accepted"]:
                results.append(check)
                continue
            shape = tuple(int(v) for v in sample["first_shape"])
            depth_key = (first, shape)
            source_depth = cache[depth_key]
            transform, _ = check_direction(
                sample["forward_source"], sample["forward_target"],
                source_depth, shape, shape, np.asarray(sample["K"], dtype=float)
            )
            if transform is None:
                raise ValueError("previously accepted PnP unexpectedly failed")
            measured = measured_relative_camera_i(transform)
            target_depth = cache[(second, shape)]
            reverse, _ = check_direction(
                sample["reverse_source"], sample["reverse_target"],
                target_depth, shape, shape, np.asarray(sample["K"], dtype=float)
            )
            if reverse is None:
                raise ValueError("previously accepted reverse PnP unexpectedly failed")
            bidirectional_mean = 0.5 * (measured + reverse[:3, 3])
            visual = visual_relative_camera_i(rows, first, second, scale)
            measured_rotation = Rotation.from_matrix(transform[:3, :3])
            visual_rotation = visual_relative_rotation(rows, first, second)
            check["measured_displacement_camera_i_m"] = measured.tolist()
            check["visual_displacement_camera_i_m"] = visual.tolist()
            check["visual_minus_stereo_mm"] = float(np.linalg.norm(visual - measured) * 1000)
            check["forward_reverse_displacement_disagreement_mm"] = float(
                np.linalg.norm(measured - reverse[:3, 3]) * 1000
            )
            check["bidirectional_mean_displacement_camera_i_m"] = bidirectional_mean.tolist()
            check["visual_minus_bidirectional_mean_mm"] = float(
                np.linalg.norm(visual - bidirectional_mean) * 1000
            )
            check["visual_minus_stereo_relative_rotation_deg"] = float(
                np.degrees((measured_rotation * visual_rotation.inv()).magnitude())
            )
            visual_norm, measured_norm = np.linalg.norm(visual), np.linalg.norm(measured)
            check["visual_minus_stereo_displacement_length_mm"] = float(
                (visual_norm - measured_norm) * 1000
            )
            if visual_norm > 0 and measured_norm > 0:
                cosine = np.clip(np.dot(visual, measured) / (visual_norm * measured_norm), -1, 1)
                check["visual_minus_stereo_displacement_direction_deg"] = float(
                    np.degrees(np.arccos(cosine))
                )
            results.append(check)
    return {
        "case": case,
        "dataset": str(dataset),
        "trajectory": str(trajectory),
        "trajectory_sha256": sha256(trajectory),
        "report_sha256": sha256(report_path),
        "matches_sha256": sha256(matches / "match_manifest.jsonl"),
        "metric_scale_m_per_mast3r_unit": scale,
        "min_gap_raw_frames": min_gap,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--stereo-report", type=Path, required=True)
    parser.add_argument("--min-gap", type=int, default=150)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite diagnostic output")
    result = run_case(args.case, args.matches, args.stereo_report, args.min_gap)
    result.update(schema="saved_metric_loop_displacement_check_v1",
                  external_reference_used=False,
                  diagnostic_only=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()

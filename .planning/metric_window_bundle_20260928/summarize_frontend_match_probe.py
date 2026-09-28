#!/usr/bin/env python3
"""Summarize same-config frontend logging replays, without GT or fusion."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def read_matches(path, frame_count):
    with Path(path).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"frame_id", "n_match", "n_match_Q", "n_opt", "n_total"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("missing match rows or required columns")
    records = [{key: int(row[key]) for key in required} for row in rows]
    ids = [row["frame_id"] for row in records]
    if ids != sorted(set(ids)) or any(i < 0 or i >= frame_count for i in ids):
        raise ValueError("match frame ids duplicate, unordered or out of range")
    for row in records:
        if not (0 <= row["n_opt"] <= row["n_match_Q"] <= row["n_match"] <= row["n_total"]):
            raise ValueError("match counts violate subset constraints")
        if row["n_total"] <= 0:
            raise ValueError("nonpositive total match pixels")
    return records


def distribution(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    return dict(n=int(len(values)), min=float(values.min()),
                median=float(np.median(values)), p05=float(np.percentile(values, 5)),
                max=float(values.max()))


def summarize_rows(rows, start, end):
    selected = [row for row in rows if start <= row["frame_id"] <= end]
    return dict(first_frame=start, last_frame=end, logged_frames=len(selected),
                expected_frames=end-start+1,
                missing_frame_ids=sorted(set(range(start, end+1))-
                                         {row["frame_id"] for row in selected}),
                n_opt=distribution([row["n_opt"] for row in selected]),
                match_fraction=distribution([row["n_match"]/row["n_total"] for row in selected]),
                qualified_match_fraction=distribution([row["n_match_Q"]/row["n_total"] for row in selected]),
                optimization_fraction=distribution([row["n_opt"]/row["n_total"] for row in selected]))


def read_frontend(path, matches, frame_count):
    with Path(path).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"frame_id", "keyframe_id", "n_opt", "n_total", "pointmap_z_current",
                "metric_pointmap_scale", "metric_relative_mad", "metric_points", "relative_scale"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("frontend columns or rows missing")
    ids = [int(row["frame_id"]) for row in rows]
    if ids != sorted(set(ids)) or any(i < 0 or i >= frame_count for i in ids):
        raise ValueError("frontend ids duplicate, unordered or out of range")
    by_id = {row["frame_id"]: row for row in matches}
    if set(ids) != set(by_id):
        raise ValueError("frontend/match frame coverage differs")
    for row in rows:
        match = by_id[int(row["frame_id"])]
        if any(int(row[key]) != match[key] for key in ("n_opt", "n_total")):
            raise ValueError("frontend/match optimization counts differ")
        for key in required - {"frame_id", "keyframe_id", "n_opt", "n_total"}:
            float(row[key])  # NaN scale samples are missing diagnostics, not zeros.
    return rows


def frontend_distributions(rows, start, end):
    selected = [row for row in rows if start <= int(row["frame_id"]) <= end]
    return {key: distribution([float(row[key]) for row in selected]) for key in (
        "pointmap_z_current", "metric_pointmap_scale", "metric_relative_mad", "relative_scale")}


def analyze(probe, frozen):
    probe, frozen = Path(probe), Path(frozen)
    old = json.loads((frozen/"run_manifest.json").read_text())
    new = json.loads((probe/"run_manifest.json").read_text())
    for key in ("config_sha256", "toolchain_commit", "toolchain_dirty_diff_sha256",
                "lietorch_commit", "checkpoint_sha256"):
        if old[key] != new[key]:
            raise ValueError(f"replay producer mismatch: {key}")
    if (probe/"dataset").resolve(strict=True) != (frozen/"dataset").resolve(strict=True):
        raise ValueError("replay does not reuse exact frozen prepared dataset")
    poses_old = np.genfromtxt(frozen/"trajectory_frames.csv", delimiter=",", skip_header=1)
    poses_new = np.genfromtxt(probe/"trajectory_frames.csv", delimiter=",", skip_header=1)
    if (poses_old.ndim != 2 or poses_old.shape != poses_new.shape or poses_old.shape[1] != 8
            or not np.isfinite(poses_old).all() or not np.isfinite(poses_new).all()
            or not np.array_equal(poses_old[:, 0], poses_new[:, 0])):
        raise ValueError("replay pose coverage/timestamps invalid or different")
    rows = read_matches(probe/"match_log.csv", len(poses_new))
    frontend = read_frontend(probe/"frontend_log.csv", rows, len(poses_new))
    identical = bool(np.array_equal(poses_old, poses_new))
    sources = [probe/"run_manifest.json", frozen/"run_manifest.json",
               probe/"match_log.csv", probe/"frontend_log.csv",
               probe/"trajectory_frames.csv", frozen/"trajectory_frames.csv"]
    return dict(diagnostic_only=True, external_ground_truth_used=False,
                estimator_config_changed=False, model_trained=False,
                original_trajectory_modified=False,
                probe=str(probe), frozen=str(frozen), frame_count=len(poses_new),
                elapsed_s=new["elapsed_s"],
                replay_trajectory_identical=identical,
                original_trajectory_reproduced=identical,
                identity_limitation=None if identical else
                    "replay differs: logs cannot be treated as original-run causal proof",
                replay_max_position_delta_mast3r_units=float(np.max(np.linalg.norm(
                    poses_new[:, 1:4]-poses_old[:, 1:4], axis=1))),
                replay_max_pose_component_delta=float(np.abs(poses_new[:, 1:]-poses_old[:, 1:]).max()),
                raw_csv_byte_identical=digest(probe/"trajectory_frames.csv")==digest(frozen/"trajectory_frames.csv"),
                match_logging_whole_run=summarize_rows(rows, 0, len(poses_new)-1),
                fixed_windows=[summarize_rows(rows, start, start+40) for start in (1000, 1040, 1080)],
                frontend_fixed_windows=[dict(first_frame=start, last_frame=start+40,
                    statistics=frontend_distributions(frontend, start, start+40))
                    for start in (1000, 1040, 1080)],
                producer_identity_fields={key: new[key] for key in (
                    "config_sha256", "toolchain_commit", "toolchain_dirty_diff_sha256",
                    "lietorch_commit", "checkpoint_sha256")},
                input_sha256={str(path): digest(path) for path in sources},
                source_sha256={str(Path(__file__).resolve()): digest(__file__)},
                limitations=["frontend instrumentation is a replay, not the missing original log",
                             "missing match rows are unknown, not zero",
                             "same frame ranges in different recordings need not have same motion",
                             "many matches do not prove correct metric depth or millimetric poses"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("refusing to overwrite probe summary")
    result = analyze(args.probe, args.frozen)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

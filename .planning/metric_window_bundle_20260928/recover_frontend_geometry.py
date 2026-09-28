#!/usr/bin/env python3
"""Reanalyze saved immutable captures after a diagnostic border-compatibility fix."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from frontend_geometry_diagnostics import summarize_geometry


FIRST, LAST = 1000, 1120


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            value.update(chunk)
    return value.hexdigest()


def check_origin(trace):
    if (trace.get("diagnostic_only") is not True
            or trace.get("external_ground_truth_used") is not False
            or trace.get("estimator_changed") is not False
            or trace.get("fixed_input_scope") != [FIRST, LAST]):
        raise ValueError("origin is not the unchanged-estimator fixed-scope probe")
    identities = trace.get("trajectory_identity", {})
    if set(identities) != {"trajectory_frames.csv", "trajectory_online_frames.csv"}:
        raise ValueError("origin full/online identity missing")
    for item in identities.values():
        if (item.get("rows") != 1199 or item.get("byte_identical") is not True
                or item.get("array_identical") is not True or item.get("max_component_delta") != 0):
            raise ValueError("origin replay did not reproduce full/online trajectories")
    errors = trace.get("errors", [])
    if ([e.get("frame_id") for e in errors] != list(range(FIRST, LAST+1))
            or any(e.get("stage") != "finish" or e.get("error") != "ValueError('pixel_border invalid')"
                   for e in errors) or trace.get("rows") != []):
        raise ValueError("only the exact diagnostic negative-border failure is recoverable")


def recover(trace_path, output_path):
    trace_path, output_path = Path(trace_path).resolve(strict=True), Path(output_path).resolve()
    if output_path.exists():
        raise FileExistsError("refusing to overwrite recovered diagnostic")
    trace = json.loads(trace_path.read_text())
    check_origin(trace)
    geometry_source = Path(__file__).with_name("frontend_geometry_diagnostics.py").resolve()
    original_hashes = trace["source_input_sha256"]
    if str(geometry_source) not in original_hashes:
        raise ValueError("original geometry source unbound")
    changed = [path for path, value in original_hashes.items() if digest(path) != value]
    if changed != [str(geometry_source)]:
        raise ValueError("only corrected pure analysis source may differ from original capture")
    for name in trace["trajectory_identity"]:
        if digest(trace_path.parent/name) != digest(Path(trace["frozen"])/name):
            raise ValueError("original replay trajectories changed after capture")
    sample_paths = sorted((trace_path.parent/"samples").glob("*.npz"))
    if [int(path.stem) for path in sample_paths] != list(range(FIRST, LAST+1)):
        raise ValueError("saved sample coverage is not the exact121frames")
    inputs = [trace_path, trace_path.parent/"frontend_log.csv", *sample_paths]
    hashes = {str(path): digest(path) for path in inputs}
    with (trace_path.parent/"frontend_log.csv").open(newline="") as stream:
        frontend = {int(row["frame_id"]): row for row in csv.DictReader(stream)}
    rows = []
    for path in sample_paths:
        frame_id = int(path.stem)
        with np.load(path, allow_pickle=False) as saved:
            capture = {key: saved[key].copy() for key in saved.files}
        original_row = frontend[frame_id]
        if int(capture["full_optimize_valid_count"]) != int(original_row["n_opt"]):
            raise ValueError("saved capture optimizer count differs from logged original frame")
        if float(capture["pixel_border"]) != -10:
            raise ValueError("saved capture is not the actual permissive native border case")
        rows.append(dict(frame_id=frame_id, keyframe_id=int(original_row["keyframe_id"]),
                         captured=True, sampled_optimize_points=len(capture["valid"]),
                         full_optimize_valid_count=int(capture["full_optimize_valid_count"]),
                         sample_path=str(path), sample_sha256=hashes[str(path)],
                         diagnostics=summarize_geometry(capture)))
    if any(digest(path) != value for path, value in hashes.items()):
        raise ValueError("capture changed while reanalyzing")
    result = dict(diagnostic_only=True, external_ground_truth_used=False,
                  estimator_changed=False, gpu_or_pose_replay_rerun=False,
                  recovered_pure_analysis=True, original_capture_status="FAILED_DIAGNOSTIC_MATH",
                  original_capture_trace=str(trace_path), original_capture_sha256=hashes[str(trace_path)],
                  sample_hashes_first_frozen="post-capture recovery entry; not claimed recorded at each GPU step",
                  fixed_input_scope=[FIRST, LAST], trajectory_identity=trace["trajectory_identity"],
                  producer_identity=trace["producer_identity"], consumed_input_sha256=hashes,
                  source_sha256={str(Path(__file__).resolve()): digest(__file__),
                                 str(geometry_source): digest(geometry_source)},
                  replaced_original_analysis_sha256=original_hashes[str(geometry_source)],
                  rows=rows, errors=[],
                  limitations=["original failed trace retained; not retroactively marked successful",
                               "only projection-filter analysis changed to mirror native project_calib",
                               "counts/residuals do not establish unique causation or absolute accuracy"])
    with output_path.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = recover(args.trace, args.output)
    print(json.dumps(dict(rows=len(result["rows"]), output=str(args.output), gpu_replay_rerun=False)))


if __name__ == "__main__":
    main()

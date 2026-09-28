#!/usr/bin/env python3
"""Summarize accepted MASt3R graph edges and pose updates without GT."""
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "reports/metric_window_bundle_20260928/backend_constraint_probe_v1"
OUTPUT = BASE / "backend_event_census.json"
CASES = ("fresh4", "fresh1", "heldout1")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize_events(events):
    additions = [event for event in events if event.get("event") == "add_factors"]
    solves = [event for event in events if event.get("event") == "solve"]
    if not additions or not solves or len(additions) > len(solves):
        raise ValueError("backend event coverage incomplete")
    accepted = [edge for event in additions for edge in event["accepted"]]
    solve_rows = []
    for event in solves:
        before = event["before"]
        after = event["after"]
        if [(row[0], row[1]) for row in before] != [(row[0], row[1]) for row in after]:
            raise ValueError("backend solve pose indices changed")
        if not before:
            continue
        frames = np.asarray([row[1] for row in after], dtype=int)
        before_translation = np.asarray([row[2][:3] for row in before], dtype=float)
        after_translation = np.asarray([row[2][:3] for row in after], dtype=float)
        delta = np.linalg.norm(after_translation - before_translation, axis=1)
        if not np.isfinite(delta).all():
            raise ValueError("non-finite backend pose update")
        recent = (frames >= 1000) & (frames <= 1120)
        solve_rows.append({"current_raw_frame": int(frames.max()),
                           "factor_count": int(event["factor_count"]),
                           "current_translation_change_native": float(delta[-1]),
                           "max_translation_change_native": float(delta.max()),
                           "recent_band_max_translation_change_native":
                               float(delta[recent].max()) if recent.any() else None})
    edge_rows = [{"source_frames": edge["source_frames"],
                  "keyframes": edge["keyframes"],
                  "raw_frame_gap": int(edge["source_frames"][1] - edge["source_frames"][0]),
                  "valid_match_fraction_i": edge["valid_match_fraction_i"],
                  "valid_match_fraction_j": edge["valid_match_fraction_j"]}
                 for edge in accepted]
    return {"event_count": len(events), "add_calls": len(additions),
            "solve_calls": len(solves), "accepted_edges": len(edge_rows),
            "accepted_edges_into_1000_1120": [row for row in edge_rows
                if 1000 <= row["source_frames"][1] <= 1120],
            "solves": solve_rows}


def main():
    if OUTPUT.exists():
        raise FileExistsError("refusing to overwrite backend census")
    paths = [Path(__file__).resolve(),
             ROOT / ".planning/metric_window_bundle_20260928/backend_hook/sitecustomize.py"]
    results = {}
    for case in CASES:
        event_path = BASE / f"{case}_events.jsonl"
        geometry_path = BASE / f"{case}_mast3r/geometry_trace.json"
        geometry = json.loads(geometry_path.read_text())
        if geometry["errors"] or not all(value["byte_identical"] for value in
                                         geometry["trajectory_identity"].values()):
            raise ValueError(f"{case}: backend replay changed trajectory or capture failed")
        events = [json.loads(line) for line in event_path.read_text().splitlines()]
        results[case] = summarize_events(events)
        paths.extend((event_path, geometry_path))
    hashes = {str(path): digest(path) for path in paths}
    if any(digest(path) != value for path, value in hashes.items()):
        raise ValueError("backend event sources changed during analysis")
    with OUTPUT.open("x") as stream:
        json.dump({"external_ground_truth_used": False,
                   "replayed_final_and_online_trajectories_byte_identical": True,
                   "translation_change_unit": "MASt3R native nonmetric units, not mm",
                   "source_and_input_sha256": hashes, "cases": results},
                  stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()

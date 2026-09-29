#!/usr/bin/env python3
"""Summarize native frontend correspondence persistence without scoring GT."""
import argparse
import json
from pathlib import Path

import numpy as np


def summarize_groups(rows):
    groups = []
    for row in rows:
        if not row["captured"]:
            raise ValueError(f"missing calibrated capture at frame {row['frame_id']}")
        if groups and groups[-1]["keyframe_id"] == row["keyframe_id"]:
            groups[-1]["rows"].append(row)
        else:
            groups.append(dict(keyframe_id=row["keyframe_id"], rows=[row]))
    result = []
    for group in groups:
        frames = group["rows"]
        ids = [np.asarray(row["ids"], dtype=np.int32) for row in frames]
        common = ids[0]
        for next_ids in ids[1:]:
            common = np.intersect1d(common, next_ids, assume_unique=True)
        adjacent = [len(np.intersect1d(a, b, assume_unique=True))
                    for a, b in zip(ids, ids[1:])]
        result.append(dict(keyframe_id=group["keyframe_id"],
                           first_frame=frames[0]["frame_id"],
                           last_frame=frames[-1]["frame_id"],
                           frames=len(frames),
                           valid_min=min(map(len, ids)),
                           valid_median=float(np.median(list(map(len, ids)))),
                           adjacent_shared_min=min(adjacent) if adjacent else None,
                           adjacent_shared_median=float(np.median(adjacent)) if adjacent else None,
                           full_span_shared=len(common)))
    return result


def summarize_probe(path):
    probe = json.loads((path/"geometry_trace.json").read_text())
    if not all(item["byte_identical"] for item in probe["trajectory_identity"].values()):
        raise ValueError("probe trajectories are not byte-identical to frozen originals")
    first, last = probe["fixed_input_scope"]
    if [row["frame_id"] for row in probe["rows"]] != list(range(first, last+1)):
        raise ValueError("incomplete frame scope")
    if probe["errors"] or not probe["dense_correspondence_ids_saved"]:
        raise ValueError("probe errors or dense IDs missing")
    rows = []
    for row in probe["rows"]:
        with np.load(path/"samples"/f"{row['frame_id']:04d}.npz") as sample:
            ids = sample["dense_keyframe_pixel_ids"]
            mapped = sample["dense_current_pixel_ids"]
            if ids.ndim != 1 or mapped.shape != ids.shape or not np.all(np.diff(ids) > 0):
                raise ValueError("invalid dense correspondence arrays")
            if len(ids) != row["full_optimize_valid_count"]:
                raise ValueError("dense IDs do not cover native valid mask")
            rows.append(dict(frame_id=row["frame_id"], keyframe_id=row["keyframe_id"],
                             captured=row["captured"], ids=ids))
    return dict(probe=str(path), scope=[first, last],
                trajectory_identity=probe["trajectory_identity"],
                producer_difference_from_frozen=probe["producer_difference_from_frozen"],
                groups=summarize_groups(rows),
                limitation="Same keyframe pixel IDs are candidate matches, not verified static 3D landmarks or metric accuracy")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probes", type=Path, nargs="+")
    args = parser.parse_args()
    print(json.dumps([summarize_probe(path) for path in args.probes], indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Read-only stereo/IMU short-hop check of frozen dense frontend matches."""
import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

from compare_dense_stereo_translation import translation_observation


def common_current_matches(left_ids, left_mapped, right_ids, right_mapped):
    common, left_index, right_index = np.intersect1d(
        left_ids, right_ids, assume_unique=True, return_indices=True)
    return common, left_mapped[left_index], right_mapped[right_index]


def integrate_pairs(start, end, rotations, observations):
    """Integrate camera-i-frame translations into the start camera frame."""
    center = np.zeros(3)
    for index in range(start, end):
        center += (rotations[start].inv()*rotations[index]).apply(
            np.asarray(observations[index], dtype=np.float64))
    return center


def imu_rotations(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    increments = Rotation.from_quat(np.asarray([
        [float(row[key]) for key in ("qx", "qy", "qz", "qw")]
        for row in rows]))
    result = []
    accumulated = Rotation.identity()
    for increment in increments:
        accumulated = accumulated*increment
        result.append(accumulated)
    return result


def analyze(probe, start, end):
    trace = json.loads((probe/"geometry_trace.json").read_text())
    if trace["errors"] or not all(
            row["byte_identical"] for row in trace["trajectory_identity"].values()):
        raise ValueError("probe trajectory was not reproduced byte-for-byte")
    manifest = json.loads((Path(trace["frozen"])/"run_manifest.json").read_text())
    sys.path.insert(0, manifest["toolchain"])
    from mast3r_slam.stereo_depth import StereoDepthProvider

    dataset = Path(trace["dataset"])
    provider = StereoDepthProvider.from_dataset(dataset)
    if provider is None:
        raise ValueError("independent stereo depth unavailable")
    rotations = imu_rotations(dataset/"imu_rotation_priors.csv")
    known = {row["frame_id"]: row for row in trace["rows"]}
    if start not in known or end not in known or end >= len(rotations):
        raise ValueError("requested frame span was not captured")

    samples = {}
    depths = {}

    def sample(index):
        if index not in samples:
            with np.load(probe/"samples"/f"{index:04d}.npz") as data:
                samples[index] = dict(ids=data["dense_keyframe_pixel_ids"],
                                      mapped=data["dense_current_pixel_ids"],
                                      K=data["K"], shape=tuple(data["image_shape"]))
        return samples[index]

    def depth(index, shape):
        if index not in depths:
            depths[index] = provider.get_depth(dataset/f"{index:010d}.png", shape)
        return depths[index]

    def correspondence(i, j):
        right = sample(j)
        anchor = known[j]["keyframe_id"]
        if i == anchor:
            return right["ids"], right["mapped"], right
        if known[i]["keyframe_id"] != anchor:
            return None
        left = sample(i)
        _, ids_i, ids_j = common_current_matches(
            left["ids"], left["mapped"], right["ids"], right["mapped"])
        return ids_i, ids_j, right

    rows = []
    observations = {}
    for i in range(start, end):
        j = i + 1
        if j not in known:
            raise ValueError(f"missing dense sample {j}")
        matched = correspondence(i, j)
        if matched is None:
            rows.append(dict(start=i, end=j, status="ANCHOR_CHANGE"))
            continue
        ids_i, ids_j, right = matched
        relative = rotations[i].inv()*rotations[j]
        observed = translation_observation(
            ids_i, ids_j, depth(i, right["shape"]), depth(j, right["shape"]),
            right["K"], relative)
        rows.append(dict(start=i, end=j, **observed))
        if observed["status"] == "OK":
            observations[i] = observed["translation_m"]
    complete = len(observations) == end-start
    chain = integrate_pairs(start, end, rotations, observations) if complete else None
    direct_match = correspondence(start, end)
    direct = None
    if direct_match is not None:
        ids_i, ids_j, right = direct_match
        direct = translation_observation(
            ids_i, ids_j, depth(start, right["shape"]),
            depth(end, right["shape"]), right["K"],
            rotations[start].inv()*rotations[end])
    spread = [row["tile_spread_max_mm"] for row in rows if row["status"] == "OK"]
    return dict(probe=str(probe), start=start, end=end, complete=complete,
                short_hop_stereo_m=None if chain is None else chain.tolist(),
                direct_stereo=direct,
                chain_vs_direct_mm=(float(np.linalg.norm(chain-np.asarray(direct["translation_m"]))*1000)
                                    if chain is not None and direct and direct["status"] == "OK" else None),
                short_hop_tile_spread_median_mm=float(np.median(spread)) if spread else None,
                short_hop_tile_spread_max_mm=float(max(spread)) if spread else None,
                rows=rows,
                limitations=["Dense MASt3R matches are reused across consecutive images; not independent of frontend matching.",
                             "Stereo depth and IMU are onboard only; no external trajectory enters this check.",
                             "The short-hop chain accumulates measurement bias and is not yet a SLAM factor."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probes", nargs="+", type=Path)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    if args.end <= args.start:
        parser.error("--end must exceed --start")
    results = [analyze(probe, args.start, args.end) for probe in args.probes]
    if args.summary_only:
        for result in results:
            result.pop("rows")
    print(json.dumps(results, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

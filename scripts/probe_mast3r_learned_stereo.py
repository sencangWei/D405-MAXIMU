#!/usr/bin/env python3
"""Measure MASt3R left/right IR correspondences before changing the SLAM frontend.

This is an observation-only probe: it neither changes poses nor reads external truth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np


def metric_matches(left_xy, right_xy, focal_px, baseline_m, depth_limits=(0.07, 0.65)):
    left = np.asarray(left_xy, dtype=float).reshape(-1, 2)
    right = np.asarray(right_xy, dtype=float).reshape(-1, 2)
    if left.shape != right.shape or focal_px <= 0 or baseline_m <= 0:
        raise ValueError("invalid rectified stereo matches or calibration")
    disparity = left[:, 0] - right[:, 0]
    epipolar_error = np.abs(left[:, 1] - right[:, 1])
    valid = np.isfinite(left).all(axis=1) & np.isfinite(right).all(axis=1)
    valid &= disparity > 0.5
    valid &= epipolar_error <= 1.0
    depth = np.full(len(left), np.nan)
    depth[valid] = focal_px * baseline_m / disparity[valid]
    valid &= (depth >= depth_limits[0]) & (depth <= depth_limits[1])
    depth[~valid] = np.nan
    return valid, depth, epipolar_error


def inspect_dataset(dataset: Path) -> dict:
    manifest = json.loads((dataset / "dataset_manifest.json").read_text())
    stereo = manifest.get("stereo_depth_source") or {}
    left = manifest.get("camera_info") or {}
    right = stereo.get("right_camera_info") or {}
    if manifest.get("stream") != "infrared_left" or not stereo:
        raise ValueError("requires a prepared left-IR dataset with synchronized right IR")
    if any(abs(float(left[k]) - float(right[k])) > 0.5 for k in ("fx", "fy", "ppx", "ppy")):
        raise ValueError("left/right IR intrinsics differ")
    if any(float(v) != 0 for v in left["coeffs"] + right["coeffs"]):
        raise ValueError("stereo images are not rectified")
    if float(stereo["max_left_right_skew_ms"]) > 0.5:
        raise ValueError("left/right image skew exceeds 0.5 ms")
    if not 0.015 <= float(stereo["baseline_m"]) <= 0.025:
        raise ValueError("unexpected D405 stereo baseline")
    return manifest


def probe(dataset: Path, indices: list[int], checkpoint: Path) -> dict:
    import torch
    from mast3r.fast_nn import fast_reciprocal_NNs
    from mast3r_slam.config import config
    from mast3r_slam.mast3r_utils import (
        load_mast3r,
        mast3r_asymmetric_inference,
        resize_img,
    )
    from mast3r_slam.stereo_depth import compute_stereo_depth

    manifest = inspect_dataset(dataset)
    stereo = manifest["stereo_depth_source"]
    config.setdefault("dataset", {})["img_downsample"] = 1
    model = load_mast3r(str(checkpoint), device="cuda:0").eval()
    reports = []
    for index in indices:
        if index < 0 or index >= int(manifest["frames"]):
            raise IndexError(f"frame {index} outside prepared dataset")
        name = f"{index:010d}.png"
        paths = (dataset / name, dataset / stereo["right_directory"] / name)
        frames = []
        raw_images = []
        for path in paths:
            raw = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if raw is None:
                raise FileNotFoundError(path)
            raw_images.append(raw)
            image = np.repeat(raw[:, :, None], 3, axis=2).astype(np.float32) / 255.0
            packed = resize_img(image, 512)
            frames.append(
                SimpleNamespace(
                    img=packed["img"].to("cuda:0"),
                    img_true_shape=torch.as_tensor(packed["true_shape"], device="cuda:0"),
                    feat=None,
                    pos=None,
                )
            )
        _, confidence, descriptors, _ = mast3r_asymmetric_inference(model, *frames)
        left_xy, right_xy = fast_reciprocal_NNs(
            descriptors[0], descriptors[1], subsample_or_initxy1=8,
            device="cuda:0", dist="dot", block_size=8192,
        )
        height, width = descriptors[0].shape[:2]
        focal_px = float(manifest["camera_info"]["fx"]) * width / float(manifest["camera_info"]["width"])
        valid, depth, epipolar_error = metric_matches(
            left_xy, right_xy, focal_px, float(stereo["baseline_m"])
        )
        left_conf = confidence[0, left_xy[:, 1], left_xy[:, 0]].cpu().numpy()
        right_conf = confidence[1, right_xy[:, 1], right_xy[:, 0]].cpu().numpy()
        confident = valid & (left_conf >= 1.5) & (right_conf >= 1.5)
        classical_depth = compute_stereo_depth(
            *raw_images,
            focal_length_px=float(manifest["camera_info"]["fx"]),
            baseline_m=float(stereo["baseline_m"]),
            minimum_depth_m=0.07,
            maximum_depth_m=0.65,
        )
        classical_depth = cv2.resize(
            classical_depth, (width, height), interpolation=cv2.INTER_NEAREST
        )
        classical_at_matches = classical_depth[left_xy[:, 1], left_xy[:, 0]]
        comparable = confident & np.isfinite(classical_at_matches)
        depth_difference = np.abs(depth[comparable] - classical_at_matches[comparable])
        reports.append(
            {
                "frame": index,
                "model_shape": [int(height), int(width)],
                "reciprocal_matches": int(len(left_xy)),
                "rectified_metric_matches": int(valid.sum()),
                "confident_metric_matches": int(confident.sum()),
                "pointmap_confidence_median": float(np.median(np.minimum(left_conf[valid], right_conf[valid]))) if valid.any() else None,
                "epipolar_p95_px": float(np.percentile(epipolar_error[confident], 95)) if confident.any() else None,
                "depth_median_m": float(np.median(depth[confident])) if confident.any() else None,
                "sgbm_comparable_matches": int(comparable.sum()),
                "sgbm_depth_abs_difference_median_mm": float(np.median(depth_difference) * 1000) if comparable.any() else None,
                "sgbm_depth_abs_difference_p95_mm": float(np.percentile(depth_difference, 95) * 1000) if comparable.any() else None,
            }
        )
    return {
        "schema": "umi_mast3r_learned_stereo_probe_v1",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "source": str(dataset),
        "checkpoint": str(checkpoint),
        "baseline_m": float(stereo["baseline_m"]),
        "minimum_pointmap_confidence": 1.5,
        "frames": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--indices", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = probe(args.dataset, args.indices, args.checkpoint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

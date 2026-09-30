"""Compare MASt3R projection, 3D distance, and descriptor matches on frame pairs.

Diagnostic only: no trajectory, external reference, or model parameter is changed.
Run with the MASt3R-SLAM virtualenv and its repository as the working directory.
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import torch

from mast3r.fast_nn import fast_reciprocal_NNs
import mast3r_slam_backends
from mast3r_slam.config import config, load_config
from mast3r_slam.mast3r_utils import (
    load_mast3r, mast3r_asymmetric_inference, resize_img,
)
from mast3r_slam.matching import prep_for_iter_proj
from mast3r_slam.stereo_depth import StereoDepthProvider
from mast3r_slam.stereo_descriptor_recovery import metric_descriptor_pose


def load_frame(dataset, index, cache):
    if index not in cache:
        raw = cv2.imread(str(dataset / f"{index:010d}.png"), cv2.IMREAD_GRAYSCALE)
        if raw is None:
            raise FileNotFoundError(dataset / f"{index:010d}.png")
        image = np.repeat(raw[:, :, None], 3, axis=2).astype(np.float32) / 255.0
        packed = resize_img(image, 512)
        cache[index] = SimpleNamespace(
            img=packed["img"].to("cuda:0"),
            img_true_shape=torch.as_tensor(packed["true_shape"], device="cuda:0"),
            feat=None, pos=None,
        )
    return cache[index]


def quantiles(values):
    selected = values[torch.isfinite(values)]
    levels = torch.tensor([0.5, 0.9, 0.95], device="cuda:0")
    return [round(float(v), 4) for v in torch.quantile(selected.float(), levels).cpu()]


def fitted_3d_residual(source, target, allow_scale):
    """Diagnostic global fit; not a pose correction or proposed production gate."""
    fit_source, fit_target = source[::2], target[::2]
    test_source, test_target = source[1::2], target[1::2]
    first = fit_source.mean(axis=0)
    second = fit_target.mean(axis=0)
    u, singular, vt = np.linalg.svd((fit_source - first).T @ (fit_target - second))
    diagonal = np.eye(3)
    diagonal[-1, -1] = np.linalg.det(vt.T @ u.T)
    rotation = vt.T @ diagonal @ u.T
    scale = (
        np.sum(singular * np.diag(diagonal)) / np.sum((fit_source - first) ** 2)
        if allow_scale else 1.0
    )
    shift = second - scale * rotation @ first
    distances = np.linalg.norm(scale * test_source @ rotation.T + shift - test_target, axis=1)
    return {
        "scale": round(float(scale), 4),
        "rotation_deg": round(float(np.degrees(np.arccos(np.clip((np.trace(rotation) - 1) / 2, -1, 1)))), 4),
        "translation": round(float(np.linalg.norm(shift)), 4),
        "holdout_3d_pass_fraction": round(float(np.mean(distances < 0.1)), 4),
        "distance_p50_p90_p95": [round(float(v), 4) for v in np.quantile(distances, [0.5, 0.9, 0.95])],
    }


def stereo_pointmap_checks(dataset, source, target, shape, source_xy, target_xy, X, C):
    manifest = json.loads((dataset / "dataset_manifest.json").read_text())
    camera = manifest["camera_info"]
    _, (scale_w, scale_h, crop_w, crop_h) = resize_img(
        np.zeros((camera["height"], camera["width"], 3)),
        512, return_transformation=True,
    )
    K = np.array([
        [camera["fx"] / scale_w, 0, camera["ppx"] / scale_w - crop_w],
        [0, camera["fy"] / scale_h, camera["ppy"] / scale_h - crop_h],
        [0, 0, 1],
    ])
    provider = StereoDepthProvider.from_dataset(dataset)
    depths = [
        provider.get_depth(dataset / f"{index:010d}.png", shape)
        for index in (source, target)
    ]
    pose, scale, report = metric_descriptor_pose(
        source_xy, target_xy, depths[0], depths[1],
        X[0, ..., 2].cpu().numpy(), C[0].cpu().numpy(), K,
        minimum_confidence=1.0,
    )
    if pose is None:
        return {"accepted": False, "reason": report.get("reason")}

    def residuals(which):
        xy = (source_xy, target_xy)[which]
        depth = depths[which][xy[:, 1], xy[:, 0]]
        keep = np.isfinite(depth) & (depth >= 0.15) & (depth <= 0.65)
        xy, depth = xy[keep], depth[keep]
        metric = np.column_stack((
            (xy[:, 0] - K[0, 2]) * depth / K[0, 0],
            (xy[:, 1] - K[1, 2]) * depth / K[1, 1],
            depth,
        ))
        if which:
            metric = (metric - pose[:3, 3]) @ pose[:3, :3]
        model_points = scale * X[which, xy[:, 1], xy[:, 0]].cpu().numpy()
        difference = model_points - metric
        length = np.linalg.norm(difference, axis=1)
        return {
            "points": len(xy),
            "median_xyz_error_mm": [round(float(v * 1000), 2) for v in np.median(difference, axis=0)],
            "distance_p50_p95_mm": [round(float(v * 1000), 2) for v in np.quantile(length, [0.5, 0.95])],
        }

    return {
        "accepted": True, "metric_scale": round(scale, 5),
        "stereo_pnp_inliers": report["inlier_points"],
        "stereo_depth_p95_mm": round(report["depth_p95_m"] * 1000, 2),
        "source": residuals(0), "target_in_source_frame": residuals(1),
    }


@torch.inference_mode()
def probe(model, dataset, source, target, cache):
    X, C, D, _ = mast3r_asymmetric_inference(
        model, load_frame(dataset, source, cache), load_frame(dataset, target, cache)
    )
    current, previous = X[0:1], X[1:2]
    rays, points, initial = prep_for_iter_proj(current, previous, None)
    settings = config["matching"]
    projection, valid = mast3r_slam_backends.iter_proj(
        rays, points, initial, settings["max_iter"],
        settings["lambda_init"], settings["convergence_thresh"],
    )
    projection = projection.long()
    height, width = X.shape[1:3]

    def distance(coordinates):
        xy = coordinates[0]
        return torch.linalg.vector_norm(
            current[0, xy[:, 1], xy[:, 0]] - previous[0].reshape(-1, 3), dim=1
        )

    original_distance = distance(projection)
    (refined,) = mast3r_slam_backends.refine_matches(
        D[0:1].half(), D[1:2].reshape(1, height * width, -1).half(),
        projection, settings["radius"], settings["dilation_max"],
    )
    refined_distance = distance(refined)
    source_xy, target_xy = fast_reciprocal_NNs(
        D[0], D[1], subsample_or_initxy1=8,
        device="cuda:0", dist="dot", block_size=8192,
    )
    source_xy = torch.as_tensor(source_xy.copy(), device="cuda:0")
    target_xy = torch.as_tensor(target_xy.copy(), device="cuda:0")
    stereo_xy = (source_xy.cpu().numpy(), target_xy.cpu().numpy())
    descriptor_distance = torch.linalg.vector_norm(
        current[0, source_xy[:, 1], source_xy[:, 0]]
        - previous[0, target_xy[:, 1], target_xy[:, 0]], dim=1
    )
    depth_ratio = (
        previous[0, target_xy[:, 1], target_xy[:, 0], 2]
        / current[0, source_xy[:, 1], source_xy[:, 0], 2]
    )
    matched_source = current[0, source_xy[:, 1], source_xy[:, 0]].cpu().numpy()
    matched_target = previous[0, target_xy[:, 1], target_xy[:, 0]].cpu().numpy()
    threshold = settings["dist_thresh"]
    stereo_check = stereo_pointmap_checks(
        dataset, source, target, (height, width), *stereo_xy, X, C
    )
    meter_scale = stereo_check.get("metric_scale")
    return {
        "source": source, "target": target,
        "projection_valid_fraction": round(float(valid.float().mean()), 4),
        "original_3d_pass_fraction": round(float((valid[0] & (original_distance < threshold)).float().mean()), 4),
        "refined_3d_pass_fraction": round(float((valid[0] & (refined_distance < threshold)).float().mean()), 4),
        "projection_distance_p50_p90_p95": quantiles(original_distance),
        "descriptor_matches": len(source_xy),
        "descriptor_3d_pass_fraction": round(float((descriptor_distance < threshold).float().mean()), 4),
        "descriptor_distance_p50_p90_p95": quantiles(descriptor_distance),
        "descriptor_depth_ratio_p50_p90_p95": quantiles(depth_ratio),
        "fixed_10mm_3d_pass_fraction": (
            round(float((valid[0] & (original_distance * meter_scale < 0.01)).float().mean()), 4)
            if meter_scale is not None else None
        ),
        "best_rigid_3d_fit": fitted_3d_residual(matched_source, matched_target, False),
        "best_similarity_3d_fit": fitted_3d_residual(matched_source, matched_target, True),
        "stereo_pointmap": stereo_check,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pair", action="append", required=True, help="source:target")
    args = parser.parse_args()
    load_config(str(args.config))
    model = load_mast3r(str(args.checkpoint), device="cuda:0").eval()
    cache = {}
    for pair in args.pair:
        source, target = (int(value) for value in pair.split(":"))
        report = probe(model, args.dataset, source, target, cache)
        print({
            "source": source, "target": target,
            "projection_valid_fraction": report["projection_valid_fraction"],
            "original_3d_pass_fraction": report["original_3d_pass_fraction"],
            "fixed_10mm_3d_pass_fraction": report["fixed_10mm_3d_pass_fraction"],
            "metric_scale": report["stereo_pointmap"].get("metric_scale"),
            "holdout_sim3_pass_fraction": report["best_similarity_3d_fit"]["holdout_3d_pass_fraction"],
            "stereo_pnp_inliers": report["stereo_pointmap"].get("stereo_pnp_inliers"),
            "stereo_depth_p95_mm": report["stereo_pointmap"].get("stereo_depth_p95_mm"),
            "stereo_reject_reason": report["stereo_pointmap"].get("reason"),
        }, flush=True)


if __name__ == "__main__":
    main()

"""Compare MASt3R projection, 3D distance, and descriptor matches on frame pairs.

Diagnostic only: no trajectory, external reference, or model parameter is changed.
Run with the MASt3R-SLAM virtualenv and its repository as the working directory.
"""

import argparse
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import torch
from scipy.ndimage import distance_transform_edt, gaussian_filter
from scipy.spatial.transform import Rotation

from mast3r.fast_nn import fast_reciprocal_NNs
import mast3r_slam_backends
from mast3r_slam.config import config, load_config
from mast3r_slam.mast3r_utils import (
    load_mast3r, mast3r_asymmetric_inference, resize_img,
)
from mast3r_slam.matching import prep_for_iter_proj
from mast3r_slam.matching import match as match_pointmaps
from mast3r_slam.stereo_depth import (
    StereoDepthProvider, solve_metric_keyframe_pnp,
)
from mast3r_slam.stereo_descriptor_recovery import (
    imu_rotation_agrees, metric_descriptor_pose,
)


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


def heldout_spatial_depth_correction(model, metric, valid, cell_size=16):
    """Diagnostic: fit depth residuals in alternating cells, test unseen cells."""
    height, width = valid.shape
    if model.shape != metric.shape or model.shape != (height, width, 3):
        raise ValueError("pointmaps and validity mask must share an image grid")
    rows, cols = np.indices((height, width))
    training = valid & (((rows // cell_size + cols // cell_size) % 2) == 0)
    holdout = valid & ~training
    n_rows = (height + cell_size - 1) // cell_size
    n_cols = (width + cell_size - 1) // cell_size
    field = np.zeros((n_rows, n_cols, 3), dtype=np.float32)
    known = np.zeros((n_rows, n_cols), dtype=bool)
    for row in range(n_rows):
        for col in range(n_cols):
            patch = training[
                row * cell_size:(row + 1) * cell_size,
                col * cell_size:(col + 1) * cell_size,
            ]
            if np.count_nonzero(patch) < 8:
                continue
            delta = (metric - model)[
                row * cell_size:(row + 1) * cell_size,
                col * cell_size:(col + 1) * cell_size,
            ][patch]
            field[row, col] = np.median(delta, axis=0)
            known[row, col] = True
    if np.count_nonzero(known) < 10 or np.count_nonzero(holdout) < 100:
        return model, {"accepted": False, "reason": "insufficient_spatial_support"}
    nearest = distance_transform_edt(~known, return_distances=False, return_indices=True)
    field = field[tuple(nearest)]
    field = gaussian_filter(field, sigma=(1.0, 1.0, 0.0))
    correction = cv2.resize(field, (width, height), interpolation=cv2.INTER_LINEAR)
    corrected = model + correction
    before = np.linalg.norm((model - metric)[holdout], axis=1)
    after = np.linalg.norm((corrected - metric)[holdout], axis=1)
    return corrected, {
        "accepted": True,
        "training_cells": int(np.count_nonzero(known)),
        "holdout_points": int(np.count_nonzero(holdout)),
        "before_p50_mm": round(float(np.median(before) * 1000), 2),
        "after_p50_mm": round(float(np.median(after) * 1000), 2),
        "before_p95_mm": round(float(np.percentile(before, 95) * 1000), 2),
        "after_p95_mm": round(float(np.percentile(after, 95) * 1000), 2),
    }


def stereo_pointmap_checks(dataset, source, target, shape, source_xy, target_xy, X, C, D):
    if source == target:
        raise ValueError("source and target frames must differ")
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

    prior_rows = list(csv.DictReader((dataset / "imu_rotation_priors.csv").open()))
    prior = Rotation.identity()
    for index in range(min(source, target) + 1, max(source, target) + 1):
        row = prior_rows[index]
        prior = prior * Rotation.from_quat([
            float(row[key]) for key in ("qx", "qy", "qz", "qw")
        ])
    # CSV row k is C_(k-1) <- C_k. The check below expects source <- target.
    if source > target:
        prior = prior.inv()
    imu_agrees, imu_disagreement = imu_rotation_agrees(
        pose, prior.as_quat()
    )

    def backproject(xy, depth):
        return np.column_stack((
            (xy[:, 0] - K[0, 2]) * depth / K[0, 0],
            (xy[:, 1] - K[1, 2]) * depth / K[1, 1],
            depth,
        ))

    spatial_correction = {"accepted": False, "reason": "imu_rotation_disagreement"}
    if imu_agrees:
        pixels_y, pixels_x = np.indices(shape)
        pixels = np.column_stack((pixels_x.ravel(), pixels_y.ravel()))
        corrected_pointmaps = []
        correction_reports = []
        for which in (0, 1):
            depth = depths[which]
            metric_points = backproject(pixels, np.nan_to_num(depth, nan=0.0).ravel())
            metric_points = metric_points.reshape(*shape, 3)
            if which:
                metric_points = (metric_points - pose[:3, 3]) @ pose[:3, :3]
            model_points = scale * X[which].cpu().numpy()
            valid_depth = (
                np.isfinite(depth) & (depth >= 0.15) & (depth <= 0.65)
                & np.isfinite(model_points).all(axis=2) & (model_points[..., 2] > 0)
                & (C[which].cpu().numpy() >= 1.0)
            )
            corrected, result = heldout_spatial_depth_correction(
                model_points, metric_points, valid_depth
            )
            corrected_pointmaps.append(corrected)
            correction_reports.append(result)
        if all(item["accepted"] for item in correction_reports):
            candidate = [
                torch.as_tensor(pointmap / scale, device="cuda:0", dtype=X.dtype)[None]
                for pointmap in corrected_pointmaps
            ]
            _, new_matches = match_pointmaps(
                candidate[0], candidate[1], D[0:1], D[1:2]
            )
            spatial_correction = {
                "accepted": True,
                "source": correction_reports[0],
                "target": correction_reports[1],
                "corrected_3d_match_fraction": round(float(new_matches.float().mean()), 4),
            }

    def pair_summary(residual):
        return {
            "p50_mm": round(float(np.median(residual) * 1000), 2),
            "p95_mm": round(float(np.percentile(residual, 95) * 1000), 2),
            "within_20mm": round(float(np.mean(residual < 0.02)), 4),
        }

    grid_y, grid_x = np.mgrid[0:shape[0]:8, 0:shape[1]:8]
    grid_xy = np.column_stack((grid_x.ravel(), grid_y.ravel()))
    model_xyz = scale * X[1, grid_xy[:, 1], grid_xy[:, 0]].cpu().numpy()
    model_confidence = C[1, grid_xy[:, 1], grid_xy[:, 0]].cpu().numpy()
    model_valid = (
        np.isfinite(model_xyz).all(axis=1) & (model_xyz[:, 2] > 0)
        & (model_confidence >= 1.0)
    )
    model_pose, model_pose_report = solve_metric_keyframe_pnp(
        model_xyz[model_valid], grid_xy[model_valid].astype(np.float32), K,
        minimum_points=100, minimum_inlier_ratio=0.4,
        reprojection_error_px=2.0,
    )
    model_pose_difference = None
    if model_pose is not None:
        model_pose_difference = {
            "translation_mm": round(float(np.linalg.norm(
                model_pose[:3, 3] - pose[:3, 3]
            ) * 1000), 2),
            "rotation_deg": round(float(np.rad2deg((
                Rotation.from_matrix(model_pose[:3, :3]).inv()
                * Rotation.from_matrix(pose[:3, :3])
            ).magnitude())), 3),
        }

    target_grid_depth = depths[1][grid_xy[:, 1], grid_xy[:, 0]]
    metric_grid_valid = (
        model_valid & np.isfinite(target_grid_depth)
        & (target_grid_depth >= 0.15) & (target_grid_depth <= 0.65)
    )
    target_grid_metric = backproject(
        grid_xy[metric_grid_valid], target_grid_depth[metric_grid_valid]
    )
    model_in_target = model_xyz[metric_grid_valid] @ prior.inv().as_matrix().T
    fit_translation = np.median(
        (target_grid_metric - model_in_target)[::2], axis=0
    )
    holdout_residual = np.linalg.norm(
        target_grid_metric[1::2] - model_in_target[1::2] - fit_translation,
        axis=1,
    )
    model_stereo_anchor = {
        "points": int(len(target_grid_metric)),
        "translation_difference_from_stereo_pnp_mm": round(float(
            np.linalg.norm(fit_translation - pose[:3, 3]) * 1000
        ), 2),
        "holdout": pair_summary(holdout_residual),
    }

    source_z = depths[0][source_xy[:, 1], source_xy[:, 0]]
    target_z = depths[1][target_xy[:, 1], target_xy[:, 0]]
    both_depths = (
        np.isfinite(source_z) & np.isfinite(target_z)
        & (source_z >= 0.15) & (source_z <= 0.65)
        & (target_z >= 0.15) & (target_z <= 0.65)
    )
    paired_source = source_xy[both_depths]
    paired_target = target_xy[both_depths]

    source_metric = backproject(paired_source, source_z[both_depths])
    target_metric = backproject(paired_target, target_z[both_depths])
    gyro_target_from_source = prior.inv().as_matrix()
    translation_samples = target_metric - source_metric @ gyro_target_from_source.T
    gyro_translation = np.median(translation_samples, axis=0)
    gyro_pair_residual = np.linalg.norm(
        translation_samples - gyro_translation, axis=1
    )
    target_in_source = (target_metric - pose[:3, 3]) @ pose[:3, :3]
    source_model = scale * X[0, paired_source[:, 1], paired_source[:, 0]].cpu().numpy()
    target_model = scale * X[1, paired_target[:, 1], paired_target[:, 0]].cpu().numpy()
    descriptor_translation_samples = (
        target_metric - target_model @ gyro_target_from_source.T
    )
    descriptor_translation = np.median(descriptor_translation_samples[::2], axis=0)
    descriptor_model_anchor = {
        "points": len(target_metric),
        "translation_difference_from_stereo_pnp_mm": round(float(
            np.linalg.norm(descriptor_translation - pose[:3, 3]) * 1000
        ), 2),
        "holdout": pair_summary(np.linalg.norm(
            descriptor_translation_samples[1::2] - descriptor_translation,
            axis=1,
        )),
    }
    stereo_residual = np.linalg.norm(source_metric - target_in_source, axis=1)
    model_residual = np.linalg.norm(source_model - target_model, axis=1)
    if spatial_correction["accepted"]:
        corrected_source = corrected_pointmaps[0][
            paired_source[:, 1], paired_source[:, 0]
        ]
        corrected_target = corrected_pointmaps[1][
            paired_target[:, 1], paired_target[:, 0]
        ]
        spatial_correction["descriptor_pair"] = pair_summary(
            np.linalg.norm(corrected_source - corrected_target, axis=1)
        )
        heldout_pairs = (
            ((paired_source[:, 0] // 16 + paired_source[:, 1] // 16) % 2 == 1)
            & ((paired_target[:, 0] // 16 + paired_target[:, 1] // 16) % 2 == 1)
        )
        spatial_correction["heldout_descriptor_points"] = int(heldout_pairs.sum())
        if heldout_pairs.sum() >= 100:
            spatial_correction["heldout_descriptor_pair"] = pair_summary(
                np.linalg.norm(
                    corrected_source[heldout_pairs] - corrected_target[heldout_pairs],
                    axis=1,
                )
            )
    source_residual = np.linalg.norm(source_model - source_metric, axis=1)
    target_residual = np.linalg.norm(target_model - target_in_source, axis=1)

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
        "imu_rotation_disagreement_deg": round(imu_disagreement, 3),
        "imu_rotation_agrees": imu_agrees,
        "spatial_correction": spatial_correction,
        "model_pose_report": model_pose_report,
        "model_pose_difference_from_stereo": model_pose_difference,
        "model_stereo_anchor": model_stereo_anchor,
        "descriptor_model_anchor": descriptor_model_anchor,
        "paired_depth_points": len(paired_source),
        "stereo_pair": pair_summary(stereo_residual),
        "gyro_fixed_stereo_pair": pair_summary(gyro_pair_residual),
        "gyro_translation_difference_from_pnp_mm": round(float(
            np.linalg.norm(gyro_translation - pose[:3, 3]) * 1000
        ), 2),
        "model_pair": pair_summary(model_residual),
        "model_source_to_stereo": pair_summary(source_residual),
        "model_target_to_stereo": pair_summary(target_residual),
        "stereo_consistent_model_inconsistent": round(float(np.mean(
            (stereo_residual < 0.02) & (model_residual >= 0.02)
        )), 4),
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
        dataset, source, target, (height, width), *stereo_xy, X, C, D
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
        print(json.dumps({
            "source": source, "target": target,
            "projection_valid_fraction": report["projection_valid_fraction"],
            "original_3d_pass_fraction": report["original_3d_pass_fraction"],
            "refined_3d_pass_fraction": report["refined_3d_pass_fraction"],
            "descriptor_3d_pass_fraction": report["descriptor_3d_pass_fraction"],
            "fixed_10mm_3d_pass_fraction": report["fixed_10mm_3d_pass_fraction"],
            "metric_scale": report["stereo_pointmap"].get("metric_scale"),
            "holdout_sim3_pass_fraction": report["best_similarity_3d_fit"]["holdout_3d_pass_fraction"],
            "stereo_pnp_inliers": report["stereo_pointmap"].get("stereo_pnp_inliers"),
            "stereo_depth_p95_mm": report["stereo_pointmap"].get("stereo_depth_p95_mm"),
            "imu_rotation_disagreement_deg": report["stereo_pointmap"].get("imu_rotation_disagreement_deg"),
            "imu_rotation_agrees": report["stereo_pointmap"].get("imu_rotation_agrees"),
            "spatial_correction": report["stereo_pointmap"].get("spatial_correction"),
            "model_pose_report": report["stereo_pointmap"].get("model_pose_report"),
            "model_pose_difference_from_stereo": report["stereo_pointmap"].get("model_pose_difference_from_stereo"),
            "model_stereo_anchor": report["stereo_pointmap"].get("model_stereo_anchor"),
            "descriptor_model_anchor": report["stereo_pointmap"].get("descriptor_model_anchor"),
            "stereo_reject_reason": report["stereo_pointmap"].get("reason"),
            "paired_depth_points": report["stereo_pointmap"].get("paired_depth_points"),
            "stereo_pair": report["stereo_pointmap"].get("stereo_pair"),
            "gyro_fixed_stereo_pair": report["stereo_pointmap"].get("gyro_fixed_stereo_pair"),
            "gyro_translation_difference_from_pnp_mm": report["stereo_pointmap"].get("gyro_translation_difference_from_pnp_mm"),
            "model_pair": report["stereo_pointmap"].get("model_pair"),
            "model_source_to_stereo": report["stereo_pointmap"].get("model_source_to_stereo"),
            "model_target_to_stereo": report["stereo_pointmap"].get("model_target_to_stereo"),
            "stereo_consistent_model_inconsistent": report["stereo_pointmap"].get("stereo_consistent_model_inconsistent"),
        }), flush=True)


if __name__ == "__main__":
    main()

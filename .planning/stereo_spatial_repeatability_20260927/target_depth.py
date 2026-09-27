"""Held-out target stereo disparity consistency, not absolute pose accuracy."""
import cv2
import numpy as np
from scipy.spatial.transform import Rotation


def target_depth_consistency(xyz, uv, k, rvec, tvec, target_disparity, valid, baseline_m):
    xyz, uv, k = np.asarray(xyz), np.asarray(uv), np.asarray(k)
    target_disparity = np.asarray(target_disparity)
    valid = np.asarray(valid, dtype=bool).copy()
    target_xyz = Rotation.from_rotvec(np.asarray(rvec).ravel()).apply(xyz) + np.asarray(tvec).ravel()
    valid &= np.isfinite(target_disparity) & (target_disparity > 0.5) & (target_xyz[:, 2] > 0)
    n = int(valid.sum())
    result = {'status': 'measured' if n >= 20 else 'insufficient_target_depth',
              'source_pnp_inliers': len(xyz), 'valid_target_depth': n,
              'valid_target_depth_fraction': float(n / len(xyz)),
              'is_absolute_accuracy': False}
    if n < 20:
        return result
    # Target-left reprojection was fitted; target-right disparity was not.
    predicted = k[0, 0] * baseline_m / target_xyz[valid, 2]
    measured = target_disparity[valid]
    residual = predicted - measured
    measured_z = k[0, 0] * baseline_m / measured
    depth_residual_mm = (target_xyz[valid, 2] - measured_z) * 1000
    reprojection = cv2.projectPoints(xyz[valid], rvec, tvec, k, None)[0].reshape(-1, 2)
    result.update(
        signed_disparity_median_px=float(np.median(residual)),
        absolute_disparity_median_px=float(np.median(np.abs(residual))),
        absolute_disparity_p95_px=float(np.percentile(np.abs(residual), 95)),
        signed_depth_median_mm=float(np.median(depth_residual_mm)),
        absolute_depth_median_mm=float(np.median(np.abs(depth_residual_mm))),
        absolute_depth_p95_mm=float(np.percentile(np.abs(depth_residual_mm), 95)),
        target_depth_median_m=float(np.median(measured_z)),
        predicted_to_measured_depth_ratio_median=float(np.median(target_xyz[valid, 2] / measured_z)),
        fitted_left_reprojection_median_px=float(np.median(np.linalg.norm(reprojection - uv[valid], axis=1))),
    )
    return result

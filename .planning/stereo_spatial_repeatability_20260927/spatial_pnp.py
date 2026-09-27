"""Delete-one-spatial-region sensitivity; NOT a calibrated covariance estimator."""
import cv2
import numpy as np
from scipy.spatial.transform import Rotation


def displacement(rvec, tvec):
    return -Rotation.from_rotvec(np.asarray(rvec).ravel()).inv().apply(np.asarray(tvec).ravel())


def spatial_repeatability(object_points, image_points, k, rvec, tvec, *, image_size):
    xyz = np.asarray(object_points, dtype=np.float64)
    uv = np.asarray(image_points, dtype=np.float64)
    k = np.asarray(k, dtype=np.float64)
    rvec = np.asarray(rvec, dtype=float).reshape(3, 1)
    tvec = np.asarray(tvec, dtype=float).reshape(3, 1)
    if len(xyz) < 20 or xyz.shape != (len(uv), 3) or uv.shape != (len(xyz), 2):
        raise ValueError('Need at least20 original PnP inliers with valid shapes')
    if not np.all(np.isfinite(xyz)) or not np.all(np.isfinite(uv)) or np.any(xyz[:, 2] <= 0):
        raise ValueError('Nonfinite or nonpositive source geometry')
    width, height = image_size
    source_uv = xyz[:, :2] / xyz[:, 2, None] * np.array([k[0, 0], k[1, 1]]) + k[:2, 2]
    tiles = np.clip((source_uv / np.array([width, height]) * 4).astype(int), 0, 3)
    tile_ids = tiles[:, 1] * 4 + tiles[:, 0]
    unique, counts = np.unique(tile_ids, return_counts=True)
    original_delta = displacement(rvec, tvec)
    baseline_rvec, baseline_tvec = cv2.solvePnPRefineLM(xyz, uv, k, None, rvec.copy(), tvec.copy())
    baseline_rotation = Rotation.from_rotvec(baseline_rvec.ravel())
    baseline_delta = displacement(baseline_rvec, baseline_tvec)
    projected = cv2.projectPoints(xyz, baseline_rvec, baseline_tvec, k, None)[0].reshape(-1, 2)
    errors = np.linalg.norm(projected - uv, axis=1)
    folds = []
    delta_vectors = []
    for tile, count in zip(unique, counts):
        keep = tile_ids != tile
        fold = {'tile': int(tile), 'removed_inliers': int(count), 'remaining_inliers': int(keep.sum())}
        # Removing the dominant region is not an independently observable fit.
        if keep.sum() < max(20, int(np.ceil(0.5 * len(xyz)))):
            fold['status'] = 'insufficient_remaining_inliers'
            folds.append(fold)
            continue
        jacobian = cv2.projectPoints(xyz[keep], baseline_rvec, baseline_tvec, k, None)[1][:, :6]
        fold['jacobian_condition_number'] = float(np.linalg.cond(jacobian))
        if np.linalg.matrix_rank(jacobian) < 6:
            fold['status'] = 'rank_deficient'
            folds.append(fold)
            continue
        fold_rvec, fold_tvec = cv2.solvePnPRefineLM(xyz[keep], uv[keep], k, None, baseline_rvec.copy(), baseline_tvec.copy())
        if not np.all(np.isfinite(fold_rvec)) or not np.all(np.isfinite(fold_tvec)):
            fold['status'] = 'invalid_refit'
            folds.append(fold)
            continue
        fold_rotation = Rotation.from_rotvec(fold_rvec.ravel())
        if np.any(fold_rotation.apply(xyz[keep])[:, 2] + fold_tvec.ravel()[2] <= 0):
            fold['status'] = 'invalid_refit'
            folds.append(fold)
            continue
        delta = displacement(fold_rvec, fold_tvec)
        variation = delta - baseline_delta
        delta_vectors.append(variation)
        fold.update(status='valid', displacement_m=delta.tolist(),
                    translation_change_mm=float(np.linalg.norm(variation) * 1000),
                    rotation_change_deg=float(np.degrees((baseline_rotation.inv() * fold_rotation).magnitude())))
        folds.append(fold)
    valid = [fold for fold in folds if fold['status'] == 'valid']
    observable = len(valid) >= 4
    deltas = np.asarray(delta_vectors).reshape(-1, 3)
    return {
        'status': 'observable' if observable else 'insufficient_spatial_support',
        'is_calibrated_covariance': False,
        'inliers': len(xyz), 'occupied_tiles': len(unique), 'valid_folds': len(valid),
        'tile_inlier_counts': {str(tile): int(count) for tile, count in zip(unique, counts)},
        'max_tile_fraction': float(counts.max() / len(xyz)),
        'source_bbox_px': [source_uv.min(axis=0).tolist(), source_uv.max(axis=0).tolist()],
        'baseline_displacement_m': baseline_delta.tolist(),
        'original_displacement_m': original_delta.tolist(),
        'refinement_shift_mm': float(np.linalg.norm(baseline_delta - original_delta) * 1000),
        'baseline_reprojection_median_px': float(np.median(errors)),
        'baseline_reprojection_p95_px': float(np.percentile(errors, 95)),
        'translation_p95_mm': float(np.percentile([fold['translation_change_mm'] for fold in valid], 95)) if observable else None,
        'translation_max_mm': max(fold['translation_change_mm'] for fold in valid) if observable else None,
        'rotation_max_deg': max(fold['rotation_change_deg'] for fold in valid) if observable else None,
        # Describes variation about the all-inlier control. Unequal correlated
        # deletion groups do NOT give a statistically calibrated covariance.
        'spatial_sensitivity_second_moment_m2': (deltas.T @ deltas / len(deltas)).tolist() if observable else None,
        'folds': folds,
    }

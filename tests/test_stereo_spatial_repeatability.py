"""Spatial deletion diagnostics must not masquerade as calibrated covariance."""
import importlib.util
from pathlib import Path

import cv2
import numpy as np

SOURCE = Path(__file__).resolve().parents[1] / '.planning/stereo_spatial_repeatability_20260927/spatial_pnp.py'
spec = importlib.util.spec_from_file_location('spatial_pnp', SOURCE)
spatial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spatial)


def scene():
    k = np.array([[650.0, 0, 640], [0, 650.0, 360], [0, 0, 1]])
    u, v = np.meshgrid(np.linspace(80, 1200, 12), np.linspace(60, 660, 8))
    z = 0.4 + 0.05 * np.sin(u.ravel() / 100)
    xyz = np.column_stack(((u.ravel() - 640) * z / 650, (v.ravel() - 360) * z / 650, z))
    rvec = np.array([0.03, -0.04, 0.02])
    tvec = np.array([-0.015, 0.008, 0.002])
    uv = cv2.projectPoints(xyz, rvec, tvec, k, None)[0].reshape(-1, 2)
    return xyz, uv, k, rvec, tvec


def test_exact_geometry_is_spatially_repeatable():
    result = spatial.spatial_repeatability(*scene(), image_size=(1280, 720))
    assert result['status'] == 'observable'
    assert result['occupied_tiles'] == 16
    assert result['valid_folds'] == 16
    assert result['translation_max_mm'] < 1e-5
    assert result['rotation_max_deg'] < 1e-6
    assert result['is_calibrated_covariance'] is False


def test_coherent_image_region_error_changes_deleted_region_solution():
    xyz, uv, k, rvec, tvec = scene()
    uv[xyz[:, 0] > 0, 0] += 3.0
    result = spatial.spatial_repeatability(xyz, uv, k, rvec, tvec, image_size=(1280, 720))
    assert result['status'] == 'observable'
    assert result['translation_max_mm'] > 0.05
    assert result['refinement_shift_mm'] > 0.05
    assert result['baseline_reprojection_median_px'] < 3.0
    assert len(result['spatial_sensitivity_second_moment_m2']) == 3


def test_one_tile_is_unobservable_not_zero_uncertainty():
    xyz, _, k, rvec, tvec = scene()
    xyz[:, 0] = np.linspace(-0.02, -0.01, len(xyz))
    xyz[:, 1] = np.linspace(-0.02, -0.01, len(xyz))
    uv = cv2.projectPoints(xyz, rvec, tvec, k, None)[0].reshape(-1, 2)
    result = spatial.spatial_repeatability(xyz, uv, k, rvec, tvec, image_size=(1280, 720))
    assert result['status'] == 'insufficient_spatial_support'
    assert result['translation_max_mm'] is None
    assert result['valid_folds'] == 0


def test_nonfinite_fold_is_invalid_not_a_nan_sensitivity(monkeypatch):
    original = cv2.solvePnPRefineLM
    calls = 0

    def nonfinite_first_fold(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            return np.full((3, 1), np.nan), np.zeros((3, 1))
        return original(*args, **kwargs)

    monkeypatch.setattr(cv2, 'solvePnPRefineLM', nonfinite_first_fold)
    result = spatial.spatial_repeatability(*scene(), image_size=(1280, 720))
    assert result['status'] == 'observable'
    assert sum(fold['status'] == 'invalid_refit' for fold in result['folds']) == 1
    assert np.isfinite(result['translation_max_mm'])

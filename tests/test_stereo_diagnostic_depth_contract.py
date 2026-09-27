"""Diagnostic replay must not silently extend the formal stereo depth range."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


def probe():
    path = Path(__file__).resolve().parents[1] / '.planning/stereo_factor_attribution_20260927/probe_raw_gyro_pnp.py'
    spec = importlib.util.spec_from_file_location('depth_contract_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formal_depth_is_default():
    args = probe().argument_parser().parse_args(['--output', '/tmp/unused-depth-contract'])
    assert args.max_depth_m == 0.6


def test_legacy_diagnostic_requires_explicit_override():
    args = probe().argument_parser().parse_args([
        '--output', '/tmp/unused-depth-contract', '--max-depth-m', '1.5'])
    assert args.max_depth_m == 1.5


def test_refinement_candidate_only_changes_free_solver():
    path = Path(__file__).resolve().parents[1] / '.planning/stereo_spatial_repeatability_20260927/probe_free_refinement.py'
    spec = importlib.util.spec_from_file_location('refinement_contract_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return 'unchanged-return'

    assert module.refine_free_motion(original, 'same-points', pnp_rotation_mode='free') == 'unchanged-return'
    assert calls[-1] == (('same-points',), dict(pnp_rotation_mode='free', refine_pnp=True))
    module.refine_free_motion(original, 'same-points', pnp_rotation_mode='trajectory-fixed')
    assert calls[-1] == (('same-points',), dict(pnp_rotation_mode='trajectory-fixed'))


@pytest.mark.parametrize('bad_refinement', ['nonfinite', 'worse'])
def test_existing_refinement_guard_retains_original_pose(monkeypatch, bad_refinement):
    path = Path(__file__).resolve().parents[1] / 'scripts/align_mast3r_scale_with_stereo.py'
    spec = importlib.util.spec_from_file_location('refinement_safety_stereo', path)
    stereo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stereo)
    uv = np.array([(x, y) for x in np.linspace(10, 90, 8)
                   for y in np.linspace(10, 90, 4)], dtype=np.float32)
    valid = np.ones(len(uv), dtype=bool)
    monkeypatch.setattr(stereo, 'left_right_consistent',
                        lambda *a, **k: (valid.copy(), np.full(len(uv), 6.0)))
    monkeypatch.setattr(stereo.cv2, 'solvePnPRansac', lambda *a, **k: (
        True, np.zeros((3, 1)), np.array([[-.01], [0.0], [0.0]]),
        np.arange(len(uv)).reshape(-1, 1)))
    if bad_refinement == 'nonfinite':
        pose = np.full((3, 1), np.nan), np.full((3, 1), np.nan)
    else:
        pose = np.zeros((3, 1)), np.array([[.2], [0.0], [0.0]])
    monkeypatch.setattr(stereo.cv2, 'solvePnPRefineLM', lambda *a, **k: pose)
    calibration = dict(left=dict(fx=100.0, fy=100.0, cx=50.0, cy=50.0), baseline_m=.018)
    disparity = np.full((100, 100), 6.0)
    args = (uv, uv-np.array([100*.01/.3, 0]), valid, disparity, -disparity,
            np.zeros(3), np.array([.01, 0, 0]), Rotation.identity(), Rotation.identity(),
            calibration, .07, .6, 'sift')
    baseline = stereo.estimate_motion_from_correspondences(*args, trajectory_frame='infrared_left')
    refined = stereo.estimate_motion_from_correspondences(*args, trajectory_frame='infrared_left', refine_pnp=True)
    assert baseline['accepted']
    assert refined == baseline
    assert not refined['pnp_refined']

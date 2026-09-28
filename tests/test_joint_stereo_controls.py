import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('joint_controls',
    ROOT/'.planning/metric_window_bundle_20260928/run_joint_window_controls.py')
controls = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controls)


def test_uniform_pairs_have_one_boundary_and_full_raw_frame_coverage():
    pairs = controls.pair_windows(1200)
    assert len(pairs) == 5
    np.testing.assert_array_equal(pairs[0], np.arange(100, 141, 5))
    assert all(len(p) == 9 and np.all(np.diff(p) == 5) for p in pairs)
    np.testing.assert_array_equal(controls.base.tracking_frames(pairs[0]), np.arange(100, 141))
    with pytest.raises(ValueError):
        controls.pair_windows(196)
    assert controls.pair_windows(197)[0][0] == 0


def test_second_endpoint_is_in_second_start_camera_not_first_gauge():
    centers = np.column_stack((np.linspace(0, .08, 9), np.zeros((9, 2))))
    rotations = Rotation.from_euler('z', np.linspace(0, np.pi/2, 9))
    a, b = controls.relative_endpoints(centers, rotations)
    np.testing.assert_allclose(a, [.04, 0, 0], atol=1e-12)
    np.testing.assert_allclose(b, [.04/np.sqrt(2), -.04/np.sqrt(2), 0], atol=1e-12)
    with pytest.raises(ValueError):
        controls.relative_endpoints(centers[:8], rotations[:8])


def test_heldout_is_anchored_at_birth_pose_and_never_fit():
    k = dict(fx=300., fy=300., cx=160., cy=90.)
    centers = np.column_stack((np.linspace(0, .04, 9), np.zeros((9, 2))))
    rotations = Rotation.from_euler('y', np.linspace(0, .1, 9))
    points = np.array([[.01, .02, .3], [-.01, .02, .4]])
    birth = np.array([0, 4])
    observations = np.full((9, 2, 4), np.nan)
    valid = np.zeros((9, 2), bool)
    for j, point in enumerate(points):
        global_point = rotations[birth[j]].apply(point)+centers[birth[j]]
        for i in range(birth[j], 9):
            xyz = rotations[i].inv().apply(global_point-centers[i])
            px = []
            for baseline in [0., .018]:
                px.extend([k['fx']*(xyz[0]-baseline)/xyz[2]+k['cx'],
                           k['fy']*xyz[1]/xyz[2]+k['cy']])
            observations[i, j] = px
            valid[i, j] = True
    data = dict(observations=observations, valid=valid, heldout=np.ones(2, bool),
                birth_indices=birth, birth_points=points)
    calibration = dict(left_intrinsics=k, right_intrinsics=k, baseline_m=.018)
    assert controls.birth_heldout_rmse(data, calibration, centers, rotations) < 1e-10
    # A change of global gauge changes neither relative prediction nor score.
    gauge = Rotation.from_euler('xyz', [.2, -.1, .3])
    moved_centers = gauge.apply(centers)+[.2, -.1, .1]
    assert controls.birth_heldout_rmse(data, calibration, moved_centers,
                                      gauge*rotations) < 1e-10


def test_joint_refusal_is_not_independent_success_fallback(monkeypatch):
    refusal = dict(accepted=False, reason='model_consistency_failed')
    # Dispatch never converts a failed joint solve into accepted endpoint rows.
    rows = controls.endpoint_rows(refusal, np.arange(0, 41, 5), np.arange(41)/30,
                                  np.zeros((9, 3)), Rotation.identity(9), 1)
    assert len(rows) == 2
    assert not any(r['accepted'] for r in rows)
    assert all(r['reason'] == refusal['reason'] for r in rows)
    assert all(r['correlated_factors_from_shared_window'] for r in rows)
    assert not any('endpoint_m' in r for r in rows)


def prepared_pair():
    rng = np.random.default_rng(152)
    points = rng.uniform([-.08, -.06, .28], [.08, .06, .45], (70, 3))
    centers = np.column_stack((np.arange(9)*.004, np.zeros((9, 2))))
    rotations = Rotation.from_rotvec(np.column_stack((np.zeros(9), np.arange(9)*.002, np.zeros(9))))
    k = dict(fx=420., fy=420., cx=320., cy=240.)
    datasets = []
    for start in [0, 4]:
        observation = []
        for node in range(start, start+5):
            xyz = rotations[node].inv().apply(points-centers[node])
            observation.append(np.column_stack((k['fx']*xyz[:, 0]/xyz[:, 2]+k['cx'],
                k['fy']*xyz[:, 1]/xyz[:, 2]+k['cy'],
                k['fx']*(xyz[:, 0]-.018)/xyz[:, 2]+k['cx'], k['fy']*xyz[:, 1]/xyz[:, 2]+k['cy'])))
        dense = np.asarray(observation)[np.minimum(np.arange(21)//5, 4)]
        datasets.append(dict(accepted=True, observations=dense,
            valid=np.ones(dense.shape[:2], bool), initial_points=rotations[start].inv().apply(points-centers[start])))
    return datasets, dict(left_intrinsics=k, right_intrinsics=k.copy(), baseline_m=.018), centers, rotations


def test_real_joint_process_uses_shared_pixels_and_keeps_metric_frame(monkeypatch):
    datasets, calibration, centers, rotations = prepared_pair()
    monkeypatch.setattr(controls.base, 'track_stereo_window', lambda *a, **kw: datasets.pop(0))
    times = np.arange(9)/6
    gyro = rotations[:-1].inv()*rotations[1:]
    jac = np.tile(-np.eye(3)/6, (8, 1, 1))
    result, independent, info, joined = controls.process_pair([None]*41, [None]*41,
                                                             calibration, times, gyro, jac)
    assert result['accepted'], result
    assert all(r['accepted'] for r in independent)
    assert info['shared_training_landmarks'] >= 40
    assert info['optimized_heldout_rmse_px'] < 1e-3
    np.testing.assert_allclose(result['centers'], centers, atol=1e-5)
    assert joined['observations'].shape[0] == 9


def test_preparation_refusal_preserves_independent_controls(monkeypatch):
    datasets, calibration, _, rotations = prepared_pair()
    monkeypatch.setattr(controls.base, 'track_stereo_window', lambda *a, **kw: datasets.pop(0))
    def refused(*args, **kwargs):
        raise ValueError('invalid birth depth')
    monkeypatch.setattr(controls, 'join_stereo_windows', refused)
    times = np.arange(9)/6
    result, independent, info, _ = controls.process_pair([None]*41, [None]*41,
        calibration, times, rotations[:-1].inv()*rotations[1:], np.tile(-np.eye(3)/6, (8, 1, 1)))
    assert not result['accepted']
    assert result['reason'] == 'joint_preparation_failed'
    assert all(r['accepted'] for r in independent)
    assert info['joint_preparation_detail'] == 'invalid birth depth'


def test_joint_solve_exception_preserves_independent_controls(monkeypatch):
    datasets, calibration, _, rotations = prepared_pair()
    monkeypatch.setattr(controls.base, 'track_stereo_window', lambda *a, **kw: datasets.pop(0))
    def refused(*args, **kwargs):
        raise ValueError('training_support_collapsed_after_admission')
    monkeypatch.setattr(controls, 'solve', refused)
    times = np.arange(9)/6
    result, independent, _, _ = controls.process_pair([None]*41, [None]*41,
        calibration, times, rotations[:-1].inv()*rotations[1:], np.tile(-np.eye(3)/6, (8, 1, 1)))
    assert not result['accepted']
    assert result['reason'] == 'joint_solve_failed'
    assert all(r['accepted'] for r in independent)


def test_heldout_diagnostic_failure_cannot_reject_joint_solver(monkeypatch):
    datasets, calibration, _, rotations = prepared_pair()
    monkeypatch.setattr(controls.base, 'track_stereo_window', lambda *a, **kw: datasets.pop(0))
    def unavailable(*args, **kwargs):
        raise ValueError('heldout_negative_depth')
    monkeypatch.setattr(controls, 'birth_heldout_rmse', unavailable)
    times = np.arange(9)/6
    result, _, info, _ = controls.process_pair([None]*41, [None]*41,
        calibration, times, rotations[:-1].inv()*rotations[1:], np.tile(-np.eye(3)/6, (8, 1, 1)))
    assert result['accepted']
    assert info['optimized_heldout_rmse_px'] is None
    assert info['optimized_heldout_unavailable_reason'] == 'heldout_negative_depth'

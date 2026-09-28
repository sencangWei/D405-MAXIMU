import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('seam_controls',
    ROOT/'.planning/metric_window_bundle_20260928/run_seam_window_controls.py')
controls = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controls)


def data():
    rotations = Rotation.from_euler('z', np.linspace(0, .5, 9))
    centers = np.column_stack((np.arange(9)*.01, np.zeros((9, 2))))
    old = dict(observations=np.ones((9, 2, 4))*20., valid=np.ones((9, 2), bool),
        initial_points=np.array([[.01, .02, .3], [.02, .04, .4]]),
        initial_centers=centers, initial_rotations=rotations,
        heldout=np.array([True, False]), birth_indices=np.array([0, 0]),
        birth_points=np.array([[.01, .02, .3], [.02, .04, .4]]))
    observation = np.ones((41, 6, 4))*70.
    observation[:, :, 0] += np.arange(6)*8
    seam = dict(accepted=True, observations=observation, valid=np.ones((41, 6), bool),
        initial_points=np.tile([.03, .02, .35], (6, 1)),
        source_indices=dict(feature_indices=[0, 1, 2, 3, 4, 5]), exclusion=dict(radius_px=7))
    return old, seam


def test_seam_landmarks_use_initial_seam_pose_not_optimized_pose():
    old, seam = data()
    old['mapping'] = {'stale_legacy': True}
    before = old['observations'].copy()
    result, info = controls.replenish(old, seam)
    expected = old['initial_rotations'][4].apply(seam['initial_points'])+old['initial_centers'][4]
    np.testing.assert_allclose(result['initial_points'][2:], expected)
    assert info['seam_born_added'] == 6
    np.testing.assert_array_equal(result['birth_indices'][2:], 4)
    np.testing.assert_array_equal(result['heldout'], [True, False, True, False, False, False, False, True])
    np.testing.assert_array_equal(old['observations'], before)
    assert len(old['initial_points']) == 2
    assert 'mapping' not in result


def test_exact_duplicate_guard_excludes_near_any_old_label_before_new_mod5():
    old, seam = data()
    seam['observations'][:, 0, :2] = [20., 20.]  # near existing heldout too
    result, info = controls.replenish(old, seam)
    assert info['excluded_near_existing'] == 1
    assert info['seam_born_added'] == 5
    np.testing.assert_array_equal(result['heldout'][2:], [False, False, False, False, True])


def test_raw_only_cross_seam_support_does_not_fake_sampled_BA_support():
    old, seam = data()
    seam['valid'][:20, 1] = False
    seam['valid'][19, 1] = True  # raw-only pre observation; no sampled pre-node
    seam['valid'][21:, 2] = False
    seam['valid'][21, 2] = True  # raw-only post observation
    result, info = controls.replenish(old, seam)
    assert info['seam_born_added'] == 4
    assert result['observations'].shape == (9, 6, 4)


def test_conservative_A_gauge_depth_guard_is_not_weakened():
    old, seam = data()
    old['initial_centers'][4, 2] = -1.
    with pytest.raises(ValueError, match='birth depth'):
        controls.replenish(old, seam)


def test_seam_refusal_preserves_independent_results_without_old_success_fallback(monkeypatch):
    old, _ = data()
    independent = [dict(accepted=True), dict(accepted=True)]
    monkeypatch.setattr(controls.previous, 'process_pair', lambda *a: (
        dict(accepted=True, reason='ok'), independent, dict(), old))
    monkeypatch.setattr(controls, 'track_seam_stereo_window', lambda *a, **kw: (
        dict(accepted=False, reason='insufficient_seam_stereo_features')))
    result, returned, _, _ = controls.process_pair(None, None, None, None, None, None)
    assert not result['accepted']
    assert result['reason'] == 'seam_observations_refused'
    assert returned is independent


def test_replenished_training_init_and_solve_never_use_heldout_pixels(monkeypatch):
    old, seam = data()
    independent = [dict(accepted=True), dict(accepted=True)]
    monkeypatch.setattr(controls.previous, 'process_pair', lambda *a: (
        dict(accepted=False, reason='insufficient_shared_training_geometry'), independent, dict(), old))
    monkeypatch.setattr(controls, 'track_seam_stereo_window', lambda *a, **kw: seam)
    observed = []

    def initialization(dataset, calibration, train):
        np.testing.assert_array_equal(train, ~dataset['heldout'])
        observed.append(train.copy())
        return dataset['initial_centers'], dataset['initial_rotations'], dataset['valid'] & train[None, :]

    def solve(dataset, *args):
        assert not dataset['admitted_train'][:, dataset['heldout']].any()
        return dict(accepted=True, reason='ok', centers=dataset['initial_centers'], rotations=dataset['initial_rotations'])

    monkeypatch.setattr(controls.previous.base, 'visual_initialization', initialization)
    monkeypatch.setattr(controls.previous.base, 'node_geometry_supported', lambda p: len(p) >= 4)
    monkeypatch.setattr(controls.previous, 'solve', solve)
    monkeypatch.setattr(controls.previous, 'birth_heldout_rmse', lambda *a: 1.)
    result, returned, info, joined = controls.process_pair(None, None, None, None, None, None)
    assert result['accepted'] and len(observed) == 1
    assert info['crosswindow_support_count'] >= 4
    assert returned is independent
    assert joined['birth_indices'][-1] == 4


def test_joint_error_and_heldout_diagnostic_error_do_not_erase_control(monkeypatch):
    old, seam = data()
    independent = [dict(accepted=True), dict(accepted=True)]
    monkeypatch.setattr(controls.previous, 'process_pair', lambda *a: (
        dict(accepted=True, reason='ok'), independent, dict(), old))
    monkeypatch.setattr(controls, 'track_seam_stereo_window', lambda *a, **kw: seam)
    monkeypatch.setattr(controls.previous.base, 'visual_initialization', lambda dataset, *a: (
        dataset['initial_centers'], dataset['initial_rotations'], dataset['valid'] & dataset['train'][None, :]))
    monkeypatch.setattr(controls.previous.base, 'node_geometry_supported', lambda p: True)

    def diagnostic(*args):
        raise ValueError('withheld only')

    monkeypatch.setattr(controls.previous, 'birth_heldout_rmse', diagnostic)
    monkeypatch.setattr(controls.previous, 'solve', lambda d, *a: dict(
        accepted=True, reason='ok', centers=d['initial_centers'], rotations=d['initial_rotations']))
    result, returned, info, _ = controls.process_pair(None, None, None, None, None, None)
    assert result['accepted'] and returned is independent
    assert info['optimized_heldout_rmse_px'] is None
    assert info['optimized_heldout_unavailable_reason'] == 'withheld only'

    def bad_solve(*args):
        raise ValueError('training solve error')

    monkeypatch.setattr(controls.previous, 'solve', bad_solve)
    result, returned, _, _ = controls.process_pair(None, None, None, None, None, None)
    assert not result['accepted'] and returned is independent


def test_real_nine_pose_bundle_uses_seam_born_bidirectional_pixels(monkeypatch):
    spec = importlib.util.spec_from_file_location('joint_fixture_for_seam',
        ROOT/'tests/test_joint_stereo_controls.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    datasets, calibration, centers, rotations = fixture.prepared_pair()
    monkeypatch.setattr(controls.previous.base, 'track_stereo_window', lambda *a, **kw: datasets.pop(0))
    rng = np.random.default_rng(812)
    global_points = rng.uniform([.15, -.05, .3], [.20, .05, .4], (25, 3))
    k = calibration['left_intrinsics']
    observation = []
    for node in range(9):
        xyz = rotations[node].inv().apply(global_points-centers[node])
        observation.append(np.column_stack((k['fx']*xyz[:, 0]/xyz[:, 2]+k['cx'],
            k['fy']*xyz[:, 1]/xyz[:, 2]+k['cy'],
            k['fx']*(xyz[:, 0]-.018)/xyz[:, 2]+k['cx'], k['fy']*xyz[:, 1]/xyz[:, 2]+k['cy'])))
    dense = np.asarray(observation)[np.minimum(np.arange(41)//5, 8)]
    seam = dict(accepted=True, observations=dense, valid=np.ones(dense.shape[:2], bool),
        initial_points=rotations[4].inv().apply(global_points-centers[4]),
        source_indices=dict(feature_indices=list(range(25))), exclusion=dict(radius_px=7))
    monkeypatch.setattr(controls, 'track_seam_stereo_window', lambda *a, **kw: seam)
    deltas = Rotation.concatenate([rotations[i].inv()*rotations[i+1] for i in range(8)])
    result, independent, info, joined = controls.process_pair([None]*41, [None]*41, calibration,
        np.arange(9)/6., deltas, -np.repeat((np.eye(3)/6.)[None], 8, axis=0))
    assert result['accepted'] and all(r['accepted'] for r in independent)
    assert info['seam_born_added'] == 25
    assert info['crosswindow_support_count'] >= 4
    np.testing.assert_allclose(result['centers'], centers, atol=1e-5)
    assert not joined['admitted_train'][:, joined['heldout']].any()
    assert info['optimized_heldout_rmse_px'] < 1e-4

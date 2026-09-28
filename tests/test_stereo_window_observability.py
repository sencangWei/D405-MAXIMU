import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'stereo_window_observability', ROOT/'scripts/stereo_window_observability.py')
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def test_direct_endpoint_units_and_axes():
    result = diagnostic.marginal_endpoint(np.diag([1000., 200., 100.]), [0, 1, 2])
    np.testing.assert_allclose(result['singular_values_per_m'], [1000, 200, 100])
    assert result['endpoint_rank'] == 3
    assert result['weak_response_mm_per_unit_normalized_residual'] == pytest.approx(10)
    np.testing.assert_allclose(np.abs(result['weak_axis_camera_i']), [0, 0, 1])
    assert result['is_calibrated_covariance'] is False


def test_nuisance_can_absorb_entire_endpoint_no_false_finite_confidence():
    result = diagnostic.marginal_endpoint(np.column_stack([np.eye(3), np.eye(3)]), [0, 1, 2])
    assert result['endpoint_rank'] == 0
    assert result['rank_deficient'] is True
    assert result['weak_response_mm_per_unit_normalized_residual'] is None


def test_one_null_axis_and_nuisance_scaling_invariance():
    endpoint = np.array([[3, 0, 0], [0, 2, 0], [0, 0, 1], [1, 0, 0.]])
    nuisance = np.array([[0, 0], [0, 0], [1, 1], [0, 0.]])
    first = diagnostic.marginal_endpoint(np.column_stack([endpoint, nuisance]), [0, 1, 2])
    scaled = diagnostic.marginal_endpoint(np.column_stack([endpoint, nuisance * [1e-8, 1e8]]), [0, 1, 2])
    assert first['endpoint_rank'] == 2
    np.testing.assert_allclose(first['singular_values_per_m'], scaled['singular_values_per_m'], atol=1e-10)
    np.testing.assert_allclose(np.abs(first['weak_axis_camera_i']), [0, 0, 1])


@pytest.mark.parametrize('frames,expected', [(2, [3, 4, 5]), (5, [21, 22, 23])])
def test_final_center_columns_follow_core_pack(frames, expected):
    assert diagnostic.endpoint_columns(frames) == expected


@pytest.mark.parametrize('jac,columns', [(np.eye(3)*np.nan,[0,1,2]),(np.eye(3),[0,0,2]),(np.eye(3),[0,1,3])])
def test_invalid_jacobians_reject_explicitly(jac, columns):
    with pytest.raises(ValueError):
        diagnostic.marginal_endpoint(jac, columns)


def load_scene_module():
    spec = importlib.util.spec_from_file_location('observability_scene', ROOT/'tests/test_stereo_window_bundle.py')
    scene = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scene)
    return scene


def test_instrumentation_does_not_change_solver_endpoint_or_acceptance():
    scene = load_scene_module()
    obs, valid, times, points, centers, rotations, gyro = scene.make_scene(noise_px=.03)
    args = (obs, valid, times, scene.LEFT, scene.RIGHT, scene.BASELINE, points,
            centers, rotations, gyro)
    kwargs = dict(gyro_noise_density=.002, gyro_bias_sigma=.02)
    original = scene.bundle.solve_stereo_window(*args, **kwargs)
    instrumented = diagnostic.solve_with_observability(scene.bundle, *args, **kwargs)
    assert instrumented['accepted'] == original['accepted']
    assert instrumented['reason'] == original['reason']
    np.testing.assert_array_equal(instrumented['centers'], original['centers'])
    np.testing.assert_array_equal(instrumented['landmarks'], original['landmarks'])
    np.testing.assert_array_equal(instrumented['gyro_bias'], original['gyro_bias'])
    record = instrumented['diagnostics']['endpoint_observability']
    assert record['diagnostic_only'] is True
    assert record['is_calibrated_covariance'] is False
    assert record['all_factors']['endpoint_rank'] == 3
    assert record['pixel_rows_only']['endpoint_rank'] == 3
    assert record['endpoint_tracks'] == int(valid[-1].sum())


def test_interception_restores_solver_on_exception(monkeypatch):
    scene = load_scene_module()
    original = scene.bundle.least_squares
    def fails(*args, **kwargs):
        raise RuntimeError('failure before solver')
    monkeypatch.setattr(scene.bundle, 'solve_stereo_window', fails)
    with pytest.raises(RuntimeError):
        diagnostic.solve_with_observability(scene.bundle)
    assert scene.bundle.least_squares is original


def test_pre_solver_rejection_is_unchanged():
    scene = load_scene_module()
    obs, valid, times, points, centers, rotations, gyro = scene.make_scene()
    result = diagnostic.solve_with_observability(scene.bundle, obs, valid, times,
        scene.LEFT, scene.RIGHT, 0., points, centers, rotations, gyro,
        gyro_noise_density=.002, gyro_bias_sigma=.02)
    assert result == dict(accepted=False, reason='invalid_baseline')


def test_pixel_only_metric_sensitivity_worsens_with_shorter_baseline():
    scene = load_scene_module()
    obs, valid, times, points, centers, rotations, gyro = scene.make_scene(noise_px=.01)
    responses = []
    for baseline in (.036, .009):
        pixels = obs.copy()
        for frame in range(len(times)):
            pixels[frame,:,2:] = scene.project(points, centers[frame], rotations[frame], scene.RIGHT, baseline=baseline)
        result = diagnostic.solve_with_observability(scene.bundle, pixels, valid, times,
            scene.LEFT, scene.RIGHT, baseline, points, centers, rotations, gyro,
            gyro_noise_density=.002, gyro_bias_sigma=.02)
        assert result['accepted']
        responses.append(result['diagnostics']['endpoint_observability']['pixel_rows_only']['weak_response_mm_per_unit_normalized_residual'])
    # Temporal parallax also constrains depth; there is no universal inverse
    # baseline ratio for arbitrary motion. Require the predicted direction.
    assert responses[1] > responses[0]


def test_diagnostic_failure_does_not_change_solver_acceptance(monkeypatch):
    scene = load_scene_module()
    obs, valid, times, points, centers, rotations, gyro = scene.make_scene(noise_px=.03)
    def fails(*args):
        raise ValueError('diagnostic unavailable')
    monkeypatch.setattr(diagnostic, '_capture_diagnostic', fails)
    result = diagnostic.solve_with_observability(scene.bundle, obs, valid, times,
        scene.LEFT, scene.RIGHT, scene.BASELINE, points, centers, rotations, gyro,
        gyro_noise_density=.002, gyro_bias_sigma=.02)
    assert result['accepted'] is True
    assert result['diagnostics']['endpoint_observability']['available'] is False

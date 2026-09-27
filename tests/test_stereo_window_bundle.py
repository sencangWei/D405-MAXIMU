import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stereo_window_bundle", ROOT / "scripts" / "stereo_window_bundle.py"
)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)


LEFT = {"fx": 420.0, "fy": 418.0, "cx": 320.0, "cy": 240.0}
RIGHT = {"fx": 421.0, "fy": 419.0, "cx": 319.0, "cy": 241.0}
BASELINE = 0.018


def project(points, center, rotation, intrinsics, *, baseline=0.0):
    camera = rotation.inv().apply(points - center)
    camera = camera - np.array([baseline, 0.0, 0.0])
    return np.column_stack(
        (
            intrinsics["fx"] * camera[:, 0] / camera[:, 2] + intrinsics["cx"],
            intrinsics["fy"] * camera[:, 1] / camera[:, 2] + intrinsics["cy"],
        )
    )


def make_scene(*, noise_px=0.0, gyro_bias=None, outlier=False, rotation_scale=1.0):
    times = np.array([0.0, 0.25, 0.55])
    centers = np.array(
        [[0.0, 0.0, 0.0], [0.018, -0.002, 0.004], [0.041, 0.003, 0.006]]
    )
    rotations = Rotation.from_rotvec(
        rotation_scale
        * np.array([[0.0, 0.0, 0.0], [0.01, -0.025, 0.006], [0.018, -0.041, 0.012]])
    )
    u, v = np.meshgrid(np.linspace(-0.08, 0.08, 4), np.linspace(-0.045, 0.045, 3))
    points = np.column_stack((u.ravel(), v.ravel(), 0.35 + 0.03 * np.sin(u.ravel() * 20.0)))
    observations = np.empty((len(times), len(points), 4), dtype=float)
    for frame in range(len(times)):
        left = project(points, centers[frame], rotations[frame], LEFT)
        right = project(points, centers[frame], rotations[frame], RIGHT, baseline=BASELINE)
        observations[frame] = np.column_stack((left, right))
    if noise_px:
        rng = np.random.default_rng(11)
        observations += rng.normal(0.0, noise_px, observations.shape)
    if outlier:
        observations[1, 2] += np.array([70.0, -40.0, 65.0, -38.0])
    bias = np.zeros(3) if gyro_bias is None else np.asarray(gyro_bias, dtype=float)
    gyro_deltas = []
    for frame in range(len(times) - 1):
        dt = times[frame + 1] - times[frame]
        true_delta = rotations[frame].inv() * rotations[frame + 1]
        gyro_deltas.append(true_delta * Rotation.from_rotvec(bias * dt))
    return observations, np.ones(observations.shape[:2], dtype=bool), times, points, centers, rotations, Rotation.concatenate(gyro_deltas)


def assert_accepted(result):
    assert result["accepted"] is True, result
    assert result["diagnostics"]["is_calibrated_covariance"] is False
    assert np.all(np.isfinite(result["centers"]))
    assert np.all(np.isfinite(result["landmarks"]))


def test_known_metric_motion_rotation_and_depth_recover_from_stereo_pixels():
    obs, valid, times, points, centers, rotations, gyro = make_scene(noise_px=0.03)
    initial_centers = centers + np.array([[0.0, 0.0, 0.0], [0.004, -0.003, 0.002], [-0.005, 0.002, -0.001]])
    initial_points = points + np.array([0.003, -0.002, 0.012])

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        initial_points,
        initial_centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert_accepted(result)
    np.testing.assert_allclose(result["centers"][0], np.zeros(3), atol=1e-12)
    assert np.linalg.norm(result["centers"][-1] - centers[-1]) < 0.006
    assert np.median(np.linalg.norm(result["landmarks"] - points, axis=1)) < 0.008
    assert result["diagnostics"]["pixel_rmse_px"] < 0.25
    assert result["diagnostics"]["minimum_depth_m"] > 0.2


def test_first_pose_is_fixed_gauge_even_if_initial_guess_moves_it():
    obs, valid, times, points, centers, rotations, gyro = make_scene()
    shifted = centers + np.array([0.2, -0.1, 0.05])
    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        shifted,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert_accepted(result)
    np.testing.assert_allclose(result["centers"][0], np.zeros(3), atol=1e-12)
    np.testing.assert_allclose(result["rotations"].as_rotvec()[0], np.zeros(3), atol=1e-12)


def test_soft_gyro_factor_estimates_constant_bias_without_hard_fixing_rotation():
    obs, valid, times, points, centers, rotations, _ = make_scene(
        noise_px=0.01, rotation_scale=4.0
    )
    true_bias = np.array([0.008, -0.006, 0.004])
    gyro = make_scene(gyro_bias=true_bias, rotation_scale=4.0)[6]

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.0005,
        gyro_bias_sigma=0.05,
        pixel_sigma_px=0.1,
    )

    assert_accepted(result)
    np.testing.assert_allclose(result["gyro_bias"], true_bias, atol=0.004)
    assert float(np.dot(result["gyro_bias"], true_bias)) > 0.0
    assert np.linalg.norm(result["rotations"][-1].as_rotvec() - rotations[-1].as_rotvec()) < 0.004
    assert result["diagnostics"]["gyro_rmse_rad"] < 0.003
    assert result["diagnostics"]["gyro_delta_convention"] == "camera_to_window_R_i_inverse_R_j"
    assert result["diagnostics"]["gyro_bias_model_is_exact"] is False


def test_supplied_right_tangent_gyro_bias_jacobian_recovers_bias_sign_and_magnitude():
    obs, valid, times, points, centers, rotations, _ = make_scene(rotation_scale=5.0)
    true_bias = np.array([-0.009, 0.007, 0.005])
    gyro = make_scene(gyro_bias=true_bias, rotation_scale=5.0)[6]
    jacobians = np.asarray(
        [-(times[frame + 1] - times[frame]) * np.eye(3) for frame in range(len(times) - 1)]
    )

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.0005,
        gyro_bias_sigma=0.05,
        pixel_sigma_px=0.1,
        gyro_bias_jacobians=jacobians,
    )

    assert_accepted(result)
    np.testing.assert_allclose(result["gyro_bias"], true_bias, atol=0.004)
    assert float(np.dot(result["gyro_bias"], true_bias)) > 0.0
    assert result["diagnostics"]["gyro_bias_jacobian_source"] == "caller_supplied_right_tangent"
    assert result["diagnostics"]["gyro_bias_model_is_exact"] is False


def test_soft_l1_pixel_policy_survives_one_large_pixel_outlier():
    obs, valid, times, points, centers, rotations, gyro = make_scene(noise_px=0.02, outlier=True)
    clean = bundle.solve_stereo_window(
        make_scene(noise_px=0.02)[0],
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )
    outlier = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert_accepted(clean)
    assert_accepted(outlier)
    assert np.linalg.norm(outlier["centers"][-1] - clean["centers"][-1]) < 0.004
    assert outlier["diagnostics"]["pixel_p95_px"] > clean["diagnostics"]["pixel_p95_px"]


def test_malformed_gyro_bias_jacobian_is_rejected():
    obs, valid, times, points, centers, rotations, gyro = make_scene()

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
        gyro_bias_jacobians=np.zeros((len(times), 3, 3)),
    )

    assert result["accepted"] is False
    assert result["reason"] == "malformed_gyro_bias_jacobians"


def test_inconsistent_pixel_solution_is_diagnostic_reject_with_state_retained():
    obs, valid, times, points, centers, rotations, gyro = make_scene()
    corrupted = obs.copy()
    offsets = np.linspace(-12.0, 12.0, corrupted.shape[1])
    corrupted[1, :, 0] += offsets
    corrupted[1, :, 1] -= offsets[::-1]
    corrupted[1, :, 2] -= offsets
    corrupted[1, :, 3] += offsets[::-1]

    result = bundle.solve_stereo_window(
        corrupted,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert result["accepted"] is False
    assert result["reason"] == "model_consistency_failed"
    assert "stereo_reprojection_inconsistent" in result["failures"]
    assert result["diagnostics"]["diagnostic_only"] is True
    assert np.all(np.isfinite(result["centers"]))


def test_inconsistent_gyro_solution_is_diagnostic_reject_with_state_retained():
    obs, valid, times, points, centers, rotations, _gyro = make_scene()
    gyro = Rotation.concatenate(
        [
            (rotations[frame].inv() * rotations[frame + 1])
            * Rotation.from_euler("xyz", [12.0, -9.0, 7.0], degrees=True)
            for frame in range(len(times) - 1)
        ]
    )

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.01,
        gyro_bias_sigma=0.02,
        pixel_sigma_px=0.05,
    )

    assert result["accepted"] is False
    assert result["reason"] == "model_consistency_failed"
    assert set(result["failures"]) & {"gyro_inconsistent", "gyro_bias_exceeds_guard"}
    assert result["diagnostics"]["diagnostic_only"] is True
    assert np.all(np.isfinite(result["centers"]))


def test_bias_guard_rejects_large_camera_bias_diagnostic_solution():
    obs, valid, times, points, centers, rotations, _gyro = make_scene(rotation_scale=4.0)
    large_bias = np.array([0.025, 0.0, 0.0])
    gyro = make_scene(gyro_bias=large_bias, rotation_scale=4.0)[6]

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.0005,
        gyro_bias_sigma=0.05,
        pixel_sigma_px=0.1,
    )

    assert result["accepted"] is False
    assert result["reason"] == "model_consistency_failed"
    assert "gyro_bias_exceeds_guard" in result["failures"]
    assert result["diagnostics"]["gyro_bias_component_limit_rad_s"] == pytest.approx(0.01)


def test_pure_rotation_with_stereo_depth_is_observable_not_zero_motion_rejected():
    obs, valid, times, points, _centers, rotations, gyro = make_scene(rotation_scale=3.0)
    centers = np.zeros((len(times), 3), dtype=float)
    for frame in range(len(times)):
        left = project(points, centers[frame], rotations[frame], LEFT)
        right = project(points, centers[frame], rotations[frame], RIGHT, baseline=BASELINE)
        obs[frame] = np.column_stack((left, right))

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert_accepted(result)
    assert np.linalg.norm(result["centers"][-1]) < 1e-5


def test_disconnected_frame_track_support_is_rejected():
    _obs, _valid, _times, points, _centers, _rotations, _gyro = make_scene()
    times = np.array([0.0, 0.25, 0.55, 0.85])
    centers = np.array(
        [[0.0, 0.0, 0.0], [0.018, -0.002, 0.004], [0.041, 0.003, 0.006], [0.05, 0.004, 0.006]]
    )
    rotations = Rotation.from_rotvec(
        np.array(
            [
                [0.0, 0.0, 0.0],
                [0.01, -0.025, 0.006],
                [0.018, -0.041, 0.012],
                [0.02, -0.045, 0.014],
            ]
        )
    )
    obs = np.empty((len(times), len(points), 4), dtype=float)
    for frame in range(len(times)):
        left = project(points, centers[frame], rotations[frame], LEFT)
        right = project(points, centers[frame], rotations[frame], RIGHT, baseline=BASELINE)
        obs[frame] = np.column_stack((left, right))
    gyro = Rotation.concatenate(
        [rotations[frame].inv() * rotations[frame + 1] for frame in range(len(times) - 1)]
    )
    valid = np.zeros(obs.shape[:2], dtype=bool)
    valid[:] = False
    valid[:2, :6] = True
    valid[2:, 6:] = True

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert result["accepted"] is False
    assert result["reason"] == "disconnected_support"


def test_rank_deficient_landmark_geometry_is_rejected_without_rejecting_zero_motion():
    obs, valid, times, points, centers, rotations, gyro = make_scene()
    points[:, 0] = np.linspace(-0.05, 0.05, len(points))
    points[:, 1] = 0.0
    points[:, 2] = 0.35

    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert result["accepted"] is False
    assert result["reason"] == "rank_deficient_geometry"


@pytest.mark.parametrize(
    "mutator, reason",
    [
        (lambda data: data.__setitem__("baseline", 0.0), "invalid_baseline"),
        (lambda data: data["observations"].__setitem__((0, 0, 0), np.nan), "nonfinite"),
        (lambda data: data["valid"].__setitem__((slice(None), slice(None)), False), "insufficient_support"),
        (lambda data: data["points"].__setitem__((0, 2), -0.1), "invalid_initial_depth"),
    ],
)
def test_rejects_malformed_or_unsupported_inputs(mutator, reason):
    obs, valid, times, points, centers, rotations, gyro = make_scene()
    data = {
        "observations": obs.copy(),
        "valid": valid.copy(),
        "times": times,
        "points": points.copy(),
        "centers": centers,
        "rotations": rotations,
        "gyro": gyro,
        "baseline": BASELINE,
    }
    mutator(data)

    result = bundle.solve_stereo_window(
        data["observations"],
        data["valid"],
        data["times"],
        LEFT,
        RIGHT,
        data["baseline"],
        data["points"],
        data["centers"],
        data["rotations"],
        data["gyro"],
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert result["accepted"] is False
    assert result["reason"] == reason


def test_finite_solve_reports_not_calibrated_covariance():
    obs, valid, times, points, centers, rotations, gyro = make_scene(noise_px=0.02)
    result = bundle.solve_stereo_window(
        obs,
        valid,
        times,
        LEFT,
        RIGHT,
        BASELINE,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert_accepted(result)
    assert result["diagnostics"]["solver_success"] is True
    assert result["diagnostics"]["observed_pixels"] == int(np.count_nonzero(valid) * 4)
    assert "covariance" not in result

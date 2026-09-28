import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stereo_window_shape_factor", ROOT / "scripts" / "stereo_window_shape_factor.py"
)
shape = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shape)


def load_scene_module():
    spec = importlib.util.spec_from_file_location(
        "shape_scene", ROOT / "tests" / "test_stereo_window_bundle.py"
    )
    scene = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scene)
    return scene


def target_only_jacobian(frames: int) -> np.ndarray:
    cols = 6 * (frames - 1) + 3
    jac = np.zeros((3 * (frames - 1), cols), dtype=float)
    jac[:, shape.center_columns(frames)] = np.eye(3 * (frames - 1))
    return jac


def straight_centers(frames: int) -> np.ndarray:
    return np.column_stack(
        (np.linspace(0.0, 0.08, frames), np.zeros(frames), np.zeros(frames))
    )


def test_nine_center_shape_factor_detects_bowed_interior_when_endpoint_matches():
    frames = 9
    rotations = Rotation.identity(frames)
    reference = straight_centers(frames)
    factor = shape.build_shape_factor(
        target_only_jacobian(frames),
        reference,
        rotations,
        point_count=0,
    )
    bowed = reference.copy()
    bowed[1:8, 1] = [0.004, 0.011, 0.018, 0.0, -0.012, -0.008, -0.003]
    bowed[4] = reference[4]
    bowed[8] = reference[8]

    old_endpoint_residual = bowed[8] - reference[8]
    old_first_half_endpoint = bowed[4] - reference[4]
    old_second_half_endpoint = (bowed[8] - bowed[4]) - (reference[8] - reference[4])
    grouped = shape.shape_residual(factor, bowed, rotations)
    gradient = shape.shape_gradient(factor, bowed, rotations).reshape(frames - 1, 3)
    desired_correction = (reference[1:] - bowed[1:]).reshape(-1)

    np.testing.assert_allclose(old_endpoint_residual, np.zeros(3))
    np.testing.assert_allclose(old_first_half_endpoint, np.zeros(3))
    np.testing.assert_allclose(old_second_half_endpoint, np.zeros(3))
    assert np.linalg.norm(grouped) > 0.02
    assert float(np.dot(-gradient.reshape(-1), desired_correction)) > 0.0
    assert factor["metadata"]["correlated_factor_group_size"] == 1
    assert factor["metadata"]["calibrated_covariance"] is False
    assert factor["metadata"]["statistical_independence_claimed"] is False


def test_shape_residual_is_rigid_frame_invariant_with_nonidentity_basis():
    frames = 9
    local_rotations = Rotation.identity(frames)
    reference = straight_centers(frames)
    factor = shape.build_shape_factor(
        target_only_jacobian(frames), reference, local_rotations, point_count=0
    )
    bowed = reference.copy()
    bowed[3:7, 2] = [0.01, 0.02, 0.015, 0.005]
    local_residual = shape.shape_residual(factor, bowed, local_rotations)

    basis = Rotation.from_rotvec([0.2, -0.15, 0.08])
    translation = np.array([1.0, -0.3, 0.2])
    world_bowed = basis.apply(bowed) + translation
    world_rotations = basis * local_rotations

    np.testing.assert_allclose(
        shape.shape_residual(factor, world_bowed, world_rotations),
        local_residual,
        atol=1e-12,
    )


def test_nuisance_projection_keeps_coupled_shape_and_reports_null_rank():
    frames = 3
    cols = 6 * (frames - 1) + 3
    jac = np.zeros((3, cols), dtype=float)
    center_cols = shape.center_columns(frames)
    jac[0, center_cols[0]] = 1.0
    jac[0, center_cols[3]] = 1.0
    jac[1, center_cols[3]] = 1.0
    jac[1, 0] = 1.0  # rotation nuisance absorbs this row
    centers = straight_centers(frames)
    factor = shape.build_shape_factor(jac, centers, Rotation.identity(frames), point_count=0)

    sensitivity = np.asarray(factor["compact_sqrt_sensitivity"])
    assert factor["conditional_rank"] == 1
    assert factor["rank_deficient"] is True
    assert factor["weak_response_mm_per_unit_normalized_residual"] is None
    assert abs(sensitivity[0, 0]) > 1e-9
    assert abs(sensitivity[0, 3]) > 1e-9
    assert len(factor["null_axes_center_gauge"]) == 5


def test_affine_profile_matches_explicit_nuisance_least_squares_and_gradient():
    frames = 3
    target_cols = shape.center_columns(frames)
    total_cols = 6 * (frames - 1) + 3
    jac = np.zeros((5, total_cols), dtype=float)
    target = np.array(
        [
            [1.0, 0.2, 0.0, 0.5, 0.0, 0.0],
            [0.0, 0.3, 1.0, 0.0, 0.4, 0.0],
            [0.0, 0.0, 0.2, 0.0, 1.0, 0.1],
            [0.5, 0.0, 0.0, 0.2, 0.0, 1.0],
            [0.0, 0.0, 0.0, 0.7, 0.3, 0.0],
        ]
    )
    nuisance = np.array(
        [
            [1.0, 0.0],
            [0.2, 0.1],
            [0.0, 0.0],
            [0.0, 1.0],
            [0.3, 0.2],
        ]
    )
    jac[:, target_cols] = target
    jac[:, :2] = nuisance
    residual0 = np.array([0.4, -0.2, 0.1, 0.3, -0.5])
    centers = straight_centers(frames)
    factor = shape.build_shape_factor(
        jac, centers, Rotation.identity(frames), point_count=0, residual=residual0
    )
    deltas = [
        np.array([0.01, -0.02, 0.03, 0.0, 0.04, -0.01]),
        np.array([-0.03, 0.01, 0.0, 0.02, -0.02, 0.05]),
    ]
    for delta in deltas:
        moved = centers.copy()
        moved[1:] += delta.reshape(2, 3)
        profiled = shape.shape_residual(factor, moved, Rotation.identity(frames))
        objective = float(profiled @ profiled + factor["dropped_constant_squared_norm"])
        explicit, *_ = np.linalg.lstsq(nuisance, -(residual0 + target @ delta), rcond=None)
        expected = float(np.sum((residual0 + target @ delta + nuisance @ explicit) ** 2))
        np.testing.assert_allclose(objective, expected, atol=1e-12)

    delta = deltas[0]
    moved = centers.copy()
    moved[1:] += delta.reshape(2, 3)
    gradient = shape.shape_gradient(factor, moved, Rotation.identity(frames))
    finite_difference = np.zeros_like(delta)
    eps = 1e-6
    for index in range(len(delta)):
        plus = moved.copy()
        minus = moved.copy()
        plus[1:].reshape(-1)[index] += eps
        minus[1:].reshape(-1)[index] -= eps
        rp = shape.shape_residual(factor, plus, Rotation.identity(frames))
        rm = shape.shape_residual(factor, minus, Rotation.identity(frames))
        finite_difference[index] = ((rp @ rp) - (rm @ rm)) / (2 * eps)
    np.testing.assert_allclose(2 * gradient, finite_difference, atol=1e-7)

    with pytest.raises(ValueError, match="Jacobian"):
        shape.build_shape_factor(jac, centers, Rotation.identity(frames), point_count=0, residual=[np.nan] * 5)
    with pytest.raises(ValueError, match="Jacobian"):
        shape.build_shape_factor(jac, centers, Rotation.identity(frames), point_count=0, residual=[0.0] * 4)


def test_rank_zero_empty_factor_has_finite_empty_residual_and_zero_gradient():
    frames = 3
    cols = 6 * (frames - 1) + 3
    jac = np.zeros((4, cols), dtype=float)
    jac[:, 0:4] = np.eye(4)
    jac[:, shape.center_columns(frames)[:4]] = np.eye(4)
    centers = straight_centers(frames)
    factor = shape.build_shape_factor(jac, centers, Rotation.identity(frames), point_count=0)
    moved = centers.copy()
    moved[1:, 1] = [0.1, -0.2]

    residual = shape.shape_residual(factor, moved, Rotation.identity(frames))
    gradient = shape.shape_gradient(factor, moved, Rotation.identity(frames))

    assert factor["conditional_rank"] == 0
    assert factor["compact_sqrt_sensitivity"] == []
    assert factor["affine_offset"] == []
    assert residual.shape == (0,)
    assert gradient.shape == (6,)
    np.testing.assert_allclose(gradient, np.zeros(6))
    bad = {**factor, "compact_sqrt_sensitivity": [[np.nan] * 6]}
    with pytest.raises(ValueError, match="sensitivity"):
        shape.shape_residual(bad, moved, Rotation.identity(frames))
    bad = {**factor, "frames": 3.0}
    with pytest.raises(ValueError, match="frames"):
        shape.shape_residual(bad, moved, Rotation.identity(frames))
    bad = {**factor, "frames": True}
    with pytest.raises(ValueError, match="frames"):
        shape.shape_residual(bad, moved, Rotation.identity(frames))


def test_large_row_svd_uses_thin_svd_for_conditional(monkeypatch):
    calls = []
    original = shape.np.linalg.svd

    def spy(matrix, *args, **kwargs):
        calls.append((matrix.shape, kwargs.get("full_matrices", False)))
        return original(matrix, *args, **kwargs)

    monkeypatch.setattr(shape.np.linalg, "svd", spy)
    centers = straight_centers(4)
    jac = target_only_jacobian(4)
    shape.build_shape_factor(jac, centers, Rotation.identity(4), point_count=0)

    assert calls[-1][0] == (jac.shape[0], len(shape.center_columns(4)))
    assert calls[-1][1] is False


def test_invalid_packing_first_gauge_and_strict_counts_are_rejected():
    centers = straight_centers(3)
    rotations = Rotation.identity(3)
    with pytest.raises(ValueError, match="packing"):
        shape.build_shape_factor(np.eye(2), centers, rotations, point_count=0)
    shifted = centers.copy()
    shifted[0, 0] = 0.01
    with pytest.raises(ValueError, match="first gauge"):
        shape.build_shape_factor(target_only_jacobian(3), shifted, rotations, point_count=0)
    with pytest.raises(ValueError, match="at least two frames"):
        shape.center_columns(True)
    with pytest.raises(ValueError, match="invalid point count"):
        shape.build_shape_factor(target_only_jacobian(3), centers, rotations, point_count=0.0)


def test_solver_capture_does_not_change_solution_and_adds_shape_factor():
    scene = load_scene_module()
    obs, valid, times, points, centers, rotations, gyro = scene.make_scene(noise_px=0.03)
    args = (
        obs,
        valid,
        times,
        scene.LEFT,
        scene.RIGHT,
        scene.BASELINE,
        points,
        centers,
        rotations,
        gyro,
    )
    kwargs = dict(gyro_noise_density=0.002, gyro_bias_sigma=0.02)

    original = scene.bundle.solve_stereo_window(*args, **kwargs)
    instrumented = shape.solve_with_shape_factor(scene.bundle, *args, **kwargs)

    assert instrumented["accepted"] == original["accepted"]
    assert instrumented["reason"] == original["reason"]
    np.testing.assert_array_equal(instrumented["centers"], original["centers"])
    np.testing.assert_array_equal(instrumented["landmarks"], original["landmarks"])
    np.testing.assert_array_equal(instrumented["gyro_bias"], original["gyro_bias"])
    record = instrumented["diagnostics"]["stereo_window_shape_factor"]
    assert record["diagnostic_only"] is True
    assert record["metadata"]["prototype_only"] is True
    assert record["metadata"]["available_for_graph"] is False
    assert record["solver_accepted"] is True
    assert record["not_admissible_for_graph"] is True
    assert record["frames"] == len(times)
    assert record["center_parameter_columns"] == shape.center_columns(len(times)).tolist()


def test_interception_restores_solver_on_failure_and_preserves_rejection(monkeypatch):
    scene = load_scene_module()
    original_least_squares = scene.bundle.least_squares

    def fails(*args, **kwargs):
        raise RuntimeError("failure before solver")

    with monkeypatch.context() as patch:
        patch.setattr(scene.bundle, "solve_stereo_window", fails)
        with pytest.raises(RuntimeError):
            shape.solve_with_shape_factor(scene.bundle)
    assert scene.bundle.least_squares is original_least_squares

    obs, valid, times, points, centers, rotations, gyro = scene.make_scene()
    rejected = shape.solve_with_shape_factor(
        scene.bundle,
        obs,
        valid,
        times,
        scene.LEFT,
        scene.RIGHT,
        0.0,
        points,
        centers,
        rotations,
        gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )
    assert rejected == {"accepted": False, "reason": "invalid_baseline"}


def test_postfit_rejection_retains_solver_rejection_and_diagnostic(monkeypatch):
    scene = load_scene_module()
    obs, valid, times, points, centers, rotations, _ = scene.make_scene(noise_px=0.01)
    bad_gyro = Rotation.identity(len(times) - 1)

    result = shape.solve_with_shape_factor(
        scene.bundle,
        obs,
        valid,
        times,
        scene.LEFT,
        scene.RIGHT,
        scene.BASELINE,
        points,
        centers,
        rotations,
        bad_gyro,
        gyro_noise_density=0.002,
        gyro_bias_sigma=0.02,
    )

    assert result["accepted"] is False
    assert result["reason"] == "model_consistency_failed"
    record = result["diagnostics"]["stereo_window_shape_factor"]
    assert record["solver_accepted"] is False
    assert record["not_admissible_for_graph"] is True
    np.testing.assert_allclose(result["centers"][0], np.zeros(3), atol=1e-12)

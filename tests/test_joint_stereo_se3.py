import inspect
import time

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

import ego_vio.vio.joint_stereo_se3 as joint
from ego_vio.vio.joint_stereo_se3 import solve_joint_stereo_se3


def body_t_camera(translation=(0.08, -0.02, 0.03), rotation=None):
    matrix = np.eye(4)
    matrix[:3, :3] = np.eye(3) if rotation is None else np.asarray(rotation)
    matrix[:3, 3] = np.asarray(translation, dtype=float)
    return matrix


def scene():
    times = np.array([0.0, 0.02, 0.04, 0.06])
    rotations = Rotation.from_rotvec(
        np.array([
            [0.0, 0.0, 0.0],
            [0.03, -0.01, 0.04],
            [0.06, -0.015, 0.075],
            [0.09, -0.025, 0.105],
        ])
    ).as_matrix()
    positions = np.array([
        [0.0, 0.0, 0.0],
        [0.04, 0.005, -0.002],
        [0.083, 0.012, -0.004],
        [0.129, 0.021, -0.006],
    ])
    return times, positions, rotations, body_t_camera()


def stereo_factor(first, second, positions, rotations, extrinsic, *, confidence=1.0):
    rbc = extrinsic[:3, :3]
    tbc = extrinsic[:3, 3]
    rotation = rbc.T @ rotations[second].T @ rotations[first] @ rbc
    displacement = (
        rbc.T
        @ rotations[first].T
        @ (
            positions[second]
            + rotations[second] @ tbc
            - positions[first]
            - rotations[first] @ tbc
        )
    )
    return {
        "first_index": first,
        "second_index": second,
        "body_t_camera": extrinsic.tolist(),
        "rotation_camera_j_from_i": rotation.tolist(),
        "metric_displacement_camera_i_m": displacement.tolist(),
        "confidence": confidence,
    }


def gyro_factor(first, second, rotations, *, confidence=1.0, sigma=0.01, **metadata):
    return {
        "first_index": first,
        "second_index": second,
        "delta_rotation_body_i_to_body_j": (
            rotations[first].T @ rotations[second]
        ).tolist(),
        "confidence": confidence,
        "rotation_sigma_rad": sigma,
        **metadata,
    }


def learned_edge(first, second, positions, *, confidence=1.0):
    return {
        "first_index": first,
        "second_index": second,
        "metric_displacement_world_m": (positions[second] - positions[first]).tolist(),
        "confidence": confidence,
    }


def all_factors(positions, rotations, extrinsic):
    stereo = [stereo_factor(i, i + 1, positions, rotations, extrinsic) for i in range(3)]
    gyro = [gyro_factor(i, i + 1, rotations) for i in range(3)]
    learned = [learned_edge(i, i + 1, positions) for i in range(3)]
    return stereo, gyro, learned


def validated_factors(times, stereo, gyro, learned, positions):
    stereo_valid = joint._validate_stereo_factors(stereo, times)
    gyro_valid = joint._validate_rotation_factors(gyro, times, "gyro_relative_rotations")
    learned_valid = joint._validate_displacement_factors(
        learned,
        times,
        "learned_displacement_edges",
        joint.LEARNED_DISPLACEMENT_SIGMA_M,
    )
    vins = [
        joint._DisplacementFactor(
            index,
            index + 1,
            positions[index + 1] - positions[index],
            1.0,
            joint.VINS_DISPLACEMENT_SIGMA_M,
        )
        for index in range(times.size - 1)
    ]
    return stereo_valid, gyro_valid, vins, learned_valid


def assert_pose_close(result, positions, rotations, *, atol_p=2e-4, atol_r=2e-4):
    np.testing.assert_allclose(result["positions"], positions, atol=atol_p)
    error = (Rotation.from_matrix(result["rotations"]).inv() * Rotation.from_matrix(rotations)).magnitude()
    assert float(np.max(error)) < atol_r


def test_nonzero_lever_pure_rotation_and_translation_are_recovered():
    times, positions, rotations, extrinsic = scene()
    # First segment has both body translation and a nonzero camera lever, so the
    # camera_i displacement differs from body displacement.
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)

    result = solve_joint_stereo_se3(times, positions, rotations, stereo, gyro, learned)

    assert result["external_ground_truth_used"] is False
    assert result["diagnostic"]["missing_physics"] == ["acceleration", "velocity", "gravity"]
    assert result["diagnostic"]["whitening"]["default_stereo_rotation_sigma_rad"] == pytest.approx(np.deg2rad(1.0))
    assert result["diagnostic"]["whitening"]["gyro_rotation_sigma_rad"]["count"] == 3
    assert_pose_close(result, positions, rotations)


def test_corrupted_initial_nodes_are_corrected_by_consistent_gyro_stereo_and_learned_edges():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    initial_positions = positions.copy()
    initial_positions[1:] += np.array([[0.02, -0.01, 0.015], [-0.015, 0.012, -0.01], [0.01, -0.018, 0.02]])
    initial_rotations = rotations.copy()
    initial_rotations[1:] = (
        Rotation.from_rotvec([[0.08, -0.03, 0.02], [-0.04, 0.05, -0.03], [0.03, 0.04, 0.05]])
        * Rotation.from_matrix(initial_rotations[1:])
    ).as_matrix()

    result = solve_joint_stereo_se3(
        times,
        initial_positions,
        initial_rotations,
        stereo,
        gyro,
        learned,
    )

    assert result["diagnostic"]["cost_after"] < 0.01 * result["diagnostic"]["cost_before"]
    assert_pose_close(result, positions, rotations, atol_p=2e-3, atol_r=2e-3)
    np.testing.assert_allclose(result["positions"][0], initial_positions[0], atol=1e-12)
    np.testing.assert_allclose(result["rotations"][0], initial_rotations[0], atol=1e-12)


def test_fixed_rotation_control_preserves_rotations_and_labels_mode():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    initial_positions = positions.copy()
    initial_positions[1:] += np.array([[0.01, -0.004, 0.006], [-0.006, 0.004, -0.003], [0.004, -0.007, 0.006]])
    initial_rotations = rotations.copy()
    initial_rotations[1:] = (
        Rotation.from_rotvec([[0.03, -0.01, 0.02], [-0.02, 0.01, -0.015], [0.01, 0.02, -0.01]])
        * Rotation.from_matrix(initial_rotations[1:])
    ).as_matrix()

    result = solve_joint_stereo_se3(
        times,
        initial_positions,
        initial_rotations,
        stereo,
        gyro,
        learned,
        optimize_rotations=False,
    )

    assert result["diagnostic"]["mode"] == "fixed_rotation_position_only"
    assert result["diagnostic"]["optimize_rotations"] is False
    np.testing.assert_allclose(result["rotations"], initial_rotations, atol=1e-12)
    assert result["diagnostic"]["cost_after"] < result["diagnostic"]["cost_before"]


def test_fixed_rotation_control_keeps_same_state_when_inputs_are_consistent():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)

    result = solve_joint_stereo_se3(
        times,
        positions,
        rotations,
        stereo,
        gyro,
        learned,
        optimize_rotations=False,
    )

    np.testing.assert_allclose(result["positions"], positions, atol=1e-10)
    np.testing.assert_allclose(result["rotations"], rotations, atol=1e-12)
    assert result["diagnostic"]["cost_after"] < 1e-18


def test_vectorized_residual_matches_scalar_residual_for_nontrivial_state():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    stereo.append(stereo_factor(0, 2, positions, rotations, extrinsic, confidence=0.7))
    gyro.append(gyro_factor(0, 2, rotations, confidence=0.6, sigma=0.02))
    learned.append(learned_edge(0, 2, positions, confidence=0.8))
    stereo_valid, gyro_valid, vins, learned_valid = validated_factors(
        times,
        stereo,
        gyro,
        learned,
        positions,
    )
    perturbed_positions = positions + np.array([
        [0.0, 0.0, 0.0],
        [0.012, -0.003, 0.005],
        [-0.004, 0.008, -0.006],
        [0.006, -0.009, 0.004],
    ])
    perturbed_rotations = rotations.copy()
    perturbed_rotations[1:] = (
        Rotation.from_rotvec(
            [[0.02, -0.01, 0.015], [-0.025, 0.012, -0.01], [0.01, 0.018, -0.02]]
        )
        * Rotation.from_matrix(rotations[1:])
    ).as_matrix()

    vectorized = joint._residual_vectorized(
        perturbed_rotations,
        perturbed_positions,
        stereo_valid,
        gyro_valid,
        vins,
        learned_valid,
    )
    scalar = joint._residual_scalar(
        perturbed_rotations,
        perturbed_positions,
        stereo_valid,
        gyro_valid,
        vins,
        learned_valid,
    )

    np.testing.assert_allclose(vectorized, scalar, atol=1e-12)


def test_fixed_rotation_direct_solution_matches_dense_lstsq_and_keeps_rotation_cost_constant():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    initial_positions = positions.copy()
    initial_positions[1:] += np.array([
        [0.019, -0.011, 0.007],
        [-0.017, 0.013, -0.004],
        [0.008, -0.014, 0.011],
    ])
    stereo_valid, gyro_valid, vins, learned_valid = validated_factors(
        times,
        stereo,
        gyro,
        learned,
        initial_positions,
    )
    linear = joint._fixed_rotation_linear_system(
        rotations,
        initial_positions,
        stereo_valid,
        vins,
        learned_valid,
    )
    dense_solution, *_ = np.linalg.lstsq(
        linear["matrix"].toarray(),
        linear["rhs"],
        rcond=None,
    )

    result = solve_joint_stereo_se3(
        times,
        initial_positions,
        rotations,
        stereo,
        gyro,
        learned,
        optimize_rotations=False,
    )

    np.testing.assert_allclose(result["positions"][1:].ravel(), dense_solution, atol=1e-10)
    np.testing.assert_allclose(result["rotations"], rotations, atol=1e-12)
    diagnostic = result["diagnostic"]
    assert diagnostic["solver"] == "scipy_sparse_lsmr_fixed_rotation_exact_linear_objective"
    assert diagnostic["fixed_rotation_linear_solver"]["matrix_shape"] == linear["matrix"].shape
    before_rotation_cost = np.dot(
        joint._residual_vectorized(rotations, initial_positions, [], gyro_valid, [], []),
        joint._residual_vectorized(rotations, initial_positions, [], gyro_valid, [], []),
    )
    after_rotation_cost = np.dot(
        joint._residual_vectorized(rotations, result["positions"], [], gyro_valid, [], []),
        joint._residual_vectorized(rotations, result["positions"], [], gyro_valid, [], []),
    )
    assert after_rotation_cost == pytest.approx(before_rotation_cost, abs=1e-18)


def test_fixed_rotation_lsmr_iteration_limit_is_reported_not_forced_success(monkeypatch):
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)

    def fake_lsmr(matrix, rhs, *, atol, btol, maxiter):
        return (
            np.zeros(matrix.shape[1]),
            7,
            maxiter,
            float(np.linalg.norm(rhs)),
            1.0,
            0.0,
            1.0e12,
            0.0,
        )

    monkeypatch.setattr(joint, "lsmr", fake_lsmr)

    result = solve_joint_stereo_se3(
        times,
        positions,
        rotations,
        stereo,
        gyro,
        learned,
        optimize_rotations=False,
    )

    assert result["diagnostic"]["least_squares_success"] is False
    assert result["diagnostic"]["least_squares_status"] == 7
    assert "iteration limit" in result["diagnostic"]["least_squares_message"]


def test_fixed_rotation_400_node_perturbed_benchmark_uses_direct_sparse_solver():
    node_count = 400
    times = np.arange(node_count, dtype=float) * 0.02
    rotations = Rotation.from_rotvec(
        np.column_stack(
            [
                0.001 * np.arange(node_count),
                -0.0004 * np.arange(node_count),
                0.0008 * np.arange(node_count),
            ]
        )
    ).as_matrix()
    positions = np.column_stack(
        [
            0.03 * np.arange(node_count),
            0.002 * np.sin(np.arange(node_count) / 9.0),
            -0.001 * np.cos(np.arange(node_count) / 11.0),
        ]
    )
    extrinsic = body_t_camera()
    stereo = [
        stereo_factor(index, index + 1, positions, rotations, extrinsic)
        for index in range(node_count - 1)
    ]
    gyro = [
        gyro_factor(index, index + 1, rotations)
        for index in range(node_count - 1)
    ]
    learned = [
        learned_edge(index, index + 1, positions)
        for index in range(node_count - 1)
    ]
    initial_positions = positions.copy()
    initial_positions[1:] += np.column_stack(
        [
            0.005 * np.sin(np.arange(1, node_count) / 7.0),
            -0.004 * np.cos(np.arange(1, node_count) / 5.0),
            0.003 * np.sin(np.arange(1, node_count) / 13.0),
        ]
    )

    start = time.perf_counter()
    result = solve_joint_stereo_se3(
        times,
        initial_positions,
        rotations,
        stereo,
        gyro,
        learned,
        optimize_rotations=False,
    )
    elapsed_s = time.perf_counter() - start

    assert result["diagnostic"]["solver"] == "scipy_sparse_lsmr_fixed_rotation_exact_linear_objective"
    assert result["diagnostic"]["fixed_rotation_linear_solver"]["matrix_shape"] == (3 * 3 * (node_count - 1), 3 * (node_count - 1))
    assert elapsed_s < 5.0


def test_world_gauge_equivariance():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    gauge = Rotation.from_rotvec([0.2, -0.1, 0.05]).as_matrix()
    offset = np.array([1.0, -0.5, 0.25])
    gauged_positions = (gauge @ positions.T).T + offset
    gauged_rotations = gauge @ rotations
    gauged_learned = [learned_edge(i, i + 1, gauged_positions) for i in range(3)]

    base = solve_joint_stereo_se3(times, positions, rotations, stereo, gyro, learned)
    moved = solve_joint_stereo_se3(
        times,
        gauged_positions,
        gauged_rotations,
        stereo,
        gyro,
        gauged_learned,
    )

    np.testing.assert_allclose(moved["positions"], (gauge @ base["positions"].T).T + offset, atol=1e-6)
    np.testing.assert_allclose(moved["rotations"], gauge @ base["rotations"], atol=1e-6)


def test_duplicate_stereo_pairs_are_rejected():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    stereo.append(dict(stereo[0]))

    with pytest.raises(ValueError, match="duplicate stereo SE3 factor pair"):
        solve_joint_stereo_se3(times, positions, rotations, stereo, gyro, learned)


def test_disconnected_pose_graph_is_rejected_instead_of_extra_anchored():
    times, positions, rotations, extrinsic = scene()
    times = np.array([0.0, 0.02, 0.20, 0.22])
    stereo = [
        stereo_factor(0, 1, positions, rotations, extrinsic),
        stereo_factor(2, 3, positions, rotations, extrinsic),
    ]
    gyro = [
        gyro_factor(0, 1, rotations),
        gyro_factor(2, 3, rotations),
    ]
    learned = [
        learned_edge(0, 1, positions),
        learned_edge(2, 3, positions),
    ]

    with pytest.raises(ValueError, match="disconnected/unobservable"):
        solve_joint_stereo_se3(times, positions, rotations, stereo, gyro, learned)


def test_vins_consecutive_prior_retains_camera_gap_with_diagnostic_count():
    times, positions, rotations, extrinsic = scene()
    times = times.copy()
    times[3] = 0.20
    stereo = [stereo_factor(0, 1, positions, rotations, extrinsic)]
    gyro = [gyro_factor(0, 1, rotations)]
    learned = [learned_edge(0, 1, positions)]

    result = solve_joint_stereo_se3(
        times,
        positions,
        rotations,
        stereo,
        gyro,
        learned,
        optimize_rotations=False,
    )

    assert result["diagnostic"]["vins_displacement_prior_count"] == 3
    assert result["diagnostic"]["vins_consecutive_gap_prior_count"] == 1
    assert result["positions"].shape == positions.shape


def test_gyro_gap_bridge_requires_explicit_imu_coverage_and_hash():
    times, positions, rotations, extrinsic = scene()
    times = times.copy()
    times[2] = 0.12
    times[3] = 0.14
    stereo = [stereo_factor(0, 1, positions, rotations, extrinsic)]
    learned = [learned_edge(0, 1, positions)]
    missing_metadata = [gyro_factor(1, 2, rotations)]

    with pytest.raises(ValueError, match="verified IMU coverage"):
        solve_joint_stereo_se3(
            times,
            positions,
            rotations,
            stereo,
            missing_metadata,
            learned,
        )

    bridged = [
        gyro_factor(
            1,
            2,
            rotations,
            imu_coverage_verified=True,
            imu_sample_count=40,
            max_imu_sample_gap_s=0.0025,
            imu_source_sha256="a" * 64,
        )
    ]
    result = solve_joint_stereo_se3(
        times,
        positions,
        rotations,
        stereo,
        bridged,
        learned,
        optimize_rotations=False,
    )

    assert result["diagnostic"]["gyro_imu_gap_bridged_count"] == 1


def test_gyro_requires_explicit_positive_rotation_sigma():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    gyro[0].pop("rotation_sigma_rad")

    with pytest.raises(ValueError, match="rotation_sigma_rad"):
        solve_joint_stereo_se3(times, positions, rotations, stereo, gyro, learned)


def test_singleton_pose_graph_fails_clearly():
    times, positions, rotations, _extrinsic = scene()

    with pytest.raises(ValueError, match="requires at least two poses"):
        solve_joint_stereo_se3(times[:1], positions[:1], rotations[:1], [], [], [])


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda times, pos, rot, st, gy, le: rot.__setitem__((1, 0, 0), 2.0), "not orthonormal"),
        (lambda times, pos, rot, st, gy, le: st[0].__setitem__("second_index", 99), "out of range"),
        (lambda times, pos, rot, st, gy, le: times.__setitem__(2, times[1]), "strictly monotonic"),
        (lambda times, pos, rot, st, gy, le: pos.__setitem__((1, 0), np.nan), "reference_positions"),
        (lambda times, pos, rot, st, gy, le: st[0].__setitem__("confidence", 0.0), "confidence"),
        (lambda times, pos, rot, st, gy, le: times.__setitem__(3, 0.20), "interval has a gap"),
    ],
)
def test_invalid_inputs_are_rejected(mutate, message):
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    mutate(times, positions, rotations, stereo, gyro, learned)

    with pytest.raises(ValueError, match=message):
        solve_joint_stereo_se3(times, positions, rotations, stereo, gyro, learned)


def test_inputs_are_immutable_and_all_nodes_preserved_with_first_gauge_fixed():
    times, positions, rotations, extrinsic = scene()
    stereo, gyro, learned = all_factors(positions, rotations, extrinsic)
    before = (
        times.copy(),
        positions.copy(),
        rotations.copy(),
        [dict(row) for row in stereo],
        [dict(row) for row in gyro],
        [dict(row) for row in learned],
    )

    result = solve_joint_stereo_se3(times, positions, rotations, stereo, gyro, learned)

    np.testing.assert_allclose(times, before[0])
    np.testing.assert_allclose(positions, before[1])
    np.testing.assert_allclose(rotations, before[2])
    assert stereo == before[3]
    assert gyro == before[4]
    assert learned == before[5]
    assert result["times"].shape == times.shape
    assert result["positions"].shape == positions.shape
    assert result["rotations"].shape == rotations.shape
    np.testing.assert_allclose(result["positions"][0], positions[0], atol=1e-12)
    np.testing.assert_allclose(result["rotations"][0], rotations[0], atol=1e-12)
    assert result["diagnostic"]["all_nodes_retained"] is True


def test_api_has_no_ground_truth_or_selector_inputs():
    parameters = inspect.signature(solve_joint_stereo_se3).parameters
    forbidden = {"ground_truth", "reference_trajectory", "tracker", "score", "selector"}
    assert forbidden.isdisjoint(parameters)


def test_extractor_rows_minimal_adapter_to_solver_integration():
    times, positions, rotations, extrinsic = scene()
    extracted = {
        "reference_times": times,
        "reference_positions": positions,
        "reference_rotations": rotations,
        "selected_stereo_se3": [
            {
                "pair": (0, 1),
                "body_t_camera": extrinsic,
                "camera_j_from_i": stereo_factor(0, 1, positions, rotations, extrinsic)["rotation_camera_j_from_i"],
                "camera_i_delta_m": stereo_factor(0, 1, positions, rotations, extrinsic)["metric_displacement_camera_i_m"],
                "confidence": 1.0,
            }
        ],
        "gyro_edges": [
            {
                "pair": (0, 1),
                "delta": gyro_factor(0, 1, rotations)["delta_rotation_body_i_to_body_j"],
                "rotation_sigma_rad": 0.01,
            }
        ],
        "learned_edges": [
            {
                "pair": (0, 1),
                "delta_world_m": learned_edge(0, 1, positions)["metric_displacement_world_m"],
            }
        ],
    }

    stereo = [
        {
            "first_index": row["pair"][0],
            "second_index": row["pair"][1],
            "body_t_camera": np.asarray(row["body_t_camera"]).tolist(),
            "rotation_camera_j_from_i": row["camera_j_from_i"],
            "metric_displacement_camera_i_m": row["camera_i_delta_m"],
            "confidence": row["confidence"],
        }
        for row in extracted["selected_stereo_se3"]
    ]
    gyro = [
        {
            "first_index": row["pair"][0],
            "second_index": row["pair"][1],
            "delta_rotation_body_i_to_body_j": row["delta"],
            "rotation_sigma_rad": row["rotation_sigma_rad"],
        }
        for row in extracted["gyro_edges"]
    ]
    learned = [
        {
            "first_index": row["pair"][0],
            "second_index": row["pair"][1],
            "metric_displacement_world_m": row["delta_world_m"],
        }
        for row in extracted["learned_edges"]
    ]

    result = solve_joint_stereo_se3(
        extracted["reference_times"],
        extracted["reference_positions"],
        extracted["reference_rotations"],
        stereo,
        gyro,
        learned,
        optimize_rotations=False,
    )

    assert result["diagnostic"]["stereo_factor_count"] == 1
    assert result["diagnostic"]["gyro_factor_count"] == 1
    assert result["diagnostic"]["learned_displacement_prior_count"] == 1

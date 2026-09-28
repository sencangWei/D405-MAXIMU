import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "join_stereo_windows", ROOT / "scripts" / "join_stereo_windows.py"
)
join = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(join)
SOLVER_SPEC = importlib.util.spec_from_file_location(
    "stereo_window_bundle", ROOT / "scripts" / "stereo_window_bundle.py"
)
solver = importlib.util.module_from_spec(SOLVER_SPEC)
SOLVER_SPEC.loader.exec_module(solver)

LEFT = {"fx": 420.0, "fy": 418.0, "cx": 320.0, "cy": 240.0}
RIGHT = {"fx": 421.0, "fy": 419.0, "cx": 319.0, "cy": 241.0}
BASELINE = 0.018


def make_window(points, offset=0.0):
    observations = np.zeros((5, len(points), 4), dtype=float)
    valid = np.ones((5, len(points)), dtype=bool)
    for frame in range(5):
        observations[frame, :, 0] = points[:, 0] + offset + frame
        observations[frame, :, 1] = points[:, 1]
        observations[frame, :, 2] = points[:, 0] + offset + frame - 5.0
        observations[frame, :, 3] = points[:, 1] + 0.2
    initial = np.column_stack((points[:, 0] * 0.01, points[:, 1] * 0.01, np.full(len(points), 0.3)))
    return {"observations": observations, "valid": valid, "initial_points": initial}


def test_boundary_matches_are_mutual_one_to_one_and_reject_ambiguous():
    first = make_window(np.array([[10.0, 10.0], [30.0, 10.0], [70.0, 10.0]]))
    second = make_window(np.array([[14.2, 10.1], [34.1, 10.0], [34.3, 10.0], [90.0, 10.0]]))
    second["observations"][0, :, 0] = [14.2, 34.1, 34.3, 500.0]
    second["observations"][0, :, 2] = [9.2, 29.1, 29.3, 495.0]

    matches = join.boundary_matches(first, second)

    assert matches["pairs_a"].tolist() == [0]
    assert matches["pairs_b"].tolist() == [0]
    assert matches["rejected_ambiguous"] == 1
    assert matches["holdout_b"].tolist() == [True, False, False, False]


def test_boundary_ambiguity_counts_b_column_conflicts_without_matching_them():
    first = make_window(np.array([[10.0, 10.0], [10.3, 10.0], [70.0, 10.0]]))
    second = make_window(np.array([[14.1, 10.0], [90.0, 10.0]]))

    matches = join.boundary_matches(first, second)

    assert matches["pairs_a"].tolist() == []
    assert matches["pairs_b"].tolist() == []
    assert matches["rejected_ambiguous"] == 1


def test_join_transforms_second_window_nonzero_pose_and_borns_without_first_observation():
    first = make_window(np.array([[10.0, 10.0], [30.0, 10.0], [50.0, 10.0], [70.0, 10.0], [90.0, 10.0]]))
    second = make_window(np.array([[14.1, 10.0], [34.1, 10.0], [120.0, 20.0], [140.0, 20.0]]))
    first_centers = np.array([[0, 0, 0], [0.01, 0, 0], [0.02, 0, 0], [0.03, 0, 0], [0.1, 0.2, 0.0]], dtype=float)
    first_rotations = Rotation.from_rotvec([[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0.0, 0.0, 0.3]])
    second_centers = np.array([[0, 0, 0], [0.02, 0.0, 0.01], [0.04, 0.0, 0.01], [0.06, 0.0, 0.01], [0.08, 0.0, 0.01]])
    second_rotations = Rotation.from_rotvec([[0, 0, 0], [0.01, 0, 0], [0.02, 0, 0], [0.03, 0, 0], [0.04, 0, 0]])
    first_admission = first["valid"].copy()
    second_admission = second["valid"].copy()

    result = join.join_stereo_windows(
        first, second, first_admission, second_admission, first_centers, first_rotations, second_centers, second_rotations
    )

    assert result["observations"].shape == (9, 7, 4)
    assert result["valid"].shape == (9, 7)
    assert result["birth_indices"].tolist() == [0, 0, 0, 0, 0, 4, 4]
    assert not result["valid"][:4, 5:].any()
    assert result["valid"][4:, 5:].all()
    expected_pose = first_rotations[4] * second_rotations[2]
    np.testing.assert_allclose(result["initial_rotations"][6].as_matrix(), expected_pose.as_matrix())
    expected_point = first_rotations[4].apply(second["initial_points"][2]) + first_centers[4]
    np.testing.assert_allclose(result["initial_points"][5], expected_point)
    np.testing.assert_allclose(result["initial_points"][0], first["initial_points"][0])


def test_boundary_duplicate_is_counted_once_and_a_admission_wins():
    first = make_window(np.array([[10.0, 10.0], [30.0, 10.0], [50.0, 10.0], [70.0, 10.0], [90.0, 10.0]]))
    second = make_window(np.array([[14.0, 10.0], [34.0, 10.0], [54.0, 10.0], [74.0, 10.0], [94.0, 10.0]]))
    second["observations"][0, 0] = first["observations"][4, 0] + [0.5, 0.0, 0.5, 0.0]
    first_admission = first["valid"].copy()
    second_admission = second["valid"].copy()

    result = join.join_stereo_windows(
        first, second, first_admission, second_admission, np.zeros((5, 3)), Rotation.identity(5), np.zeros((5, 3)), Rotation.identity(5)
    )

    assert result["observations"].shape[1] == 5
    np.testing.assert_allclose(result["observations"][4, 0], first["observations"][4, 0])
    assert result["valid"][4, 0]
    assert int(result["valid"][:, 0].sum()) == 9


def test_heldout_inherits_from_a_for_matched_b_points():
    first = make_window(np.array([[10.0, 10.0], [30.0, 10.0], [50.0, 10.0], [70.0, 10.0], [90.0, 10.0], [110.0, 10.0]]))
    second = make_window(np.array([[14.0, 10.0], [34.0, 10.0], [54.0, 10.0], [74.0, 10.0], [94.0, 10.0], [114.0, 10.0]]))

    result = join.join_stereo_windows(
        first, second, first["valid"], second["valid"], np.zeros((5, 3)), Rotation.identity(5), np.zeros((5, 3)), Rotation.identity(5)
    )

    assert result["heldout"].tolist() == [True, False, False, False, False, True]
    assert result["train"].tolist() == [False, True, True, True, True, False]
    assert result["heldout_rawvalid"].shape == result["valid"].shape
    assert result["admitted_train"].shape == result["valid"].shape
    assert result["shared_train_count"] == 4
    assert result["mapping"]["matched_first"] == [0, 1, 2, 3, 4, 5]
    assert result["mapping"]["matched_second"] == [0, 1, 2, 3, 4, 5]


def test_invalid_shape_and_negative_global_birth_depth_reject():
    first = make_window(np.array([[10.0, 10.0], [30.0, 10.0], [50.0, 10.0], [70.0, 10.0]]))
    second = make_window(np.array([[100.0, 20.0], [120.0, 20.0], [140.0, 20.0], [160.0, 20.0]]))
    with pytest.raises(ValueError, match="shape"):
        join.join_stereo_windows(first, second, np.ones((4, 4), bool), second["valid"], np.zeros((5, 3)), Rotation.identity(5), np.zeros((5, 3)), Rotation.identity(5))
    second["initial_points"][0, 2] = -0.1
    with pytest.raises(ValueError, match="birth"):
        join.join_stereo_windows(first, second, first["valid"], second["valid"], np.zeros((5, 3)), Rotation.identity(5), np.zeros((5, 3)), Rotation.identity(5))


def test_aliases_removed_and_masks_not_mutated():
    first = make_window(np.array([[10.0, 10.0], [30.0, 10.0], [50.0, 10.0], [70.0, 10.0], [90.0, 10.0]]))
    second = make_window(np.array([[14.0, 10.0], [34.0, 10.0], [54.0, 10.0], [74.0, 10.0], [94.0, 10.0]]))
    first_holdout = np.array([False, True, False, False, True])
    second_holdout = np.zeros(5, dtype=bool)
    second_holdout_before = second_holdout.copy()
    result = join.join_stereo_windows(
        first, second, first["valid"], second["valid"], np.zeros((5, 3)), Rotation.identity(5),
        np.zeros((5, 3)), Rotation.identity(5), first_holdout=first_holdout, second_holdout=second_holdout
    )

    for key in ("rawvalid", "globalheldoutmask", "fulltrain", "birth_node", "local_birth_points", "merged_admission"):
        assert key not in result
    np.testing.assert_array_equal(second_holdout, second_holdout_before)
    assert result["heldout"].tolist() == first_holdout.tolist()


def test_admission_cannot_mark_rawinvalid_observation():
    first = make_window(np.array([[10.0, 10.0], [30.0, 10.0], [50.0, 10.0], [70.0, 10.0]]))
    second = make_window(np.array([[100.0, 20.0], [120.0, 20.0], [140.0, 20.0], [160.0, 20.0]]))
    admission = first["valid"].copy()
    first["valid"][2, 1] = False

    with pytest.raises(ValueError, match="rawinvalid"):
        join.join_stereo_windows(first, second, admission, second["valid"], np.zeros((5, 3)), Rotation.identity(5), np.zeros((5, 3)), Rotation.identity(5))


def project(points, center, rotation, intrinsics, baseline=0.0):
    camera = rotation.inv().apply(points - center) - np.array([baseline, 0.0, 0.0])
    return np.column_stack((
        intrinsics["fx"] * camera[:, 0] / camera[:, 2] + intrinsics["cx"],
        intrinsics["fy"] * camera[:, 1] / camera[:, 2] + intrinsics["cy"],
    ))


def physical_window(global_points, centers, rotations):
    obs = np.empty((5, len(global_points), 4), dtype=float)
    for frame in range(5):
        left = project(global_points, centers[frame], rotations[frame], LEFT)
        right = project(global_points, centers[frame], rotations[frame], RIGHT, BASELINE)
        obs[frame] = np.column_stack((left, right))
    local_points = rotations[0].inv().apply(global_points - centers[0])
    return {"observations": obs, "valid": np.ones(obs.shape[:2], dtype=bool), "initial_points": local_points}


def test_joined_laterborn_window_solves_metric_with_no_first_observation():
    centers = np.column_stack((0.008 * np.arange(9), 0.002 * np.sin(np.arange(9)), np.zeros(9)))
    rotations = Rotation.from_rotvec(np.column_stack((0.002 * np.arange(9), -0.003 * np.arange(9), 0.004 * np.arange(9))))
    shared = np.array([
        [-0.09, -0.04, 0.34], [-0.04, 0.03, 0.37], [0.02, -0.05, 0.36], [0.07, 0.04, 0.39],
        [-0.08, 0.05, 0.43], [0.00, 0.00, 0.41], [0.09, -0.02, 0.35], [0.04, 0.06, 0.45],
    ])
    born = np.array([[-0.03, -0.02, 0.32], [0.03, 0.02, 0.33], [0.08, 0.00, 0.34], [-0.07, 0.03, 0.36]])
    first = physical_window(shared, centers[:5], rotations[:5])
    second_points_global = np.vstack((shared, born))
    second_data = physical_window(second_points_global, centers[4:9], rotations[4:9])
    second_local_centers = rotations[4].inv().apply(centers[4:9] - centers[4])
    second_local_rotations = rotations[4].inv() * rotations[4:9]

    result = join.join_stereo_windows(
        first, second_data, first["valid"], second_data["valid"], centers[:5], rotations[:5],
        second_local_centers, second_local_rotations, first_holdout=np.zeros(len(shared), dtype=bool),
        second_holdout=np.zeros(len(second_points_global), dtype=bool),
    )
    assert result["valid"][:4, len(shared):].sum() == 0
    assert np.all(result["valid"][4:, len(shared):].sum(axis=0) >= 2)
    assert result["shared_train_count"] >= 4

    gyro = rotations[:-1].inv() * rotations[1:]
    solved = solver.solve_stereo_window(
        result["observations"], result["admission"], np.arange(9, dtype=float) * 0.1,
        LEFT, RIGHT, BASELINE, result["initial_points"], result["initial_centers"],
        result["initial_rotations"], gyro, gyro_noise_density=0.002, gyro_bias_sigma=0.02,
    )

    assert solved["accepted"], solved
    assert np.median(np.linalg.norm(solved["landmarks"] - np.vstack((shared, born)), axis=1)) < 1e-6

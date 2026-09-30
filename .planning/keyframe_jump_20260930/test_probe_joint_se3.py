import numpy as np
from scipy.spatial.transform import Rotation

from probe_joint_se3 import (
    integrate_local_stereo_scale,
    motion_disagreement_nodes,
    relative_pose,
    solve_joint_poses,
)


def test_joint_se3_recovers_consistent_metric_motion_without_external_reference():
    frames = 26
    nodes = np.arange(0, frames, 5)
    truth = np.column_stack((np.arange(frames) * 0.01, np.zeros(frames), np.zeros(frames)))
    visual_position = truth.copy()
    visual_position[:, 1] = 0.018 * np.sin(np.pi * np.arange(frames) / 25)
    visual_rotation = Rotation.from_rotvec(
        np.column_stack((np.zeros(frames), np.zeros(frames),
                         0.04 * np.sin(np.pi * np.arange(frames) / 25)))
    )
    inertial_rotation = Rotation.identity(frames)
    observations = [
        {
            "accepted": True,
            "first_index": int(first),
            "second_index": int(second),
            "metric_displacement_camera_i_m": [0.05, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        }
        for first, second in zip(nodes[:-1], nodes[1:])
    ]
    position, rotation, report = solve_joint_poses(
        visual_position, visual_rotation, truth, inertial_rotation,
        np.ones(frames, dtype=bool), nodes, observations,
    )
    assert report["stereo_edges_selected"] == 5
    assert report["stereo_translation_rmse_after_mm"] < report["stereo_translation_rmse_before_mm"]
    assert np.linalg.norm(position - truth) < np.linalg.norm(visual_position - truth)
    assert np.linalg.norm(rotation.as_rotvec()) < np.linalg.norm(visual_rotation.as_rotvec())


def test_stereo_edge_conflicting_with_vins_is_not_inserted():
    frames = 31
    nodes = np.arange(0, frames, 5)
    truth = np.column_stack((np.arange(frames) * 0.01, np.zeros(frames), np.zeros(frames)))
    observations = [
        {
            "accepted": True,
            "first_index": int(first),
            "second_index": int(second),
            "metric_displacement_camera_i_m": [0.05 if first else 0.10, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        }
        for first, second in zip(nodes[:-1], nodes[1:])
    ]
    _, _, report = solve_joint_poses(
        truth, Rotation.identity(frames), truth, Rotation.identity(frames),
        np.ones(frames, dtype=bool), nodes, observations,
    )
    assert report["stereo_edges_selected"] == 5


def test_single_frame_jump_exposes_both_sides_as_joint_states():
    truth = np.column_stack((np.arange(11) * 0.01, np.zeros(11), np.zeros(11)))
    visual = truth.copy()
    visual[6, 1] = 0.03
    nodes, edges, _ = motion_disagreement_nodes(
        visual, Rotation.identity(11), truth, Rotation.identity(11),
        np.ones(11, dtype=bool),
    )
    assert edges.tolist() == [5, 6]
    assert nodes.tolist() == [5, 6, 7]


def test_all_frame_joint_states_use_visual_motion_and_metric_edges():
    frames = 26
    truth = np.column_stack((np.arange(frames) * 0.01, np.zeros(frames), np.zeros(frames)))
    visual = truth.copy()
    visual[8:16, 1] = 0.012
    observations = [
        {
            "accepted": True,
            "first_index": first,
            "second_index": first + 5,
            "metric_displacement_camera_i_m": [0.05, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        }
        for first in range(0, 25, 5)
    ]
    position, _, report = solve_joint_poses(
        visual, Rotation.identity(frames), truth, Rotation.identity(frames),
        np.ones(frames, dtype=bool), np.arange(frames), observations,
        visual_prior_nodes=np.arange(0, frames, 5), use_visual_step_factors=True,
    )
    assert report["nodes"] == frames
    assert report["visual_absolute_prior_nodes"] == 6
    assert report["visual_step_factors"] == frames - 1
    assert np.linalg.norm(position[8:16] - truth[8:16]) < np.linalg.norm(visual[8:16] - truth[8:16])
    assert report["external_ground_truth_used"] is False


def test_joint_rotation_uses_stereo_pnp_direction_when_motion_is_nonzero():
    frames = 26
    nodes = np.arange(0, frames, 5)
    truth_pos = np.column_stack((np.arange(frames) * 0.01, np.zeros(frames), np.zeros(frames)))
    truth_rot = Rotation.from_euler("z", np.linspace(0.0, 0.2, frames))
    visual_rot = truth_rot * Rotation.from_rotvec(
        np.column_stack((np.zeros(frames), np.zeros(frames),
                         0.03 * np.sin(np.pi * np.arange(frames) / 25)))
    )
    observations = []
    for first, second in zip(nodes[:-1], nodes[1:]):
        translation, rotation = relative_pose(truth_pos, truth_rot, first, second)
        observations.append({
            "accepted": True,
            "first_index": int(first),
            "second_index": int(second),
            "metric_displacement_camera_i_m": translation.tolist(),
            "pnp_rotation_quaternion_xyzw": rotation.as_quat().tolist(),
        })
    _, corrected_rot, _ = solve_joint_poses(
        truth_pos, visual_rot, truth_pos, truth_rot,
        np.ones(frames, dtype=bool), nodes, observations,
    )
    before = (truth_rot.inv() * visual_rot).magnitude()
    after = (truth_rot.inv() * corrected_rot).magnitude()
    assert np.linalg.norm(after) < np.linalg.norm(before)


def test_constant_local_stereo_scale_preserves_metric_path():
    native = np.column_stack((np.arange(101) * 0.01, np.zeros(101), np.zeros(101)))
    observations = [
        {"accepted": True, "first_index": index, "second_index": index + 5, "scale": 0.2}
        for index in range(0, 95, 5)
    ]
    metric, report = integrate_local_stereo_scale(native, observations, 0.2)
    assert np.allclose(metric, native * 0.2)
    assert np.isclose(report["local_scale_first_100_median"], 0.2)

import numpy as np
from scipy.spatial.transform import Rotation

from probe_joint_se3 import motion_disagreement_nodes, solve_joint_poses


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

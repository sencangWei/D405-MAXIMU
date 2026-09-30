#!/usr/bin/env python3
"""Isolated, source-only joint camera pose experiment; never used in production.

The graph jointly moves camera positions and orientations at saved keyframes
and regular five-frame nodes. Stereo PnP and VINS contribute relative SE(3)
edges; neither Lighthouse nor robot poses are read to generate the result.
"""

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import lsqr
from scipy.spatial.transform import Rotation, Slerp

from fuse_mast3r_stereo_imu import load_trajectory, load_vins_config


def camera_vins_at(times, vins_path, config_path):
    vins_times, body_pos, body_rot, _ = load_trajectory(vins_path)
    config = load_vins_config(config_path, expected_td_s=-0.009109323)
    transform = config["body_T_camera"]
    camera_pos = body_pos + body_rot.apply(transform[:3, 3])
    camera_rot = body_rot * Rotation.from_matrix(transform[:3, :3])
    valid = (times >= vins_times[0]) & (times <= vins_times[-1])
    clipped = np.clip(times, vins_times[0], vins_times[-1])
    pos = np.column_stack(
        [np.interp(clipped, vins_times, camera_pos[:, axis]) for axis in range(3)]
    )
    return pos, Slerp(vins_times, camera_rot)(clipped), valid


def skew(vector):
    x, y, z = vector
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def relative_pose(positions, rotations, first, second):
    return (
        rotations[first].inv().apply(positions[second] - positions[first]),
        rotations[second].inv() * rotations[first],
    )


def motion_disagreement_nodes(base_pos, base_rot, vins_pos, vins_rot, valid,
                              threshold_m=0.008):
    """Expose only rare single-frame metric disagreements as graph states."""
    visual_step = base_rot[:-1].inv().apply(np.diff(base_pos, axis=0))
    inertial_step = vins_rot[:-1].inv().apply(np.diff(vins_pos, axis=0))
    mismatch = np.linalg.norm(visual_step - inertial_step, axis=1)
    unusual = np.flatnonzero(
        (valid[:-1] & valid[1:]) & (mismatch > threshold_m)
    )
    return np.union1d(unusual, unusual + 1), unusual, mismatch


def solve_joint_poses(base_pos, base_rot, vins_pos, vins_rot, valid, nodes, stereo,
                      visual_position_sigma=0.020, visual_prior_mode="absolute"):
    """Solve one global camera gauge with joint position/rotation increments."""
    count = len(nodes)
    node_pos = base_pos[nodes]
    node_rot = base_rot[nodes]
    vins_node_pos = vins_pos[nodes]
    vins_node_rot = vins_rot[nodes]
    position_delta = np.zeros((count, 3))
    rotation_delta = np.zeros((count, 3))
    if visual_position_sigma <= 0:
        raise ValueError("visual position sigma must be positive")
    if visual_prior_mode not in {"absolute", "random-walk"}:
        raise ValueError("unsupported visual position prior mode")
    visual_rotation_sigma = np.radians(3.0)
    vins_translation_sigma = 0.008
    vins_rotation_sigma = np.radians(1.0)
    stereo_translation_sigma = 0.004
    stereo_rotation_sigma = np.radians(1.0)
    accepted_edges = []
    node_of_frame = {int(frame): index for index, frame in enumerate(nodes)}
    for observation in stereo:
        if not observation.get("accepted") or "metric_displacement_camera_i_m" not in observation:
            continue
        if "pnp_rotation_quaternion_xyzw" not in observation:
            continue
        first, second = int(observation["first_index"]), int(observation["second_index"])
        if first not in node_of_frame or second not in node_of_frame:
            continue
        if not (valid[first] and valid[second]):
            continue
        translation = np.asarray(observation["metric_displacement_camera_i_m"], dtype=float)
        rotation = Rotation.from_quat(observation["pnp_rotation_quaternion_xyzw"])
        vins_translation, vins_rotation = relative_pose(vins_pos, vins_rot, first, second)
        translation_agreement = float(np.linalg.norm(translation - vins_translation))
        rotation_agreement = float(np.degrees((rotation.inv() * vins_rotation).magnitude()))
        if translation_agreement > 0.010 or rotation_agreement > 2.0:
            continue
        accepted_edges.append((node_of_frame[first], node_of_frame[second], translation, rotation))
    if len(accepted_edges) < 4:
        raise ValueError("fewer than four stereo/VINS-consistent SE(3) edges")

    rows = 6 * count + 6 + 6 * (count - 1) + 6 * len(accepted_edges)
    columns = 6 * count
    stereo_residual_before = []
    stereo_residual_after = []

    def add_block(matrix, row, node, block, rotation=False):
        start = 3 * node + (3 * count if rotation else 0)
        matrix[row : row + 3, start : start + 3] += block

    def add_edge(matrix, target, row, first, second, translation, rotation, current_pos, current_rot,
                 translation_sigma, rotation_sigma, robust):
        predicted_translation, predicted_rotation = relative_pose(
            current_pos, current_rot, first, second
        )
        translation_error = predicted_translation - translation
        rotation_error = (rotation.inv() * predicted_rotation).as_rotvec()
        translation_weight = (
            min(1.0, 0.010 / max(np.linalg.norm(translation_error), 1e-12)) if robust else 1.0
        ) ** 0.5 / translation_sigma
        rotation_weight = (
            min(1.0, np.radians(2.0) / max(np.linalg.norm(rotation_error), 1e-12)) if robust else 1.0
        ) ** 0.5 / rotation_sigma
        inverse_first = current_rot[first].inv().as_matrix()
        add_block(matrix, row, first, -inverse_first * translation_weight)
        add_block(matrix, row, second, inverse_first * translation_weight)
        add_block(matrix, row, first, skew(predicted_translation) * translation_weight, rotation=True)
        target[row : row + 3] = -translation_error * translation_weight
        row += 3
        add_block(matrix, row, first, np.eye(3) * rotation_weight, rotation=True)
        add_block(matrix, row, second, -np.eye(3) * rotation_weight, rotation=True)
        target[row : row + 3] = -rotation_error * rotation_weight
        return row + 3

    for iteration in range(4):
        current_pos = node_pos + position_delta
        current_rot = node_rot * Rotation.from_rotvec(rotation_delta)
        matrix = lil_matrix((rows, columns), dtype=float)
        target = np.zeros(rows)
        row = 0
        for node in range(count):
            add_block(matrix, row, node, np.eye(3) / visual_position_sigma)
            if visual_prior_mode == "random-walk" and node > 0:
                add_block(matrix, row, node - 1, -np.eye(3) / visual_position_sigma)
                target[row : row + 3] = -(
                    position_delta[node] - position_delta[node - 1]
                ) / visual_position_sigma
            else:
                target[row : row + 3] = -position_delta[node] / visual_position_sigma
            row += 3
            add_block(matrix, row, node, np.eye(3) / visual_rotation_sigma, rotation=True)
            target[row : row + 3] = -rotation_delta[node] / visual_rotation_sigma
            row += 3
        add_block(matrix, row, 0, np.eye(3) / 0.0001)
        target[row : row + 3] = -position_delta[0] / 0.0001
        row += 3
        add_block(matrix, row, 0, np.eye(3) / np.radians(0.02), rotation=True)
        target[row : row + 3] = -rotation_delta[0] / np.radians(0.02)
        row += 3
        for first in range(count - 1):
            second = first + 1
            if valid[nodes[first]] and valid[nodes[second]]:
                translation, rotation = relative_pose(vins_node_pos, vins_node_rot, first, second)
                row = add_edge(matrix, target, row, first, second, translation, rotation,
                               current_pos, current_rot, vins_translation_sigma,
                               vins_rotation_sigma, robust=False)
            else:
                row += 6
        for first, second, translation, rotation in accepted_edges:
            row = add_edge(matrix, target, row, first, second, translation, rotation,
                           current_pos, current_rot, stereo_translation_sigma,
                           stereo_rotation_sigma, robust=True)
        if row != rows:
            raise AssertionError("joint graph row count mismatch")
        solution = lsqr(matrix.tocsr(), target, atol=1e-9, btol=1e-9, iter_lim=3000)
        if solution[1] not in (1, 2):
            raise ValueError(f"joint graph LSQR failed: istop={solution[1]}")
        increment = solution[0]
        position_delta += increment[: 3 * count].reshape(count, 3)
        rotation_delta += increment[3 * count :].reshape(count, 3)
        if not np.all(np.isfinite(position_delta)) or not np.all(np.isfinite(rotation_delta)):
            raise ValueError("joint graph returned nonfinite state")
    final_pos = node_pos + position_delta
    final_rot = node_rot * Rotation.from_rotvec(rotation_delta)
    for first, second, translation, _ in accepted_edges:
        stereo_residual_before.append(np.linalg.norm(relative_pose(node_pos, node_rot, first, second)[0] - translation))
        stereo_residual_after.append(np.linalg.norm(relative_pose(final_pos, final_rot, first, second)[0] - translation))
    frame_indices = np.arange(len(base_pos))
    full_position_delta = np.column_stack(
        [np.interp(frame_indices, nodes, position_delta[:, axis]) for axis in range(3)]
    )
    full_rotation_delta = np.column_stack(
        [np.interp(frame_indices, nodes, rotation_delta[:, axis]) for axis in range(3)]
    )
    report = {
        "nodes": count,
        "stereo_edges_selected": len(accepted_edges),
        "max_position_correction_mm": float(np.max(np.linalg.norm(full_position_delta, axis=1)) * 1000),
        "max_rotation_correction_deg": float(np.degrees(np.max(np.linalg.norm(full_rotation_delta, axis=1)))),
        "stereo_translation_rmse_before_mm": float(np.sqrt(np.mean(np.square(stereo_residual_before))) * 1000),
        "stereo_translation_rmse_after_mm": float(np.sqrt(np.mean(np.square(stereo_residual_after))) * 1000),
        "external_ground_truth_used": False,
        "diagnostic_only": True,
        "visual_position_sigma_m": visual_position_sigma,
        "visual_prior_mode": visual_prior_mode,
    }
    return base_pos + full_position_delta, base_rot * Rotation.from_rotvec(full_rotation_delta), report


def run(frontend, vins, config, output, visual_position_sigma_mm=20.0,
        visual_prior_mode="absolute", add_motion_disagreement_nodes=False):
    times, native_pos, base_rot, _ = load_trajectory(frontend / "trajectory_frames.csv")
    stereo_report = json.loads((frontend / "stereo_scale_bidirectional_report.json").read_text())
    scale = float(stereo_report["scale_m_per_mast3r_unit"])
    base_pos = native_pos * scale
    vins_pos, vins_rot, valid = camera_vins_at(times, vins, config)
    saved_keyframes = np.rint(
        np.loadtxt(frontend / "mast3r_logs/dataset.txt", usecols=0) * 30
    ).astype(int)
    nodes = np.union1d(np.arange(0, len(times), 5), saved_keyframes)
    nodes = np.union1d(nodes, [0, len(times) - 1])
    disagreement_edges = np.array([], dtype=int)
    if add_motion_disagreement_nodes:
        extra_nodes, disagreement_edges, _ = motion_disagreement_nodes(
            base_pos, base_rot, vins_pos, vins_rot, valid
        )
        nodes = np.union1d(nodes, extra_nodes)
    candidate_pos, candidate_rot, report = solve_joint_poses(
        base_pos, base_rot, vins_pos, vins_rot, valid, nodes,
        stereo_report["observations"], visual_position_sigma_mm / 1000.0,
        visual_prior_mode,
    )
    output.mkdir(parents=True, exist_ok=False)
    for name, positions, rotations in (("baseline", base_pos, base_rot),
                                       ("candidate", candidate_pos, candidate_rot)):
        with (output / f"{name}_camera_m.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"))
            for timestamp, position, quaternion in zip(times, positions, rotations.as_quat()):
                writer.writerow((f"{timestamp:.9f}", *(f"{x:.12g}" for x in position),
                                 f"{quaternion[3]:.12g}", *(f"{x:.12g}" for x in quaternion[:3])))
    report.update({
        "frames": len(times),
        "vins_valid_frames": int(np.count_nonzero(valid)),
        "vins_valid_fraction": float(np.mean(valid)),
        "motion_disagreement_edges": disagreement_edges.tolist(),
        "motion_disagreement_node_policy": (
            "consecutive_onboard_displacement_over_8mm"
            if add_motion_disagreement_nodes else "disabled"
        ),
        "stereo_scale_m_per_native_unit": scale,
        "source": str(frontend),
        "vins_source": str(vins),
        "max_step_before_mm": float(np.max(np.linalg.norm(np.diff(base_pos, axis=0), axis=1)) * 1000),
        "max_step_after_mm": float(np.max(np.linalg.norm(np.diff(candidate_pos, axis=0), axis=1)) * 1000),
    })
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--vins", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--visual-position-sigma-mm", type=float, default=20.0)
    parser.add_argument("--visual-prior-mode", choices=("absolute", "random-walk"),
                        default="absolute")
    parser.add_argument("--add-motion-disagreement-nodes", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.frontend, arguments.vins, arguments.config, arguments.output,
                         arguments.visual_position_sigma_mm,
                         arguments.visual_prior_mode,
                         arguments.add_motion_disagreement_nodes), indent=2))

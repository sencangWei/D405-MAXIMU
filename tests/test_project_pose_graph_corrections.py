import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from project_pose_graph_corrections import project_pose_graph, reject_path_collisions


def test_projects_known_pose_graph_transform_without_endpoint_truth():
    times = np.linspace(0.0, 4.0, 9)
    positions = np.column_stack((times, np.zeros_like(times), np.zeros_like(times)))
    rotations = Rotation.identity(len(times))
    graph_times = times[2:8:2]
    graph_raw_positions = positions[2:8:2]
    correction = Rotation.from_euler("z", 10.0, degrees=True)
    translation = np.array([0.2, -0.1, 0.03])
    graph_positions = correction.apply(graph_raw_positions) + translation
    graph_rotations = correction * Rotation.identity(len(graph_times))

    emitted_times, corrected, _, report = project_pose_graph(
        times, positions, rotations, graph_times, graph_positions, graph_rotations
    )

    np.testing.assert_allclose(
        corrected[np.searchsorted(emitted_times, graph_times)], graph_positions, atol=1e-12
    )
    assert report["uses_endpoint_constraint"] is False
    assert report["keyframe_reconstruction_max_mm"] < 1e-9


def test_preserves_raw_prefix_before_first_pose_graph_keyframe():
    times = np.arange(6.0)
    positions = np.column_stack((times, np.zeros_like(times), np.zeros_like(times)))
    rotations = Rotation.identity(len(times))
    graph_times = np.array([2.0, 4.0])
    graph_positions = positions[[2, 4]] + np.array([0.2, 0.0, 0.0])
    graph_rotations = Rotation.identity(2)

    _, corrected, _, _ = project_pose_graph(
        times, positions, rotations, graph_times, graph_positions, graph_rotations
    )

    np.testing.assert_allclose(corrected[0], positions[0], atol=1e-12)
    np.testing.assert_allclose(corrected[4], graph_positions[1], atol=1e-12)
    np.testing.assert_allclose(corrected[-1], positions[-1] + [0.2, 0, 0], atol=1e-12)


def test_inserts_missing_pose_graph_knot_and_reconstructs_it_exactly():
    raw_times = np.array([0.0, 1.0, 3.0, 4.0])
    raw_positions = np.column_stack(
        (raw_times, np.zeros_like(raw_times), np.zeros_like(raw_times))
    )
    raw_rotations = Rotation.identity(len(raw_times))
    graph_times = np.array([1.0, 2.0, 3.0])
    graph_positions = np.array(
        [[1.0, 0.0, 0.0], [2.0, 0.2, 0.0], [3.0, 0.0, 0.0]]
    )
    graph_rotations = Rotation.from_euler("z", [0.0, 5.0, 0.0], degrees=True)

    emitted_times, corrected, corrected_rotations, report = project_pose_graph(
        raw_times,
        raw_positions,
        raw_rotations,
        graph_times,
        graph_positions,
        graph_rotations,
    )

    assert report["result"] == "PASS"
    assert report["inserted_pose_graph_samples"] == 1
    assert report["emitted_samples"] == 5
    graph_indices = np.searchsorted(emitted_times, graph_times)
    np.testing.assert_allclose(corrected[graph_indices], graph_positions, atol=1e-12)
    np.testing.assert_allclose(
        (corrected_rotations[graph_indices] * graph_rotations.inv()).magnitude(),
        0.0,
        atol=1e-12,
    )


def test_4dof_projection_removes_pitch_and_roll_from_rotation_correction():
    times = np.linspace(0.0, 4.0, 9)
    positions = np.column_stack((times, 0.25 * times**2, np.zeros_like(times)))
    raw_rotations = Rotation.from_euler(
        "zyx",
        np.column_stack((2.0 * times, 0.4 * times, -0.3 * times)),
        degrees=True,
    )
    graph_times = times[2:8:2]
    graph_raw_positions = positions[2:8:2]
    yaw_correction = Rotation.from_euler("z", 12.0, degrees=True)
    full_graph_correction = Rotation.from_euler(
        "zyx", [12.0, 5.0, -4.0], degrees=True
    )
    graph_positions = yaw_correction.apply(graph_raw_positions) + [0.2, -0.1, 0.03]
    graph_rotations = full_graph_correction * raw_rotations[2:8:2]

    emitted_times, _, corrected_rotations, report = project_pose_graph(
        times,
        positions,
        raw_rotations,
        graph_times,
        graph_positions,
        graph_rotations,
    )

    emitted_raw_rotations = Rotation.from_euler(
        "zyx",
        np.column_stack(
            (
                2.0 * emitted_times,
                0.4 * emitted_times,
                -0.3 * emitted_times,
            )
        ),
        degrees=True,
    )
    applied_correction = corrected_rotations * emitted_raw_rotations.inv()
    correction_matrices = applied_correction.as_matrix()
    np.testing.assert_allclose(correction_matrices[:, 2, :2], 0.0, atol=1e-12)
    np.testing.assert_allclose(correction_matrices[:, :2, 2], 0.0, atol=1e-12)
    assert report["rotation_projection"] == "yaw_only"
    assert report["maximum_removed_tilt_deg"] > 6.0


def test_rejects_output_aliasing_pose_graph_input(tmp_path: Path):
    raw = tmp_path / "raw.csv"
    graph = tmp_path / "graph.csv"
    report = tmp_path / "report.json"
    try:
        reject_path_collisions([raw, graph], [graph, report])
    except ValueError as error:
        assert "aliases an input" in str(error)
    else:
        raise AssertionError("input/output alias was accepted")

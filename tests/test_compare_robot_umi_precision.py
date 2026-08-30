from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "compare_robot_umi_precision.py"
SPEC = importlib.util.spec_from_file_location("compare_robot_umi_precision", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_umi(path: Path, times: list[float]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"])
        for t in times:
            writer.writerow([t, t, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])


def _write_robot(path: Path) -> None:
    rows = []
    for index in range(31):
        t = 100.0 + index * 0.1
        rows.append(
            {
                "host_wall_epoch_s": t,
                "leader_deg": [0.0] * 7,
                "target_deg": [0.0] * 7,
                "actual_deg": [0.0] * 7,
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_default_source_requires_full_corrected_stream(tmp_path: Path) -> None:
    slam_dir = tmp_path / "slam"
    slam_dir.mkdir()
    (slam_dir / "loop_output").mkdir()
    (slam_dir / "loop_output" / "vio_loop.csv").write_text("legacy\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="不自动回退"):
        MODULE.resolve_umi_path(slam_dir, None)

    full = slam_dir / "vio_corrected_stream.csv"
    full.write_text("full\n", encoding="utf-8")
    path, kind = MODULE.resolve_umi_path(slam_dir, None)
    assert path == full.resolve()
    assert kind == "full_corrected_stream"


def test_linear_interpolation_and_association_gate() -> None:
    source_t = np.asarray([0.0, 1.0, 2.0])
    source_values = np.asarray([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0], [2.0, 4.0, 6.0]])
    values, nearest_delta, valid = MODULE.interpolate_vector(
        np.asarray([0.25, 1.8]), source_t, source_values, max_gap_s=1.1
    )
    np.testing.assert_allclose(values, [[0.25, 0.5, 0.75], [1.8, 3.6, 5.4]])
    np.testing.assert_allclose(nearest_delta, [0.25, 0.2])
    assert valid.tolist() == [True, True]
    assert (nearest_delta <= 0.1).tolist() == [False, False]


def test_body_to_tcp_chain_rotates_lever_arm_with_body_pose() -> None:
    # The hand-eye translation is a lever arm.  A 90-degree body yaw must
    # rotate that arm; a translation-only offset would be wrong on turns.
    body_to_camera = np.eye(4)
    gripper_camera = np.eye(4)
    gripper_camera[:3, 3] = [0.1, 0.0, 0.0]  # camera -> gripper
    positions = np.zeros((2, 3))
    quaternions = np.asarray(
        [
            [0.0, 0.0, 0.0, 1.0],
            [0.0, 0.0, np.sqrt(0.5), np.sqrt(0.5)],
        ]
    )

    tcp = MODULE.transform_body_poses_to_tcp(
        positions, quaternions, body_to_camera, gripper_camera
    )

    np.testing.assert_allclose(tcp[0, :3, 3], [-0.1, 0.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(tcp[1, :3, 3], [0.0, -0.1, 0.0], atol=1e-9)


def test_gripper_tcp_offset_rotates_with_gripper_pose() -> None:
    world_gripper = np.tile(np.eye(4), (2, 1, 1))
    world_gripper[1, :3, :3] = MODULE.Rotation.from_euler("z", 90, degrees=True).as_matrix()
    offset = np.eye(4)
    offset[:3, 3] = [0.05, -0.01, 0.02]

    tcp = MODULE.apply_gripper_tcp_offset(world_gripper, offset)

    np.testing.assert_allclose(tcp[0, :3, 3], [0.05, -0.01, 0.02], atol=1e-9)
    np.testing.assert_allclose(tcp[1, :3, 3], [0.01, 0.05, 0.02], atol=1e-9)


def test_gripper_tcp_offset_rejects_non_rigid_matrix() -> None:
    world_gripper = np.tile(np.eye(4), (1, 1, 1))
    invalid = np.eye(4)
    invalid[0, 0] = 2.0
    with pytest.raises(ValueError, match=r"SO\(3\)"):
        MODULE.apply_gripper_tcp_offset(world_gripper, invalid)


def test_load_gripper_tcp_offset_requires_measured_schema(tmp_path: Path) -> None:
    path = tmp_path / "tcp_offset.json"
    path.write_text(
        json.dumps(
            {
                "schema": "umi_gripper_tcp_offset_v1",
                "measured": False,
                "T_gripper_tcp": np.eye(4).tolist(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="measured: true"):
        MODULE.load_gripper_tcp_offset(path)

    path.write_text(
        json.dumps(
            {
                "schema": "umi_gripper_tcp_offset_v1",
                "measured": True,
                "T_gripper_tcp": np.eye(4).tolist(),
            }
        ),
        encoding="utf-8",
    )
    np.testing.assert_allclose(MODULE.load_gripper_tcp_offset(path), np.eye(4))


def test_load_gripper_tcp_frame_uses_child_label(tmp_path: Path) -> None:
    path = tmp_path / "tcp_offset.json"
    path.write_text(
        json.dumps(
            {
                "schema": "umi_gripper_tcp_offset_v1",
                "measured": True,
                "frame_child": "umi_right_jaw_tcp",
                "T_gripper_tcp": np.eye(4).tolist(),
            }
        ),
        encoding="utf-8",
    )
    assert MODULE.load_gripper_tcp_frame(path) == "umi_right_jaw_tcp"


def test_load_robot_clock_offset_requires_validated_schema(tmp_path: Path) -> None:
    path = tmp_path / "clock.json"
    path.write_text(
        json.dumps(
            {
                "schema": "robot_umi_clock_offset_calibration_v1",
                "robot_query_offset_ms": 16.58,
                "source": "multi_run_median",
            }
        ),
        encoding="utf-8",
    )
    offset, source = MODULE.load_robot_clock_offset_ms(path)
    assert offset == pytest.approx(16.58)
    assert source == str(path.resolve())

    path.write_text(
        json.dumps({"schema": "wrong", "robot_query_offset_ms": 16.58}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="受支持"):
        MODULE.load_robot_clock_offset_ms(path)


def test_load_opencv_body_transform(tmp_path: Path) -> None:
    config = tmp_path / "vins_config.yaml"
    config.write_text(
        "body_T_cam0: !!opencv-matrix\n"
        "   rows: 4\n   cols: 4\n   dt: d\n"
        "   data: [ 1, 0, 0, 0.1, 0, 1, 0, 0.2, "
        "0, 0, 1, 0.3, 0, 0, 0, 1 ]\n",
        encoding="utf-8",
    )
    matrix = MODULE.load_transform(config, "body_T_cam0")
    np.testing.assert_allclose(matrix[:3, 3], [0.1, 0.2, 0.3])


def test_relative_metrics_applies_robot_query_offset_to_endpoints() -> None:
    # A nonlinear trajectory makes a missing endpoint offset observable.  The
    # robot stream is the same motion sampled in a clock shifted by 250 ms;
    # with the offset applied, the 1 s displacement vectors match exactly.
    offset = 0.25
    umi_t = np.arange(0.0, 4.0, 0.01)
    robot_t = np.arange(offset, 4.25, 0.01)
    umi_p = np.column_stack(((umi_t + 1.0) ** 2, np.zeros_like(umi_t), np.zeros_like(umi_t)))
    robot_p = np.column_stack(((robot_t - offset + 1.0) ** 2, np.zeros_like(robot_t), np.zeros_like(robot_t)))
    identity = MODULE.Rotation.identity()
    start = umi_t[(umi_t >= 0.0) & (umi_t <= 3.0)]
    start_u = np.column_stack(((start + 1.0) ** 2, np.zeros_like(start), np.zeros_like(start)))
    start_r = np.column_stack(((start + 1.0) ** 2, np.zeros_like(start), np.zeros_like(start)))
    metrics = MODULE._relative_metrics(
        start,
        start_u,
        MODULE.Rotation.identity(start.size),
        start_r,
        MODULE.Rotation.identity(start.size),
        umi_t,
        umi_p,
        MODULE.Rotation.identity(len(umi_t)),
        robot_t,
        robot_p,
        MODULE.Rotation.identity(len(robot_t)),
        0.02,
        np.eye(3),
        offset,
    )
    assert metrics["1.0s"]["samples"] > 0
    assert metrics["1.0s"]["aligned_vector_error_mm"]["rmse"] < 1.0e-6


def test_report_uses_common_overlap_for_path_length(tmp_path: Path) -> None:
    slam_dir = tmp_path / "slam"
    slam_dir.mkdir()
    umi = slam_dir / "vio_corrected_stream.csv"
    _write_umi(umi, [100.0 + index * 0.1 for index in range(31)])
    robot = tmp_path / "robot.jsonl"
    _write_robot(robot)
    out = tmp_path / "out"

    report = MODULE.process(
        slam_dir=slam_dir,
        robot_path=robot,
        output_dir=out,
        max_pair_error_ms=20.0,
        max_bracket_gap_ms=101.0,
    )
    assert report["umi_source_kind"] == "full_corrected_stream"
    assert report["relative_motion"]["path_length_interval_definition"].startswith("common paired")
    assert report["sample_counts"]["paired_after_gate"] >= 2
    assert report["robot_fk_coordinate_frame"] == "base_link"
    assert report["artifacts"]["trajectory_plot"]["views"] == 1
    assert report["artifacts"]["trajectory_plot_interactive"]["views"] == 1
    assert "leader_vs_target_joint_error_deg" in report["follower_tracking"]
    assert "target_vs_actual_tcp_error_mm" in report["follower_tracking"]
    assert (out / "matched_relative.csv").is_file()
    assert (out / "robot_vs_umi_two_3d.html").is_file()

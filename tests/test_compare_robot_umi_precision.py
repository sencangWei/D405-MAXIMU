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
    assert "leader_vs_target_joint_error_deg" in report["follower_tracking"]
    assert "target_vs_actual_tcp_error_mm" in report["follower_tracking"]
    assert (out / "matched_relative.csv").is_file()

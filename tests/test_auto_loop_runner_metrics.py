import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from test_vins_auto_loop import (
    accel_calibration_replay_arguments,
    camera_frame_count,
    classify_run_scope,
    drain_is_complete,
    expected_pose_samples,
    load_accel_calibration,
    run_provenance,
    parse_loop_configuration,
    parse_loop_retrieval,
    parse_loop_stage_counts,
    parse_pnp_quality,
    parse_pose_graph_health,
    trajectory_diagnostics,
)


def test_accelerometer_calibration_is_validated_and_forwarded(tmp_path):
    calibration = tmp_path / "imu_accel.yaml"
    calibration.write_text(
        """
accelerometer:
  matrix:
    - [1.0, 0.01, 0.02]
    - [0.03, 1.0, 0.04]
    - [0.05, 0.06, 1.0]
  offset_g: [0.001, -0.002, 0.003]
""".strip(),
        encoding="utf-8",
    )

    loaded = load_accel_calibration(calibration)

    assert loaded["matrix"] == [
        1.0,
        0.01,
        0.02,
        0.03,
        1.0,
        0.04,
        0.05,
        0.06,
        1.0,
    ]
    assert loaded["offset_g"] == [0.001, -0.002, 0.003]
    assert accel_calibration_replay_arguments(loaded) == [
        "--imu-accel-matrix",
        "1.0",
        "0.01",
        "0.02",
        "0.03",
        "1.0",
        "0.04",
        "0.05",
        "0.06",
        "1.0",
        "--imu-accel-offset-g",
        "0.001",
        "-0.002",
        "0.003",
    ]


def test_drain_requires_target_pose_coverage_before_quiet_exit():
    expected = expected_pose_samples(camera_frames=1799, skip_s=1.5)

    assert expected == 1754
    assert not drain_is_complete(
        raw_poses=1020,
        corrected_poses=1020,
        expected_poses=expected,
        min_pose_coverage=0.98,
        quiet_duration_s=10.0,
        require_coverage=True,
    )
    assert not drain_is_complete(
        raw_poses=1718,
        corrected_poses=1754,
        expected_poses=expected,
        min_pose_coverage=0.98,
        quiet_duration_s=10.0,
        require_coverage=True,
    )
    assert drain_is_complete(
        raw_poses=1719,
        corrected_poses=1754,
        expected_poses=expected,
        min_pose_coverage=0.98,
        quiet_duration_s=6.0,
        require_coverage=True,
    )


def test_drain_keeps_legacy_quiet_exit_when_frame_count_is_unavailable():
    assert drain_is_complete(
        raw_poses=10,
        corrected_poses=10,
        expected_poses=1,
        min_pose_coverage=0.98,
        quiet_duration_s=6.0,
        require_coverage=False,
    )
    assert not drain_is_complete(
        raw_poses=10,
        corrected_poses=10,
        expected_poses=1,
        min_pose_coverage=0.98,
        quiet_duration_s=5.99,
        require_coverage=False,
    )


def test_trajectory_diagnostics_reports_step_and_vertical_span():
    rows = [
        [0.0, 0.0, 0.0, 0.0],
        [1.0, 0.01, 0.0, 0.02],
        [2.0, 0.01, 0.03, -0.01],
    ]

    metrics = trajectory_diagnostics(rows)

    assert abs(metrics["max_step_m"] - (0.03**2 + 0.03**2) ** 0.5) < 1e-12
    assert abs(metrics["z_span_m"] - 0.03) < 1e-12
    assert abs(metrics["endpoint_delta_m"] - (0.01**2 + 0.03**2 + 0.01**2) ** 0.5) < 1e-12


def test_trajectory_diagnostics_handles_incomplete_trajectory():
    assert trajectory_diagnostics([]) == {
        "max_step_m": None,
        "z_span_m": None,
        "endpoint_delta_m": None,
    }


def test_camera_frame_count_prefers_actual_image_cache(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    with (session / "d405_frames.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frame"])
        writer.writerows([[index] for index in range(1000)])

    cache = tmp_path / "stereo_prefix.db3"
    database = sqlite3.connect(cache)
    database.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT NOT NULL)"
    )
    database.execute(
        "CREATE TABLE messages(id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL)"
    )
    database.execute(
        "INSERT INTO topics VALUES(1, '/device_0/sensor_0/Infrared_1/image/data')"
    )
    database.executemany(
        "INSERT INTO messages VALUES(?, 1)",
        [(index,) for index in range(1, 451)],
    )
    database.commit()
    database.close()

    assert camera_frame_count(session, cache) == (450, str(cache.resolve()))


def test_run_scope_distinguishes_infrastructure_from_slam_failure():
    assert classify_run_scope("replay exceeded 420s", 0, 0) == "INFRASTRUCTURE"
    assert classify_run_scope("DDS discovery failed", 10, 0) == "INFRASTRUCTURE"
    assert classify_run_scope("replay exceeded 420s", 1000, 353) == "SLAM_RUNTIME"
    assert classify_run_scope(None, 1000, 998) == "SLAM"


def test_parse_pnp_quality_exposes_accepted_edge_spatial_support():
    log = "\n".join(
        (
            "[AUTO_LOOP_PNP_QUALITY] current=10 matched=1 inliers=20 "
            "rmse_px=2.100 p95_px=3.700 current_hull=0.1200 old_hull=0.1100",
            "[AUTO_LOOP_GEOMETRY_PASS] current=10 matched=1 inliers=20 ratio=0.5",
            "[AUTO_LOOP_PNP_QUALITY] current=11 matched=1 inliers=22 "
            "rmse_px=2.300 p95_px=3.900 current_hull=0.1400 old_hull=0.1300",
            "[AUTO_LOOP_GEOMETRY_PASS] current=11 matched=1 inliers=22 ratio=0.5",
            "[AUTO_LOOP_ACCEPT] current=11 matched=1 confirmations=4",
        )
    )

    report = parse_pnp_quality(log)

    assert report["samples"] == 2
    assert report["finite_samples"] == 2
    assert report["geometry_pass_samples"] == 2
    assert report["accepted_edges"] == [
        {
            "current": 11,
            "matched": 1,
            "inliers": 22,
            "rmse_px": 2.3,
            "p95_px": 3.9,
            "current_hull_fraction": 0.14,
            "old_hull_fraction": 0.13,
        }
    ]


def test_parse_pose_graph_health_rejects_unusable_solution():
    log = "\n".join(
        (
            "[POSE_GRAPH_OPTIMIZATION] current=100 usable=1 initial_cost=1.0 "
            "final_cost=0.2 iterations=5 time_s=0.01",
            "[POSE_GRAPH_OPTIMIZATION] current=120 usable=0 initial_cost=nan "
            "final_cost=nan iterations=0 time_s=0.00",
            "[POSE_GRAPH_OPTIMIZATION_REJECT] current=120 reason=FAILURE",
        )
    )

    report = parse_pose_graph_health(log)

    assert report == {
        "optimizations": 2,
        "usable_optimizations": 1,
        "rejected_optimizations": 1,
    }


def test_parse_loop_configuration_reports_effective_spatial_threshold():
    assert parse_loop_configuration(
        "min_loop_spatial_support: 0.0617 (enabled)\n"
        "max_loop_candidates: 24\n"
    ) == {"min_loop_spatial_support": 0.0617, "max_loop_candidates": 24}
    assert parse_loop_configuration("unrelated log\n") == {
        "min_loop_spatial_support": None,
        "max_loop_candidates": None,
    }


def test_parse_loop_retrieval_summarizes_candidate_recall():
    log = "\n".join(
        (
            "[AUTO_LOOP_RETRIEVAL] current=100 returned=3 eligible=2 "
            "rank1=4:0.12000 rank2=8:0.04000 rank3=2:0.01000",
            "[AUTO_LOOP_RETRIEVAL] current=101 returned=2 eligible=0 "
            "rank1=9:0.01000 rank2=3:0.00500",
        )
    )

    assert parse_loop_retrieval(log) == {
        "frames": 2,
        "returned": {"min": 2, "max": 3, "mean": 2.5},
        "eligible": {"min": 0, "max": 2, "mean": 1.0},
        "zero_eligible_frames": 1,
        "top_score": {"min": 0.01, "max": 0.12, "mean": 0.065},
    }


def test_parse_loop_retrieval_handles_old_logs_without_diagnostics():
    assert parse_loop_retrieval("unrelated log\n") == {
        "frames": 0,
        "returned": {"min": None, "max": None, "mean": None},
        "eligible": {"min": None, "max": None, "mean": None},
        "zero_eligible_frames": 0,
        "top_score": {"min": None, "max": None, "mean": None},
    }


def test_run_report_stage_counts_can_be_read_from_log_markers():
    log = "\n".join(
        (
            "[AUTO_LOOP_PENDING] current=10 matched=1 confirmations=1/4",
            "[AUTO_LOOP_PENDING] current=11 matched=1 confirmations=2/4",
            "[AUTO_LOOP_CORRECTION_REJECT] current=12 matched=1",
        )
    )

    assert parse_loop_stage_counts(log) == {
        "pending": 2,
        "inconsistent": 0,
        "correction_rejected": 1,
        "cooldown": 0,
    }


def test_run_provenance_hashes_effective_config_and_calibration(
    tmp_path, monkeypatch
):
    session = tmp_path / "session"
    session.mkdir()
    acceptance = session / "acceptance.json"
    acceptance.write_text('{"result":"PASS"}', encoding="utf-8")
    camera_timestamps = session / "d405_frames.csv"
    camera_timestamps.write_text("camera", encoding="utf-8")
    imu_samples = session / "external_imu" / "imu.bin"
    imu_samples.parent.mkdir()
    imu_samples.write_bytes(b"imu")
    run_config = tmp_path / "config.yaml"
    left = tmp_path / "left.yaml"
    right = tmp_path / "right.yaml"
    run_config.write_text("config", encoding="utf-8")
    left.write_text("left", encoding="utf-8")
    right.write_text("right", encoding="utf-8")
    executable = tmp_path / "node"
    executable.write_bytes(b"node")
    loop_executable = tmp_path / "loop_node"
    loop_executable.write_bytes(b"loop")
    monkeypatch.setattr("test_vins_auto_loop.VINS_EXECUTABLE", executable)
    monkeypatch.setattr("test_vins_auto_loop.LOOP_EXECUTABLE", executable)
    monkeypatch.setattr("test_vins_auto_loop.REPLAY_EXECUTABLE", executable)

    provenance = run_provenance(
        session,
        run_config,
        left,
        right,
        "cpp",
        loop_executable=loop_executable,
    )

    assert provenance["files"]["capture_acceptance"]["sha256"] == (
        "98d844ea900c08231a1f6e1e12a4aeacf82570dc71285911ff5e66c9f1bb1915"
    )
    assert provenance["files"]["run_config"]["path"] == str(run_config)
    assert provenance["files"]["camera_timestamps"]["path"] == str(
        camera_timestamps
    )
    assert provenance["files"]["imu_samples"]["path"] == str(imu_samples)
    assert provenance["files"]["replay_executable"]["sha256"] == (
        "545ea538461003efdc8c81c244531b003f6f26cfccf6c0073b3239fdedf49446"
    )
    assert provenance["files"]["loop_executable"]["path"] == str(
        loop_executable.resolve()
    )
    assert provenance["source_db3_hashed"] is False

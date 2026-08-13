import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from test_vins_auto_loop import (
    camera_frame_count,
    classify_run_scope,
    parse_loop_configuration,
    parse_pnp_quality,
    parse_pose_graph_health,
    trajectory_diagnostics,
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
    ) == {"min_loop_spatial_support": 0.0617}
    assert parse_loop_configuration("unrelated log\n") == {
        "min_loop_spatial_support": None
    }

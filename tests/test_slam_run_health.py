import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from slam_benchmark_environment import evaluate_environment
import pytest

from slam_run_health import evaluate_slam_health, trajectory_diagnostics_from_csv


def passing_environment() -> dict:
    return evaluate_environment(
        {
            "load_average": {"one_minute_per_cpu": 0.1},
            "memory_available_gib": 16.0,
            "pressure": {
                "cpu": {"some": {"avg10": 0.1}},
                "memory": {"full": {"avg10": 0.0}},
                "io": {"full": {"avg10": 0.1}},
            },
            "conflicting_processes": [],
        }
    )


def healthy_report() -> dict:
    return {
        "result": "PASS",
        "failure_scope": "SLAM",
        "runtime_error": None,
        "runtime_watchdog": {
            "state": "SLAM_HEALTHY",
            "product_usable": True,
        },
        "failures": [],
        "benchmark_environment": passing_environment(),
        "raw_odometry_samples": 100,
        "corrected_odometry_samples": 99,
        "expected_pose_samples_after_skip": 100,
        "pose_coverage": 0.99,
        "loop_input_drop_events": 0,
        "estimator_keyframe_queue_drop_events": 0,
        "pose_graph_health": {"rejected_optimizations": 0},
        "raw_trajectory_diagnostics": {"max_step_m": 0.01, "z_span_m": 0.2},
        "corrected_trajectory_diagnostics": {
            "max_step_m": 0.02,
            "z_span_m": 0.2,
        },
        "z_span_retention_ratio": 1.0,
    }


def test_healthy_run_is_product_usable():
    health = evaluate_slam_health(healthy_report())

    assert health["state"] == "SLAM_HEALTHY"
    assert health["product_usable"] is True
    assert health["failures"] == []


def test_infrastructure_failure_is_not_mislabeled_as_slam_failure():
    report = healthy_report()
    report["failure_scope"] = "INFRASTRUCTURE"

    health = evaluate_slam_health(report)

    assert health["state"] == "INFRASTRUCTURE_BLOCKED"
    assert health["product_usable"] is False


def test_drops_jump_and_elevation_loss_fail_health():
    report = healthy_report()
    report["loop_input_drop_events"] = 1
    report["corrected_trajectory_diagnostics"]["max_step_m"] = 0.05
    report["z_span_retention_ratio"] = 0.5

    health = evaluate_slam_health(report)

    assert health["state"] == "SLAM_FAILED"
    assert health["failures"] == [
        "loop_input_drop_events",
        "corrected_trajectory_jump",
        "true_elevation_retention",
    ]


def test_planar_run_skips_elevation_retention():
    report = healthy_report()
    report["raw_trajectory_diagnostics"]["z_span_m"] = 0.02
    report["z_span_retention_ratio"] = None

    health = evaluate_slam_health(report)

    check = next(
        item for item in health["checks"] if item["name"] == "true_elevation_retention"
    )
    assert check["result"] == "SKIPPED"
    assert health["state"] == "SLAM_HEALTHY"


def test_forged_pose_coverage_and_nonempty_failures_are_rejected():
    report = healthy_report()
    report["pose_coverage"] = 1.0
    report["failures"] = ["runtime warning"]

    health = evaluate_slam_health(report)

    assert health["state"] == "SLAM_FAILED"
    assert "declared_failures_empty" in health["failures"]
    assert "pose_coverage_consistency" in health["failures"]


def test_runtime_watchdog_failure_rejects_otherwise_passing_run():
    report = healthy_report()
    report["runtime_watchdog"] = {
        "state": "SLAM_FAILED",
        "product_usable": False,
    }

    health = evaluate_slam_health(report)

    assert health["state"] == "SLAM_FAILED"
    assert "runtime_watchdog" in health["failures"]


def test_trajectory_diagnostics_reject_timestamp_regression(tmp_path):
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text(
        "t_sec,x,y,z\n1.0,0,0,0\n0.5,1,0,0\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="non-increasing trajectory timestamp"):
        trajectory_diagnostics_from_csv(trajectory)

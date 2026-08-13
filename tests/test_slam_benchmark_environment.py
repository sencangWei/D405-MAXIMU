import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from slam_benchmark_environment import (
    CONFLICTING_PROCESS_MARKERS,
    RESOURCE_GATED_PROCESS_MARKERS,
    evaluate_environment,
    process_matches,
    validate_environment_report,
)


def snapshot(
    *,
    load_per_cpu: float = 0.2,
    cpu_pressure: float = 1.0,
    io_pressure: float = 1.0,
    memory_pressure: float = 0.0,
    memory_gib: float = 16.0,
    conflicts: list | None = None,
) -> dict:
    return {
        "load_average": {"one_minute_per_cpu": load_per_cpu},
        "memory_available_gib": memory_gib,
        "pressure": {
            "cpu": {"some": {"avg10": cpu_pressure}},
            "memory": {"full": {"avg10": memory_pressure}},
            "io": {"full": {"avg10": io_pressure}},
        },
        "conflicting_processes": conflicts or [],
    }


def test_environment_preflight_passes_qualified_idle_host():
    report = evaluate_environment(snapshot())

    assert report["result"] == "PASS"
    assert len(report["checks"]) == 6
    assert report["failures"] == []
    assert validate_environment_report(report) == []


def test_isolated_hik_camera_is_resource_gated_not_an_absolute_conflict():
    assert "hik_camera_node" not in CONFLICTING_PROCESS_MARKERS
    assert "hik_camera_node" in RESOURCE_GATED_PROCESS_MARKERS
    assert "rebot_rs_trajectory_replay.py" not in CONFLICTING_PROCESS_MARKERS
    assert "rebot_rs_trajectory_replay.py" in RESOURCE_GATED_PROCESS_MARKERS
    assert "scripts/train.py" in CONFLICTING_PROCESS_MARKERS
    assert "run_pi05_rebot_e2_after_training.py" in CONFLICTING_PROCESS_MARKERS


def test_process_matching_uses_real_argv_tokens_not_shell_command_text():
    assert process_matches(
        ["python3", "scripts/train.py", "pi05_rebot"],
        CONFLICTING_PROCESS_MARKERS,
    )
    assert process_matches(
        ["uv", "run", "scripts/train.py", "pi05_rebot"],
        CONFLICTING_PROCESS_MARKERS,
    )
    assert process_matches(
        [
            "rtk",
            "proxy",
            "/usr/bin/python3",
            "tools/run_pi05_rebot_e2_after_training.py",
        ],
        CONFLICTING_PROCESS_MARKERS,
    )
    assert process_matches(
        [
            "/home/robot/ego_vio_humble/install/lib/vins_fusion_ros2/loop_fusion_node",
            "/tmp/config.yaml",
        ],
        CONFLICTING_PROCESS_MARKERS,
    )
    assert not process_matches(
        [
            "find",
            "/home/robot",
            "-path",
            "*/tools/run_pi05_rebot_e2_after_training.py",
        ],
        CONFLICTING_PROCESS_MARKERS,
    )
    assert not process_matches(
        [
            "/usr/bin/zsh",
            "-lc",
            "pgrep -af 'scripts/train.py|loop_fusion_node' || true",
        ],
        CONFLICTING_PROCESS_MARKERS,
    )


def test_environment_preflight_rejects_io_pressure_and_conflicting_slam():
    report = evaluate_environment(
        snapshot(
            io_pressure=35.0,
            conflicts=[{"pid": 42, "command": "loop_fusion_node"}],
        )
    )

    assert report["result"] == "FAIL"
    assert any("io_pressure_full_avg10_percent" in item for item in report["failures"])
    assert any("conflicting_processes" in item for item in report["failures"])
    assert "benchmark environment preflight is not PASS" in validate_environment_report(
        report
    )


def test_environment_report_rejects_weakened_thresholds():
    report = evaluate_environment(snapshot())
    report["thresholds"]["max_io_pressure_full_avg10_percent"] = 100.0

    assert "benchmark threshold max_io_pressure_full_avg10_percent was weakened" in (
        validate_environment_report(report)
    )


def test_environment_report_rejects_forged_pass_value():
    report = evaluate_environment(snapshot())
    report["checks"][2]["value"] = 0.0

    assert "benchmark check io_pressure_full_avg10_percent does not match snapshot" in (
        validate_environment_report(report)
    )

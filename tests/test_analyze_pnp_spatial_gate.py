import json
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_pnp_spatial_gate import analyze_manifest, confirmation_windows
from slam_benchmark_environment import evaluate_environment


def environment() -> dict:
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


def write_dataset(
    root: Path,
    dataset_id: str,
    role: str,
    expected_loop: bool,
    support: float,
) -> dict:
    log = root / f"{dataset_id}.log"
    lines = []
    matched = 1 if expected_loop else 7
    for current in range(10, 14):
        lines.extend(
            (
                f"[AUTO_LOOP_PNP_QUALITY] current={current} matched={matched} "
                f"inliers=20 rmse_px=2.0 p95_px=3.5 "
                f"current_hull={support:.4f} old_hull={support:.4f}",
                f"[AUTO_LOOP_GEOMETRY_PASS] current={current} matched={matched}",
            )
        )
    if expected_loop:
        lines.append(f"[AUTO_LOOP_ACCEPT] current=13 matched={matched}")
    log.write_text("\n".join(lines), encoding="utf-8")
    report = root / f"{dataset_id}.json"
    report.write_text(
        json.dumps(
            {
                "failure_scope": "SLAM",
                "result": "PASS",
                "session": dataset_id,
                "runtime_error": None,
                "pose_coverage": 1.0,
                "loop_input_drop_events": 0,
                "estimator_keyframe_queue_drop_events": 0,
                "automatic_loop_accepts": int(expected_loop),
                "benchmark_environment": environment(),
            }
        ),
        encoding="utf-8",
    )
    return {
        "id": dataset_id,
        "session": dataset_id,
        "role": role,
        "expected_loop": expected_loop,
        "loop_log": log.name,
        "loop_log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "run_report": report.name,
        "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
    }


def test_confirmation_window_requires_consecutive_same_match():
    text = "\n".join(
        f"[AUTO_LOOP_PNP_QUALITY] current={current} matched=1 inliers=20 "
        "rmse_px=2.0 p95_px=3.0 current_hull=0.1 old_hull=0.1\n"
        f"[AUTO_LOOP_GEOMETRY_PASS] current={current} matched=1"
        for current in (10, 11, 13, 14)
    )

    assert confirmation_windows(text, 4) == []


def test_spatial_gate_freezes_only_with_independent_qualified_separation(tmp_path):
    manifest = {
        "schema_version": 1,
        "confirmation_frames": 4,
        "datasets": [
            write_dataset(tmp_path, "positive-dev", "development", True, 0.10),
            write_dataset(tmp_path, "positive-val", "validation", True, 0.09),
            write_dataset(tmp_path, "negative-dev", "development", False, 0.04),
            write_dataset(tmp_path, "negative-val", "validation", False, 0.05),
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = analyze_manifest(path)

    assert report["result"] == "PASS"
    assert report["threshold_freeze_allowed"] is True
    assert report["qualified_interval"] == {
        "lower_exclusive": 0.05,
        "upper_inclusive": 0.09,
        "separable": True,
        "midpoint_candidate": 0.07,
    }


def test_spatial_gate_reports_insufficient_independent_runs(tmp_path):
    manifest = {
        "schema_version": 1,
        "datasets": [
            write_dataset(tmp_path, "positive", "development", True, 0.10),
            write_dataset(tmp_path, "negative", "validation", False, 0.04),
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = analyze_manifest(path)

    assert report["result"] == "INSUFFICIENT_EVIDENCE"
    assert report["threshold_freeze_allowed"] is False
    assert report["qualified_interval"]["separable"] is True
    assert len(report["failures"]) == 3


def test_spatial_gate_forbids_hidden_test_tuning(tmp_path):
    manifest = {
        "schema_version": 1,
        "datasets": [
            write_dataset(tmp_path, "hidden", "hidden_test", True, 0.10),
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="hidden-test data cannot tune"):
        analyze_manifest(path)


def test_repeated_replay_of_same_session_counts_once(tmp_path):
    first = write_dataset(tmp_path, "positive-a", "development", True, 0.10)
    second = write_dataset(tmp_path, "positive-b", "validation", True, 0.09)
    second["session"] = first["session"]
    second_report = json.loads((tmp_path / "positive-b.json").read_text())
    second_report["session"] = first["session"]
    (tmp_path / "positive-b.json").write_text(json.dumps(second_report))
    second["run_report_sha256"] = hashlib.sha256(
        (tmp_path / "positive-b.json").read_bytes()
    ).hexdigest()
    manifest = {
        "schema_version": 1,
        "datasets": [
            first,
            second,
            write_dataset(tmp_path, "negative-a", "development", False, 0.04),
            write_dataset(tmp_path, "negative-b", "validation", False, 0.05),
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    report = analyze_manifest(path)

    assert report["qualified_counts"]["positive_runs"] == 1
    assert report["threshold_freeze_allowed"] is False


def test_spatial_gate_rejects_changed_log(tmp_path):
    dataset = write_dataset(tmp_path, "positive", "development", True, 0.10)
    (tmp_path / "positive.log").write_text("changed", encoding="utf-8")
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": 1, "datasets": [dataset]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="loop log hash mismatch"):
        analyze_manifest(path)


def test_spatial_gate_does_not_qualify_accept_count_mismatch(tmp_path):
    dataset = write_dataset(tmp_path, "negative", "validation", False, 0.04)
    report_path = tmp_path / "negative.json"
    report = json.loads(report_path.read_text())
    report["automatic_loop_accepts"] = 1
    report_path.write_text(json.dumps(report))
    dataset["run_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "datasets": [dataset]}))

    result = analyze_manifest(path)

    assert result["datasets"][0]["accepts_match_expectation"] is False
    assert result["datasets"][0]["qualified_for_freeze"] is False


def test_spatial_gate_requirements_cannot_be_weakened(tmp_path):
    manifest = {
        "schema_version": 1,
        "requirements": {
            "min_positive_runs": 0,
            "min_negative_runs": 0,
            "min_validation_positive_runs": 0,
            "min_validation_negative_runs": 0,
        },
        "datasets": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))

    result = analyze_manifest(path)

    assert result["requirements"] == {
        "min_positive_runs": 2,
        "min_negative_runs": 2,
        "min_validation_positive_runs": 1,
        "min_validation_negative_runs": 1,
    }
    assert result["threshold_freeze_allowed"] is False


def test_spatial_gate_requires_robust_support_margin(tmp_path):
    manifest = {
        "schema_version": 1,
        "datasets": [
            write_dataset(tmp_path, "positive-dev", "development", True, 0.060),
            write_dataset(tmp_path, "positive-val", "validation", True, 0.061),
            write_dataset(tmp_path, "negative-dev", "development", False, 0.049),
            write_dataset(tmp_path, "negative-val", "validation", False, 0.050),
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))

    result = analyze_manifest(path)

    assert result["qualified_interval"]["separable"] is True
    assert result["threshold_freeze_allowed"] is False
    assert result["failures"] == [
        "qualified spatial-support margin is below 0.0200"
    ]


def test_spatial_gate_report_keeps_manifest_relative_paths(tmp_path):
    dataset = write_dataset(tmp_path, "positive", "development", True, 0.10)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "datasets": [dataset]}))

    result = analyze_manifest(path)

    assert result["datasets"][0]["loop_log"] == "positive.log"
    assert result["datasets"][0]["run_report"] == "positive.json"

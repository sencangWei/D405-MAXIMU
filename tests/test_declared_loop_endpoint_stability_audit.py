import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from audit_declared_loop_endpoint_stability import build_audit, write_markdown


def write_trajectory(path: Path, endpoint_m: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        for index in range(301):
            timestamp = index / 30
            x = 0.0 if timestamp <= 3 else endpoint_m if timestamp >= 7 else 0.2
            writer.writerow([timestamp, x, 0.0, 0.0])


def test_audit_uses_predeclared_expected_loop_as_denominator(tmp_path: Path):
    accepted = tmp_path / "accepted"
    negative = tmp_path / "negative"
    accepted.mkdir()
    negative.mkdir()
    write_trajectory(accepted / "vio_corrected_stream.csv", 0.006)
    write_trajectory(negative / "vio_corrected_stream.csv", 0.5)
    (accepted / "run_acceptance.json").write_text(
        json.dumps({"result": "PASS", "automatic_loop_accepts": 1}),
        encoding="utf-8",
    )
    (negative / "run_acceptance.json").write_text(
        json.dumps({"result": "PASS", "automatic_loop_accepts": 0}),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "datasets": [
                    {
                        "id": "positive",
                        "runs": [
                            {
                                "repetition": 1,
                                "report": str(accepted / "run_acceptance.json"),
                            }
                        ],
                    },
                    {
                        "id": "negative",
                        "runs": [
                            {
                                "repetition": 1,
                                "report": str(negative / "run_acceptance.json"),
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "datasets": [{"id": "positive", "expected_loop": True}],
                "safety_controls": [
                    {"id": "negative", "expected_loop": False}
                ],
            }
        ),
        encoding="utf-8",
    )

    audit = build_audit(summary, manifest)

    assert audit["expected_loop_run_count"] == 1
    assert audit["stable_sub_centimeter_run_count"] == 1
    assert audit["all_expected_loops_stable_sub_centimeter"] is True
    assert audit["datasets"][1]["runs"][0]["automatic_loop_accepted"] is False

    markdown = tmp_path / "audit.md"
    write_markdown(markdown, audit)
    text = markdown.read_text(encoding="utf-8")
    assert "自动回环端点稳定性审计" in text
    assert "安全样本PASS" in text
    assert "+6.00/+0.00/+0.00" in text


def test_missing_automatic_loop_cannot_disappear_from_positive_denominator(
    tmp_path: Path,
):
    run = tmp_path / "run"
    run.mkdir()
    write_trajectory(run / "vio_corrected_stream.csv", 0.005)
    (run / "run_acceptance.json").write_text(
        json.dumps({"result": "PASS", "automatic_loop_accepts": 0}),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "datasets": [
                    {
                        "id": "positive",
                        "runs": [
                            {
                                "repetition": 1,
                                "report": str(run / "run_acceptance.json"),
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"datasets": [{"id": "positive", "expected_loop": True}]}
        ),
        encoding="utf-8",
    )

    audit = build_audit(summary, manifest)

    assert audit["expected_loop_run_count"] == 1
    assert audit["stable_sub_centimeter_run_count"] == 0
    assert audit["all_expected_loops_stable_sub_centimeter"] is False

    markdown = tmp_path / "missing_loop.md"
    write_markdown(markdown, audit)
    assert "| 否 | 5.00" in markdown.read_text(encoding="utf-8")
    assert "| FAIL |" in markdown.read_text(encoding="utf-8")


def test_missing_report_stays_in_expected_positive_denominator(tmp_path: Path):
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "datasets": [
                    {"id": "positive", "runs": [{"repetition": 1}]}
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"datasets": [{"id": "positive", "expected_loop": True}]}
        ),
        encoding="utf-8",
    )

    audit = build_audit(summary, manifest)

    assert audit["expected_loop_run_count"] == 1
    assert audit["stable_sub_centimeter_run_count"] == 0
    assert audit["all_expected_loops_stable_sub_centimeter"] is False


def test_missing_trajectory_is_recorded_instead_of_aborting(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    report = run / "run_acceptance.json"
    report.write_text(
        json.dumps({"result": "FAIL", "automatic_loop_accepts": 0}),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "datasets": [
                    {
                        "id": "positive",
                        "runs": [{"repetition": 1, "report": str(report)}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"datasets": [{"id": "positive", "expected_loop": True}]}
        ),
        encoding="utf-8",
    )

    audit = build_audit(summary, manifest)

    assert audit["expected_loop_run_count"] == 1
    assert audit["stable_sub_centimeter_run_count"] == 0
    assert audit["datasets"][0]["runs"][0]["result"] == "TRAJECTORY_UNAVAILABLE"


def test_empty_infrastructure_summary_cannot_vacuously_pass(tmp_path: Path):
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "result": "INFRASTRUCTURE_BLOCKED",
                "datasets": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "thresholds": {"required_repetitions_per_dataset": 3},
                "datasets": [
                    {"id": "positive_a", "expected_loop": True},
                    {"id": "positive_b", "expected_loop": True},
                ],
                "safety_controls": [
                    {"id": "negative", "expected_loop": False}
                ],
            }
        ),
        encoding="utf-8",
    )

    audit = build_audit(summary, manifest)

    assert audit["expected_loop_run_count"] == 6
    assert audit["stable_sub_centimeter_run_count"] == 0
    assert audit["all_expected_loops_stable_sub_centimeter"] is False
    assert len(audit["datasets"]) == 3
    assert all(
        run["result"] == "MISSING_REGRESSION_RUN"
        for dataset in audit["datasets"]
        for run in dataset["runs"]
    )


def test_missing_declared_dataset_stays_in_denominator(tmp_path: Path):
    completed = tmp_path / "completed"
    completed.mkdir()
    write_trajectory(completed / "vio_corrected_stream.csv", 0.004)
    report = completed / "run_acceptance.json"
    report.write_text(
        json.dumps({"result": "PASS", "automatic_loop_accepts": 1}),
        encoding="utf-8",
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "REPEATED_REGRESSION",
                "required_repetitions_per_dataset": 1,
                "datasets": [
                    {
                        "id": "positive_a",
                        "runs": [{"repetition": 1, "report": str(report)}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "datasets": [
                    {"id": "positive_a", "expected_loop": True},
                    {"id": "positive_b", "expected_loop": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    audit = build_audit(summary, manifest)

    assert audit["expected_loop_run_count"] == 2
    assert audit["stable_sub_centimeter_run_count"] == 1
    assert audit["all_expected_loops_stable_sub_centimeter"] is False
    assert audit["datasets"][1]["runs"][0]["result"] == "MISSING_REGRESSION_RUN"

import hashlib
import json
from pathlib import Path

from run_declared_loop_regression import (
    score_run,
    validate_manifest,
    write_markdown_report,
)


def healthy_report(endpoint: float = 0.005, accepts: int = 1) -> dict:
    return {
        "result": "PASS",
        "automatic_loop_accepts": accepts,
        "pose_coverage": 0.99,
        "corrected_trajectory_diagnostics": {"endpoint_delta_m": endpoint},
        "loop_input_drop_events": 0,
        "estimator_keyframe_queue_drop_events": 0,
        "pose_graph_health": {"rejected_optimizations": 0},
        "max_loop_candidates": 24,
        "loop_retrieval": {
            "frames": 100,
            "returned": {"min": 24, "max": 24, "mean": 24.0},
            "eligible": {"min": 0, "max": 8, "mean": 1.5},
            "zero_eligible_frames": 20,
            "top_score": {"min": 0.01, "max": 0.2, "mean": 0.08},
        },
        "health": {"state": "SLAM_HEALTHY"},
    }


def test_scores_autonomous_sub_centimeter_loop_as_pass():
    result = score_run(
        healthy_report(),
        expected_loop=True,
        max_endpoint_m=0.01,
        min_coverage=0.98,
    )
    assert result["result"] == "PASS"


def test_rejects_missing_loop_or_centimeter_violation():
    no_loop = score_run(
        healthy_report(accepts=0),
        expected_loop=True,
        max_endpoint_m=0.01,
        min_coverage=0.98,
    )
    inaccurate = score_run(
        healthy_report(endpoint=0.010001),
        expected_loop=True,
        max_endpoint_m=0.01,
        min_coverage=0.98,
    )
    assert no_loop["result"] == "FAIL"
    assert "no automatic loop was accepted" in no_loop["failures"]
    assert inaccurate["result"] == "FAIL"
    assert any("exceeds" in failure for failure in inaccurate["failures"])


def test_negative_control_rejects_false_loop_and_does_not_score_endpoint():
    clean = score_run(
        healthy_report(endpoint=0.5, accepts=0),
        expected_loop=False,
        max_endpoint_m=0.01,
        min_coverage=0.98,
    )
    false_loop = score_run(
        healthy_report(accepts=1),
        expected_loop=False,
        max_endpoint_m=0.01,
        min_coverage=0.98,
    )
    assert clean["result"] == "PASS"
    assert false_loop["result"] == "FAIL"
    assert "false automatic loops accepted: 1" in false_loop["failures"]


def test_new_candidate_contract_requires_retrieval_observability():
    candidate = healthy_report()
    candidate["loop_retrieval"] = {"frames": 0}

    result = score_run(
        candidate,
        expected_loop=True,
        max_endpoint_m=0.01,
        min_coverage=0.98,
        expected_max_candidates=24,
    )

    assert result["result"] == "FAIL"
    assert "loop retrieval diagnostics are missing" in result["failures"]


def test_manifest_requires_immutable_passing_capture(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    acceptance = session / "acceptance.json"
    acceptance.write_text(json.dumps({"result": "PASS"}), encoding="utf-8")
    digest = hashlib.sha256(acceptance.read_bytes()).hexdigest()
    manifest = {
        "truth_policy": {
            "usage": "post_run_scoring_only",
            "never_input_to_slam": True,
        },
        "datasets": [
            {
                "id": "closed",
                "session": str(session),
                "expected_loop": True,
                "acceptance_sha256": digest,
            }
        ],
    }
    assert validate_manifest(manifest) == []

    acceptance.write_text(json.dumps({"result": "FAIL"}), encoding="utf-8")
    failures = validate_manifest(manifest)
    assert any("hash changed" in failure for failure in failures)
    assert any("not PASS" in failure for failure in failures)


def test_markdown_report_exposes_per_run_retrieval_and_accuracy(tmp_path: Path):
    output = tmp_path / "report.md"
    write_markdown_report(
        output,
        {
            "result": "PASS",
            "datasets": [
                {
                    "id": "closed_a",
                    "runs": [
                        {
                            "repetition": 1,
                            "result": "PASS",
                            "automatic_loop_accepts": 1,
                            "endpoint_error_m": 0.0062,
                            "pose_coverage": 0.993,
                            "loop_retrieval": {
                                "returned": {"mean": 24.0},
                                "eligible": {"mean": 3.5},
                            },
                            "failures": [],
                        }
                    ],
                }
            ],
        },
    )

    text = output.read_text(encoding="utf-8")
    assert "仅在SLAM完成后评分" in text
    assert "| closed_a | 1 | PASS | 1 | 6.20 | 99.30% | 24.0/3.5 | — |" in text

from pathlib import Path

import pytest
import yaml

from product_calibration.workflow import (
    CalibrationSession,
    WorkflowError,
    load_workflow,
    sha256_file,
)


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / "product_calibration/workflow.yaml"
BASELINE = ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml"
COMMAND_CONTRACT = ROOT / "product_calibration/STAGE_COMMAND_CONTRACT.yaml"


def test_workflow_dependencies_are_acyclic_and_ordered():
    workflow = load_workflow(WORKFLOW)

    order = workflow.topological_order()

    assert order[0] == "identity"
    assert order.index("d405_stereo") < order.index("camera_imu")
    assert order.index("imu_multipose") < order.index("camera_imu")
    assert order.index("encoder_transport") < order.index("encoder_distance")
    assert order[-1] == "final_acceptance"


def test_new_session_starts_blocked_and_binds_golden_baseline(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow,
        tmp_path / "unit-001",
        product_id="unit-001",
        golden_baseline=BASELINE,
    )

    report = session.status()

    assert report["overall"] == "BLOCKED"
    assert report["stages"]["identity"]["state"] == "READY"
    assert report["stages"]["camera_imu"]["state"] == "BLOCKED"
    manifest = yaml.safe_load(session.manifest_path.read_text())
    assert manifest["golden_baseline"]["sha256"] == sha256_file(BASELINE)
    assert manifest["golden_baseline"]["path"] == "_frozen_inputs/golden_baseline.yaml"
    assert report["bound_input_integrity"]["golden_baseline"]["state"] == "PASS"


def test_pass_requires_existing_artifact_and_passed_dependencies(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-002", "unit-002", BASELINE
    )
    artifact = tmp_path / "identity.yaml"
    artifact.write_text("result: PASS\n", encoding="utf-8")

    with pytest.raises(WorkflowError, match="前置阶段"):
        session.record_result("camera_imu", "PASS", artifact)
    with pytest.raises(WorkflowError, match="不存在"):
        session.record_result("identity", "PASS", tmp_path / "missing.yaml")

    session.record_result("identity", "PASS", artifact)
    stage = session.status()["stages"]["identity"]
    stored = session.root / "identity/report.yaml"
    assert stage["state"] == "PASS"
    assert stage["artifact"] == "identity/report.yaml"
    assert stage["artifact_sha256"] == sha256_file(stored)
    artifact.unlink()
    assert session.status()["stages"]["identity"]["state"] == "PASS"


def test_failed_stage_blocks_dependents_and_preserves_failure_artifact(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-003", "unit-003", BASELINE
    )
    artifact = tmp_path / "identity-fail.yaml"
    artifact.write_text("result: FAIL\n", encoding="utf-8")
    session.record_result("identity", "FAIL", artifact, note="camera identity mismatch")

    report = session.status()
    assert report["stages"]["identity"]["state"] == "FAIL"
    assert report["stages"]["d405_stereo"]["state"] == "BLOCKED"
    assert report["stages"]["identity"]["note"] == "camera identity mismatch"


def test_artifact_tamper_changes_pass_to_fail_closed(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-004", "unit-004", BASELINE
    )
    artifact = tmp_path / "identity.yaml"
    artifact.write_text("result: PASS\n", encoding="utf-8")
    session.record_result("identity", "PASS", artifact)
    stored = session.root / "identity/report.yaml"
    stored.write_text("result: PASS\nchanged: true\n", encoding="utf-8")

    stage = session.status()["stages"]["identity"]
    assert stage["state"] == "FAIL"
    assert stage["reason"] == "ARTIFACT_HASH_MISMATCH"


def test_guide_contains_operator_steps_and_historical_comparison():
    workflow = load_workflow(WORKFLOW)

    guide = workflow.guide("camera_imu")

    assert "两份独立" in guide
    assert "-11.7 ms" in guide
    assert "不会自动写入生产配置" in guide


def test_tampered_golden_baseline_fails_entire_session(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-005", "unit-005", BASELINE
    )
    baseline = session.root / "_frozen_inputs/golden_baseline.yaml"
    baseline.write_text(baseline.read_text() + "\nchanged: true\n", encoding="utf-8")

    report = session.status()

    assert report["overall"] == "FAIL"
    assert report["bound_input_integrity"]["golden_baseline"]["reason"] == "HASH_MISMATCH"


def test_customer_calibration_is_six_separate_capture_solve_commands():
    contract = yaml.safe_load(COMMAND_CONTRACT.read_text(encoding="utf-8"))
    steps = contract["customer_steps"]

    assert contract["run_all_forbidden"] is True
    assert [step["number"] for step in steps] == list(range(1, 7))
    assert [step["stage"] for step in steps] == [
        "imu_static_bias",
        "imu_allan",
        "imu_multipose",
        "d405_stereo",
        "camera_imu",
        "world_z",
    ]
    assert len({step["command"].split()[0] for step in steps}) == 6
    assert all(step["captures"] and step["solves"] and step["report"] for step in steps)


def test_customer_steps_are_strictly_ordered_in_workflow():
    workflow = load_workflow(WORKFLOW)

    assert workflow.stages["imu_allan"].prerequisites == ("imu_static_bias",)
    assert workflow.stages["imu_multipose"].prerequisites == ("imu_allan",)
    assert workflow.stages["d405_stereo"].prerequisites == ("imu_multipose",)
    assert set(workflow.stages["camera_imu"].prerequisites) == {
        "d405_stereo",
        "imu_multipose",
        "imu_allan",
    }
    assert workflow.stages["world_z"].prerequisites == ("camera_imu",)

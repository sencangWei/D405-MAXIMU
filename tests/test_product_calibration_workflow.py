from pathlib import Path

import numpy as np
import pytest
import yaml

from product_calibration.workflow import (
    CalibrationSession,
    WorkflowError,
    load_workflow,
    sha256_file,
)
from product_calibration.compare_camera_imu import candidate_consensus


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / "product_calibration/workflow.yaml"
BASELINE = ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml"
COMMAND_CONTRACT = ROOT / "product_calibration/STAGE_COMMAND_CONTRACT.yaml"


def test_workflow_dependencies_are_acyclic_and_ordered():
    workflow = load_workflow(WORKFLOW)

    order = workflow.topological_order()

    assert order[0] == "identity"
    assert order.index("d405_stereo") < order.index("camera_imu")
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


def test_customer_calibration_has_five_required_commands_and_optional_intrinsic_diagnostic():
    contract = yaml.safe_load(COMMAND_CONTRACT.read_text(encoding="utf-8"))
    steps = contract["customer_release_steps"]
    diagnostics = contract["optional_engineering_diagnostics"]

    assert contract["run_all_forbidden"] is True
    assert contract["format_version"] == 2
    assert "customer_steps_renamed_to_customer_release_steps" in contract["schema_migration"]
    assert [step["number"] for step in steps] == [1, 2, 4, 5, 6]
    assert [step["stage"] for step in steps] == [
        "imu_static_bias",
        "imu_allan",
        "d405_stereo",
        "camera_imu",
        "world_z",
    ]
    assert len({step["command"].split()[0] for step in steps}) == 5
    assert all(step["captures"] and step["solves"] and step["report"] for step in steps)
    camera = steps[2]
    assert camera["command"].startswith("./calibrate_04_d405_factory.sh")
    assert "factory" in camera["solves"]
    assert "intrinsics_fit" not in camera["solves"]
    assert diagnostics == [{
        "number": 3,
        "stage": "imu_multipose",
        "command": "./calibrate_03_imu_intrinsic.sh {product_id}",
        "purpose": "engineering_diagnostics_only_not_applied_to_product_runtime",
        "report": "imu_multipose/report.yaml",
    }]


def test_camera_imu_consensus_outputs_rigid_candidate_for_later_stages():
    first = {
        "cam0": {"T_cam_imu": np.eye(4).tolist(), "timeshift_cam_imu": -0.010},
        "cam1": {"T_cam_imu": np.eye(4).tolist(), "timeshift_cam_imu": -0.011},
    }
    second_cam0 = np.eye(4)
    second_cam0[:3, 3] = [0.002, -0.004, 0.006]
    second_cam1 = np.eye(4)
    second_cam1[:3, 3] = [0.004, -0.002, 0.008]
    second = {
        "cam0": {"T_cam_imu": second_cam0.tolist(), "timeshift_cam_imu": -0.008},
        "cam1": {"T_cam_imu": second_cam1.tolist(), "timeshift_cam_imu": -0.009},
    }

    candidate = candidate_consensus(first, second)

    assert candidate["td_s"] == pytest.approx(-0.009)
    assert np.asarray(candidate["T_cam0_imu"])[:3, 3].tolist() == pytest.approx(
        [0.001, -0.002, 0.003]
    )
    rotation = np.asarray(candidate["T_cam0_imu"])[:3, :3]
    np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)


def test_required_customer_steps_skip_optional_multipose_stage():
    workflow = load_workflow(WORKFLOW)

    assert workflow.stages["imu_allan"].prerequisites == ("imu_static_bias",)
    assert workflow.stages["imu_multipose"].prerequisites == ("imu_allan",)
    assert workflow.stages["imu_multipose"].required_for_release is False
    assert workflow.stages["d405_stereo"].prerequisites == ("imu_allan",)
    assert set(workflow.stages["camera_imu"].prerequisites) == {
        "d405_stereo",
        "imu_allan",
    }
    assert workflow.stages["world_z"].prerequisites == ("camera_imu",)


def test_optional_multipose_failure_does_not_fail_product_session(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-optional", "unit-optional", BASELINE
    )
    failed = tmp_path / "multipose-fail.yaml"
    failed.write_text("result: FAIL\n", encoding="utf-8")
    session.record_result("imu_multipose", "FAIL", failed)

    report = session.status()

    assert report["stages"]["imu_multipose"]["state"] == "FAIL"
    assert report["stages"]["imu_multipose"]["required_for_release"] is False
    assert report["overall"] == "BLOCKED"


def _record_all_required_passes(session, tmp_path):
    for name in session.workflow.topological_order():
        if not session.workflow.stages[name].required_for_release:
            continue
        artifact = tmp_path / f"{name}-pass.yaml"
        artifact.write_text("result: PASS\n", encoding="utf-8")
        session.record_result(name, "PASS", artifact)


def test_final_pass_requires_every_current_required_stage_to_still_pass(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-final-fail-closed", "unit-final-fail-closed", BASELINE
    )
    _record_all_required_passes(session, tmp_path)
    assert session.status()["overall"] == "PASS"

    failed = tmp_path / "camera-imu-fail.yaml"
    failed.write_text("result: FAIL\n", encoding="utf-8")
    session.record_result("camera_imu", "FAIL", failed)

    assert session.status()["overall"] == "FAIL"


def test_final_pass_with_required_stage_blocked_is_not_pass(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-final-blocked", "unit-final-blocked", BASELINE
    )
    _record_all_required_passes(session, tmp_path)
    blocked = tmp_path / "camera-imu-blocked.yaml"
    blocked.write_text("result: BLOCKED\n", encoding="utf-8")
    session.record_result("camera_imu", "BLOCKED", blocked)

    assert session.status()["overall"] == "BLOCKED"


def test_final_pass_ignores_optional_diagnostic_failure_only(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-final-optional", "unit-final-optional", BASELINE
    )
    _record_all_required_passes(session, tmp_path)
    failed = tmp_path / "multipose-fail-after-final.yaml"
    failed.write_text("result: FAIL\n", encoding="utf-8")
    session.record_result("imu_multipose", "FAIL", failed)

    assert session.status()["overall"] == "PASS"


def test_final_pass_fails_closed_if_required_artifact_is_tampered(tmp_path):
    workflow = load_workflow(WORKFLOW)
    session = CalibrationSession.create(
        workflow, tmp_path / "unit-final-tamper", "unit-final-tamper", BASELINE
    )
    _record_all_required_passes(session, tmp_path)
    stored = session.root / workflow.stages["camera_imu"].evidence
    stored.write_text("result: PASS\ntampered: true\n", encoding="utf-8")

    assert session.status()["overall"] == "FAIL"


def test_world_z_live_uses_isolated_product_candidate_not_frozen_chain():
    source = (ROOT / "product_calibration_stage.py").read_text(encoding="utf-8")

    assert '"EGO_VIO_PRODUCT_LIVE_DEVICE_CONFIG"' in source
    assert '"EGO_VIO_PRODUCT_LIVE_CONFIG"' in source
    assert '"EGO_VIO_DISABLE_VIEWER": "1"' in source
    assert '[str(launcher), "product-live", "--duration-s"' in source
    assert '"frozen-record"' not in source
    assert '"activation"] = "CANDIDATE_ONLY_NOT_INSTALLED"' in source


def test_customer_preflight_and_setup_are_safe_ubuntu_2204_entrypoints():
    setup = (ROOT / "calib_setup.sh").read_text(encoding="utf-8")
    preflight = (ROOT / "calibrate_preflight.sh").read_text(encoding="utf-8")

    assert "Ubuntu 22.04" in setup
    assert "dialout,docker" in setup
    assert "kalibr_docker_command" in setup
    assert '"numpy==1.24.4"' in setup
    assert '"rosbags==0.11.4"' in setup
    assert '"aprilgrid==0.5.0"' in setup
    assert "4e1506d4ff12b1c6918441ca514bc0001f4c10bf17efe0283b5db1453640f863" in setup
    assert "chmod 666" not in setup
    assert "ros-jazzy" not in setup
    assert "--break-system-packages" not in setup
    assert "只读预检" in preflight
    assert "detect_d405" in preflight
    assert "/dev/serial/by-id/" in preflight
    status = (ROOT / "calibrate_status.sh").read_text(encoding="utf-8")
    assert "product_calibration_wizard.py" in status
    assert "EGO_VIO_CALIBRATION_SESSIONS" in status
    allan = (ROOT / "calibrate_02_imu_noise.sh").read_text(encoding="utf-8")
    assert "systemd-inhibit --what=sleep" in allan

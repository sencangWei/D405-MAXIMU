from pathlib import Path

import struct
import os
import subprocess
import sys
import types

import cv2
import numpy as np
import pytest
import yaml

from product_calibration.imu_stream import NORMALIZED_FORMAT
from product_calibration.kalibr_pipeline import (
    _aprilgrid_center,
    apply_accelerometer_calibration,
    camera_capture_health,
    factory_calibration_to_camchain,
    factory_stereo_report,
    heldout_epipolar_report,
    imucam_residuals,
    imu_capture_health,
    rectified_epipolar_errors,
    require_executable,
    run_command,
    solve_camera_imu,
    stereo_report,
)
from product_calibration.workflow import CalibrationSession, load_workflow, sha256_file


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = Path("/home/robot/ego_vio_handoff_20260816/calibration/calib_run_20260808")


def _factory_d405_calibration() -> dict:
    return {
        "format_version": 1,
        "source": "librealsense_active_factory_profile",
        "device": {
            "name": "Intel RealSense D405",
            "serial": "260322273737",
            "firmware": "5.17.0.10",
        },
        "stream": {"width": 1280, "height": 720, "fps": 30, "format": "y8"},
        "cam0_left_ir": {
            "fx": 647.519775,
            "fy": 647.519775,
            "cx": 638.534302,
            "cy": 369.768250,
            "distortion_model": "brown_conrady",
            "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "cam1_right_ir": {
            "fx": 647.519775,
            "fy": 647.519775,
            "cx": 638.534302,
            "cy": 369.768250,
            "distortion_model": "brown_conrady",
            "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "T_cam1_cam0": {
            "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "translation_m": [-0.018079, 0.0, 0.0],
        },
    }


def test_factory_d405_export_generates_fixed_kalibr_camchain_and_passes_policy(tmp_path):
    factory = _factory_d405_calibration()
    export = tmp_path / "d405_factory.yaml"
    export.write_text(yaml.safe_dump(factory, sort_keys=False), encoding="utf-8")
    camchain = tmp_path / "d405_factory_camchain.yaml"

    factory_calibration_to_camchain(factory, camchain)
    report = factory_stereo_report(
        factory,
        camchain,
        ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml",
        export_path=export,
    )

    document = yaml.safe_load(camchain.read_text(encoding="utf-8"))
    assert document["cam0"]["intrinsics"] == [
        647.519775, 647.519775, 638.534302, 369.76825,
    ]
    assert document["cam0"]["distortion_coeffs"] == [0.0, 0.0, 0.0, 0.0]
    assert document["cam1"]["T_cn_cnm1"][0][3] == -0.018079
    assert report["result"] == "PASS"
    assert report["runtime_policy"] == "USE_INTEL_FACTORY_RECTIFIED_INTRINSICS"
    assert report["metrics"]["baseline_m"] == 0.018079
    assert report["evidence"]["factory_calibration_sha256"]


def test_factory_d405_policy_rejects_non_rectified_ir_distortion(tmp_path):
    factory = _factory_d405_calibration()
    factory["cam1_right_ir"]["coeffs"][0] = 0.01
    camchain = tmp_path / "d405_factory_camchain.yaml"

    factory_calibration_to_camchain(factory, camchain)
    report = factory_stereo_report(
        factory,
        camchain,
        ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml",
    )

    assert report["result"] == "FAIL"
    assert report["checks"]["factory_ir_profiles_rectified"] is False


def _write_camera_health_recording(root: Path, frame_numbers: list[int]) -> Path:
    unit = root / "left_hand"
    left, right = unit / "frames", unit / "frames_right"
    left.mkdir(parents=True)
    right.mkdir()
    lines = ["idx,frame_number,ts_mono,ts_wall,has_depth"]
    for index, frame_number in enumerate(frame_numbers, 1):
        name = f"{index:06d}.jpg"
        (left / name).write_bytes(b"left")
        (right / name).write_bytes(b"right")
        lines.append(f"{index},{frame_number},{index / 30.0:.9f},{index / 30.0:.9f},0")
    (unit / "camera_ts.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (unit / "camera_capture_health.yaml").write_text(
        """format_version: 1
result: PASS
metrics:
  frames: 3
  formal_dropped_frames: 0
  formal_pair_mismatches: 0
  warmup:
    required_consecutive_frames: 60
    completed: true
    observed_frames: 62
    consecutive_frames: 60
    dropped_frames: 2
    reset_events: 1
checks: {}
""",
        encoding="utf-8",
    )
    return root


def test_camera_capture_health_accepts_warmup_drops_but_requires_clean_formal_window(tmp_path):
    recording = _write_camera_health_recording(tmp_path / "clean", [100, 101, 102])
    clean = camera_capture_health(recording)
    assert clean["result"] == "PASS"
    assert clean["metrics"]["warmup"]["dropped_frames"] == 2
    assert clean["metrics"]["formal_device_frame_drops"] == 0

    dirty = _write_camera_health_recording(tmp_path / "dirty", [100, 103, 104])
    failed = camera_capture_health(dirty)
    assert failed["result"] == "FAIL"
    assert failed["metrics"]["formal_device_frame_drops"] == 2
    assert failed["checks"]["reported_formal_drops_match_csv"] is False


def test_camera_capture_health_fails_closed_without_warmup_evidence(tmp_path):
    report = camera_capture_health(tmp_path / "legacy")
    assert report["result"] == "FAIL"
    assert report["checks"] == {"warmup_and_formal_evidence_present": False}


def test_imu_capture_health_requires_clean_formal_ttl_window(tmp_path):
    unit = tmp_path / "recording" / "left_hand"
    unit.mkdir(parents=True)
    (unit / "imu_ts.csv").write_text(
        "counter,ts_mono,rx_mono,ts_wall\n"
        "100,1.000,1.000,1.000\n"
        "101,1.0025,1.0025,1.0025\n"
        "102,1.0050,1.0050,1.0050\n",
        encoding="utf-8",
    )
    (unit / "imu_capture_health.yaml").write_text(
        yaml.safe_dump({
            "result": "PASS",
            "metrics": {
                "formal_frames": 3,
                "user_aborted": False,
                "protocol": "legacy_37_byte_ttl",
                "reader": {
                    "frames_bad": 0, "resyncs": 0, "dropped_frames": 0,
                    "sequence_gaps": 0, "counter_resets": 0, "counter_stalls": 0,
                    "invalid_imu_flags": 0, "queue_overflow_flags": 0,
                    "serial_errors": 0, "serial_reconnects": 0, "rate_hz": 400.0,
                },
                "warmup_cumulative_reader": {
                    "frames_bad": 1, "resyncs": 46, "dropped_frames": 1,
                },
                "lifetime_reader": {
                    "frames_bad": 1, "resyncs": 46, "dropped_frames": 1,
                },
                "unavailable_in_protocol": [
                    "packet_sequence_gaps", "invalid_imu_flags", "queue_overflow_flags",
                ],
            },
        }, sort_keys=False),
        encoding="utf-8",
    )

    clean = imu_capture_health(tmp_path / "recording")
    assert clean["result"] == "PASS"
    assert clean["metrics"]["formal_counter_gaps"] == 0
    assert clean["metrics"]["protocol"] == "legacy_37_byte_ttl"
    assert clean["metrics"]["reader"]["frames_bad"] == 0

    text = (unit / "imu_ts.csv").read_text(encoding="utf-8").replace(
        "102,1.0050", "104,1.0050"
    )
    (unit / "imu_ts.csv").write_text(text, encoding="utf-8")
    dirty = imu_capture_health(tmp_path / "recording")
    assert dirty["result"] == "FAIL"
    assert dirty["metrics"]["formal_counter_gaps"] == 2


def test_imu_capture_health_enforces_stm32_sequence_and_flag_counters(tmp_path):
    unit = tmp_path / "recording" / "left_hand"
    unit.mkdir(parents=True)
    (unit / "imu_ts.csv").write_text(
        "counter,ts_mono,rx_mono,ts_wall\n"
        "100,1.000,1.000,1.000\n"
        "101,1.0025,1.0025,1.0025\n",
        encoding="utf-8",
    )
    reader = {
        "frames_bad": 0, "resyncs": 0, "dropped_frames": 0,
        "counter_resets": 0, "counter_stalls": 0,
        "serial_errors": 0, "serial_reconnects": 0,
        "sequence_gaps": 1, "invalid_imu_flags": 0, "queue_overflow_flags": 0,
    }
    (unit / "imu_capture_health.yaml").write_text(
        yaml.safe_dump({
            "result": "FAIL",
            "metrics": {
                "formal_frames": 2,
                "user_aborted": False,
                "protocol": "stm32_combined_v1",
                "reader": reader,
            },
        }, sort_keys=False),
        encoding="utf-8",
    )

    report = imu_capture_health(tmp_path / "recording")

    assert report["result"] == "FAIL"
    assert report["checks"]["reader_transport_counters_zero"] is False


def test_require_executable_falls_back_to_user_local_bin(tmp_path, monkeypatch):
    executable = tmp_path / ".local/bin/kalibr_calibrate_cameras"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert require_executable("kalibr_calibrate_cameras") == str(executable)


def test_run_command_accepts_postsolve_report_failure_when_outputs_are_complete(tmp_path):
    artifact = tmp_path / "result.yaml"
    log = tmp_path / "solver.log"
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            "Path('result.yaml').write_text('ok\\n'); "
            "print('Calibration complete.'); "
            "raise SystemExit(1)"
        ),
    ]

    run_command(command, tmp_path, log, completed_outputs=(artifact,))

    assert artifact.read_text(encoding="utf-8") == "ok\n"
    assert "Calibration complete." in log.read_text(encoding="utf-8")


def test_solve_camera_imu_uses_kalibr_default_time_calibration(tmp_path, monkeypatch):
    bag = tmp_path / "source.bag"
    camchain = tmp_path / "stereo.yaml"
    imu = tmp_path / "imu.yaml"
    target = tmp_path / "target.yaml"
    for path in (bag, camchain, imu, target):
        path.write_text("placeholder\n", encoding="utf-8")
    observed = {}

    monkeypatch.setattr(
        "product_calibration.kalibr_pipeline.require_executable",
        lambda _name: "kalibr_calibrate_imu_camera",
    )

    def fake_run(command, cwd, _log, *, completed_outputs=()):
        observed["command"] = command
        for output in completed_outputs:
            Path(output).write_text("complete\n", encoding="utf-8")

    monkeypatch.setattr("product_calibration.kalibr_pipeline.run_command", fake_run)

    solve_camera_imu(bag, camchain, imu, target, tmp_path / "solve")

    assert "--time-calibration" not in observed["command"]
    assert "--no-time-calibration" not in observed["command"]


def test_aprilgrid_center_accepts_four_tags_in_one_row():
    detections = {}
    for tag_id in range(4):
        x0 = tag_id * 1.3
        object_corners = np.array([
            [x0, 0.0], [x0 + 1.0, 0.0],
            [x0 + 1.0, 1.0], [x0, 1.0],
        ])
        detections[tag_id] = object_corners * 100.0 + np.array([10.0, 20.0])
    center = _aprilgrid_center(detections)
    assert center is not None
    assert np.allclose(center, [[385.0, 395.0]], atol=1e-6)


def test_historical_stereo_result_passes_product_parser():
    report = stereo_report(
        HISTORICAL / "calib_intrinsics-camchain.yaml",
        HISTORICAL / "calib_intrinsics-results-cam.txt",
        ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml",
    )
    assert report["result"] == "PASS"
    assert report["metrics"]["reprojection_rms_px"]["cam1"] < 0.5


def test_historical_imucam_residuals_are_parsed_with_units():
    metrics = imucam_residuals(HISTORICAL / "calib_imucam-results-imucam.txt")
    assert metrics["reprojection_mean_px"] < 0.5
    assert metrics["gyroscope_mean_rad_s"] < 0.01
    assert metrics["accelerometer_mean_m_s2"] < 0.15
    second = imucam_residuals(HISTORICAL / "calib_imucam2-results-imucam.txt")
    assert second["gyroscope_mean_rad_s"] < 0.02
    assert second["accelerometer_mean_m_s2"] < 0.25


def test_rectified_epipolar_errors_use_independent_matched_corners(tmp_path):
    camchain = tmp_path / "camchain.yaml"
    camchain.write_text(
        """
cam0:
  camera_model: pinhole
  distortion_model: radtan
  intrinsics: [600.0, 600.0, 640.0, 360.0]
  distortion_coeffs: [0.0, 0.0, 0.0, 0.0]
  resolution: [1280, 720]
cam1:
  camera_model: pinhole
  distortion_model: radtan
  intrinsics: [600.0, 600.0, 640.0, 360.0]
  distortion_coeffs: [0.0, 0.0, 0.0, 0.0]
  resolution: [1280, 720]
  T_cn_cnm1:
    - [1.0, 0.0, 0.0, -0.018]
    - [0.0, 1.0, 0.0, 0.0]
    - [0.0, 0.0, 1.0, 0.0]
    - [0.0, 0.0, 0.0, 1.0]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    left = np.array([[100.0, 100.0], [200.0, 100.0], [200.0, 200.0], [100.0, 200.0]])
    right = left + np.array([-10.0, 0.0])
    errors = rectified_epipolar_errors(camchain, [(left, right)])
    assert errors.shape == (4,)
    assert np.max(errors) < 1e-9


def test_heldout_report_enforces_views_coverage_and_p95(tmp_path, monkeypatch):
    camchain = tmp_path / "camchain.yaml"
    camchain.write_text(
        """
cam0: {camera_model: pinhole, distortion_model: radtan, intrinsics: [600.0, 600.0, 150.0, 150.0], distortion_coeffs: [0.0, 0.0, 0.0, 0.0], resolution: [300, 300]}
cam1:
  camera_model: pinhole
  distortion_model: radtan
  intrinsics: [600.0, 600.0, 150.0, 150.0]
  distortion_coeffs: [0.0, 0.0, 0.0, 0.0]
  resolution: [300, 300]
  T_cn_cnm1: [[1.0, 0.0, 0.0, -0.018], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    unit = tmp_path / "heldout" / "left_hand"
    left_dir, right_dir = unit / "frames", unit / "frames_right"
    left_dir.mkdir(parents=True)
    right_dir.mkdir()
    for index in range(45):
        image = np.full((300, 300), index % 9, dtype=np.uint8)
        encoded = cv2.imencode(".jpg", image)[1].tobytes()
        (left_dir / f"{index:06d}.jpg").write_bytes(encoded)
        (right_dir / f"{index:06d}.jpg").write_bytes(encoded)

    class FakeDetector:
        created = 0
        vertical_error_px = 0.0
        detection_calls = 0
        fail_first_detection = False

        def __init__(self, _family):
            self.right = FakeDetector.created % 2 == 1
            FakeDetector.created += 1

        def detect(self, image):
            call = FakeDetector.detection_calls
            FakeDetector.detection_calls += 1
            if FakeDetector.fail_first_detection and call == 0:
                raise cv2.error("cornerSubPix edge failure")
            cell = int(round(float(np.mean(image)))) % 9
            x = (cell % 3 + 0.5) * 100.0 - (10.0 if self.right else 0.0)
            y = (cell // 3 + 0.5) * 100.0 + (
                self.vertical_error_px if self.right else 0.0
            )
            detections = []
            for tag_id, dx, dy in ((14, -6.0, -6.0), (15, 6.0, -6.0),
                                   (20, -6.0, 6.0), (21, 6.0, 6.0)):
                corners = np.array([[x + dx - 2, y + dy - 2], [x + dx + 2, y + dy - 2],
                                    [x + dx + 2, y + dy + 2], [x + dx - 2, y + dy + 2]])
                detections.append(types.SimpleNamespace(tag_id=tag_id, corners=corners))
            return detections

    monkeypatch.setitem(sys.modules, "aprilgrid", types.SimpleNamespace(Detector=FakeDetector))
    report = heldout_epipolar_report(tmp_path / "heldout", camchain)
    assert report["result"] == "PASS"
    assert report["metrics"]["valid_matched_views"] == 45
    assert report["metrics"]["left_coverage_cells"] == list(range(9))
    assert report["metrics"]["right_coverage_cells"] == list(range(9))
    assert report["metrics"]["epipolar_vertical_p95_px"] < 1e-9

    FakeDetector.created = 0
    FakeDetector.detection_calls = 0
    FakeDetector.fail_first_detection = True
    flaky = heldout_epipolar_report(tmp_path / "heldout", camchain)
    assert flaky["result"] == "PASS"
    assert flaky["metrics"]["detector_errors"] == 1
    FakeDetector.fail_first_detection = False

    FakeDetector.created = 0
    FakeDetector.vertical_error_px = 2.0
    failed = heldout_epipolar_report(tmp_path / "heldout", camchain)
    assert failed["result"] == "FAIL"
    assert not failed["checks"]["epipolar_vertical_p95_le_1px"]

    class EmptyDetector:
        def __init__(self, _family):
            pass

        def detect(self, _image):
            return []

    monkeypatch.setitem(sys.modules, "aprilgrid", types.SimpleNamespace(Detector=EmptyDetector))
    empty = heldout_epipolar_report(tmp_path / "heldout", camchain)
    assert empty["result"] == "FAIL"
    assert empty["metrics"]["valid_matched_views"] == 0
    assert empty["metrics"]["epipolar_vertical_p95_px"] is None

    monkeypatch.setitem(sys.modules, "aprilgrid", types.SimpleNamespace(Detector=FakeDetector))
    FakeDetector.created = 0
    FakeDetector.vertical_error_px = 0.0
    (right_dir / "000044.jpg").unlink()
    unpaired = heldout_epipolar_report(tmp_path / "heldout", camchain)
    assert unpaired["result"] == "FAIL"
    assert not unpaired["checks"]["all_left_right_files_paired"]


def test_five_required_commands_and_optional_diagnostic_are_executable():
    for index in range(1, 7):
        path = ROOT / f"calibrate_{index:02d}_{['imu_static', 'imu_noise', 'imu_intrinsic', 'd405_factory', 'camera_imu', 'world_z'][index - 1]}.sh"
        assert path.is_file()
        assert path.stat().st_mode & 0o111


def test_engineering_accel_intrinsic_utility_preserves_raw_input(tmp_path):
    recording = tmp_path / "recording"
    unit = recording / "left_hand"
    unit.mkdir(parents=True)
    source = struct.pack(NORMALIZED_FORMAT, 1.0, 7, 0.1, 0.2, 0.3, 1.1, 0.2, -0.1, 25.0)
    (unit / "imu.bin").write_bytes(source)
    report = {
        "bias_m_s2": [0.1, -0.2, 0.3],
        "correction_matrix": np.diag([1.01, 0.99, 1.02]).tolist(),
    }
    evidence = apply_accelerometer_calibration(recording, report)
    assert (unit / "imu_raw.bin").read_bytes() == source
    corrected = struct.unpack(NORMALIZED_FORMAT, (unit / "imu.bin").read_bytes())
    expected = np.diag([1.01, 0.99, 1.02]) @ (np.array([1.1, 0.2, -0.1]) * 9.80665 - np.array([0.1, -0.2, 0.3])) / 9.80665
    assert np.allclose(corrected[5:8], expected)
    assert evidence["samples"] == 1


def test_stage4_and_stage5_customer_commands_reanalyze_historical_gold(tmp_path):
    workflow = load_workflow(ROOT / "product_calibration/workflow.yaml")
    sessions = tmp_path / "sessions"
    session = CalibrationSession.create(
        workflow, sessions / "P001", "P001",
        ROOT / "product_calibration/GOLDEN_BASELINE_20260808.yaml",
    )
    seed = tmp_path / "seed.yaml"
    seed.write_text("result: PASS\n", encoding="utf-8")
    for stage in ("identity", "imu_static_bias", "imu_allan"):
        session.record_result(stage, "PASS", seed)
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    rejected_stage4 = subprocess.run([
        sys.executable, str(ROOT / "product_calibration_stage.py"), "d405-stereo", "P001",
        "--session-root", str(sessions),
        "--input-camchain", str(HISTORICAL / "calib_intrinsics-camchain.yaml"),
        "--input-results", str(HISTORICAL / "calib_intrinsics-results-cam.txt"),
    ], env=environment, capture_output=True, text=True)
    assert rejected_stage4.returncode == 2
    assert "--input-validation" in rejected_stage4.stderr
    stage4 = subprocess.run([
        sys.executable, str(ROOT / "product_calibration_stage.py"), "d405-stereo", "P001",
        "--session-root", str(sessions),
        "--input-camchain", str(HISTORICAL / "calib_intrinsics-camchain.yaml"),
        "--input-results", str(HISTORICAL / "calib_intrinsics-results-cam.txt"),
        "--legacy-reference-only",
    ], env=environment, capture_output=True, text=True)
    assert stage4.returncode == 0, stage4.stderr
    stage4_report = yaml.safe_load(
        (sessions / "P001/d405_stereo/report.yaml").read_text(encoding="utf-8")
    )
    assert stage4_report["result"] == "PASS"
    assert stage4_report["release_eligible"] is False
    assert stage4_report["heldout_epipolar"]["result"] == "NOT_AVAILABLE_LEGACY_REFERENCE"
    stage5 = subprocess.run([
        sys.executable, str(ROOT / "product_calibration_stage.py"), "camera-imu", "P001",
        "--session-root", str(sessions),
        "--run1", str(HISTORICAL / "calib_imucam-camchain-imucam.yaml"),
        "--results1", str(HISTORICAL / "calib_imucam-results-imucam.txt"),
        "--run2", str(HISTORICAL / "calib_imucam2-camchain-imucam.yaml"),
        "--results2", str(HISTORICAL / "calib_imucam2-results-imucam.txt"),
    ], env=environment, capture_output=True, text=True)
    assert stage5.returncode == 2, stage5.stderr
    assert session.status()["stages"]["camera_imu"]["state"] == "BLOCKED"
    stage5_report = yaml.safe_load(
        (sessions / "P001/camera_imu/report.yaml").read_text(encoding="utf-8")
    )
    assert stage5_report["accelerometer_intrinsic_application"]["policy"] == (
        "NOT_APPLIED_PRODUCT_BASELINE"
    )
    assert stage5_report["numerical_result"] == "PASS"
    assert stage5_report["release_eligible"] is False
    assert "原始录制" in stage5_report["blocking_reason"]


def test_stage_evidence_hash_must_match_before_formal_reuse(tmp_path):
    from product_calibration_stage import _verified_report_evidence
    from product_calibration.workflow import WorkflowError

    artifact = tmp_path / "imu_kalibr.yaml"
    artifact.write_text("update_rate: 400\n", encoding="utf-8")
    report = {"evidence": {
        "imu_kalibr_yaml": str(artifact),
        "imu_kalibr_yaml_sha256": sha256_file(artifact),
    }}
    assert _verified_report_evidence(report, "imu_kalibr_yaml") == artifact.resolve()

    artifact.write_text("update_rate: 200\n", encoding="utf-8")
    with pytest.raises(WorkflowError, match="SHA-256不匹配"):
        _verified_report_evidence(report, "imu_kalibr_yaml")

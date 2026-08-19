from pathlib import Path

import struct
import os
import subprocess
import sys
import types

import cv2
import numpy as np
import yaml

from product_calibration.imu_stream import NORMALIZED_FORMAT
from product_calibration.kalibr_pipeline import (
    _aprilgrid_center,
    apply_accelerometer_calibration,
    heldout_epipolar_report,
    imucam_residuals,
    rectified_epipolar_errors,
    stereo_report,
)
from product_calibration.workflow import CalibrationSession, load_workflow


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = Path("/home/robot/ego_vio_handoff_20260816/calibration/calib_run_20260808")


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

        def __init__(self, _family):
            self.right = FakeDetector.created % 2 == 1
            FakeDetector.created += 1

        def detect(self, image):
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


def test_all_six_customer_commands_are_executable():
    for index in range(1, 7):
        path = ROOT / f"calibrate_{index:02d}_{['imu_static', 'imu_noise', 'imu_intrinsic', 'd405_stereo', 'camera_imu', 'world_z'][index - 1]}.sh"
        assert path.is_file()
        assert path.stat().st_mode & 0o111


def test_camera_imu_input_applies_multipose_accel_intrinsics_without_losing_raw(tmp_path):
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
    for stage in ("identity", "imu_static_bias", "imu_allan", "imu_multipose"):
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
    assert stage5.returncode == 0, stage5.stderr
    assert session.status()["stages"]["camera_imu"]["state"] == "PASS"

from pathlib import Path

import struct
import os
import subprocess
import sys

import numpy as np

from product_calibration.imu_stream import NORMALIZED_FORMAT
from product_calibration.kalibr_pipeline import (
    apply_accelerometer_calibration,
    imucam_residuals,
    stereo_report,
)
from product_calibration.workflow import CalibrationSession, load_workflow


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = Path("/home/robot/ego_vio_handoff_20260816/calibration/calib_run_20260808")


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
    stage4 = subprocess.run([
        sys.executable, str(ROOT / "product_calibration_stage.py"), "d405-stereo", "P001",
        "--session-root", str(sessions),
        "--input-camchain", str(HISTORICAL / "calib_intrinsics-camchain.yaml"),
        "--input-results", str(HISTORICAL / "calib_intrinsics-results-cam.txt"),
    ], env=environment, capture_output=True, text=True)
    assert stage4.returncode == 0, stage4.stderr
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

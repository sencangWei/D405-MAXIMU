from pathlib import Path

import numpy as np
import yaml

from product_calibration.compare_camera_imu import compare_runs
from product_calibration.imu_ellipsoid import fit_and_validate


BASELINE = Path(__file__).parents[1] / "product_calibration/GOLDEN_BASELINE_20260808.yaml"


def _sphere_points(count: int, offset: float = 0.0) -> np.ndarray:
    indices = np.arange(count, dtype=float) + offset
    z = 1.0 - 2.0 * (indices + 0.5) / count
    angle = indices * np.pi * (3.0 - np.sqrt(5.0))
    radius = np.sqrt(1.0 - z * z)
    return np.column_stack((radius * np.cos(angle), radius * np.sin(angle), z))


def _write_golden_camchain(path: Path, name: str) -> None:
    golden = yaml.safe_load(BASELINE.read_text())["camera_imu"][name]
    document = {
        "cam0": {
            "T_cam_imu": golden["T_cam0_imu"],
            "timeshift_cam_imu": golden["timeshift_cam_imu_s"]["cam0"],
        },
        "cam1": {
            "T_cam_imu": golden["T_cam1_imu"],
            "timeshift_cam_imu": golden["timeshift_cam_imu_s"]["cam1"],
        },
    }
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def test_known_good_kalibr_pair_reproduces_golden_metrics(tmp_path):
    run1 = tmp_path / "run1.yaml"
    run2 = tmp_path / "run2.yaml"
    _write_golden_camchain(run1, "run1")
    _write_golden_camchain(run2, "run2")
    report = compare_runs(run1, run2, BASELINE)

    assert report["result"] == "PASS"
    assert abs(report["candidate_repeatability"]["rotation_deg"] - 0.267327307) < 1e-6
    assert abs(report["candidate_repeatability"]["translation_mm"] - 2.663190623) < 1e-6
    assert abs(report["candidate_repeatability"]["cam0_td_ms"] - 0.125959169) < 1e-6


def test_arbitrary_pose_fit_recovers_bias_and_scale_with_heldout_data():
    gravity = 9.80665
    bias = np.array([0.12, -0.08, 0.05])
    sensor_matrix = np.array(
        [[1.04, 0.012, -0.006], [0.0, 0.97, 0.009], [0.0, 0.0, 1.02]]
    )

    def raw(points: np.ndarray) -> np.ndarray:
        return (np.linalg.inv(sensor_matrix) @ (points * gravity).T).T + bias

    fit = raw(_sphere_points(30))
    validation = raw(_sphere_points(12, offset=0.37))
    report = fit_and_validate(fit, validation, gravity=gravity)

    assert report["result"] == "PASS"
    assert report["metrics"]["validation_norm_rmse_g"] < 1e-10
    assert np.allclose(report["bias_m_s2"], bias, atol=1e-9)


def test_multipose_fit_fails_product_gate_without_heldout_poses():
    gravity = 9.80665
    report = fit_and_validate(
        _sphere_points(30) * gravity,
        _sphere_points(4, offset=0.2) * gravity,
        gravity=gravity,
    )

    assert report["result"] == "FAIL"
    assert report["checks"]["validation_pose_count"] is False

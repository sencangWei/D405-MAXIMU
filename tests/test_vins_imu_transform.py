from pathlib import Path

import numpy as np
import pytest
import yaml

from ego_vio.imu.vins_transform import (
    DEFAULT_VINS_IMU_ROTATION,
    load_vins_imu_rotation,
)


def write_calibration(path: Path, rotation: np.ndarray, result: str = "PASS") -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "result": result,
                "R_level_from_imu": rotation.tolist(),
                "activation": {"automatically_enabled": False},
            }
        ),
        encoding="utf-8",
    )


def test_default_rotation_preserves_stable_pipeline() -> None:
    np.testing.assert_array_equal(load_vins_imu_rotation(), DEFAULT_VINS_IMU_ROTATION)


def test_loads_passed_candidate(tmp_path: Path) -> None:
    path = tmp_path / "level.yaml"
    write_calibration(path, np.eye(3))
    np.testing.assert_array_equal(load_vins_imu_rotation(path), np.eye(3))


def test_rejects_failed_or_non_rotation_candidate(tmp_path: Path) -> None:
    failed = tmp_path / "failed.yaml"
    write_calibration(failed, np.eye(3), result="FAIL")
    with pytest.raises(ValueError, match="未通过"):
        load_vins_imu_rotation(failed)

    invalid = tmp_path / "invalid.yaml"
    write_calibration(invalid, np.diag([1.0, 1.0, 2.0]))
    with pytest.raises(ValueError, match="正交"):
        load_vins_imu_rotation(invalid)

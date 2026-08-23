import math
from pathlib import Path

import numpy as np
import pytest

from scripts.apply_verified_loop_correction import (
    VerifiedLoopCorrection,
    apply_smooth_correction,
    correction_consistency,
    parse_verified_corrections,
    reject_path_collisions,
)


def test_parses_only_accepted_geometry(tmp_path: Path):
    log = tmp_path / "loop.log"
    log.write_text(
        "[AUTO_LOOP_REJECT] current=9 matched=1\n"
        "[AUTO_LOOP_ACCEPT] current=12 matched=1 confirmations=3 "
        "inliers_gate=>99 fused_correction_t_m=0.1000 "
        "fused_correction_xyz_m=(0.1000,0.0000,0.0000) "
        "fused_correction_yaw_deg=1.00\n"
    )
    parsed = parse_verified_corrections(log)
    assert len(parsed) == 1
    assert parsed[0].current == 12
    assert np.allclose(parsed[0].translation_m, [0.1, 0.0, 0.0])


def test_rejects_inconsistent_reported_norm(tmp_path: Path):
    log = tmp_path / "loop.log"
    log.write_text(
        "[AUTO_LOOP_ACCEPT] current=12 matched=1 confirmations=3 "
        "fused_correction_t_m=0.2000 "
        "fused_correction_xyz_m=(0.1000,0.0000,0.0000) "
        "fused_correction_yaw_deg=0.00\n"
    )
    with pytest.raises(ValueError, match="norm"):
        parse_verified_corrections(log)


def test_distributes_correction_without_a_terminal_jump():
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    quaternions = np.tile([0.0, 0.0, 0.0, 1.0], (3, 1))
    correction = VerifiedLoopCorrection(2, 0, 3, np.array([0.2, 0.0, 0.0]), 0.0)
    corrected, _, progress = apply_smooth_correction(points, quaternions, correction)
    assert np.allclose(progress, [0.0, 0.5, 1.0])
    assert np.allclose(corrected[:, 0], [0.0, 1.1, 2.2])
    assert np.allclose(np.diff(corrected[:, 0]), [1.1, 1.1])


def test_interpolates_yaw_and_normalizes_quaternion():
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    quaternions = np.tile([0.0, 0.0, 0.0, 1.0], (2, 1))
    correction = VerifiedLoopCorrection(1, 0, 3, np.zeros(3), 90.0)
    corrected, rotations, _ = apply_smooth_correction(points, quaternions, correction)
    assert np.allclose(corrected[-1], [0.0, 1.0, 0.0], atol=1e-12)
    assert np.allclose(np.linalg.norm(rotations, axis=1), 1.0)
    assert math.isclose(rotations[-1, 2], math.sin(math.pi / 4.0))


def test_reports_pairwise_consistency():
    corrections = [
        VerifiedLoopCorrection(1, 0, 3, np.array([0.10, 0.0, 0.0]), 0.0),
        VerifiedLoopCorrection(2, 0, 3, np.array([0.11, 0.0, 0.0]), 0.5),
    ]
    result = correction_consistency(corrections)
    assert result["max_pairwise_translation_m"] == pytest.approx(0.01)
    assert result["max_pairwise_yaw_deg"] == pytest.approx(0.5)


def test_rejects_nonfinite_correction_and_zero_quaternion(tmp_path: Path):
    log = tmp_path / "loop.log"
    log.write_text(
        "[AUTO_LOOP_ACCEPT] current=12 matched=1 confirmations=3 "
        "fused_correction_t_m=1e309 "
        "fused_correction_xyz_m=(1e309,0.0,0.0) "
        "fused_correction_yaw_deg=0.0\n"
    )
    with pytest.raises(ValueError, match="non-finite"):
        parse_verified_corrections(log)

    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    quaternions = np.zeros((2, 4))
    correction = VerifiedLoopCorrection(1, 0, 3, np.zeros(3), 0.0)
    with pytest.raises(ValueError, match="zero quaternion"):
        apply_smooth_correction(points, quaternions, correction)


def test_rejects_output_aliasing_inputs(tmp_path: Path):
    trajectory = tmp_path / "trajectory.csv"
    loop_log = tmp_path / "loop.log"
    report = tmp_path / "report.json"
    with pytest.raises(ValueError, match="aliases an input"):
        reject_path_collisions([trajectory, loop_log], [trajectory, report])
    with pytest.raises(ValueError, match="must be distinct"):
        reject_path_collisions([trajectory, loop_log], [report, report])

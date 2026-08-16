import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_depth_plane_factor_evidence import validate_factor_evidence


def write_trajectory(path: Path, z_values: list[float], x_offset: float = 0.0) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("t_sec", "x", "y", "z", "qw", "qx", "qy", "qz"))
        for index, z_value in enumerate(z_values):
            writer.writerow((index / 30, index * 0.01 + x_offset, 0, z_value, 1, 0, 0, 0))


def write_report(path: Path, factor: dict) -> None:
    path.write_text(json.dumps({"plane_factor": factor}), encoding="utf-8")


def active_factor(maximum: float) -> dict:
    return {
        "status": "ACTIVE",
        "reason": None,
        "support_observations": 8,
        "min_support": 5,
        "activations": 1,
        "active_trajectory_samples": 4,
        "correction_axis_world": [0.0, 0.0, 1.0],
        "max_correction_m": 0.03,
        "applied_correction_max_abs_m": maximum,
        "causal": True,
        "uses_absolute_height": False,
        "uses_endpoint_constraint": False,
    }


def test_active_factor_changes_only_z_within_bound(tmp_path: Path):
    raw = tmp_path / "raw.csv"
    corrected = tmp_path / "corrected.csv"
    report = tmp_path / "analysis.json"
    write_trajectory(raw, [0.0, 0.1, 0.2, 0.1])
    write_trajectory(corrected, [0.0, 0.102, 0.204, 0.106])
    write_report(report, active_factor(0.006))

    evidence = validate_factor_evidence(report, raw, corrected)

    assert evidence["result"] == "PASS"
    assert evidence["measured_correction_max_abs_m"] == pytest.approx(0.006)


def test_disabled_factor_requires_identical_trajectory(tmp_path: Path):
    raw = tmp_path / "raw.csv"
    corrected = tmp_path / "corrected.csv"
    report = tmp_path / "analysis.json"
    write_trajectory(raw, [0.0, 0.1, 0.2])
    write_trajectory(corrected, [0.0, 0.1, 0.201])
    write_report(
        report,
        {
            "status": "DISABLED",
            "reason": "insufficient support",
            "support_observations": 2,
            "active_trajectory_samples": 0,
            "applied_correction_max_abs_m": 0.0,
            "causal": True,
            "uses_absolute_height": False,
            "uses_endpoint_constraint": False,
        },
    )

    evidence = validate_factor_evidence(report, raw, corrected)

    assert evidence["result"] == "FAIL"
    assert "disabled factor changed the trajectory" in evidence["failures"]


def test_factor_rejects_xy_or_attitude_changes(tmp_path: Path):
    raw = tmp_path / "raw.csv"
    corrected = tmp_path / "corrected.csv"
    report = tmp_path / "analysis.json"
    write_trajectory(raw, [0.0, 0.1, 0.2])
    write_trajectory(corrected, [0.0, 0.1, 0.2], x_offset=0.001)
    write_report(report, active_factor(0.0))

    evidence = validate_factor_evidence(report, raw, corrected)

    assert evidence["result"] == "FAIL"
    assert any("non-Z pose field changed" in item for item in evidence["failures"])

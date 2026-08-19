import importlib.util
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest


MODULE_PATH = Path(__file__).parents[1] / "fit_multisession_world_z.py"
SPEC = importlib.util.spec_from_file_location("fit_multisession_world_z", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_rotation_maps_fitted_plane_normal_to_world_z():
    x, y = np.meshgrid(np.linspace(-1, 1, 10), np.linspace(-1, 1, 10))
    points = np.column_stack((x.ravel(), y.ravel(), (0.04 * x - 0.02 * y).ravel()))
    normal = MODULE.fit_normal([points])
    rotation = MODULE.rotation_to_world_z(normal)
    corrected = (rotation @ points.T).T

    np.testing.assert_allclose(np.linalg.det(rotation), 1.0, atol=1e-12)
    np.testing.assert_allclose(rotation @ normal, [0.0, 0.0, 1.0], atol=1e-12)
    assert np.ptp(corrected[:, 2]) < 1e-12


def test_rotation_handles_antiparallel_normal():
    rotation = MODULE.rotation_to_world_z(np.array([0.0, 0.0, -1.0]))
    np.testing.assert_allclose(rotation @ [0.0, 0.0, -1.0], [0.0, 0.0, 1.0])
    np.testing.assert_allclose(np.linalg.det(rotation), 1.0)


def test_fit_rejects_zero_variance_and_line_geometry():
    with pytest.raises(ValueError, match="zero spatial variance"):
        MODULE.fit_normal([np.zeros((10, 3))], validate_geometry=True)
    line = np.column_stack((np.arange(10), np.zeros(10), np.zeros(10)))
    with pytest.raises(ValueError, match="in-plane rank"):
        MODULE.fit_normal([line], validate_geometry=True)


def test_elevation_gate_uses_required_80_percent_lower_bound():
    assert MODULE.elevation_retention_passes(0.80)
    assert MODULE.elevation_retention_passes(0.85)
    assert MODULE.elevation_retention_passes(1.20)
    assert not MODULE.elevation_retention_passes(0.799)


def test_duplicate_session_names_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate session name"):
        MODULE.named_paths_to_dict([("same", tmp_path / "a"), ("same", tmp_path / "b")])


def write_xyz(path, points):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["x", "y", "z"])
        writer.writeheader()
        for x, y, z in points:
            writer.writerow({"x": x, "y": y, "z": z})


def planar_points(x_slope):
    x, y = np.meshgrid(np.linspace(-1, 1, 16), np.linspace(-1, 1, 12))
    return np.column_stack((x.ravel(), y.ravel(), (x_slope * x).ravel()))


def run_main(monkeypatch, planar, elevation, output):
    argv = [str(MODULE_PATH)]
    for name, path in planar:
        argv += ["--planar", f"{name}={path}"]
    for name, path in elevation:
        argv += ["--elevation", f"{name}={path}"]
    argv += ["--out", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    return MODULE.main()


def test_main_pass_report_provenance_and_vertical_elevation(monkeypatch, tmp_path):
    planar = []
    for index in range(4):
        path = tmp_path / f"planar_{index}.csv"
        write_xyz(path, planar_points(0.02))
        planar.append((f"p{index}", path))
    elevation_path = tmp_path / "vertical.csv"
    write_xyz(elevation_path, np.column_stack((np.zeros(50), np.zeros(50), np.linspace(0, 1, 50))))
    output = tmp_path / "pass.json"

    assert run_main(monkeypatch, planar, [("up", elevation_path)], output) == 0
    report = json.loads(output.read_text())
    assert report["result"] == "PASS"
    assert report["activation"] == "FORBIDDEN_PENDING_END_TO_END_VALIDATION"
    assert report["elevation_safety"]["up"]["result"] == "PASS"
    assert "plane_tilt_deg" not in report["elevation_safety"]["up"]["baseline"]
    for held_out, result in report["planar_leave_one_out"].items():
        assert held_out not in result["training_sessions"]
    for item in report["provenance"]["inputs"]:
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]


def test_main_fail_sets_exit_code_and_activation(monkeypatch, tmp_path):
    planar = []
    for index, slope in enumerate([0.02, 0.02, 0.02, -0.08]):
        path = tmp_path / f"planar_{index}.csv"
        write_xyz(path, planar_points(slope))
        planar.append((f"p{index}", path))
    elevation_path = tmp_path / "vertical.csv"
    write_xyz(elevation_path, np.column_stack((np.zeros(50), np.zeros(50), np.linspace(0, 1, 50))))
    output = tmp_path / "fail.json"

    assert run_main(monkeypatch, planar, [("up", elevation_path)], output) == 3
    report = json.loads(output.read_text())
    assert report["result"] == "FAIL"
    assert report["activation"] == "FORBIDDEN_VALIDATION_FAILED"


def test_main_requires_elevation_argument(monkeypatch, tmp_path):
    path = tmp_path / "planar.csv"
    write_xyz(path, planar_points(0.02))
    monkeypatch.setattr(
        sys,
        "argv",
        [str(MODULE_PATH), "--planar", f"a={path}", "--planar", f"b={path}", "--planar", f"c={path}", "--out", str(tmp_path / "out.json")],
    )
    with pytest.raises(SystemExit) as error:
        MODULE.main()
    assert error.value.code == 2

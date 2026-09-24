import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "align_mast3r_scale_with_imu", ROOT / "scripts/align_mast3r_scale_with_imu.py"
)
scale_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scale_module)


def test_onboard_attitude_is_aligned_without_extrapolating_or_changing_visual_poses():
    times = np.arange(11, dtype=float)
    reference_times = times[1:-1]
    reference_body = Rotation.from_euler(
        "zy", np.column_stack((0.03 * reference_times, 0.02 * reference_times))
    )
    world_alignment = Rotation.from_euler("xyz", [0.4, -0.2, 0.1])
    body_to_camera = Rotation.from_euler("x", 0.3)
    body_t_camera = np.eye(4)
    body_t_camera[:3, :3] = body_to_camera.as_matrix()
    visual_body = world_alignment * Rotation.from_euler(
        "zy", np.column_stack((0.03 * times, 0.02 * times))
    )
    visual_camera_quaternions = (visual_body * body_to_camera).as_quat()

    keep, fitted_body, metadata = scale_module.select_scale_attitude(
        times,
        visual_camera_quaternions,
        body_t_camera,
        reference_times,
        reference_body.as_quat(),
    )

    assert np.array_equal(np.flatnonzero(keep), np.arange(1, 10))
    assert np.max((fitted_body.inv() * visual_body[keep]).magnitude()) < 1e-8
    assert metadata["source"] == "onboard_orientation_trajectory"
    assert metadata["overlap_ratio"] == pytest.approx(9 / 11)


def test_default_attitude_source_preserves_legacy_mast3r_rotations():
    times = np.arange(6, dtype=float)
    visual_camera = Rotation.from_euler("z", 0.03 * times)
    keep, body_rotations, metadata = scale_module.select_scale_attitude(
        times, visual_camera.as_quat(), np.eye(4)
    )
    assert keep.all()
    assert np.max((body_rotations.inv() * visual_camera).magnitude()) < 1e-8
    assert metadata["source"] == "mast3r"


def test_production_fusion_passes_onboard_orientation_to_scale_estimator():
    workflow = (ROOT / "scripts/mast3r_slam_precision_workflow.sh").read_text()
    fusion = workflow.split("    fusion)", 1)[1].split("    compare)", 1)[0]
    assert '--orientation-trajectory "$vins_trajectory"' in fusion
    assert '--stereo-scale-report "$mast3r_output/stereo_scale_bidirectional_report.json"' in fusion


def test_attitude_source_selection_uses_only_independent_stereo_scale():
    stereo = {"result": "PASS", "scale_m_per_mast3r_unit": 0.32}
    legacy = {"scale": 0.319, "gravity_norm": 9.8, "rank": 30, "unknowns": 30}
    onboard = {"scale": 0.34, "gravity_norm": 9.8, "rank": 30, "unknowns": 30}
    source, details = scale_module.choose_attitude_scale(legacy, onboard, stereo)
    assert source == "mast3r"
    assert details["mast3r_scale"] == 0.319
    assert details["onboard_scale"] == 0.34

    legacy["scale"] = 0.48
    onboard["scale"] = 0.33
    source, _ = scale_module.choose_attitude_scale(legacy, onboard, stereo)
    assert source == "onboard_orientation_trajectory"


def test_attitude_source_selection_rejects_unvalidated_stereo_scale():
    result = {"scale": 0.32, "gravity_norm": 9.8, "rank": 30, "unknowns": 30}
    with pytest.raises(ValueError, match="validated stereo scale"):
        scale_module.choose_attitude_scale(
            result, result, {"result": "FAIL", "scale_m_per_mast3r_unit": 0.32}
        )

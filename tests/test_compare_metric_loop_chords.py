import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


MODULE = (Path(__file__).resolve().parents[1] / ".planning" /
          "metric_window_bundle_20260928" / "compare_metric_loop_chords.py")
spec = importlib.util.spec_from_file_location("compare_metric_loop_chords", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_camera_chord_includes_rotating_body_lever():
    times = np.array([0.0, 1.0])
    positions = np.zeros((2, 3))
    rotations = Rotation.from_euler("z", [0, 90], degrees=True)
    chord = module.camera_chord(times, positions, rotations, 0.0, 1.0,
                                np.array([0.1, 0.0, 0.0]))
    assert chord == pytest.approx(0.1 * np.sqrt(2))
    assert module.camera_chord(times, positions, rotations, 0.0, 1.0,
                               np.zeros(3)) == pytest.approx(0.0)
    assert module.camera_chord(times, positions, rotations, -0.1, 1.0,
                               np.zeros(3)) is None


def test_camera_chord_vector_uses_first_camera_axes_and_rotating_lever():
    times = np.array([0.0, 1.0])
    positions = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    rotations = Rotation.from_euler("z", [0, 90], degrees=True)
    body_t_camera = np.eye(4)
    body_t_camera[:3, :3] = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    body_t_camera[:3, 3] = [0.1, 0.0, 0.0]

    vector = module.camera_chord_vector(
        times, positions, rotations, 0.0, 1.0, body_t_camera)

    np.testing.assert_allclose(vector, [1.1, 0.1, 0.0], atol=1e-12)
    assert np.linalg.norm(vector) == pytest.approx(module.camera_chord(
        times, positions, rotations, 0.0, 1.0, body_t_camera[:3, 3]))
    assert module.camera_chord_vector(
        times, positions, rotations, -0.1, 1.0, body_t_camera) is None


def test_compare_rejects_stereo_report_with_external_supervision():
    with pytest.raises(ValueError, match="onboard stereo report"):
        module.compare(
            {"external_reference_used": False},
            {"external_ground_truth_used": True},
            None, None, None, None, None, None)

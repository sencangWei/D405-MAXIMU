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

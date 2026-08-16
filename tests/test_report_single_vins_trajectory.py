import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from report_single_vins_trajectory import (
    dwell_closure_metrics,
    horizontal_rectangle_metrics,
)


def rotated_rectangle(width_m: float, height_m: float, angle_deg: float) -> np.ndarray:
    counts = (180, 35, 120, 65)
    corners = np.array(
        [
            [0.0, 0.0],
            [width_m, 0.0],
            [width_m, height_m],
            [0.0, height_m],
            [0.0, 0.0],
        ]
    )
    segments = []
    for index, count in enumerate(counts):
        alpha = np.linspace(0.0, 1.0, count, endpoint=False)
        segments.append(
            corners[index][None, :] * (1.0 - alpha[:, None])
            + corners[index + 1][None, :] * alpha[:, None]
        )
    xy = np.vstack(segments + [corners[:1]])
    angle = math.radians(angle_deg)
    rotation = np.array(
        [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]
    )
    return xy @ rotation.T


def test_horizontal_rectangle_metrics_are_rotation_and_speed_invariant():
    xy = rotated_rectangle(0.80, 0.60, 31.0)

    metrics = horizontal_rectangle_metrics(xy)

    assert np.allclose(metrics["robust_extent_cm"], [80.0, 60.0], atol=0.6)
    assert metrics["boundary_rms_mm"] < 0.2
    assert metrics["boundary_p95_mm"] < 0.2
    assert metrics["resampled_points"] == 1000


def test_dwell_closure_uses_static_window_median_and_reports_signed_axes():
    time_s = np.arange(0.0, 12.0, 0.1)
    points = np.zeros((len(time_s), 3))
    points[40:80, 0] = np.linspace(0.0, 0.5, 40)
    points[80:, 0] = 0.004
    points[80:, 1] = -0.003
    points[80:, 2] = 0.002
    points[-1] = [0.20, 0.20, 0.20]

    metrics = dwell_closure_metrics(time_s, points, window_s=3.0)

    assert metrics["start_samples"] == 31
    assert metrics["end_samples"] == 31
    assert np.allclose(metrics["delta_xyz_cm"], [0.4, -0.3, 0.2])
    assert math.isclose(metrics["distance_cm"], math.sqrt(0.29), rel_tol=1e-12)

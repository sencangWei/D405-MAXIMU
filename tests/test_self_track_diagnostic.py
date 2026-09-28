import importlib.util
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".planning" / "metric_window_bundle_20260928" / "diagnose_self_tracks.py"
spec = importlib.util.spec_from_file_location("diagnose_self_tracks", SCRIPT)
diag = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diag
spec.loader.exec_module(diag)


def test_polygon_membership_matches_existing_normalized_jaw_region():
    points = np.array([[160, 160], [160, 100], [80, 190], [250, 120]], dtype=float)
    inside = diag.inside_polygon(points, (200, 300))

    assert inside.tolist() == [True, False, False, False]
    np.testing.assert_allclose(
        diag.polygon_pixels((200, 300)),
        [[157, 126], [182, 126], [204, 200], [135, 200]],
        atol=1,
    )


def test_signed_ray_casting_handles_both_winding_orders_and_descending_edges(monkeypatch):
    polygon = np.array([[0.2, 0.2], [0.8, 0.2], [0.7, 0.8], [0.3, 0.8]])
    points = np.array([[50, 50], [24, 50], [76, 50], [50, 19], [50, 81]], dtype=float)
    monkeypatch.setattr(diag, "POLYGON_NORMALIZED", polygon)
    forward = diag.inside_polygon(points, (100, 100))
    monkeypatch.setattr(diag, "POLYGON_NORMALIZED", polygon[::-1])
    reverse = diag.inside_polygon(points, (100, 100))

    assert forward.tolist() == [True, False, False, False, False]
    assert reverse.tolist() == forward.tolist()


def test_real_jaw_near_edge_and_boundary_points_are_stable():
    shape = (200, 300)
    points = np.array([
        [160, 126],   # top boundary: excluded by half-open ray rule
        [160, 127],   # just below top edge inside
        [136, 199],   # near lower-left boundary inside
        [134, 199],   # outside lower-left boundary
        [204, 199],   # near lower-right boundary outside by half-open x test
        [203, 199],   # just inside lower-right boundary
    ], dtype=float)

    assert diag.inside_polygon(points, shape).tolist() == [False, True, True, False, False, True]


def test_summary_separates_source_and_endpoint_membership_and_motion():
    observations = np.zeros((2, 4, 4), dtype=float)
    observations[0, :, :2] = [[160, 160], [165, 170], [50, 50], [60, 60]]
    observations[-1, :, :2] = [[160, 160], [180, 175], [70, 50], [60, 90]]
    observations[0, :, 2] = observations[0, :, 0] - [20, 20, 30, 30]
    observations[-1, :, 2:] = observations[-1, :, :2] - [10, 0]
    valid = np.array([[True, True, True, True], [True, True, True, False]])
    data = {
        "accepted": True,
        "observations": observations,
        "valid": valid,
        "initial_points": np.array([[0, 0, 0.27], [0, 0, 0.28], [0, 0, 0.4], [0, 0, 0.41]]),
    }

    result = diag.summarize_tracks(data, (200, 300))

    assert result["source_inside_polygon_count"] == 2
    assert result["source_outside_polygon_count"] == 2
    assert result["endpoint_valid_count"] == 3
    assert result["inside_survival_fraction"] == 1.0
    assert result["outside_survival_fraction"] == 0.5
    assert result["median_left_pixel_motion_inside_source_mask_px"] is not None
    assert result["median_left_pixel_motion_outside_source_mask_px"] == 20.0
    assert result["median_source_depth_inside_m"] == 0.275
    assert result["median_source_disparity_outside_px"] == 30.0


def test_empty_groups_are_none_not_fake_zero():
    observations = np.zeros((2, 2, 4), dtype=float)
    observations[:, :, :2] = [[[160, 160], [165, 170]], [[160, 160], [165, 170]]]
    observations[:, :, 2] = observations[:, :, 0] - 20
    data = {
        "accepted": True,
        "observations": observations,
        "valid": np.ones((2, 2), dtype=bool),
        "initial_points": np.array([[0, 0, 0.27], [0, 0, 0.28]]),
    }

    result = diag.summarize_tracks(data, (200, 300))

    assert result["source_outside_polygon_count"] == 0
    assert result["outside_survival_fraction"] is None
    assert result["median_left_pixel_motion_outside_source_mask_px"] is None
    assert result["inside_to_outside_source_count_ratio"] is None


def test_refusal_is_retained_without_track_metrics():
    result = diag.summarize_tracks({"accepted": False, "reason": "insufficient_persistent_stereo_tracks"}, (200, 300))

    assert result == {"accepted": False, "reason": "insufficient_persistent_stereo_tracks"}

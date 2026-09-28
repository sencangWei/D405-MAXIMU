import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "seam_observations", ROOT / "scripts" / "prepare_seam_stereo_observations.py"
)
seam = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seam)


def calibration():
    camera = dict(fx=300.0, fy=300.0, cx=160.0, cy=90.0, coeffs=[0.0] * 5)
    return dict(left_intrinsics=camera, right_intrinsics=camera.copy(), baseline_m=0.018)


def blank_frames(count=41):
    frames = []
    for index in range(count):
        image = np.zeros((180, 320), dtype=np.uint8)
        image[0, 0] = index
        frames.append(image)
    return frames


def grid_points(count=25):
    pts = [(x, y) for y in range(50, 150, 20) for x in range(70, 170, 20)]
    return np.asarray(pts[:count], dtype=float)


def install_mock_tracking(monkeypatch, points, invalid_stereo=None, flow_drop=None, flow_drop_all=None):
    points = np.asarray(points, dtype=np.float32)
    invalid_stereo = set(invalid_stereo or [])
    flow_drop = set(flow_drop or [])
    flow_drop_all = set(flow_drop_all or [])
    def good_features(image, **kwargs):
        mask = kwargs.get("mask")
        admitted = []
        for point in points:
            x, y = np.rint(point).astype(int)
            if mask is None or mask[y, x] > 0:
                admitted.append(point)
        if not admitted:
            return None
        return np.asarray(admitted, dtype=np.float32).reshape(-1, 1, 2)

    def disparity(left, right, count):
        left_disp = np.full(left.shape, 18.0, np.float32)
        right_disp = np.full(left.shape, -18.0, np.float32)
        left_disp[0, 0] = float(left[0, 0])
        return left_disp, right_disp

    def consistent(query, left_disp, right_disp, tolerance_px):
        frame_index = int(left_disp[0, 0])
        valid = np.ones(len(query), dtype=bool)
        disparity_values = np.full(len(query), 18.0)
        if frame_index in invalid_stereo:
            valid[0] = False
            disparity_values[0] = np.nan
        return valid, disparity_values

    def flow(source, target, query, guess=None):
        frame_index = int(target[0, 0])
        valid = np.ones(len(query), dtype=bool)
        if frame_index in flow_drop_all:
            valid[:] = False
        if frame_index in flow_drop:
            valid[0] = False
        return (query.copy() if guess is None else guess.copy()), valid

    monkeypatch.setattr(seam.cv2, "goodFeaturesToTrack", good_features)
    monkeypatch.setattr(seam, "stereo_disparity", disparity)
    monkeypatch.setattr(seam, "left_right_consistent", consistent)
    monkeypatch.setattr(seam, "_flow", flow)


def test_tracks_exact_identity_backward_and_forward_from_one_seam_observation(monkeypatch):
    left = blank_frames()
    right = blank_frames()
    points = grid_points()
    install_mock_tracking(monkeypatch, points)

    result = seam.track_seam_stereo_window(left, right, calibration())

    assert result["accepted"]
    assert result["observations"].shape == (41, 25, 4)
    assert result["valid"].all()
    np.testing.assert_allclose(result["observations"][20, :, :2], points)
    np.testing.assert_allclose(result["observations"][20, :, 2], points[:, 0] - 18.0)
    np.testing.assert_allclose(np.median(result["initial_points"][:, 2]), 0.3)
    assert result["source_indices"]["seam_index"] == 20
    assert result["source_indices"]["tracked_backward_frames"] == 20
    assert result["source_indices"]["tracked_forward_frames"] == 20


def test_backward_and_forward_temporal_loss_are_independent(monkeypatch):
    left = blank_frames()
    right = blank_frames()
    points = grid_points()
    install_mock_tracking(monkeypatch, points, flow_drop={18, 22})
    original_flow = seam._flow

    def flow_with_frame(source, target, query, guess=None):
        return original_flow(source, target, query, guess)

    monkeypatch.setattr(seam, "_flow", flow_with_frame)
    result = seam.track_seam_stereo_window(left, right, calibration())

    assert result["accepted"]
    assert not result["valid"][18, 0]
    assert not result["valid"][17, 0]
    assert result["valid"][19, 0]
    assert not result["valid"][22, 0]
    assert not result["valid"][23, 0]
    assert result["valid"][21, 0]
    assert result["valid"][20, 0]


def test_invalid_stereo_masks_point_but_keeps_if_has_pre_and_post(monkeypatch):
    left = blank_frames()
    right = blank_frames()
    points = grid_points()
    install_mock_tracking(monkeypatch, points, invalid_stereo={10, 30})

    result = seam.track_seam_stereo_window(left, right, calibration())

    assert result["accepted"]
    assert not result["valid"][10, 0]
    assert not result["valid"][30, 0]
    assert result["valid"][:20, 0].any()
    assert result["valid"][21:, 0].any()


def test_refuses_without_pre_and_post_support_or_geometry(monkeypatch):
    left = blank_frames()
    right = blank_frames()
    points = grid_points()
    install_mock_tracking(monkeypatch, points, flow_drop_all=set(range(21, 41)))

    result = seam.track_seam_stereo_window(left, right, calibration())

    assert not result["accepted"]
    assert result["reason"] == "insufficient_pre_post_seam_tracks"


def test_non_mutating_inputs_and_calibration_guards(monkeypatch):
    left = blank_frames()
    right = blank_frames()
    left_before = [image.copy() for image in left]
    right_before = [image.copy() for image in right]
    points = grid_points()
    install_mock_tracking(monkeypatch, points)

    result = seam.track_seam_stereo_window(left, right, calibration())

    assert result["accepted"]
    for before, after in zip(left_before, left):
        np.testing.assert_array_equal(before, after)
    for before, after in zip(right_before, right):
        np.testing.assert_array_equal(before, after)
    bad = calibration()
    bad["right_intrinsics"]["fx"] += 1.0
    with pytest.raises(ValueError, match="equal rectified"):
        seam.track_seam_stereo_window(left, right, bad)


def test_excluded_left_points_are_masked_before_gftt(monkeypatch):
    left = blank_frames()
    right = blank_frames()
    points = grid_points()
    install_mock_tracking(monkeypatch, points)

    result = seam.track_seam_stereo_window(
        left, right, calibration(), excluded_left_points=np.array([points[0]])
    )

    assert result["accepted"]
    assert result["tracked_landmarks"] == 24
    assert result["source_indices"]["feature_indices"] == list(range(24))
    assert result["exclusion"]["radius_px"] == 7
    assert result["exclusion"]["excluded_left_points"] == 1
    assert result["exclusion"]["candidate_mask_pixels_after_exclusion"] > 0
    assert not np.any(np.all(np.isclose(result["observations"][20, :, :2], points[0]), axis=1))


def test_refuses_when_seam_source_features_below_twenty(monkeypatch):
    left = blank_frames()
    right = blank_frames()
    install_mock_tracking(monkeypatch, grid_points(19))

    result = seam.track_seam_stereo_window(left, right, calibration())

    assert not result["accepted"]
    assert result["reason"] == "insufficient_seam_stereo_features"

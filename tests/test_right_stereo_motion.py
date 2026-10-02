from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ego_vio.vio.right_stereo_motion import (
    MIRROR,
    estimate_right_motion_from_correspondences,
)


def calibration(width=160, height=120, baseline=0.018083254, transform_baseline=None):
    intrinsics = {
        "width": width,
        "height": height,
        "fx": 120.0,
        "fy": 118.0,
        "cx": width / 2.0 - 3.0,
        "cy": height / 2.0 + 2.0,
        "coeffs": [0.0, 0.0, 0.0, 0.0],
    }
    return {
        "left": dict(intrinsics),
        "right": dict(intrinsics),
        "baseline_m": baseline,
        "right_rotation_from_left": np.eye(3),
        "right_translation_from_left_m": np.array(
            [-(baseline if transform_baseline is None else transform_baseline), 0.0, 0.0]
        ),
    }


def project(points, intrinsics):
    return np.column_stack(
        (
            intrinsics["fx"] * points[:, 0] / points[:, 2] + intrinsics["cx"],
            intrinsics["fy"] * points[:, 1] / points[:, 2] + intrinsics["cy"],
        )
    ).astype(np.float32)


def synthetic_case():
    calib = calibration()
    right = calib["right"]
    width, height = right["width"], right["height"]
    rotation = Rotation.from_euler("zyx", [8.0, -5.0, 4.0], degrees=True)
    displacement = np.array([0.036, -0.007, 0.017])
    pose_i = Rotation.from_euler("xyz", [11.0, -7.0, 5.0], degrees=True)
    pose_j = pose_i * rotation.inv()
    position_i = np.array([0.22, -0.05, 0.13])
    position_j = position_i + pose_i.apply(displacement)
    translation = -rotation.apply(displacement)
    candidates = []
    for yi in range(24, 95, 10):
        for xi in range(34, 126, 10):
            disparity_px = 3 + ((xi + yi) % 5)
            z = right["fx"] * calib["baseline_m"] / disparity_px
            x = (xi - right["cx"]) * z / right["fx"]
            y = (yi - right["cy"]) * z / right["fy"]
            candidates.append((xi, yi, disparity_px, [x, y, z]))
    points_i = np.asarray([point for *_unused, point in candidates], dtype=float)
    points_j = rotation.apply(points_i) + translation
    uv_i = project(points_i, calib["right"])
    uv_j = project(points_j, calib["right"])
    keep = (
        (uv_j[:, 0] >= 2.0)
        & (uv_j[:, 0] < width - 2.0)
        & (uv_j[:, 1] >= 2.0)
        & (uv_j[:, 1] < height - 2.0)
    )
    assert int(np.count_nonzero(keep)) >= 30
    candidates = [candidate for candidate, is_kept in zip(candidates, keep) if is_kept]
    points_i = points_i[keep]
    uv_i = uv_i[keep]
    uv_j = uv_j[keep]
    rounded_right = [tuple(np.rint(uv).astype(int)) for uv in uv_i]
    assert len(set(rounded_right)) == len(rounded_right)
    disparity_left = np.zeros((height, width), dtype=np.float32)
    disparity_right = np.zeros((height, width), dtype=np.float32)
    rounded_left = []
    for xi, yi, disparity_px, _point in candidates:
        rx, ry = int(xi), int(yi)
        lx = rx + int(disparity_px)
        rounded_left.append((lx, ry))
        assert 0 <= lx < width
        disparity_right[ry, rx] = -float(disparity_px)
        disparity_left[ry, lx] = float(disparity_px)
    assert len(set(rounded_left)) == len(rounded_left)
    return (
        calib,
        uv_i,
        uv_j,
        disparity_left,
        disparity_right,
        displacement,
        rotation,
        position_i,
        position_j,
        pose_i,
        pose_j,
    )


def run_estimate(*, refine_pnp=False, disparity_sign=-1.0):
    (
        calib,
        uv_i,
        uv_j,
        dl,
        dr,
        displacement,
        rotation,
        position_i,
        position_j,
        pose_i,
        pose_j,
    ) = synthetic_case()
    result = estimate_right_motion_from_correspondences(
        uv_i,
        uv_j,
        np.ones(len(uv_i), dtype=bool),
        dl,
        dr * (-disparity_sign),
        position_i,
        position_j,
        pose_i,
        pose_j,
        calib,
        0.05,
        2.0,
        "sift",
        pnp_iterations=1000,
        pnp_reprojection_error_px=1.0,
        refine_pnp=refine_pnp,
    )
    return result, displacement, rotation


def test_right_native_pnp_recovers_noncommuting_motion():
    result, displacement, rotation = run_estimate(refine_pnp=False)
    assert result["accepted"] is True
    assert result["metric_displacement_frame"] == "infrared_right_camera_i"
    assert result["right_centric_motion_source"] == "independent_right_pixels_negative_disparity"
    np.testing.assert_allclose(result["metric_displacement_camera_i_m"], displacement, atol=1e-4)
    restored_rotation = Rotation.from_quat(result["pnp_rotation_quaternion_xyzw"])
    assert (restored_rotation.inv() * rotation).magnitude() < 1e-3
    assert result["scale"] == pytest.approx(1.0, abs=1e-3)


def test_lm_refine_path_also_preserves_right_frame():
    result, displacement, _rotation = run_estimate(refine_pnp=True)
    assert result["accepted"] is True
    assert result["pnp_refined"] is True
    np.testing.assert_allclose(result["metric_displacement_camera_i_m"], displacement, atol=1e-4)


def test_positive_right_disparity_sign_is_rejected_without_reason_rewrite():
    result, _displacement, _rotation = run_estimate(disparity_sign=1.0)
    assert result["accepted"] is False
    assert result["reason"] == "insufficient_consistent_points"
    assert result["source_provenance"]["right_pixels"] is True


def test_mismatched_disparity_shapes_are_invalid():
    calib, uv_i, uv_j, dl, dr, *_unused = synthetic_case()
    with pytest.raises(ValueError, match="matching 2D"):
        estimate_right_motion_from_correspondences(
            uv_i,
            uv_j,
            np.ones(len(uv_i), dtype=bool),
            dl[:, :-1],
            dr,
            np.zeros(3),
            np.ones(3),
            Rotation.identity(),
            Rotation.identity(),
            calib,
            0.05,
            2.0,
            "sift",
        )


def test_calibration_and_inputs_are_not_mutated():
    calib, uv_i, uv_j, dl, dr, *_unused = synthetic_case()
    original_calib = {
        key: (value.copy() if isinstance(value, dict) else np.array(value).copy())
        for key, value in calib.items()
    }
    dl_before = dl.copy()
    dr_before = dr.copy()
    estimate_right_motion_from_correspondences(
        uv_i,
        uv_j,
        np.ones(len(uv_i), dtype=bool),
        dl,
        dr,
        np.zeros(3),
        np.ones(3),
        Rotation.identity(),
        Rotation.identity(),
        calib,
        0.05,
        2.0,
        "sift",
    )
    np.testing.assert_array_equal(dl, dl_before)
    np.testing.assert_array_equal(dr, dr_before)
    assert calib["right"]["cx"] == original_calib["right"]["cx"]
    np.testing.assert_allclose(calib["right_translation_from_left_m"], original_calib["right_translation_from_left_m"])


def test_non_rectified_factory_calibration_rejected():
    calib, uv_i, uv_j, dl, dr, *_unused = synthetic_case()
    calib["right_rotation_from_left"] = Rotation.from_euler("z", 0.1, degrees=True).as_matrix()
    with pytest.raises(ValueError, match="rectified factory rotation"):
        estimate_right_motion_from_correspondences(
            uv_i,
            uv_j,
            np.ones(len(uv_i), dtype=bool),
            dl,
            dr,
            np.zeros(3),
            np.ones(3),
            Rotation.identity(),
            Rotation.identity(),
            calib,
            0.05,
            2.0,
            "sift",
        )


def test_real_factory_precision_layout_is_accepted():
    (
        calib,
        uv_i,
        uv_j,
        dl,
        dr,
        _displacement,
        _rotation,
        position_i,
        position_j,
        pose_i,
        pose_j,
    ) = synthetic_case()
    calib["baseline_m"] = 0.018083254
    calib["right_translation_from_left_m"] = np.array([-0.018083, 0.0, 0.0])
    result = estimate_right_motion_from_correspondences(
        uv_i,
        uv_j,
        np.ones(len(uv_i), dtype=bool),
        dl,
        dr,
        position_i,
        position_j,
        pose_i,
        pose_j,
        calib,
        0.05,
        2.0,
        "sift",
        pnp_iterations=1000,
        pnp_reprojection_error_px=0.5,
    )
    assert result["accepted"] is True


def test_right_focal_length_must_be_positive():
    calib, uv_i, uv_j, dl, dr, *_unused = synthetic_case()
    calib["right"]["fx"] = 0.0
    with pytest.raises(ValueError, match="fx/fy must be positive"):
        estimate_right_motion_from_correspondences(
            uv_i,
            uv_j,
            np.ones(len(uv_i), dtype=bool),
            dl,
            dr,
            np.zeros(3),
            np.ones(3),
            Rotation.identity(),
            Rotation.identity(),
            calib,
            0.05,
            2.0,
            "sift",
        )


def test_disparity_shape_must_match_calibrated_dimensions():
    calib, uv_i, uv_j, dl, dr, *_unused = synthetic_case()
    calib["right"]["height"] += 1
    with pytest.raises(ValueError, match="calibrated camera dimensions"):
        estimate_right_motion_from_correspondences(
            uv_i,
            uv_j,
            np.ones(len(uv_i), dtype=bool),
            dl,
            dr,
            np.zeros(3),
            np.ones(3),
            Rotation.identity(),
            Rotation.identity(),
            calib,
            0.05,
            2.0,
            "sift",
        )


def test_nonfinite_distortion_coefficients_rejected():
    calib, uv_i, uv_j, dl, dr, *_unused = synthetic_case()
    calib["right"]["coeffs"] = [0.0, np.nan, 0.0, 0.0]
    with pytest.raises(ValueError, match="zero distortion coefficients"):
        estimate_right_motion_from_correspondences(
            uv_i,
            uv_j,
            np.ones(len(uv_i), dtype=bool),
            dl,
            dr,
            np.zeros(3),
            np.ones(3),
            Rotation.identity(),
            Rotation.identity(),
            calib,
            0.05,
            2.0,
            "sift",
        )


def test_mirror_rotation_is_proper():
    rotation = Rotation.from_euler("xyz", [10, 20, -5], degrees=True)
    mirrored = MIRROR @ rotation.as_matrix() @ MIRROR
    assert np.linalg.det(mirrored) == pytest.approx(1.0)

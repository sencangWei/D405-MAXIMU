import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".planning/metric_window_bundle_20260928/frontend_geometry_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("frontend_geometry_diagnostics", MODULE_PATH)
diag = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = diag
SPEC.loader.exec_module(diag)


def _project(K, xyz):
    uv = xyz[:, :2] / xyz[:, 2:3]
    return np.column_stack((K[0, 0] * uv[:, 0] + K[0, 2], K[1, 1] * uv[:, 1] + K[1, 2]))


def _capture(n=40, *, alpha_current=2.0, alpha_keyframe=4.0, rotation=None, translation=None):
    K = np.array([[220.0, 0.0, 320.0], [0.0, 220.0, 240.0], [0.0, 0.0, 1.0]])
    xs = np.linspace(-0.08, 0.08, n)
    ys = 0.03 * np.sin(np.linspace(0.0, 2.0 * np.pi, n))
    zs = np.linspace(0.28, 0.55, n)
    current_m = np.column_stack((xs, ys, zs))
    R = Rotation.from_euler("zyx", [5.0, -2.0, 1.0], degrees=True) if rotation is None else rotation
    t = np.array([0.035, -0.012, 0.045]) if translation is None else np.asarray(translation, dtype=float)
    key_m = R.apply(current_m) + t
    Xf = current_m / alpha_current
    Xk = key_m / alpha_keyframe
    scale = alpha_current / alpha_keyframe
    t_learned = t / alpha_keyframe
    return {
        "Xf": Xf,
        "Xk": Xk,
        "valid": np.ones(n, dtype=bool),
        "pixel_current": _project(K, current_m),
        "pixel_keyframe": _project(K, key_m),
        "depth_current_m": current_m[:, 2],
        "depth_keyframe_m": key_m[:, 2],
        "K": K,
        "T_pre": np.r_[t_learned, R.as_quat(), scale],
        "T_post": np.r_[t_learned, R.as_quat(), scale],
        "quat_current_xyzw": R.as_quat(),
        "quat_keyframe_xyzw": Rotation.identity().as_quat(),
    }


def test_exact_known_sim3_and_stereo_geometry():
    out = diag.summarize_geometry(_capture())
    assert out["optimization_run"] is False
    assert out["visual_residuals"]["pre"]["pixel_residual_norm_px"]["max"] < 1e-10
    assert abs(out["visual_residuals"]["pre"]["logdepth_residual"]["max"]) < 1e-12
    assert out["relative_rotation_vs_imu"]["pre"]["visual_vs_imu_deg"] < 1e-12
    metric = out["common_metric_geometry"]["by_transform"]["pre"]
    assert metric["learned_minus_stereo_median_norm_m"] < 1e-12
    assert abs(metric["sim3_metric_scale_consistency"] - 1.0) < 1e-12


def test_wrong_rotation_and_translation_are_visible():
    cap = _capture()
    wrong_R = Rotation.from_euler("z", 11.0, degrees=True)
    cap["T_post"] = np.r_[np.array([0.2, 0.1, -0.05]), wrong_R.as_quat(), cap["T_pre"][7]]
    out = diag.summarize_geometry(cap)
    assert out["relative_rotation_vs_imu"]["post"]["visual_vs_imu_deg"] > 5.0
    assert out["visual_residuals"]["post"]["pixel_residual_norm_px"]["median"] > 20.0
    assert out["common_metric_geometry"]["by_transform"]["post"]["learned_minus_stereo_median_norm_m"] > 0.1


def test_antipodal_quaternion_is_same_rotation():
    cap = _capture()
    cap["T_pre"][3:7] *= -1.0
    cap["quat_current_xyzw"] *= -1.0
    out = diag.summarize_geometry(cap)
    assert out["relative_rotation_vs_imu"]["pre"]["visual_vs_imu_deg"] < 1e-12
    assert out["visual_residuals"]["pre"]["pixel_residual_norm_px"]["max"] < 1e-10


def test_learned_unit_gauge_rescale_keeps_metric_translation_comparison():
    cap = _capture()
    base = diag.summarize_geometry(cap)["common_metric_geometry"]["by_transform"]["pre"]
    beta = 3.5
    cap["Xf"] = cap["Xf"] * beta
    cap["Xk"] = cap["Xk"] * beta
    cap["T_pre"][:3] *= beta
    cap["T_post"][:3] *= beta
    out = diag.summarize_geometry(cap)["common_metric_geometry"]["by_transform"]["pre"]
    np.testing.assert_allclose(out["learned_translation_keyframe_metric_m"], base["learned_translation_keyframe_metric_m"], atol=1e-12)
    assert out["learned_minus_stereo_median_norm_m"] < 1e-12


def test_missing_depth_returns_unknown_not_zero():
    cap = _capture()
    cap["depth_keyframe_m"][:25] = np.nan
    out = diag.summarize_geometry(cap)
    metric = out["common_metric_geometry"]
    assert metric["status"] == "UNKNOWN"
    assert metric["count"] == 15
    assert "by_transform" not in metric


def test_malformed_shapes_nan_and_nonpositive_sim3_fail_closed():
    cap = _capture()
    cap["Xk"] = cap["Xk"][:, :2]
    with pytest.raises(ValueError, match="Xk shape"):
        diag.summarize_geometry(cap)

    cap = _capture()
    cap["T_pre"][0] = np.nan
    with pytest.raises(ValueError, match="T_pre"):
        diag.summarize_geometry(cap)

    cap = _capture()
    cap["T_pre"][7] = 0.0
    with pytest.raises(ValueError, match="nonpositive"):
        diag.summarize_geometry(cap)


def test_valid_must_be_strict_bool_not_float_cast():
    cap = _capture()
    cap["valid"] = cap["valid"].astype(float)
    with pytest.raises(ValueError, match="strict bool"):
        diag.summarize_geometry(cap)


def test_project_calib_border_filter_only_affects_visual_not_common_metric():
    cap = _capture(n=40)
    cap["image_shape"] = [480, 640]
    cap["pixel_border"] = 5.0
    cap["depth_eps"] = 1e-6
    cap["T_post"] = cap["T_pre"].copy()
    cap["T_post"][0] += 10.0
    out = diag.summarize_geometry(cap)
    assert out["visual_residuals"]["pre"]["status"] == "OK"
    assert out["visual_residuals"]["post"]["status"] == "UNKNOWN"
    assert out["visual_residuals"]["post"]["candidate_count"] == 40
    assert out["common_metric_geometry"]["status"] == "OK"
    assert out["common_metric_geometry"]["count"] == 40


def test_depth_eps_excludes_visual_nonprojectable_but_not_positive_stereo_common_metric():
    cap = _capture(n=40)
    cap["image_shape"] = [480, 640]
    cap["depth_eps"] = 0.1
    out = diag.summarize_geometry(cap)
    assert out["visual_residuals"]["pre"]["count"] < 40
    assert out["visual_residuals"]["pre"]["projection_filter"]["depth_eps"] == 0.1
    assert out["visual_residuals"]["pre"]["residual_sign"].startswith("predicted_minus_measured")
    assert out["common_metric_geometry"]["count"] == 40


def test_optional_final_transform_and_learned_map_update_depth_ratio():
    cap = _capture()
    cap["T_final"] = cap["T_pre"].copy()
    cap["Xk_after"] = cap["Xk"].copy()
    cap["Xk_after"][:, 2] *= 1.1
    out = diag.summarize_geometry(cap)
    assert out["relative_rotation_vs_imu"]["final"]["visual_vs_imu_deg"] < 1e-12
    assert abs(out["learned_map_update"]["Xk_after_z_over_Xk_z"]["median"] - 1.1) < 1e-12
    assert out["learned_map_update"]["scope"].startswith("learned keyframe")


def test_metric_translation_median_is_robust_to_directional_depth_outliers():
    cap = _capture()
    cap["depth_keyframe_m"][:3] += np.array([0.25, -0.12, 0.18])
    out = diag.summarize_geometry(cap)
    metric = out["common_metric_geometry"]["by_transform"]["pre"]
    assert metric["learned_minus_stereo_median_norm_m"] < 0.01
    assert metric["fixed_rotation_translation_residual_norm_m"]["p95"] > metric["fixed_rotation_translation_residual_norm_m"]["median"]

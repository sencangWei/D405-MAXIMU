import importlib.util
from pathlib import Path

import numpy as np
import pytest


PATH = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928/map_depth_propagation.py"
SPEC = importlib.util.spec_from_file_location("map_depth_propagation", PATH)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


def sample(count=40):
    pre = np.column_stack((np.linspace(-.1, .1, count), np.zeros(count), np.linspace(.3, .6, count)))
    return dict(Xk=pre, Xk_after=pre.copy(), keyframe_pixel_ids=np.arange(count),
                depth_keyframe_m=pre[:, 2].copy(), valid=np.ones(count, bool))


def test_uniform_scale_is_separated_from_shape():
    a = sample()
    a["Xk_after"] *= 1.25
    r = diagnostic.compare_update(a)
    assert r["uniform_log_depth_change"] == pytest.approx(np.log(1.25))
    assert r["centered_log_depth_shape_after"]["max"] < 1e-12
    assert r["fixed_pre_scale_update_norm_mm"]["median"] > 100


def test_local_shape_deformation_and_canonical_gauge_invariance():
    a = sample()
    a["Xk_after"][10:20] *= 1.1
    first = diagnostic.compare_update(a)
    assert first["centered_log_depth_shape_after"]["p95"] > .09
    assert first["centered_log_depth_shape_before"]["max"] == 0
    a["Xk"] *= 7
    a["Xk_after"] *= 7
    changed = diagnostic.compare_update(a)
    assert changed["fixed_pre_scale_update_norm_mm"]["p95"] == pytest.approx(first["fixed_pre_scale_update_norm_mm"]["p95"])
    assert changed["centered_log_depth_shape_after"]["p95"] == pytest.approx(first["centered_log_depth_shape_after"]["p95"])


def test_continuity_binds_pixel_ids_not_row_order_and_retains_float_xyz():
    a, b = sample(), sample()
    b["keyframe_pixel_ids"] = b["keyframe_pixel_ids"][::-1]
    b["Xk"] = b["Xk"][::-1]
    b["Xk_after"] = b["Xk_after"][::-1]
    b["Xk"][:, 0] += 1e-8
    result = diagnostic.compare_continuity(a, b, 10, 10)
    assert result["count"] == 40
    assert result["absolute_z_delta_learned_units"]["max"] == 0
    assert result["xyz_delta_learned_units"]["max"] == pytest.approx(1e-8)
    assert diagnostic.compare_continuity(a, b, 10, 11)["status"] == "UNKNOWN"


def test_missing_stereo_or_sampled_intersection_is_unknown():
    a, b = sample(), sample()
    a["depth_keyframe_m"][:] = np.nan
    assert diagnostic.compare_update(a)["status"] == "UNKNOWN"
    b["keyframe_pixel_ids"] += 100
    assert diagnostic.compare_continuity(a, b, 1, 1)["status"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ["duplicate", "valid", "shape"])
def test_invalid_capture_rejected(fault):
    a = sample()
    if fault == "duplicate":
        a["keyframe_pixel_ids"][1] = 0
    elif fault == "valid":
        a["valid"] = np.ones(40)
    else:
        a["Xk_after"] = np.zeros((40, 2))
    with pytest.raises(ValueError):
        diagnostic.compare_update(a)

import importlib.util
from pathlib import Path
import sys
import json

import numpy as np
import pytest


DIRECTORY = Path(__file__).resolve().parents[1]/".planning/metric_window_bundle_20260928"
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("probe_map_and_right", DIRECTORY/"probe_map_and_right.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
sys.path.pop(0)


def fixture():
    cam = dict(width=1280, height=720, fx=649.207, fy=649.207, ppx=638.44, ppy=354.548, coeffs=[0]*5)
    meta = dict(camera_info=cam, stereo_depth_source=dict(right_camera_info=cam.copy(), left_focal_length_px=649.207, baseline_m=.018))
    pixels = np.array([[123, 85], [200, 100]])
    capture = dict(image_shape=np.array([288, 512]), K=np.array([[649.207/2.5, 0, 638.44/2.5], [0, 649.207/2.5, 354.548/2.5], [0, 0, 1]]),
                   pixel_keyframe=pixels, pixel_current=pixels+np.array([2, 0]),
                   depth_keyframe_m=np.array([.4, np.nan]), depth_current_m=np.array([.4, .3]), valid=np.ones(2, bool))
    return capture, meta


def test_exact_native_nn_lookup_and_factory_disparity_not_resized_depth_units():
    sample, meta = fixture()
    mask, key, current = probe.right_points(sample, meta)
    np.testing.assert_array_equal(mask, [True, False])
    assert key[0, 0] == pytest.approx(307-649.207*.018/.4, abs=1e-5)
    assert key[0, 1] == 212
    assert current[0, 0]-key[0, 0] == 5
    assert sample["pixel_keyframe"][0, 0] == 123


@pytest.mark.parametrize("fault", ["K", "baseline", "mask", "bounds"])
def test_bad_grid_or_metadata_fails_closed(fault):
    sample, meta = fixture()
    if fault == "K":
        sample["K"][0, 0] += 1
    elif fault == "baseline":
        meta["stereo_depth_source"]["baseline_m"] = -1
    elif fault == "mask":
        sample["valid"] = np.ones(2)
    else:
        sample["pixel_keyframe"][0, 0] = 512
    with pytest.raises(ValueError):
        probe.right_points(sample, meta)


def test_missing_depth_is_empty_unknown_support_not_fabricated_point():
    sample, meta = fixture()
    sample["depth_keyframe_m"][:] = np.nan
    _, key, current = probe.right_points(sample, meta)
    assert key.shape == current.shape == (0, 2)


def test_raw_nan_arrays_and_chain_masks_leave_only_json_safe_summary_without_mutating_input():
    original = dict(fb_closure_norm_px=[float("nan"), 2.],
                    backward_closed_key_points_right=[[float("nan"), 1.]],
                    forward_steps=[dict(label="forward_0_1", kept_count=1, opencv_success_mask=[True, False])])
    summary, raw = probe.split_raw_diagnostics(original)
    json.dumps(summary, allow_nan=False)
    assert np.isnan(raw["fb_closure_norm_px"][0])
    np.testing.assert_array_equal(raw["forward_steps_0_opencv_success_mask"], [True, False])
    assert "opencv_success_mask" not in summary["forward_steps"][0]
    assert "opencv_success_mask" in original["forward_steps"][0]
    assert "fb_closure_norm_px" in original

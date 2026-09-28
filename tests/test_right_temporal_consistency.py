import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".planning/metric_window_bundle_20260928/right_temporal_consistency.py"
SPEC = importlib.util.spec_from_file_location("right_temporal_consistency", MODULE_PATH)
rtc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rtc
SPEC.loader.exec_module(rtc)


def _textured_translation(shift=(3.0, -2.0)):
    height, width = 120, 160
    image = np.zeros((height, width), dtype=np.uint8)
    points = []
    for y in range(25, 96, 20):
        for x in range(25, 136, 20):
            cv2.circle(image, (x, y), 4, 180 + ((x + y) % 60), -1)
            cv2.line(image, (x - 5, y), (x + 5, y), 255, 1)
            cv2.line(image, (x, y - 5), (x, y + 5), 255, 1)
            points.append((float(x), float(y)))
    matrix = np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]]], dtype=np.float32)
    current = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=0)
    key_points = np.asarray(points, dtype=np.float32)
    expected = key_points + np.asarray(shift, dtype=np.float32)
    return image, current, key_points, expected


def test_exact_translated_texture_has_small_expected_and_closure_errors():
    key, current, points, expected = _textured_translation()
    out = rtc.summarize_right_temporal_consistency(key, current, points, expected)
    assert out["status"] == "OK"
    assert out["initial_flow_seed_used"] is False
    assert out["classification_threshold_used"] is False
    assert out["usable_expected_comparison_count"] == len(points)
    assert out["forward_vs_expected_norm_px_stats"]["median"] < 0.05
    assert out["fb_closure_norm_px_stats"]["median"] < 0.05


def test_deliberately_wrong_expected_points_show_discrepancy():
    key, current, points, expected = _textured_translation()
    wrong = expected + np.array([5.0, 0.0], dtype=np.float32)
    out = rtc.summarize_right_temporal_consistency(key, current, points, wrong)
    assert out["forward_vs_expected_norm_px_stats"]["median"] > 4.5
    assert out["fb_closure_norm_px_stats"]["median"] < 0.05


def test_occlusion_status_and_continuous_arrays_are_reported_without_threshold_class():
    key, current, points, expected = _textured_translation()
    current[:, :] = 0
    out = rtc.summarize_right_temporal_consistency(key, current, points, expected)
    assert out["status"] == "OK"
    assert out["forward_vs_expected_norm_px_stats"]["median"] > 10.0
    assert out["fb_closure_norm_px_stats"]["status"] == "UNKNOWN"
    assert len(out["fb_closure_norm_px"]) == len(points)
    assert out["classification_threshold_used"] is False


def test_opencv_status_masks_are_reported(monkeypatch):
    key, current, points, expected = _textured_translation()
    calls = []

    def fake_lk(source, target, start, *args, **kwargs):
        calls.append(1)
        out = np.asarray(start, dtype=np.float32).copy()
        status = np.ones((len(out), 1), dtype=np.uint8)
        status[::2] = 0
        if len(calls) == 1:
            out[:, 0, 0] += 3.0
            out[:, 0, 1] -= 2.0
        return out, status, None

    monkeypatch.setattr(rtc.cv2, "calcOpticalFlowPyrLK", fake_lk)
    out = rtc.summarize_right_temporal_consistency(key, current, points, expected)
    assert out["status"] == "OK"
    assert sum(out["opencv_forward_success_mask"]) == len(points) // 2
    assert sum(out["opencv_backward_success_mask"]) == len(points) // 4
    assert out["usable_expected_comparison_count"] == len(points) // 2


def test_backward_lk_only_receives_forward_status_finite_inbounds_points(monkeypatch):
    key = np.zeros((20, 20), dtype=np.uint8)
    current = key.copy()
    points = np.array([[2.0, 2.0], [4.0, 4.0], [6.0, 6.0], [8.0, 8.0]], dtype=np.float32)
    expected = points.copy()
    calls = []

    def fake_lk(source, target, start, *args, **kwargs):
        calls.append(np.asarray(start).reshape(-1, 2).copy())
        if len(calls) == 1:
            forward = np.array(
                [
                    [[3.0, 2.0]],
                    [[np.nan, 4.0]],
                    [[30.0, 6.0]],
                    [[9.0, 8.0]],
                ],
                dtype=np.float32,
            )
            status = np.array([[1], [1], [1], [0]], dtype=np.uint8)
            return forward, status, None
        assert calls[-1].shape == (1, 2)
        np.testing.assert_allclose(calls[-1][0], [3.0, 2.0])
        backward = np.array([[[2.0, 2.0]]], dtype=np.float32)
        status = np.array([[1]], dtype=np.uint8)
        return backward, status, None

    monkeypatch.setattr(rtc.cv2, "calcOpticalFlowPyrLK", fake_lk)
    out = rtc.summarize_right_temporal_consistency(key, current, points, expected)
    assert len(calls) == 2
    assert out["opencv_forward_success_mask"] == [True, True, True, False]
    assert out["finite_forward_mask"] == [True, False, True, True]
    assert out["forward_in_bounds_mask"] == [True, False, False, True]
    assert out["opencv_backward_success_mask"] == [True, False, False, False]
    assert out["usable_forward_backward_count"] == 1
    assert out["usable_expected_comparison_count"] == 1
    assert out["fb_closure_norm_px"][0] == 0.0
    assert np.isnan(out["forward_vs_expected_norm_px"][1])
    assert np.isnan(out["forward_vs_expected_norm_px"][2])
    assert np.isnan(out["forward_vs_expected_norm_px"][3])


def test_bounds_filter_prevents_invalid_points_reaching_opencv():
    key, current, points, expected = _textured_translation()
    points = points.copy()
    expected = expected.copy()
    points[0] = [-5.0, 20.0]
    expected[1] = [1000.0, 20.0]
    out = rtc.summarize_right_temporal_consistency(key, current, points, expected)
    assert out["native_bounds_count"] == len(points) - 2
    assert out["native_bounds_mask"][0] is False
    assert out["native_bounds_mask"][1] is False
    assert np.isnan(out["forward_vs_expected_norm_px"][0])
    assert np.isnan(out["forward_vs_expected_norm_px"][1])


def test_no_initial_flow_flag_is_used_for_forward_and_backward(monkeypatch):
    key, current, points, expected = _textured_translation()
    calls = []
    real = rtc.cv2.calcOpticalFlowPyrLK

    def wrapped(*args, **kwargs):
        calls.append(kwargs.get("flags"))
        return real(*args, **kwargs)

    monkeypatch.setattr(rtc.cv2, "calcOpticalFlowPyrLK", wrapped)
    out = rtc.summarize_right_temporal_consistency(key, current, points, expected)
    assert out["status"] == "OK"
    assert calls == [0, 0]


def test_malformed_inputs_fail_closed_and_unknown_cases():
    key, current, points, expected = _textured_translation()
    with pytest.raises(ValueError, match="uint8 grayscale"):
        rtc.summarize_right_temporal_consistency(key.astype(np.float32), current, points, expected)
    bad = points.copy()
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="nonfinite"):
        rtc.summarize_right_temporal_consistency(key, current, bad, expected)
    out = rtc.summarize_right_temporal_consistency(key, current, points[:0], expected[:0])
    assert out["status"] == "UNKNOWN"
    assert out["reason"] == "no_points"


def test_no_points_inside_native_bounds_returns_unknown_not_zero():
    key, current, points, expected = _textured_translation()
    points[:] = -10.0
    expected[:] = -10.0
    out = rtc.summarize_right_temporal_consistency(key, current, points, expected)
    assert out["status"] == "UNKNOWN"
    assert out["reason"] == "no_points_inside_native_bounds"
    assert out["native_bounds_count"] == 0

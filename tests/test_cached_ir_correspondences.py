import importlib.util
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from ego_vio.vio.cached_ir_correspondences import CachedIrSiftCorrespondences

spec = importlib.util.spec_from_file_location(
    "diagnose_independent_right_stereo",
    ROOT / "scripts/diagnose_independent_right_stereo.py",
)
diag = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diag
spec.loader.exec_module(diag)


def textured(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.zeros((160, 160), dtype=np.uint8)
    for _ in range(28):
        center = tuple(int(v) for v in rng.integers(12, 148, size=2))
        radius = int(rng.integers(3, 9))
        color = int(rng.integers(80, 255))
        cv2.circle(image, center, radius, color, -1)
    for _ in range(14):
        p0 = tuple(int(v) for v in rng.integers(0, 160, size=2))
        p1 = tuple(int(v) for v in rng.integers(0, 160, size=2))
        cv2.line(image, p0, p1, int(rng.integers(50, 220)), 1)
    return image


def mask_left_half() -> np.ndarray:
    mask = np.zeros((160, 160), dtype=bool)
    mask[:, :120] = True
    return mask


def assert_corr_equal(actual, expected):
    for got, want in zip(actual, expected):
        assert got.dtype == want.dtype
        assert got.shape == want.shape
        np.testing.assert_array_equal(got, want)


def test_cached_correspondences_match_raw_diagnostic_forward_reverse_and_multi_pair():
    images = [textured(1), textured(2), textured(3)]
    masks = [mask_left_half(), np.roll(mask_left_half(), 10, axis=1), np.ones((160, 160), dtype=bool)]
    cache = CachedIrSiftCorrespondences(max_feature_entries=16)

    for first, second in ((0, 1), (1, 0), (0, 2), (2, 1)):
        raw = diag.sift_correspondences(images[first], images[second], masks[first])
        cached = cache.correspondences(first, images[first], masks[first], second, images[second])
        assert_corr_equal(cached, raw)


def test_detect_and_compute_calls_decrease_for_repeated_targets_and_pairs(monkeypatch):
    import ego_vio.vio.cached_ir_correspondences as cached

    calls = {"detect": 0}

    class Detector:
        def detectAndCompute(self, _image, _mask):
            calls["detect"] += 1
            kp = [type("KP", (), {"pt": (float(calls["detect"]), float(calls["detect"] + 1))})()]
            return kp, np.full((1, 4), float(calls["detect"]), dtype=np.float32)

    class Matcher:
        def knnMatch(self, *_args, **_kwargs):
            a = type("M", (), {"distance": 0.1, "queryIdx": 0, "trainIdx": 0})()
            b = type("M", (), {"distance": 1.0, "queryIdx": 0, "trainIdx": 0})()
            return [[a, b]]

    monkeypatch.setattr(cached.cv2, "SIFT_create", lambda **_kwargs: Detector())
    monkeypatch.setattr(cached.cv2, "BFMatcher", lambda *_args, **_kwargs: Matcher())
    cache = CachedIrSiftCorrespondences(max_feature_entries=8)
    image0 = np.zeros((4, 4), dtype=np.uint8)
    image1 = np.ones((4, 4), dtype=np.uint8)
    image2 = 2 * np.ones((4, 4), dtype=np.uint8)
    mask = np.ones((4, 4), dtype=bool)

    cache.correspondences(0, image0, mask, 1, image1)
    cache.correspondences(2, image2, mask, 1, image1)
    cache.correspondences(0, image0, mask, 1, image1)

    assert calls["detect"] == 3  # masked frame 0, unmasked frame 1, masked frame 2
    assert cache.feature_entry_count == 3


def test_same_cache_key_rejects_different_image(monkeypatch):
    import ego_vio.vio.cached_ir_correspondences as cached

    class Detector:
        def detectAndCompute(self, *_args):
            return [type("KP", (), {"pt": (1.0, 2.0)})()], np.ones((1, 4), dtype=np.float32)

    class Matcher:
        def knnMatch(self, *_args, **_kwargs):
            a = type("M", (), {"distance": 0.1, "queryIdx": 0, "trainIdx": 0})()
            b = type("M", (), {"distance": 1.0, "queryIdx": 0, "trainIdx": 0})()
            return [[a, b]]

    monkeypatch.setattr(cached.cv2, "SIFT_create", lambda **_kwargs: Detector())
    monkeypatch.setattr(cached.cv2, "BFMatcher", lambda *_args, **_kwargs: Matcher())
    cache = CachedIrSiftCorrespondences(max_feature_entries=8)
    mask = np.ones((4, 4), dtype=bool)
    cache.correspondences(0, np.zeros((4, 4), dtype=np.uint8), mask, 1, np.ones((4, 4), dtype=np.uint8))
    with pytest.raises(ValueError, match="different image"):
        cache.correspondences(0, 7 * np.ones((4, 4), dtype=np.uint8), mask, 1, np.ones((4, 4), dtype=np.uint8))


def test_empty_and_single_knn_rows_match_raw_empty_behavior(monkeypatch):
    import ego_vio.vio.cached_ir_correspondences as cached

    class EmptyDetector:
        def detectAndCompute(self, *_args):
            return [], None

    monkeypatch.setattr(cached.cv2, "SIFT_create", lambda **_kwargs: EmptyDetector())
    cache = CachedIrSiftCorrespondences(max_feature_entries=8)
    pts_i, pts_j, valid = cache.correspondences(
        0, np.zeros((4, 4), dtype=np.uint8), np.ones((4, 4), dtype=bool), 1, np.ones((4, 4), dtype=np.uint8)
    )
    assert pts_i.shape == (0, 2)
    assert pts_j.shape == (0, 2)
    assert valid.shape == (0,)

    class Detector:
        def detectAndCompute(self, *_args):
            return [type("KP", (), {"pt": (1.0, 2.0)})()], np.ones((1, 4), dtype=np.float32)

    class OneNeighborMatcher:
        def knnMatch(self, *_args, **_kwargs):
            return [[type("M", (), {"distance": 0.1, "queryIdx": 0, "trainIdx": 0})()]]

    monkeypatch.setattr(cached.cv2, "SIFT_create", lambda **_kwargs: Detector())
    monkeypatch.setattr(cached.cv2, "BFMatcher", lambda *_args, **_kwargs: OneNeighborMatcher())
    cache = CachedIrSiftCorrespondences(max_feature_entries=8)
    pts_i, pts_j, valid = cache.correspondences(
        0, np.zeros((4, 4), dtype=np.uint8), np.ones((4, 4), dtype=bool), 1, np.ones((4, 4), dtype=np.uint8)
    )
    assert pts_i.shape == (0, 2)
    assert pts_j.shape == (0, 2)
    assert valid.shape == (0,)


def test_bounded_lru_evicts_old_feature_entries(monkeypatch):
    import ego_vio.vio.cached_ir_correspondences as cached

    class Detector:
        def detectAndCompute(self, *_args):
            return [type("KP", (), {"pt": (1.0, 2.0)})()], np.ones((1, 4), dtype=np.float32)

    class Matcher:
        def knnMatch(self, *_args, **_kwargs):
            a = type("M", (), {"distance": 0.1, "queryIdx": 0, "trainIdx": 0})()
            b = type("M", (), {"distance": 1.0, "queryIdx": 0, "trainIdx": 0})()
            return [[a, b]]

    monkeypatch.setattr(cached.cv2, "SIFT_create", lambda **_kwargs: Detector())
    monkeypatch.setattr(cached.cv2, "BFMatcher", lambda *_args, **_kwargs: Matcher())
    cache = CachedIrSiftCorrespondences(max_feature_entries=2)
    mask = np.ones((4, 4), dtype=bool)
    cache.correspondences(0, np.zeros((4, 4), dtype=np.uint8), mask, 1, np.ones((4, 4), dtype=np.uint8))
    cache.correspondences(2, 2 * np.ones((4, 4), dtype=np.uint8), mask, 3, 3 * np.ones((4, 4), dtype=np.uint8))
    assert cache.feature_entry_count == 2


def test_constructor_requires_explicit_positive_int_bound():
    for bad in (None, 0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive int"):
            CachedIrSiftCorrespondences(max_feature_entries=bad)  # type: ignore[arg-type]


def test_frame_image_guard_survives_eviction_and_crosses_roles(monkeypatch):
    import ego_vio.vio.cached_ir_correspondences as cached

    class Detector:
        def detectAndCompute(self, *_args):
            return [type("KP", (), {"pt": (1.0, 2.0)})()], np.ones((1, 4), dtype=np.float32)

    class Matcher:
        def knnMatch(self, *_args, **_kwargs):
            a = type("M", (), {"distance": 0.1, "queryIdx": 0, "trainIdx": 0})()
            b = type("M", (), {"distance": 1.0, "queryIdx": 0, "trainIdx": 0})()
            return [[a, b]]

    monkeypatch.setattr(cached.cv2, "SIFT_create", lambda **_kwargs: Detector())
    monkeypatch.setattr(cached.cv2, "BFMatcher", lambda *_args, **_kwargs: Matcher())
    mask = np.ones((4, 4), dtype=bool)

    cache = CachedIrSiftCorrespondences(max_feature_entries=8)
    cache.correspondences(0, np.zeros((4, 4), dtype=np.uint8), mask, 1, np.ones((4, 4), dtype=np.uint8))
    with pytest.raises(ValueError, match="frame index reused"):
        cache.correspondences(2, 2 * np.ones((4, 4), dtype=np.uint8), mask, 0, 7 * np.ones((4, 4), dtype=np.uint8))

    cache = CachedIrSiftCorrespondences(max_feature_entries=1)
    cache.correspondences(0, np.zeros((4, 4), dtype=np.uint8), mask, 1, np.ones((4, 4), dtype=np.uint8))
    cache.correspondences(2, 2 * np.ones((4, 4), dtype=np.uint8), mask, 3, 3 * np.ones((4, 4), dtype=np.uint8))
    assert cache.feature_entry_count == 1
    with pytest.raises(ValueError, match="frame index reused"):
        cache.correspondences(0, 9 * np.ones((4, 4), dtype=np.uint8), mask, 1, np.ones((4, 4), dtype=np.uint8))

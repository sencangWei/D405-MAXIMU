"""Deterministic per-record SIFT correspondence cache for IR stereo diagnostics.

This module only caches SIFT keypoints/descriptors.  It does not cache images,
disparities, PnP outputs, gates, or scoring state.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from typing import Literal

import cv2
import numpy as np


SIFT_PARAMS = {"nfeatures": 4000, "contrastThreshold": 0.01, "edgeThreshold": 15, "ratio": 0.75}
FeatureRole = Literal["masked_first", "unmasked_target"]


@dataclass(frozen=True)
class _FeatureEntry:
    image_sha256: str
    points_xy: np.ndarray
    descriptors: np.ndarray | None


def _array_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(contiguous.shape).encode("ascii"))
    digest.update(str(contiguous.dtype).encode("ascii"))
    digest.update(contiguous.view(np.uint8))
    return digest.hexdigest()


def _empty() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    empty = np.empty((0, 2), dtype=np.float32)
    return empty, empty, np.zeros(0, dtype=bool)


class CachedIrSiftCorrespondences:
    """Per-record bounded SIFT feature cache."""

    def __init__(self, *, max_feature_entries: int):
        if isinstance(max_feature_entries, bool) or not isinstance(max_feature_entries, int) or max_feature_entries < 1:
            raise ValueError("max_feature_entries must be a positive int")
        self.max_feature_entries = max_feature_entries
        self._features: OrderedDict[tuple[int, FeatureRole, str | None], _FeatureEntry] = OrderedDict()
        self._frame_image_sha256: dict[int, str] = {}

    @property
    def feature_entry_count(self) -> int:
        return len(self._features)

    def _detect(self, frame_index: int, image: np.ndarray, mask: np.ndarray | None, role: FeatureRole) -> _FeatureEntry:
        frame_index = int(frame_index)
        image_hash = _array_sha256(image)
        previous_image_hash = self._frame_image_sha256.setdefault(frame_index, image_hash)
        if previous_image_hash != image_hash:
            raise ValueError(f"frame index reused with different image: frame={frame_index}")
        mask_hash = _array_sha256(mask.astype(np.uint8)) if mask is not None else None
        key = (frame_index, role, mask_hash)
        if key in self._features:
            entry = self._features.pop(key)
            self._features[key] = entry
            return entry

        detector = cv2.SIFT_create(**{k: SIFT_PARAMS[k] for k in ("nfeatures", "contrastThreshold", "edgeThreshold")})
        cv_mask = None if mask is None else mask.astype(np.uint8) * 255
        keypoints, descriptors = detector.detectAndCompute(image, cv_mask)
        points = np.asarray([kp.pt for kp in keypoints], dtype=np.float32).reshape(-1, 2)
        entry = _FeatureEntry(image_hash, points, descriptors)
        self._features[key] = entry
        while len(self._features) > self.max_feature_entries:
            self._features.popitem(last=False)
        return entry

    def correspondences(
        self,
        first_frame_index: int,
        first_image: np.ndarray,
        first_mask: np.ndarray,
        second_frame_index: int,
        second_image: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        first = self._detect(first_frame_index, first_image, first_mask, "masked_first")
        second = self._detect(second_frame_index, second_image, None, "unmasked_target")
        if first.descriptors is None or second.descriptors is None:
            return _empty()
        good = [
            row[0]
            for row in cv2.BFMatcher(cv2.NORM_L2).knnMatch(first.descriptors, second.descriptors, k=2)
            if len(row) == 2 and row[0].distance < SIFT_PARAMS["ratio"] * row[1].distance
        ]
        return (
            np.asarray([first.points_xy[m.queryIdx] for m in good], dtype=np.float32).reshape(-1, 2),
            np.asarray([second.points_xy[m.trainIdx] for m in good], dtype=np.float32).reshape(-1, 2),
            np.ones(len(good), dtype=bool),
        )

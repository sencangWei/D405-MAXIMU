"""MASt3R training dataset for metric D405 left/right infrared pairs."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from mast3r.datasets.base.mast3r_base_stereo_view_dataset import (
    MASt3RBaseStereoViewDataset,
)


def low_observability_temporal_samples(
    scenes: list[dict],
    maximum_tracked_depth_points: int,
    minimum_angular_speed_deg_s: float,
) -> list[dict]:
    """Select labelled turns whose visual geometry is weak but still valid."""
    if maximum_tracked_depth_points <= 0:
        raise ValueError("maximum tracked depth points must be positive")
    if minimum_angular_speed_deg_s < 0.0:
        raise ValueError("minimum angular speed must be non-negative")
    return [
        sample
        for sample in scenes
        if int(sample["tracked_depth_points"]) <= maximum_tracked_depth_points
        and float(sample["angular_speed_deg_s"])
        >= minimum_angular_speed_deg_s
    ]


def low_observability_sample_weight(
    sample: dict,
    maximum_tracked_depth_points: int,
    minimum_angular_speed_deg_s: float,
    selected_weight: float,
) -> float:
    """Return a motion-loss weight without changing the sample distribution."""
    if selected_weight <= 0.0:
        raise ValueError("low observability loss weight must be positive")
    selected = low_observability_temporal_samples(
        [sample], maximum_tracked_depth_points, minimum_angular_speed_deg_s
    )
    return float(selected_weight if selected else 1.0)


def motion_blur_kernel(length: int, angle_deg: float) -> np.ndarray:
    length = max(1, int(length) | 1)
    kernel = np.zeros((length, length), dtype=np.float32)
    center = length // 2
    radians = np.deg2rad(angle_deg)
    dx = int(round(np.cos(radians) * center))
    dy = int(round(np.sin(radians) * center))
    cv2.line(kernel, (center - dx, center - dy), (center + dx, center + dy), 1.0, 1)
    total = float(kernel.sum())
    return kernel / total if total > 0 else np.eye(1, dtype=np.float32)


def augment_ir_pair(
    left: np.ndarray,
    right: np.ndarray,
    rng: np.random.Generator,
    blur_probability: float,
    maximum_blur_px: int,
) -> tuple[np.ndarray, np.ndarray]:
    if left.shape != right.shape or left.ndim != 2:
        raise ValueError("IR augmentation expects matching grayscale images")
    images = [left.astype(np.float32), right.astype(np.float32)]
    if rng.random() < blur_probability and maximum_blur_px >= 3:
        length = int(rng.integers(3, maximum_blur_px + 1)) | 1
        kernel = motion_blur_kernel(length, float(rng.uniform(0.0, 180.0)))
        images = [cv2.filter2D(image, -1, kernel) for image in images]
    gamma = float(rng.uniform(0.75, 1.35))
    common_gain = float(rng.uniform(0.8, 1.2))
    output = []
    for image in images:
        sensor_gain = common_gain * float(rng.uniform(0.94, 1.06))
        normalized = np.clip(image / 255.0, 0.0, 1.0) ** gamma
        noise = rng.normal(0.0, float(rng.uniform(0.0, 2.0)), image.shape)
        output.append(np.clip(255.0 * normalized * sensor_gain + noise, 0, 255).astype(np.uint8))
    return output[0], output[1]


class D405IRStereo(MASt3RBaseStereoViewDataset):
    def __init__(
        self,
        *args,
        manifest: str,
        split: str,
        high_motion_repeat: int = 3,
        blur_probability: float = 0.35,
        maximum_blur_px: int = 13,
        **kwargs,
    ):
        super().__init__(*args, split=split, **kwargs)
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
        if payload.get("external_ground_truth_used") is not False:
            raise ValueError("D405 training manifest must exclude external ground truth")
        if payload.get("result") != "READY":
            raise ValueError("D405 training depths are not materialized")
        scenes = [sample for sample in payload["samples"] if sample["split"] == split]
        if split == "train" and high_motion_repeat > 1:
            fast = [sample for sample in scenes if sample["motion_bin"] in ("fast", "very_fast")]
            scenes.extend(fast * (high_motion_repeat - 1))
        if not scenes:
            raise ValueError(f"no D405 samples for split {split}")
        self.scenes = scenes
        self.is_metric_scale = True
        self.blur_probability = float(blur_probability) if split == "train" else 0.0
        self.maximum_blur_px = int(maximum_blur_px)

    def _get_views(self, idx, resolution, rng):
        sample = self.scenes[idx]
        left = cv2.imread(sample["left_image"], cv2.IMREAD_GRAYSCALE)
        right = cv2.imread(sample["right_image"], cv2.IMREAD_GRAYSCALE)
        left_depth = cv2.imread(sample["depth_left"], cv2.IMREAD_UNCHANGED)
        right_depth = cv2.imread(sample["depth_right"], cv2.IMREAD_UNCHANGED)
        if any(value is None for value in (left, right, left_depth, right_depth)):
            raise FileNotFoundError(f"incomplete D405 sample: {sample['session_id']}/{sample['input_index']}")
        left, right = augment_ir_pair(
            left, right, rng, self.blur_probability, self.maximum_blur_px
        )
        intrinsics = np.asarray(sample["intrinsics"], dtype=np.float32)
        left_pose = np.eye(4, dtype=np.float32)
        right_pose = np.eye(4, dtype=np.float32)
        right_pose[0, 3] = float(sample["baseline_m"])
        views = []
        for side, image, depth, pose in (
            ("left", left, left_depth, left_pose),
            ("right", right, right_depth, right_pose),
        ):
            image = Image.fromarray(image).convert("RGB")
            depth_m = depth.astype(np.float32) * float(0.0001)
            views.append(
                {
                    "img": image,
                    "depthmap": depth_m.astype(np.float32),
                    "camera_pose": pose.copy(),
                    "camera_intrinsics": intrinsics.copy(),
                    "dataset": "D405IRStereo",
                    "label": sample["session_id"],
                    "instance": f"{sample['input_index']}:{side}",
                }
            )
        return views


class D405IRTemporal(MASt3RBaseStereoViewDataset):
    """Temporal left-IR pairs with stereo-depth and onboard-IMU pose labels."""

    def __init__(
        self,
        *args,
        manifest: str,
        split: str,
        high_motion_repeat: int = 3,
        low_observability_repeat: int = 1,
        low_observability_loss_weight: float = 1.0,
        low_observability_max_tracked_points: int = 160,
        low_observability_min_angular_speed_deg_s: float = 8.0,
        blur_probability: float = 0.35,
        maximum_blur_px: int = 13,
        **kwargs,
    ):
        super().__init__(*args, split=split, **kwargs)
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
        if payload.get("external_ground_truth_used") is not False:
            raise ValueError("D405 temporal manifest must exclude external ground truth")
        if payload.get("result") != "READY" or "temporal" not in payload.get("schema", ""):
            raise ValueError("D405 temporal training manifest is not ready")
        scenes = [sample for sample in payload["samples"] if sample["split"] == split]
        base_scenes = list(scenes)
        if split == "train" and high_motion_repeat > 1:
            fast = [sample for sample in scenes if sample["motion_bin"] in ("fast", "very_fast")]
            scenes.extend(fast * (high_motion_repeat - 1))
        if low_observability_repeat < 1:
            raise ValueError("low observability repeat must be positive")
        if split == "train" and low_observability_repeat > 1:
            weak_turns = low_observability_temporal_samples(
                base_scenes,
                low_observability_max_tracked_points,
                low_observability_min_angular_speed_deg_s,
            )
            scenes.extend(weak_turns * (low_observability_repeat - 1))
        if not scenes:
            raise ValueError(f"no D405 temporal samples for split {split}")
        self.scenes = scenes
        self.is_metric_scale = True
        self.low_observability_loss_weight = float(low_observability_loss_weight)
        self.low_observability_max_tracked_points = int(
            low_observability_max_tracked_points
        )
        self.low_observability_min_angular_speed_deg_s = float(
            low_observability_min_angular_speed_deg_s
        )
        if self.low_observability_loss_weight <= 0.0:
            raise ValueError("low observability loss weight must be positive")
        self.blur_probability = float(blur_probability) if split == "train" else 0.0
        self.maximum_blur_px = int(maximum_blur_px)

    def _get_views(self, idx, resolution, rng):
        sample = self.scenes[idx]
        images = [
            cv2.imread(sample["first_image"], cv2.IMREAD_GRAYSCALE),
            cv2.imread(sample["second_image"], cv2.IMREAD_GRAYSCALE),
        ]
        depths = [
            cv2.imread(sample["depth_first"], cv2.IMREAD_UNCHANGED),
            cv2.imread(sample["depth_second"], cv2.IMREAD_UNCHANGED),
        ]
        if any(value is None for value in (*images, *depths)):
            raise FileNotFoundError(
                f"incomplete temporal sample: {sample['session_id']}/"
                f"{sample['first_input_index']}->{sample['second_input_index']}"
            )
        images[0], images[1] = augment_ir_pair(
            images[0], images[1], rng, self.blur_probability, self.maximum_blur_px
        )
        intrinsics = np.asarray(sample["intrinsics"], dtype=np.float32)
        poses = [
            np.eye(4, dtype=np.float32),
            np.asarray(sample["camera_pose_second"], dtype=np.float32),
        ]
        temporal_loss_weight = (
            low_observability_sample_weight(
                sample,
                self.low_observability_max_tracked_points,
                self.low_observability_min_angular_speed_deg_s,
                self.low_observability_loss_weight,
            )
            if self.split == "train"
            else 1.0
        )
        views = []
        for view_index, (image, depth, pose) in enumerate(zip(images, depths, poses)):
            views.append(
                {
                    "img": Image.fromarray(image).convert("RGB"),
                    "depthmap": depth.astype(np.float32) * 0.0001,
                    "camera_pose": pose.copy(),
                    "camera_intrinsics": intrinsics.copy(),
                    "dataset": "D405IRTemporal",
                    "label": sample["session_id"],
                    "temporal_loss_weight": np.float32(temporal_loss_weight),
                    "instance": (
                        f"{sample['first_input_index']}->{sample['second_input_index']}:"
                        f"{view_index}"
                    ),
                }
            )
        return views


def transform_correspondences_between_intrinsics(
    points: np.ndarray,
    source_intrinsics: np.ndarray,
    target_intrinsics: np.ndarray,
) -> np.ndarray:
    """Map pixels through the affine resize/crop encoded by two intrinsics."""
    points = np.asarray(points, dtype=np.float32)
    source = np.asarray(source_intrinsics, dtype=np.float32)
    target = np.asarray(target_intrinsics, dtype=np.float32)
    transformed = points.copy()
    transformed[:, 0] = (
        (points[:, 0] - source[0, 2]) * target[0, 0] / source[0, 0]
        + target[0, 2]
    )
    transformed[:, 1] = (
        (points[:, 1] - source[1, 2]) * target[1, 1] / source[1, 1]
        + target[1, 2]
    )
    return transformed


class D405IRRotationMatches(MASt3RBaseStereoViewDataset):
    """High-turn temporal IR pairs supervised only by robust image matches."""

    def __init__(self, *args, manifest: str, split: str, **kwargs):
        super().__init__(*args, split=split, **kwargs)
        payload = json.loads(Path(manifest).read_text(encoding="utf-8"))
        if payload.get("external_ground_truth_used") is not False:
            raise ValueError("rotation-match manifest must exclude external ground truth")
        if payload.get("result") != "READY" or "rotation_matches" not in payload.get(
            "schema", ""
        ):
            raise ValueError("D405 rotation-match manifest is not ready")
        self.scenes = [
            sample for sample in payload["samples"] if sample["split"] == split
        ]
        if not self.scenes:
            raise ValueError(f"no D405 rotation-match samples for split {split}")
        self.is_metric_scale = True

    def _get_views(self, idx, resolution, rng):
        sample = self.scenes[idx]
        images = [
            cv2.imread(sample["first_image"], cv2.IMREAD_GRAYSCALE),
            cv2.imread(sample["second_image"], cv2.IMREAD_GRAYSCALE),
        ]
        depths = [
            cv2.imread(sample["depth_first"], cv2.IMREAD_UNCHANGED),
            cv2.imread(sample["depth_second"], cv2.IMREAD_UNCHANGED),
        ]
        if any(value is None for value in (*images, *depths)):
            raise FileNotFoundError(
                f"incomplete rotation-match sample: {sample['session_id']}/"
                f"{sample['first_input_index']}->{sample['second_input_index']}"
            )
        intrinsics = np.asarray(sample["intrinsics"], dtype=np.float32)
        poses = [
            np.eye(4, dtype=np.float32),
            np.asarray(sample["camera_pose_second"], dtype=np.float32),
        ]
        raw_correspondences = [
            np.asarray(sample["flow_corres_first"], dtype=np.float32),
            np.asarray(sample["flow_corres_second"], dtype=np.float32),
        ]
        views = []
        for view_index, (image, depth, pose, correspondences) in enumerate(
            zip(images, depths, poses, raw_correspondences)
        ):
            views.append(
                {
                    "img": Image.fromarray(image).convert("RGB"),
                    "depthmap": depth.astype(np.float32) * 0.0001,
                    "camera_pose": pose.copy(),
                    "camera_intrinsics": intrinsics.copy(),
                    "source_intrinsics": intrinsics.copy(),
                    "flow_corres": correspondences,
                    "dataset": "D405IRRotationMatches",
                    "label": sample["session_id"],
                    "instance": (
                        f"{sample['first_input_index']}->{sample['second_input_index']}:"
                        f"{view_index}"
                    ),
                }
            )
        return views

    def __getitem__(self, idx):
        views = super().__getitem__(idx)
        transformed = [
            transform_correspondences_between_intrinsics(
                view["flow_corres"],
                view["source_intrinsics"],
                view["camera_intrinsics"],
            )
            for view in views
        ]
        rounded = [np.rint(points).astype(np.int64) for points in transformed]
        valid = np.ones(len(rounded[0]), dtype=bool)
        for points, view in zip(rounded, views):
            height, width = map(int, view["true_shape"])
            valid &= (
                (points[:, 0] >= 0)
                & (points[:, 0] < width)
                & (points[:, 1] >= 0)
                & (points[:, 1] < height)
            )
        positives_first = rounded[0][valid]
        positives_second = rounded[1][valid]
        requested_positive_count = int(self.n_corres * (1.0 - self.nneg))
        positive_count = min(len(positives_first), requested_positive_count)
        if positive_count == 0:
            raise ValueError(
                "rotation-match sample has no visible transformed correspondences"
            )
        choice = self._rng.permutation(len(positives_first))[:positive_count]
        correspondences = [positives_first[choice], positives_second[choice]]
        negative_count = self.n_corres - positive_count
        if negative_count:
            for view_index, view in enumerate(views):
                height, width = map(int, view["true_shape"])
                negatives = np.column_stack(
                    (
                        self._rng.integers(0, width, size=negative_count),
                        self._rng.integers(0, height, size=negative_count),
                    )
                ).astype(np.int64)
                correspondences[view_index] = np.concatenate(
                    (correspondences[view_index], negatives), axis=0
                )
        valid_corres = np.concatenate(
            (
                np.ones(positive_count, dtype=bool),
                np.zeros(negative_count, dtype=bool),
            )
        )
        for view, points in zip(views, correspondences):
            view["corres"] = points
            view["valid_corres"] = valid_corres.copy()
        return views

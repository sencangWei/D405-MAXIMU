import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest


TOOL = Path(os.environ.get("MAST3R_SLAM_DIR", "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM"))
sys.path.insert(0, str(TOOL))
from mast3r_slam import stereo_depth  # noqa: E402


class Matcher:
    def __init__(self, disparity):
        self.disparity = disparity

    def compute(self, *_args):
        return self.disparity


def patch_sgbm(monkeypatch, left_disparity_px, right_disparity_px):
    dl = (np.asarray(left_disparity_px, dtype=np.float32) * 16).astype(np.int16)
    dr = (np.asarray(right_disparity_px, dtype=np.float32) * 16).astype(np.int16)

    def create(minDisparity, **_kwargs):
        return Matcher(dl if minDisparity == 0 else dr)

    monkeypatch.setattr(stereo_depth.cv2, "StereoSGBM_create", create)


def test_default_left_depth_matches_explicit_left(monkeypatch):
    left_disp = np.ones((3, 8), dtype=np.float32)
    right_disp = -np.ones((3, 8), dtype=np.float32)
    patch_sgbm(monkeypatch, left_disp, right_disp)
    left = np.zeros((3, 8), dtype=np.uint8)
    right = np.zeros((3, 8), dtype=np.uint8)

    default = stereo_depth.compute_stereo_depth(left, right, 10.0, 0.02, 0.15, 0.65)
    explicit = stereo_depth.compute_stereo_depth(
        left, right, 10.0, 0.02, 0.15, 0.65, reference_eye="left"
    )

    np.testing.assert_array_equal(np.isnan(default), np.isnan(explicit))
    np.testing.assert_allclose(
        np.nan_to_num(default, nan=-1.0),
        np.nan_to_num(explicit, nan=-1.0),
        rtol=0,
        atol=0,
    )


def test_right_depth_uses_right_pixel_grid_and_signed_lr_consistency(monkeypatch):
    patch_sgbm(
        monkeypatch,
        np.ones((3, 8), dtype=np.float32),
        -np.ones((3, 8), dtype=np.float32),
    )
    left = np.zeros((3, 8), dtype=np.uint8)
    right = np.zeros((3, 8), dtype=np.uint8)

    depth = stereo_depth.compute_stereo_depth(
        left, right, 10.0, 0.02, 0.15, 0.65, reference_eye="right"
    )

    assert np.isnan(depth[:, -1]).all()
    np.testing.assert_allclose(depth[:, :-1], 0.2)

    patch_sgbm(
        monkeypatch,
        np.ones((3, 8), dtype=np.float32),
        np.full((3, 8), -3.0, dtype=np.float32),
    )
    rejected = stereo_depth.compute_stereo_depth(
        left, right, 10.0, 0.02, 0.15, 0.65, reference_eye="right"
    )
    assert np.isnan(rejected).all()


def write_manifest(path, stereo):
    path.mkdir()
    (path / "dataset_manifest.json").write_text(
        json.dumps({"stereo_depth_source": stereo}), encoding="utf-8"
    )


def test_provider_right_eye_reads_right_reference_and_paired_left(monkeypatch, tmp_path):
    dataset = tmp_path / "right_dataset"
    left_dir = dataset / "paired_left"
    write_manifest(
        dataset,
        {
            "reference_eye": "right",
            "left_directory": "paired_left",
            "baseline_m": 0.018,
            "right_camera_info": {
                "fx": 100.0,
                "fy": 100.0,
                "ppx": 4.0,
                "ppy": 3.0,
                "coeffs": [0, 0, 0, 0, 0],
            },
        },
    )
    left_image = np.ones((3, 4), dtype=np.uint8)
    right_image = np.full((3, 4), 2, dtype=np.uint8)

    def imread(path, flags):
        assert flags == stereo_depth.cv2.IMREAD_GRAYSCALE
        path = Path(path)
        if path == left_dir / "0000000003.png":
            return left_image
        if path == dataset / "0000000003.png":
            return right_image
        return None

    calls = []

    def compute(left, right, focal, baseline, minimum, maximum, **kwargs):
        calls.append((left.copy(), right.copy(), focal, baseline, minimum, maximum, kwargs))
        return np.full(left.shape, 0.3, dtype=np.float32)

    monkeypatch.setattr(stereo_depth.cv2, "imread", imread)
    monkeypatch.setattr(stereo_depth, "compute_stereo_depth", compute)

    provider = stereo_depth.StereoDepthProvider.from_dataset(dataset)
    depth = provider.get_depth(dataset / "0000000003.png", (6, 8))

    assert depth.shape == (6, 8)
    assert calls[0][0][0, 0] == 1
    assert calls[0][1][0, 0] == 2
    assert calls[0][2:6] == (100.0, 0.018, 0.15, 0.65)
    assert calls[0][6]["reference_eye"] == "right"


@pytest.mark.parametrize(
    "metadata",
    [
        {"reference_eye": "center", "right_directory": "right", "left_focal_length_px": 1, "baseline_m": 0.018},
        {
            "reference_eye": "right",
            "baseline_m": 0.018,
            "right_camera_info": {"fx": 1, "fy": 1, "ppx": 0, "ppy": 0, "coeffs": [0, 0, 0, 0, 0]},
        },
        {
            "reference_eye": "right",
            "left_directory": "left",
            "baseline_m": 0.018,
            "right_camera_info": {"fx": 1, "fy": 1, "ppx": 0, "ppy": 0, "coeffs": [0, 0, 0, 0, 0.1]},
        },
    ],
)
def test_provider_rejects_invalid_eye_or_right_metadata(tmp_path, metadata):
    dataset = tmp_path / "dataset"
    write_manifest(dataset, metadata)

    with pytest.raises(ValueError):
        stereo_depth.StereoDepthProvider.from_dataset(dataset)

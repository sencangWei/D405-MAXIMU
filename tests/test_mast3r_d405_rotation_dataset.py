import importlib.util
import sys
from pathlib import Path

import numpy as np
import cv2


ROOT = Path(__file__).resolve().parents[1]
TRAIN_REPO = Path("/home/robot/ego_pipeline/work/toolchains/MASt3R-training")
sys.path[:0] = [str(TRAIN_REPO), str(TRAIN_REPO / "dust3r")]
SCRIPT = ROOT / "scripts" / "mast3r_d405_ir_dataset.py"
SPEC = importlib.util.spec_from_file_location(SCRIPT.stem, SCRIPT)
dataset = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dataset)
PREPARE_SCRIPT = ROOT / "scripts" / "prepare_mast3r_d405_rotation_matches.py"
PREPARE_SPEC = importlib.util.spec_from_file_location(
    PREPARE_SCRIPT.stem, PREPARE_SCRIPT
)
rotation_matches = importlib.util.module_from_spec(PREPARE_SPEC)
PREPARE_SPEC.loader.exec_module(rotation_matches)


def test_correspondence_transform_follows_resize_and_crop_intrinsics():
    source = np.array([[400.0, 0.0, 320.0], [0.0, 400.0, 240.0], [0.0, 0.0, 1.0]])
    target = np.array([[200.0, 0.0, 150.0], [0.0, 100.0, 80.0], [0.0, 0.0, 1.0]])
    points = np.array([[320.0, 240.0], [420.0, 440.0]], dtype=np.float32)

    transformed = dataset.transform_correspondences_between_intrinsics(
        points, source, target
    )

    np.testing.assert_allclose(transformed, [[150.0, 80.0], [200.0, 130.0]])


def test_rotation_match_tracks_require_forward_backward_consistency(monkeypatch, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    cv2.imwrite(str(first), np.zeros((16, 16), dtype=np.uint8))
    cv2.imwrite(str(second), np.zeros((16, 16), dtype=np.uint8))
    features = np.array([[[2.0, 3.0]], [[8.0, 9.0]]], dtype=np.float32)
    calls = iter(
        [
            (
                np.array([[[3.0, 3.0]], [[9.0, 9.0]]], dtype=np.float32),
                np.ones((2, 1), dtype=np.uint8),
                None,
            ),
            (
                np.array([[[2.2, 3.0]], [[4.0, 9.0]]], dtype=np.float32),
                np.ones((2, 1), dtype=np.uint8),
                None,
            ),
        ]
    )
    monkeypatch.setattr(cv2, "goodFeaturesToTrack", lambda *_args, **_kwargs: features)
    monkeypatch.setattr(cv2, "calcOpticalFlowPyrLK", lambda *_args, **_kwargs: next(calls))

    tracked_first, tracked_second = rotation_matches.tracked_correspondences(
        first, second, maximum_points=8, maximum_forward_backward_error_px=1.0
    )

    assert tracked_first.tolist() == [[2.0, 3.0]]
    assert tracked_second.tolist() == [[3.0, 3.0]]

import sys
from argparse import Namespace
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from apply_verified_loop_closure import (
    Frame,
    MatchEvidence,
    apply_path_weighted_loop_constraint,
    evidence_failures,
    match_images,
    pair_stereo_indices,
)


def test_path_weighted_loop_constraint_closes_anchor_without_endpoint_jump():
    times = np.arange(6, dtype=float)
    points = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.02, 0.01, 0.03],
            [0.02, 0.01, 0.03],
        ]
    )

    corrected, indices, loop_error = apply_path_weighted_loop_constraint(
        times, points, start_epoch_s=0.0, end_epoch_s=4.0
    )

    np.testing.assert_array_equal(indices, [0, 4])
    np.testing.assert_allclose(loop_error, [0.02, 0.01, 0.03])
    np.testing.assert_allclose(corrected[0], corrected[4], atol=1e-12)
    np.testing.assert_allclose(corrected[4], corrected[5], atol=1e-12)
    assert np.max(np.linalg.norm(np.diff(corrected, axis=0), axis=1)) < 1.5


def test_unrelated_images_do_not_produce_verified_matches():
    rng = np.random.default_rng(7)
    first = rng.integers(0, 256, (480, 640), dtype=np.uint8)
    second = rng.integers(0, 256, (480, 640), dtype=np.uint8)
    detector = cv2.ORB_create(nfeatures=1500)

    evidence = match_images(
        detector.detectAndCompute(first, None),
        detector.detectAndCompute(second, None),
    )

    assert evidence.inliers < 200
    assert evidence.inlier_ratio < 0.5


def test_stereo_pairing_uses_timestamps_instead_of_list_positions():
    image = np.zeros((2, 2), dtype=np.uint8)
    left = [
        Frame(100.0, 1.000, image),
        Frame(100.1, 1.100, image),
        Frame(100.2, 1.200, image),
    ]
    right = [
        Frame(100.0, 1.001, image),
        Frame(100.2, 1.201, image),
    ]

    assert pair_stereo_indices(left, right) == [(0, 0), (2, 1)]


def test_large_viewpoint_change_is_rejected_even_with_many_inliers():
    args = Namespace(
        min_inliers=200,
        min_inlier_ratio=0.5,
        max_median_error_px=1.5,
        max_median_displacement_px=60.0,
    )
    evidence = {
        "ir_left": MatchEvidence(900, 700, 0.78, 0.5, 75.0),
        "ir_right": MatchEvidence(850, 650, 0.76, 0.6, 72.0),
    }

    failures = evidence_failures(evidence, args)

    assert any("ir_left 中位位移" in failure for failure in failures)
    assert any("ir_right 中位位移" in failure for failure in failures)

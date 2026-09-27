import importlib.util
import csv
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "align_mast3r_scale_with_stereo.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
stereo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stereo)


def write_rows(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_parse_recorded_realsense_stereo_calibration():
    info = stereo.parse_camera_info(
        "width=1280;height=720;fx=649.207;ppx=638.44;fy=649.207;"
        "ppy=354.548;model=Brown Conrady;coeffs=0,0,0,0,0"
    )
    rotation, translation = stereo.parse_transform(
        "rotation=1,0,0,0,1,0,0,0,1;translation=-0.018083,0,0"
    )
    assert info["fx"] == pytest.approx(649.207)
    np.testing.assert_allclose(rotation, np.eye(3))
    np.testing.assert_allclose(translation, [-0.018083, 0.0, 0.0])


def test_set_topic_filter_sorts_and_limits_rosbag_reader(monkeypatch):
    class FakeStorageFilter:
        def __init__(self, *, topics):
            self.topics = topics

    class FakeReader:
        def set_filter(self, storage_filter):
            self.storage_filter = storage_filter

    monkeypatch.setitem(
        sys.modules,
        "rosbag2_py",
        type("FakeRosbag", (), {"StorageFilter": FakeStorageFilter}),
    )
    reader = FakeReader()

    stereo.set_topic_filter(reader, {"/right", "/left"})

    assert reader.storage_filter.topics == ["/left", "/right"]


def test_load_selected_prepared_stereo_images_uses_recorded_frame_pairing(tmp_path):
    dataset = tmp_path / "dataset"
    right_dir = dataset / "stereo_right"
    right_dir.mkdir(parents=True)
    write_rows(
        dataset / "frames.csv",
        ["input_index", "image", "source_frame_number", "t_sec"],
        [
            {
                "input_index": "0",
                "image": "0000000000.png",
                "source_frame_number": "30",
                "t_sec": "1.0",
            }
        ],
    )
    write_rows(
        tmp_path / "d405_frames.csv",
        ["infrared_left_frame_number", "infrared_right_frame_number"],
        [
            {
                "infrared_left_frame_number": "30",
                "infrared_right_frame_number": "80",
            }
        ],
    )
    assert stereo.cv2.imwrite(
        str(dataset / "0000000000.png"), np.full((3, 4), 11, dtype=np.uint8)
    )
    assert stereo.cv2.imwrite(
        str(right_dir / "0000000000.png"), np.full((3, 4), 22, dtype=np.uint8)
    )

    left, right = stereo.load_selected_prepared_stereo_images(
        dataset, tmp_path / "d405_frames.csv", {30}, {80}
    )

    assert int(left[30][0, 0]) == 11
    assert int(right[80][0, 0]) == 22


def test_load_stereo_calibration_includes_left_ir_to_color_transform(monkeypatch):
    info = (
        "width=1280;height=720;fx=649.207;ppx=638.44;fy=649.207;"
        "ppy=354.548;model=Brown Conrady;coeffs=0,0,0,0,0"
    )
    monkeypatch.setattr(
        stereo,
        "read_static_strings",
        lambda _db3, topics: {
            stereo.LEFT_INFO_TOPIC: info,
            stereo.RIGHT_INFO_TOPIC: info,
            stereo.RIGHT_TF_TOPIC: (
                "rotation=1,0,0,0,1,0,0,0,1;translation=-0.018083,0,0"
            ),
            stereo.COLOR_TF_TOPIC: (
                "rotation=0,-1,0,1,0,0,0,0,1;translation=0.001,0.002,0.003"
            ),
            stereo.BASELINE_TOPIC: "18.083",
        },
    )

    calibration = stereo.load_stereo_calibration(Path("unused.db3"))

    np.testing.assert_allclose(
        calibration["color_rotation_from_left"],
        [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
    )
    np.testing.assert_allclose(
        calibration["color_translation_from_left_m"], [0.001, 0.002, 0.003]
    )
    assert stereo.COLOR_TF_TOPIC in calibration["factory_topics"]


def test_change_relative_pose_frame_uses_full_rigid_conjugation():
    source_rotation = stereo.Rotation.from_euler("z", 30.0, degrees=True)
    source_translation = np.asarray([0.03, -0.02, 0.01])
    target_rotation_from_source = stereo.Rotation.from_euler(
        "x", 20.0, degrees=True
    )
    target_translation_from_source = np.asarray([0.01, 0.02, -0.03])

    target_rotation, target_translation = stereo.change_relative_pose_frame(
        source_rotation,
        source_translation,
        target_rotation_from_source,
        target_translation_from_source,
    )

    source_transform = np.eye(4)
    source_transform[:3, :3] = source_rotation.as_matrix()
    source_transform[:3, 3] = source_translation
    target_from_source = np.eye(4)
    target_from_source[:3, :3] = target_rotation_from_source.as_matrix()
    target_from_source[:3, 3] = target_translation_from_source
    expected = target_from_source @ source_transform @ np.linalg.inv(
        target_from_source
    )
    np.testing.assert_allclose(target_rotation.as_matrix(), expected[:3, :3])
    np.testing.assert_allclose(target_translation, expected[:3, 3])


def test_map_mast3r_points_to_original_undoes_resize_and_crop():
    points = np.asarray([[0.0, 0.0], [256.0, 144.0], [511.0, 287.0]])
    transform = {
        "scale_x": 0.4,
        "scale_y": 0.4,
        "crop_left_px": 0,
        "crop_top_px": 0,
    }

    mapped = stereo.map_mast3r_points_to_original(points, transform)

    np.testing.assert_allclose(
        mapped,
        [[0.0, 0.0], [640.0, 360.0], [1277.5, 717.5]],
    )


def test_mast3r_correspondences_honor_stereo_3d_motion_estimator(monkeypatch):
    disparity = np.ones((4, 4), dtype=np.float32)
    monkeypatch.setattr(
        stereo,
        "stereo_disparity",
        lambda *_args: (disparity, -disparity),
    )
    points_i = np.asarray([[1.0, 1.0]], dtype=np.float32)
    points_j = np.asarray([[2.0, 1.0]], dtype=np.float32)
    monkeypatch.setattr(
        stereo,
        "estimate_mast3r_correspondences",
        lambda *_args: {
            "accepted": True,
            "points_i": points_i,
            "points_j": points_j,
            "correspondence_valid": np.ones(1, dtype=bool),
            "raw_matches": 1,
            "epipolar_inliers": 1,
            "epipolar_inlier_ratio": 1.0,
        },
    )
    captured = {}

    def fake_stereo_3d(*args, **_kwargs):
        captured["points_i"] = args[0]
        captured["points_j"] = args[1]
        return {"accepted": True, "scale": 1.0}

    monkeypatch.setattr(stereo, "estimate_motion_from_stereo_3d", fake_stereo_3d)
    monkeypatch.setattr(
        stereo,
        "estimate_motion_from_correspondences",
        lambda *_args, **_kwargs: pytest.fail("PnP path must not be used"),
    )

    result = stereo.estimate_pair_scale(
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros((4, 4), dtype=np.uint8),
        np.zeros(3),
        np.ones(3),
        stereo.Rotation.identity(),
        stereo.Rotation.identity(),
        {},
        num_disparities=16,
        min_depth_m=0.1,
        max_depth_m=1.0,
        motion_estimator="stereo_3d",
        correspondence_estimator="mast3r",
        mast3r_matcher={},
    )

    assert result["accepted"] is True
    assert result["method"] == "mast3r_stereo_3d"
    np.testing.assert_array_equal(captured["points_i"], points_i)
    np.testing.assert_array_equal(captured["points_j"], points_j)


def test_classical_correspondences_forward_pnp_rotation_mode(monkeypatch):
    disparity = np.ones((8, 8), dtype=np.float32)
    monkeypatch.setattr(
        stereo,
        "stereo_disparity",
        lambda *_args: (disparity, -disparity),
    )
    features = np.tile(
        np.asarray([[[2.0, 2.0]]], dtype=np.float32), (40, 1, 1)
    )
    monkeypatch.setattr(stereo.cv2, "goodFeaturesToTrack", lambda *_args, **_kwargs: features)
    monkeypatch.setattr(
        stereo.cv2,
        "calcOpticalFlowPyrLK",
        lambda _first, _second, points, *_args, **_kwargs: (
            points.copy(),
            np.ones((len(points), 1), dtype=np.uint8),
            None,
        ),
    )
    rotation_modes = []

    def fake_motion(*_args, **kwargs):
        rotation_modes.append(kwargs["pnp_rotation_mode"])
        return {"accepted": False, "reason": "translation_excitation_low"}

    monkeypatch.setattr(stereo, "estimate_motion_from_correspondences", fake_motion)

    result = stereo.estimate_pair_scale(
        np.zeros((8, 8), dtype=np.uint8),
        np.zeros((8, 8), dtype=np.uint8),
        np.zeros((8, 8), dtype=np.uint8),
        np.zeros((8, 8), dtype=np.uint8),
        np.zeros(3),
        np.ones(3),
        stereo.Rotation.identity(),
        stereo.Rotation.identity(),
        {},
        num_disparities=16,
        min_depth_m=0.1,
        max_depth_m=1.0,
        correspondence_estimator="classical",
        pnp_rotation_mode="trajectory-fixed",
    )

    assert result["reason"] == "translation_excitation_low"
    assert rotation_modes == ["trajectory-fixed"]


def test_match_trajectory_can_use_left_ir_timestamps(tmp_path):
    frame_csv = tmp_path / "d405_frames.csv"
    rows = [
        {
            "color_device_ms": "1000.5",
            "infrared_left_device_ms": "1000.0",
            "infrared_right_device_ms": "1000.0",
            "infrared_left_frame_number": "10",
            "infrared_right_frame_number": "20",
        },
        {
            "color_device_ms": "1033.8",
            "infrared_left_device_ms": "1033.3",
            "infrared_right_device_ms": "1033.3",
            "infrared_left_frame_number": "11",
            "infrared_right_frame_number": "21",
        },
    ]
    with frame_csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)

    left, right, sync = stereo.match_trajectory_to_stereo_frames(
        frame_csv, np.asarray([1.0, 1.0333]), trajectory_frame="infrared_left"
    )

    assert left.tolist() == [10, 11]
    assert right.tolist() == [20, 21]
    assert sync["trajectory_frame"] == "infrared_left"
    assert sync["max_trajectory_to_source_delta_ms"] == pytest.approx(0.0)


def test_robust_scale_rejects_one_large_outlier():
    observations = [
        {"accepted": True, "scale": value}
        for value in (0.31, 0.32, 0.321, 0.325, 0.318, 3.4)
    ]
    scale, quality = stereo.robust_scale(observations, min_observations=4)
    assert scale == pytest.approx(0.32, abs=0.005)
    assert quality["robust_inliers"] == 5


def test_robust_scale_rejects_insufficient_observations():
    observations = [
        {"accepted": True, "scale": 0.32},
        {"accepted": False, "reason": "pnp_failed"},
    ]
    with pytest.raises(ValueError, match="insufficient accepted"):
        stereo.robust_scale(observations, min_observations=4)


def test_robust_scale_weights_longer_translation_more_than_short_motion():
    observations = [
        {
            "accepted": True,
            "scale": scale,
            "metric_distance_m": distance,
            "pnp_inlier_ratio": 0.8,
        }
        for scale, distance in (
            (0.30, 0.01),
            (0.30, 0.01),
            (0.30, 0.01),
            (0.32, 0.10),
            (0.32, 0.10),
        )
    ]

    scale, quality = stereo.robust_scale(observations, min_observations=4)

    assert scale > 0.315
    assert quality["scale_estimator"] == "robust_inverse_variance_weighted_mean"


def test_sample_pairs_adds_longer_hops_without_crossing_sequence_end():
    pairs = stereo.sample_pairs(np.asarray([0, 5, 10, 15]), max_hop=3)
    assert pairs == [
        (0, 5, 1),
        (5, 10, 1),
        (10, 15, 1),
        (0, 10, 2),
        (5, 15, 2),
        (0, 15, 3),
    ]


def test_sample_pairs_can_select_sparse_long_hops():
    pairs = stereo.sample_pairs(
        np.asarray([0, 5, 10, 15, 20]),
        max_hop=1,
        hop_values=(1, 3, 3, 8),
    )
    assert pairs == [
        (0, 5, 1),
        (5, 10, 1),
        (10, 15, 1),
        (15, 20, 1),
        (0, 15, 3),
        (5, 20, 3),
    ]


def test_bidirectional_scale_combines_consistent_forward_and_reverse():
    forward = {
        "accepted": True,
        "scale": 0.22,
        "metric_distance_m": 0.08,
        "pnp_inlier_ratio": 0.8,
        "rotation_error_deg": 0.5,
    }
    reverse = {
        "accepted": True,
        "scale": 0.24,
        "metric_distance_m": 0.04,
        "pnp_inlier_ratio": 0.5,
        "rotation_error_deg": 0.7,
    }

    combined = stereo.combine_bidirectional_scale(forward, reverse)

    assert combined["accepted"] is True
    assert 0.22 < combined["scale"] < 0.23
    assert combined["scale_estimator"] == "bidirectional_pnp_weighted_mean"
    assert combined["bidirectional_relative_disagreement"] == pytest.approx(
        0.02 / 0.23
    )


def test_bidirectional_scale_rejects_inconsistent_directions():
    forward = {
        "accepted": True,
        "scale": 0.20,
        "metric_distance_m": 0.08,
        "pnp_inlier_ratio": 0.8,
    }
    reverse = {
        "accepted": True,
        "scale": 0.30,
        "metric_distance_m": 0.08,
        "pnp_inlier_ratio": 0.8,
        "rotation_error_deg": 0.7,
    }

    combined = stereo.combine_bidirectional_scale(forward, reverse)

    assert combined["accepted"] is False
    assert combined["reason"] == "bidirectional_scale_disagrees"


def test_mast3r_forward_edge_can_survive_weak_reverse_depth():
    forward = {
        "accepted": True,
        "scale": 0.63,
        "metric_distance_m": 0.15,
        "pnp_inliers": 124,
        "pnp_inlier_ratio": 0.327,
        "direction_cosine": 0.999,
        "rotation_error_deg": 1.3,
    }
    reverse = {"accepted": False, "reason": "pnp_inlier_ratio_low"}

    combined = stereo.combine_mast3r_bidirectional_scale(
        forward, reverse, {"epipolar_inlier_ratio": 0.994}
    )

    assert combined["accepted"] is True
    assert combined["scale_estimator"] == "strict_forward_mast3r_pnp"
    assert combined["reverse_failure_reason"] == "pnp_inlier_ratio_low"


def test_mast3r_forward_edge_still_rejects_weak_geometric_support():
    forward = {
        "accepted": True,
        "scale": 0.63,
        "metric_distance_m": 0.15,
        "pnp_inliers": 40,
        "pnp_inlier_ratio": 0.20,
        "direction_cosine": 0.999,
        "rotation_error_deg": 1.3,
    }
    reverse = {"accepted": False, "reason": "pnp_inlier_ratio_low"}

    combined = stereo.combine_mast3r_bidirectional_scale(
        forward, reverse, {"epipolar_inlier_ratio": 0.994}
    )

    assert combined["accepted"] is False
    assert combined["reason"] == "reverse_motion_failed"


def test_fit_rigid_transform_3d_rejects_outlier():
    source = np.asarray(
        [[x, y, z] for x in (-0.1, 0.0, 0.1) for y in (-0.1, 0.1) for z in (0.5, 0.8)]
    )
    expected_rotation = stereo.Rotation.from_euler("xyz", [2.0, -3.0, 5.0], degrees=True)
    expected_translation = np.asarray([0.03, -0.01, 0.02])
    target = expected_rotation.apply(source) + expected_translation
    source = np.vstack((source, [[0.2, 0.2, 0.6]]))
    target = np.vstack((target, [[2.0, -1.0, 3.0]]))

    rotation, translation, inliers, _ = stereo.fit_rigid_transform_3d(
        source, target, minimum_inliers=8
    )

    assert np.count_nonzero(inliers) == len(source) - 1
    np.testing.assert_allclose(rotation.as_matrix(), expected_rotation.as_matrix(), atol=1e-8)
    np.testing.assert_allclose(translation, expected_translation, atol=1e-8)


def test_left_right_consistency_uses_negative_right_disparity():
    left = np.full((8, 12), 4.0, dtype=np.float32)
    right = np.full((8, 12), -4.0, dtype=np.float32)
    points = np.asarray([[8.0, 4.0], [3.0, 4.0]])
    valid, disparity = stereo.left_right_consistent(points, left, right, 0.5)
    assert valid.tolist() == [True, False]
    assert disparity.tolist() == [4.0, 4.0]


def test_correspondence_motion_keeps_method_and_metric_scale(monkeypatch):
    monkeypatch.setattr(
        stereo.cv2,
        "solvePnPRansac",
        lambda *args, **kwargs: (
            True,
            np.zeros((3, 1)),
            np.asarray([[-0.01], [0.0], [0.0]]),
            np.arange(40).reshape(-1, 1),
        ),
    )
    points_i = np.column_stack(
        (np.linspace(20.0, 80.0, 40), np.linspace(20.0, 80.0, 40))
    ).astype(np.float32)
    result = stereo.estimate_motion_from_correspondences(
        points_i,
        points_i.copy(),
        np.ones(40, dtype=bool),
        np.full((100, 100), 10.0, dtype=np.float32),
        np.full((100, 100), -10.0, dtype=np.float32),
        np.zeros(3),
        np.asarray([0.02, 0.0, 0.0]),
        stereo.Rotation.identity(),
        stereo.Rotation.identity(),
        {
            "left": {"fx": 100.0, "fy": 100.0, "cx": 50.0, "cy": 50.0},
            "baseline_m": 0.1,
            "color_rotation_from_left": np.eye(3),
            "color_translation_from_left_m": np.zeros(3),
        },
        min_depth_m=0.5,
        max_depth_m=1.5,
        method="sift",
    )
    assert result["accepted"] is True
    assert result["method"] == "sift"
    assert result["scale"] == pytest.approx(0.5)


def test_correspondence_motion_is_reported_in_mast3r_color_frame(monkeypatch):
    monkeypatch.setattr(
        stereo.cv2,
        "solvePnPRansac",
        lambda *args, **kwargs: (
            True,
            np.zeros((3, 1)),
            np.asarray([[-0.01], [0.0], [0.0]]),
            np.arange(40).reshape(-1, 1),
        ),
    )
    points_i = np.column_stack(
        (np.linspace(20.0, 80.0, 40), np.linspace(20.0, 80.0, 40))
    ).astype(np.float32)
    result = stereo.estimate_motion_from_correspondences(
        points_i,
        points_i.copy(),
        np.ones(40, dtype=bool),
        np.full((100, 100), 10.0, dtype=np.float32),
        np.full((100, 100), -10.0, dtype=np.float32),
        np.zeros(3),
        np.asarray([0.0, 0.02, 0.0]),
        stereo.Rotation.identity(),
        stereo.Rotation.identity(),
        {
            "left": {"fx": 100.0, "fy": 100.0, "cx": 50.0, "cy": 50.0},
            "baseline_m": 0.1,
            "color_rotation_from_left": stereo.Rotation.from_euler(
                "z", 90.0, degrees=True
            ).as_matrix(),
            "color_translation_from_left_m": np.zeros(3),
        },
        min_depth_m=0.5,
        max_depth_m=1.5,
        method="sift",
    )
    assert result["accepted"] is True
    np.testing.assert_allclose(
        result["metric_displacement_camera_i_m"], [0.0, 0.01, 0.0], atol=1e-12
    )
    assert result["metric_displacement_frame"] == "color_camera_i"
    assert result["scale"] == pytest.approx(0.5)


def test_correspondence_motion_stays_in_left_ir_frame(monkeypatch):
    monkeypatch.setattr(
        stereo.cv2,
        "solvePnPRansac",
        lambda *args, **kwargs: (
            True,
            np.zeros((3, 1)),
            np.asarray([[-0.01], [0.0], [0.0]]),
            np.arange(40).reshape(-1, 1),
        ),
    )
    points = np.column_stack(
        (np.linspace(20.0, 80.0, 40), np.linspace(20.0, 80.0, 40))
    ).astype(np.float32)
    result = stereo.estimate_motion_from_correspondences(
        points,
        points.copy(),
        np.ones(40, dtype=bool),
        np.full((100, 100), 10.0, dtype=np.float32),
        np.full((100, 100), -10.0, dtype=np.float32),
        np.zeros(3),
        np.asarray([0.02, 0.0, 0.0]),
        stereo.Rotation.identity(),
        stereo.Rotation.identity(),
        {
            "left": {"fx": 100.0, "fy": 100.0, "cx": 50.0, "cy": 50.0},
            "baseline_m": 0.1,
            "color_rotation_from_left": stereo.Rotation.from_euler(
                "z", 90.0, degrees=True
            ).as_matrix(),
            "color_translation_from_left_m": np.zeros(3),
        },
        min_depth_m=0.5,
        max_depth_m=1.5,
        method="sift",
        trajectory_frame="infrared_left",
    )
    assert result["accepted"] is True
    np.testing.assert_allclose(
        result["metric_displacement_camera_i_m"], [0.01, 0.0, 0.0], atol=1e-12
    )
    assert result["metric_displacement_frame"] == "infrared_left_camera_i"


def test_fixed_rotation_translation_solver_recovers_metric_translation():
    rotation = stereo.Rotation.from_euler("y", 4.0, degrees=True)
    translation = np.asarray([0.025, -0.008, 0.012])
    object_points = np.asarray(
        [
            [-0.2, -0.1, 0.8],
            [0.1, -0.15, 0.9],
            [0.2, 0.1, 1.0],
            [-0.15, 0.2, 1.1],
            [0.0, 0.0, 0.7],
        ]
    )
    camera_matrix = np.asarray(
        [[420.0, 0.0, 320.0], [0.0, 418.0, 240.0], [0.0, 0.0, 1.0]]
    )
    transformed = rotation.apply(object_points) + translation
    image_points = np.column_stack(
        (
            camera_matrix[0, 0] * transformed[:, 0] / transformed[:, 2]
            + camera_matrix[0, 2],
            camera_matrix[1, 1] * transformed[:, 1] / transformed[:, 2]
            + camera_matrix[1, 2],
        )
    )

    estimated, reprojection = stereo.solve_translation_with_fixed_rotation(
        object_points, image_points, camera_matrix, rotation
    )

    np.testing.assert_allclose(estimated, translation, atol=1e-9)
    np.testing.assert_allclose(reprojection, 0.0, atol=1e-9)

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "fuse_mast3r_stereo_imu.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)


def test_regular_nodes_cover_full_trajectory_independent_of_stereo_edges():
    np.testing.assert_array_equal(
        fusion.regular_node_indices(sample_count=96, stride=30),
        np.array([0, 30, 60, 90, 95]),
    )


def test_auto_visual_sigma_relaxes_only_for_large_onboard_disagreement():
    selected, report = fusion.select_visual_position_sigma(
        0.020,
        {"position_disagreement_p95_m": 0.156},
        enabled=True,
    )
    assert selected == pytest.approx(0.040)
    assert report["relaxed"] is True
    assert report["external_ground_truth_used"] is False

    selected, report = fusion.select_visual_position_sigma(
        0.020,
        {"position_disagreement_p95_m": 0.022},
        enabled=True,
    )
    assert selected == pytest.approx(0.020)
    assert report["relaxed"] is False


def test_auto_visual_sigma_requires_relative_motion_alignment():
    with pytest.raises(ValueError, match="aligned relative-motion"):
        fusion.select_visual_position_sigma(0.020, None, enabled=True)


def test_per_node_correction_cap_interpolates_already_capped_nodes():
    node_corrections = np.array([[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    frame_left = np.array([0, 0, 1])
    frame_right = np.array([0, 1, 1])
    frame_alpha = np.array([0.0, 0.5, 0.0])

    capped, requested_norm, minimum_scale = (
        fusion.cap_interpolated_position_corrections(
            node_corrections,
            frame_left,
            frame_right,
            frame_alpha,
            maximum_norm_m=1.0,
            mode="per-node",
        )
    )

    np.testing.assert_allclose(
        capped,
        np.array([[1.0, 0.0, 0.0], [0.5, 0.5, 0.0], [0.0, 1.0, 0.0]]),
    )
    np.testing.assert_allclose(requested_norm, [2.0, np.sqrt(2.0), 2.0])
    assert minimum_scale == pytest.approx(0.5)


def test_per_frame_correction_cap_preserves_legacy_interpolated_clipping():
    capped, _, _ = fusion.cap_interpolated_position_corrections(
        np.array([[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]]),
        np.array([0, 0, 1]),
        np.array([0, 1, 1]),
        np.array([0.0, 0.5, 0.0]),
        maximum_norm_m=1.0,
        mode="per-frame",
    )

    assert np.linalg.norm(capped[1]) == pytest.approx(1.0)
    np.testing.assert_allclose(capped[0], [1.0, 0.0, 0.0])
    np.testing.assert_allclose(capped[2], [0.0, 1.0, 0.0])


def test_pchip_position_correction_passes_through_nodes():
    node_indices = np.asarray([0, 2, 5, 8])
    frame_indices = np.arange(9)
    left, right, alpha = fusion.interpolation_stencil(frame_indices, node_indices)
    nodes = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [0.01, 0.0, 0.0],
            [0.012, 0.004, 0.0],
            [0.02, 0.0, 0.0],
        ]
    )

    interpolated, _, _ = fusion.cap_interpolated_position_corrections(
        nodes,
        left,
        right,
        alpha,
        maximum_norm_m=1.0,
        mode="per-node",
        interpolation_mode="pchip",
        node_indices=node_indices,
        frame_indices=frame_indices,
    )

    np.testing.assert_allclose(interpolated[node_indices], nodes, atol=1e-12)
    assert np.max(np.linalg.norm(interpolated, axis=1)) <= 1.0


def test_scale_consistency_accepts_close_independent_estimates():
    quality = fusion.scale_consistency(imu_scale=0.5757, stereo_scale=0.5530)
    assert quality["consistent"] is True
    assert quality["relative_difference"] == pytest.approx(0.0402, rel=0.02)
    assert quality["joint_scale_m_per_mast3r_unit"] == pytest.approx(
        np.sqrt(0.5757 * 0.5530)
    )


def test_scale_consistency_rejects_large_disagreement():
    quality = fusion.scale_consistency(imu_scale=0.80, stereo_scale=0.55)
    assert quality["consistent"] is False


def test_default_policy_keeps_every_failure_blocking():
    failures = ["imu_stereo_metric_scale_disagreement", "visual_gyro_rotation_inconsistent"]
    assert fusion.blocking_failures(failures, "fail") == failures


def test_diagnose_policy_releases_only_the_scale_disagreement():
    failures = ["visual_gyro_rotation_inconsistent", "imu_stereo_metric_scale_disagreement"]
    assert fusion.blocking_failures(failures, "diagnose") == [
        "visual_gyro_rotation_inconsistent"
    ]


def test_diagnose_policy_still_blocks_when_scale_disagreement_absent():
    failures = ["gravity_norm_out_of_range"]
    assert fusion.blocking_failures(failures, "diagnose") == failures
    assert fusion.blocking_failures([], "diagnose") == []


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError, match="unknown scale disagreement policy"):
        fusion.blocking_failures([], "typo")


def test_relative_rmse_regression_allows_small_multiobjective_tradeoff():
    regression = fusion.relative_rmse_regression(0.011554, 0.011571)
    assert regression < fusion.MAX_INERTIAL_RMSE_REGRESSION_RATIO


def test_relative_rmse_regression_rejects_large_degradation():
    regression = fusion.relative_rmse_regression(0.010, 0.011)
    assert regression > fusion.MAX_INERTIAL_RMSE_REGRESSION_RATIO


def test_filter_stereo_observations_keeps_requested_longer_baselines():
    observations = [
        {"sample_hop": 1},
        {"sample_hop": 3},
        {"sample_hop": 5},
    ]
    assert fusion.filter_stereo_observations(observations, 3) == observations[1:]


def test_merge_stereo_reports_combines_same_session_sparse_hops():
    calibration = {"baseline_m": 0.018083254}
    primary = {
        "session": "/session/a",
        "trajectory": "/trajectory/a.csv",
        "observation_frame": "infrared_left_camera_i",
        "factory_stereo_calibration": calibration,
        "scale_m_per_mast3r_unit": 0.22575,
        "observations": [{"sample_hop": 1}],
        "report_path": "/short.json",
    }
    addition = {
        **primary,
        "scale_m_per_mast3r_unit": 0.22617,
        "observations": [{"sample_hop": 12}],
        "report_path": "/long.json",
    }

    merged = fusion.merge_stereo_reports(primary, [addition])

    assert [item["sample_hop"] for item in merged["observations"]] == [1, 12]
    assert merged["merged_report_count"] == 2
    assert merged["scale_m_per_mast3r_unit"] == primary["scale_m_per_mast3r_unit"]


def test_merge_stereo_reports_rejects_different_sessions():
    primary = {
        "session": "/session/a",
        "trajectory": "/trajectory/a.csv",
        "observation_frame": "infrared_left_camera_i",
        "factory_stereo_calibration": {"baseline_m": 0.018083254},
        "scale_m_per_mast3r_unit": 0.22575,
        "observations": [],
    }
    addition = {**primary, "session": "/session/b"}

    with pytest.raises(ValueError, match="different sessions"):
        fusion.merge_stereo_reports(primary, [addition])


def test_stereo_observation_confidence_downweights_inconsistent_edge():
    strong = fusion.stereo_observation_confidence(
        {
            "pnp_inlier_ratio": 0.85,
            "rotation_error_deg": 0.3,
            "scale": 0.226,
            "bidirectional_relative_disagreement": 0.02,
        },
        0.22575,
    )
    weak = fusion.stereo_observation_confidence(
        {
            "pnp_inlier_ratio": 0.30,
            "rotation_error_deg": 3.0,
            "scale": 0.18,
            "bidirectional_relative_disagreement": 0.30,
        },
        0.22575,
    )

    assert strong > 0.75
    assert weak == pytest.approx(0.05)


def test_condition_learned_stereo_report_filters_and_removes_rotation():
    report = {
        "correspondence_estimator": "mast3r",
        "observations": [
            {
                "accepted": True,
                "pnp_inlier_ratio": 0.8,
                "rotation_error_deg": 0.2,
                "scale": 0.63,
                "bidirectional_relative_disagreement": 0.02,
                "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            {
                "accepted": True,
                "pnp_inlier_ratio": 0.1,
                "rotation_error_deg": 4.0,
                "scale": 0.8,
                "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
        ],
    }

    conditioned = fusion.condition_learned_stereo_report(
        report, reference_scale=0.63, minimum_confidence=0.3, translation_only=True
    )

    assert conditioned["observations"][0]["accepted"] is True
    assert "pnp_rotation_quaternion_xyzw" not in conditioned["observations"][0]
    assert conditioned["observations"][1]["accepted"] is False
    assert conditioned["observations"][1]["reason"] == "learned_stereo_confidence_low"


def test_condition_fixed_rotation_stereo_report_rejects_inconsistent_edges():
    report = {
        "pnp_rotation_mode": "trajectory-fixed",
        "observations": [
            {
                "accepted": True,
                "pnp_free_rotation_delta_deg": 1.5,
                "pnp_reprojection_p95_px": 3.0,
            },
            {
                "accepted": True,
                "pnp_free_rotation_delta_deg": 4.0,
                "pnp_reprojection_p95_px": 2.0,
            },
            {
                "accepted": True,
                "pnp_free_rotation_delta_deg": 2.0,
                "pnp_reprojection_p95_px": 5.0,
            },
        ],
    }

    conditioned = fusion.condition_fixed_rotation_stereo_report(
        report,
        maximum_free_rotation_delta_deg=3.0,
        maximum_reprojection_p95_px=4.0,
    )

    assert conditioned["observations"][0]["accepted"] is True
    assert conditioned["observations"][1]["reason"] == "fixed_pnp_rotation_delta_high"
    assert conditioned["observations"][2]["reason"] == "fixed_pnp_reprojection_high"


def test_local_stereo_scale_state_downweights_consistent_local_scale_warp():
    observations = []
    for frame in range(0, 181, 5):
        observations.append(
            {
                "accepted": True,
                "first_index": frame,
                "second_index": frame + 5,
                "scale": 1.20 if 60 <= frame <= 120 else 1.00,
            }
        )

    local_reference, visual_weights, quality = (
        fusion.local_stereo_scale_state(
            observations,
            np.asarray([20.0, 90.0, 160.0]),
            radius_frames=20.0,
            reference_scale=1.00,
        )
    )

    assert local_reference[1] == pytest.approx(1.20)
    assert visual_weights[1] < 0.5
    assert visual_weights[0] > 0.9
    assert visual_weights[2] > 0.9
    assert quality["downweighted_queries"] == 1
    assert quality["global_scale_m_per_mast3r_unit"] == pytest.approx(1.00)


def test_write_trajectory_rejects_pose_count_mismatch(tmp_path):
    rows = [
        {
            "t_sec": "0",
            "x": "0",
            "y": "0",
            "z": "0",
            "qw": "1",
            "qx": "0",
            "qy": "0",
            "qz": "0",
        }
    ]

    with pytest.raises(ValueError, match="lengths do not match"):
        fusion.write_trajectory(
            tmp_path / "trajectory.csv",
            rows,
            np.zeros((2, 3)),
            Rotation.identity(2),
        )


def test_body_to_color_transform_composes_factory_left_ir_extrinsic():
    body_from_left = np.eye(4)
    body_from_left[:3, 3] = [0.1, -0.2, 0.3]
    color_from_left = np.eye(4)
    color_from_left[:3, :3] = Rotation.from_euler(
        "z", 20.0, degrees=True
    ).as_matrix()
    color_from_left[:3, 3] = [0.01, 0.02, 0.03]
    stereo_report = {
        "observation_frame": "color_camera_i",
        "factory_stereo_calibration": {
            "color_rotation_from_left": color_from_left[:3, :3].tolist(),
            "color_translation_from_left_m": color_from_left[:3, 3].tolist(),
        },
    }

    body_from_color = fusion.body_t_color_from_stereo_report(
        body_from_left, stereo_report
    )

    np.testing.assert_allclose(
        body_from_color, body_from_left @ np.linalg.inv(color_from_left)
    )


def test_body_to_color_transform_rejects_legacy_left_ir_observations():
    with pytest.raises(ValueError, match="color camera frame"):
        fusion.body_t_color_from_stereo_report(
            np.eye(4),
            {
                "factory_stereo_calibration": {
                    "color_rotation_from_left": np.eye(3).tolist(),
                    "color_translation_from_left_m": [0.0, 0.0, 0.0],
                }
            },
        )


def test_body_to_trajectory_camera_keeps_left_ir_extrinsic_for_ir_input():
    body_from_left = np.eye(4)
    body_from_left[:3, 3] = [0.1, -0.2, 0.3]

    actual = fusion.body_t_trajectory_camera_from_stereo_report(
        body_from_left,
        {"observation_frame": "infrared_left_camera_i"},
    )

    np.testing.assert_allclose(actual, body_from_left)


def test_orientation_limit_expands_only_for_strong_stereo_imu_consensus():
    strong_edges = [
        {"visual_error_deg": 1.5, "imu_error_deg": 0.8},
        {"visual_error_deg": 1.6, "imu_error_deg": 0.9},
    ]
    weak_edges = [
        {"visual_error_deg": 1.4, "imu_error_deg": 1.2},
        {"visual_error_deg": 1.5, "imu_error_deg": 1.3},
    ]

    strong_limit, strong_policy = fusion.orientation_correction_limit(strong_edges)
    weak_limit, weak_policy = fusion.orientation_correction_limit(weak_edges)

    assert strong_limit == 4.0
    assert strong_policy == "stereo_strongly_supports_imu"
    assert weak_limit == 3.0
    assert weak_policy == "stereo_consensus_standard"


def test_auto_position_mode_preserves_shape_when_metric_scales_agree():
    mode, policy = fusion.select_position_mode(
        "auto", {"relative_difference": 0.04}
    )

    assert mode == "projected"
    assert policy == "stereo_imu_scale_consistent_preserve_visual_shape"


def test_auto_position_mode_uses_joint_inertial_for_scale_disagreement():
    mode, policy = fusion.select_position_mode(
        "auto", {"relative_difference": 0.06}
    )

    assert mode == "joint-inertial"
    assert policy == "stereo_imu_scale_disagreement_use_joint_inertial"


def test_auto_position_mode_requires_independent_imu_scale():
    with pytest.raises(ValueError, match="requires --imu-scale-report"):
        fusion.select_position_mode("auto", None)


def test_stereo_motion_aligns_attitude_to_translation_frame():
    positions = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
        ]
    )
    error = Rotation.from_euler("z", 1.5, degrees=True)
    rotations = Rotation.from_quat(
        np.repeat(error.as_quat()[None, :], len(positions), axis=0)
    )
    observations = [
        {
            "accepted": True,
            "first_index": first,
            "second_index": first + 1,
            "metric_displacement_camera_i_m": (
                positions[first + 1] - positions[first]
            ).tolist(),
            "pnp_inlier_ratio": 0.8,
        }
        for first in range(len(positions) - 1)
    ]

    aligned, quality = fusion.align_orientations_to_translation_frame(
        positions, rotations, observations
    )

    np.testing.assert_allclose(aligned.magnitude(), 0.0, atol=1e-10)
    assert quality["correction_applied_deg"] == pytest.approx(1.5)
    assert quality["after_median_deg"] < 1e-6
    assert quality["external_ground_truth_used"] is False


def test_translation_frame_alignment_is_skipped_without_residual_improvement():
    positions = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
        ]
    )
    rotations = Rotation.identity(len(positions))
    observations = [
        {
            "accepted": True,
            "first_index": first,
            "second_index": first + 1,
            "metric_displacement_camera_i_m": (
                positions[first + 1] - positions[first]
            ).tolist(),
            "pnp_inlier_ratio": 0.8,
        }
        for first in range(len(positions) - 1)
    ]

    aligned, quality = fusion.align_orientations_to_translation_frame(
        positions, rotations, observations
    )

    np.testing.assert_allclose(aligned.as_quat(), rotations.as_quat(), atol=1e-12)
    assert quality["accepted"] is False
    assert quality["rejection_reason"] == "insufficient_observability"
    assert quality["correction_applied_deg"] == pytest.approx(0.0)


def test_translation_frame_alignment_falls_back_when_edges_are_insufficient():
    positions = np.zeros((3, 3))
    rotations = Rotation.identity(3)

    aligned, quality = fusion.align_orientations_to_translation_frame(
        positions, rotations, []
    )

    np.testing.assert_allclose(aligned.as_quat(), rotations.as_quat())
    assert quality["accepted"] is False
    assert quality["rejection_reason"] == "insufficient_edges"


def test_position_fusion_reduces_stereo_edge_residual():
    positions = np.column_stack((np.arange(5, dtype=float), np.zeros((5, 2))))
    rotations = Rotation.identity(5)
    edges = [
        {
            "first_index": index,
            "second_index": index + 1,
            "metric_displacement_camera_i_m": [0.99, 0.01, 0.0],
            "pnp_inlier_ratio": 0.9,
        }
        for index in range(4)
    ]
    refined, quality = fusion.refine_positions(positions, rotations, edges)
    assert quality["stereo_edge_rmse_after_m"] < quality["stereo_edge_rmse_before_m"]
    assert quality["position_correction_max_m"] < 0.05
    np.testing.assert_allclose(refined[0], positions[0], atol=1e-4)


def test_projected_position_fusion_preserves_visual_direction():
    positions = np.column_stack((np.arange(5, dtype=float), np.zeros((5, 2))))
    rotations = Rotation.identity(5)
    edges = [
        {
            "first_index": index,
            "second_index": index + 1,
            "metric_displacement_camera_i_m": [0.98, 0.20, -0.10],
            "pnp_inlier_ratio": 0.9,
        }
        for index in range(4)
    ]
    refined, quality = fusion.refine_positions(
        positions, rotations, edges, target_mode="projected"
    )
    assert quality["mode"] == "projected_length_refinement"
    np.testing.assert_allclose(refined[:, 1:], 0.0, atol=1e-12)


def test_incremental_position_fusion_blends_only_accepted_edges():
    positions = np.column_stack((np.arange(6, dtype=float), np.zeros((6, 2))))
    observations = [
        {
            "first_index": index,
            "second_index": index + 1,
            "accepted": index != 1,
            "metric_displacement_camera_i_m": [0.8, 0.0, 0.0],
            "pnp_inlier_ratio": 0.8,
        }
        for index in range(5)
    ]
    refined, quality = fusion.refine_positions_incremental(
        positions,
        Rotation.identity(6),
        observations,
        blend=0.25,
    )
    np.testing.assert_allclose(refined[:, 0], [0.0, 0.997, 1.997, 2.994, 3.991, 4.988])
    assert quality["stereo_edges"] == 4
    assert quality["position_correction_requested_max_m"] == pytest.approx(0.20)
    assert quality["position_correction_max_m"] == pytest.approx(0.012)
    assert quality["correction_scale"] == pytest.approx(0.06)


def test_incremental_position_fusion_uses_shortest_hop_chain():
    positions = np.column_stack((np.arange(6.0), np.zeros((6, 2))))
    observations = [
        {
            "accepted": True,
            "first_index": index,
            "second_index": index + 1,
            "sample_hop": 1,
            "metric_displacement_camera_i_m": [1.0, 0.0, 0.0],
            "pnp_inlier_ratio": 0.8,
        }
        for index in range(5)
    ]
    observations.append(
        {
            "accepted": True,
            "first_index": 0,
            "second_index": 2,
            "sample_hop": 2,
            "metric_displacement_camera_i_m": [2.0, 0.0, 0.0],
            "pnp_inlier_ratio": 0.8,
        }
    )

    refined, quality = fusion.refine_positions_incremental(
        positions,
        Rotation.identity(6),
        observations,
    )

    np.testing.assert_allclose(refined, positions)
    assert quality["candidate_observations"] == 6
    assert quality["selected_sample_hop"] == 1
    assert quality["selected_chain_observations"] == 5


def test_joint_visual_inertial_position_fusion_reduces_local_shape_error():
    visual_times = np.linspace(0.0, 2.0, 21)
    true_positions = np.column_stack(
        (0.10 * visual_times, np.zeros((len(visual_times), 2)))
    )
    positions = true_positions.copy()
    positions[8:13, 1] += np.array([0.004, 0.008, 0.012, 0.008, 0.004])
    rotations = Rotation.identity(len(visual_times))
    imu_times = np.linspace(0.0, 2.0, 801)
    gyro = np.zeros((len(imu_times), 3))
    accel = np.tile([0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1))
    observations = [
        {
            "accepted": True,
            "first_index": index,
            "second_index": index + 2,
            "metric_displacement_camera_i_m": [0.02, 0.0, 0.0],
            "pnp_inlier_ratio": 0.9,
        }
        for index in range(0, len(visual_times) - 2, 2)
    ]

    refined, quality = fusion.refine_positions_visual_inertial(
        positions,
        rotations,
        observations,
        visual_times,
        imu_times,
        gyro,
        accel,
        np.eye(4),
        td_s=0.0,
        node_stride=2,
    )

    before = np.sqrt(np.mean(np.sum((positions - true_positions) ** 2, axis=1)))
    after = np.sqrt(np.mean(np.sum((refined - true_positions) ** 2, axis=1)))
    assert after < before
    assert quality["stereo_edge_rmse_after_m"] < quality["stereo_edge_rmse_before_m"]
    assert quality["position_correction_max_m"] <= 0.020 + 1e-9
    assert np.linalg.norm(quality["gravity_solution_mps2"]) == pytest.approx(
        fusion.STANDARD_GRAVITY, abs=0.2
    )


def test_relaxed_visual_position_prior_allows_more_observation_correction():
    visual_times = np.linspace(0.0, 2.0, 21)
    true_positions = np.column_stack(
        (0.10 * visual_times, np.zeros((len(visual_times), 2)))
    )
    positions = true_positions.copy()
    positions[6:15, 1] += 0.015
    rotations = Rotation.identity(len(visual_times))
    imu_times = np.linspace(0.0, 2.0, 801)
    gyro = np.zeros((len(imu_times), 3))
    accel = np.tile([0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1))
    observations = [
        {
            "accepted": True,
            "first_index": index,
            "second_index": index + 2,
            "metric_displacement_camera_i_m": [0.02, 0.0, 0.0],
            "pnp_inlier_ratio": 0.95,
        }
        for index in range(0, len(visual_times) - 2, 2)
    ]

    default, default_quality = fusion.refine_positions_visual_inertial(
        positions,
        rotations,
        observations,
        visual_times,
        imu_times,
        gyro,
        accel,
        np.eye(4),
        td_s=0.0,
        node_stride=2,
    )
    relaxed, relaxed_quality = fusion.refine_positions_visual_inertial(
        positions,
        rotations,
        observations,
        visual_times,
        imu_times,
        gyro,
        accel,
        np.eye(4),
        td_s=0.0,
        node_stride=2,
        visual_position_sigma_m=0.040,
    )

    default_error = np.linalg.norm(default - true_positions, axis=1).max()
    relaxed_error = np.linalg.norm(relaxed - true_positions, axis=1).max()
    assert relaxed_error < default_error
    assert relaxed_quality["position_correction_max_m"] > default_quality[
        "position_correction_max_m"
    ]
    assert relaxed_quality["visual_position_sigma_m"] == pytest.approx(0.040)


def test_joint_fusion_uses_aligned_relative_motion_factors():
    visual_times = np.linspace(0.0, 2.0, 21)
    true_positions = np.column_stack(
        (0.10 * visual_times, np.zeros((len(visual_times), 2)))
    )
    positions = true_positions.copy()
    positions[7:14, 1] += np.array(
        [0.003, 0.006, 0.009, 0.012, 0.009, 0.006, 0.003]
    )
    rotations = Rotation.identity(len(visual_times))
    imu_times = np.linspace(0.0, 2.0, 801)
    gyro = np.zeros((len(imu_times), 3))
    accel = np.tile(
        [0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1)
    )
    observations = [
        {
            "accepted": True,
            "first_index": index,
            "second_index": index + 2,
            "metric_displacement_camera_i_m": [0.02, 0.0, 0.0],
            "pnp_inlier_ratio": 0.5,
        }
        for index in range(0, len(visual_times) - 2, 2)
    ]

    refined, quality = fusion.refine_positions_visual_inertial(
        positions,
        rotations,
        observations,
        visual_times,
        imu_times,
        gyro,
        accel,
        np.eye(4),
        td_s=0.0,
        node_stride=2,
        relative_motion_positions_body=true_positions,
    )

    before = np.sqrt(np.mean(np.sum((positions - true_positions) ** 2, axis=1)))
    after = np.sqrt(np.mean(np.sum((refined - true_positions) ** 2, axis=1)))
    assert after < before
    assert quality["relative_motion_edges"] == 10
    assert quality["relative_motion_rmse_after_m"] < quality["relative_motion_rmse_before_m"]
    assert quality["robust_relative_motion_inliers"] > 0


def test_relative_motion_alignment_interpolates_without_changing_metric_scale():
    query_times = np.array([1.0, 1.5, 2.0])
    target = np.array(
        [[2.0, -0.5, 0.0], [2.0, 0.0, 0.0], [2.0, 0.5, 0.0]]
    )
    reference_times = np.array([0.5, 1.0, 1.5, 2.0, 2.5])
    reference = np.column_stack(
        (reference_times - 1.5, np.zeros((len(reference_times), 2)))
    )

    aligned, valid, quality = fusion.align_relative_motion_positions(
        query_times, target, reference_times, reference
    )

    np.testing.assert_allclose(aligned, target, atol=1e-12)
    np.testing.assert_allclose(
        np.linalg.norm(np.diff(aligned, axis=0), axis=1),
        np.linalg.norm(np.diff(reference[1:4], axis=0), axis=1),
        atol=1e-12,
    )
    np.testing.assert_array_equal(valid, [True, True, True])
    assert quality["alignment"] == "robust_SE3_relative_motion_to_visual_body_no_scale"


def test_relative_motion_alignment_requires_timestamp_overlap():
    with pytest.raises(ValueError, match="insufficient timestamp overlap"):
        fusion.align_relative_motion_positions(
            np.array([0.0, 1.0]),
            np.zeros((2, 3)),
            np.array([2.0, 3.0]),
            np.zeros((2, 3)),
        )


def test_relative_motion_report_allows_only_raw_jump_as_degraded_input(tmp_path):
    session = tmp_path / "session"
    trajectory = tmp_path / "vio.csv"
    report = {
        "schema": "umi_docker2_run_acceptance_v1",
        "result": "FAIL",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "failure_scope": "SLAM",
        "runtime_error": None,
        "runtime_watchdog": {"failures": ["raw_trajectory_jump"]},
        "session": str(session),
        "corrected_trajectory": str(trajectory),
        "corrected_odometry_samples": 10,
    }

    quality = fusion.validate_relative_motion_report(
        report, tmp_path / "report.json", trajectory, session, 10
    )

    assert quality["policy"] == "degraded_raw_jump_robust_downweight"
    assert quality["source_result"] == "FAIL"


def test_relative_motion_report_rejects_other_failures(tmp_path):
    session = tmp_path / "session"
    trajectory = tmp_path / "vio.csv"
    report = {
        "schema": "umi_docker2_run_acceptance_v1",
        "result": "FAIL",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "failure_scope": "SLAM",
        "runtime_error": None,
        "runtime_watchdog": {"failures": ["raw_trajectory_stale"]},
        "session": str(session),
        "corrected_trajectory": str(trajectory),
        "corrected_odometry_samples": 10,
    }

    with pytest.raises(ValueError, match="did not pass"):
        fusion.validate_relative_motion_report(
            report, tmp_path / "report.json", trajectory, session, 10
        )


def test_relative_motion_alignment_is_robust_to_position_spike():
    query_times = np.arange(20, dtype=float)
    target = np.column_stack((0.02 * query_times, np.zeros((20, 2))))
    reference = target.copy()
    reference[9:12, 1] += 0.5

    aligned, valid, quality = fusion.align_relative_motion_positions(
        query_times, target, query_times, reference
    )

    assert np.count_nonzero(valid) == 20
    assert quality["alignment_outliers"] >= 3
    np.testing.assert_allclose(aligned[[0, 5, 15, 19]], target[[0, 5, 15, 19]], atol=1e-9)


def test_full_rate_imu_refinement_downweights_visual_position_spike():
    visual_times = np.linspace(0.0, 2.0, 61)
    true_positions = np.column_stack(
        (0.10 * visual_times, np.zeros((len(visual_times), 2)))
    )
    positions = true_positions.copy()
    positions[len(positions) // 2, 1] = 0.010
    rotations = Rotation.identity(len(visual_times))
    imu_times = np.linspace(0.0, 2.0, 801)
    accel = np.tile(
        [0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1)
    )

    refined, quality = fusion.refine_positions_full_rate_imu(
        positions,
        rotations,
        visual_times,
        imu_times,
        accel,
        np.eye(4),
        td_s=0.0,
        gravity_world=np.array([0.0, 0.0, -fusion.STANDARD_GRAVITY]),
    )

    before = np.linalg.norm(positions - true_positions, axis=1)
    after = np.linalg.norm(refined - true_positions, axis=1)
    assert len(refined) == len(positions)
    assert after.max() < before.max()
    assert quality["visual_downweighted_frames"] > 0
    assert quality["acceleration_residual_after_max_mps2"] < quality[
        "acceleration_residual_before_max_mps2"
    ]
    assert quality["correction_max_m"] <= 0.012 + 1e-12


def test_interpolation_stencil_uses_neighboring_keyframes():
    left, right, alpha = fusion.interpolation_stencil(
        np.array([0, 2, 5, 8, 10]), np.array([0, 5, 10])
    )

    np.testing.assert_array_equal(left, [0, 0, 1, 1, 2])
    np.testing.assert_array_equal(right, [0, 1, 1, 2, 2])
    np.testing.assert_allclose(alpha, [0.0, 0.4, 0.0, 0.6, 0.0])


def test_load_mast3r_keyframes_matches_relative_trajectory_time(tmp_path):
    for timestamp in ("0.0", "0.10", "0.21"):
        (tmp_path / f"{timestamp}.png").touch()
    times = np.array([100.0, 100.033, 100.101, 100.150, 100.209, 100.240])

    indices, quality = fusion.load_mast3r_keyframe_indices(tmp_path, times)

    np.testing.assert_array_equal(indices, [0, 2, 4, 5])
    assert quality["saved_keyframes"] == 3
    assert quality["correction_nodes"] == 4
    assert quality["max_timestamp_match_error_s"] < 0.002


def test_keyframe_correction_nodes_are_densified_by_position_stride():
    nodes, quality = fusion.densify_correction_nodes(
        np.array([0, 20, 95]), sample_count=96, stride=30
    )

    np.testing.assert_array_equal(nodes, [0, 20, 30, 60, 90, 95])
    assert quality["keyframe_nodes"] == 3
    assert quality["regular_nodes"] == 5
    assert quality["correction_nodes"] == 6
    assert quality["maximum_gap_frames"] <= 30


def test_keyframe_only_correction_nodes_preserve_visual_support():
    nodes, quality = fusion.select_keyframe_correction_nodes(
        np.array([0, 20, 95]),
        sample_count=96,
        stride=30,
        keyframe_only=True,
    )

    np.testing.assert_array_equal(nodes, [0, 20, 95])
    assert quality["keyframe_nodes"] == 3
    assert quality["regular_nodes"] == 0
    assert quality["correction_nodes"] == 3
    assert quality["maximum_gap_frames"] == 75


def test_keyframe_graph_joint_fusion_propagates_sparse_corrections():
    visual_times = np.linspace(0.0, 2.0, 21)
    true_positions = np.column_stack(
        (0.10 * visual_times, np.zeros((len(visual_times), 2)))
    )
    positions = true_positions.copy()
    positions[:, 1] = np.linspace(0.0, 0.018, len(positions))
    rotations = Rotation.identity(len(visual_times))
    imu_times = np.linspace(0.0, 2.0, 801)
    gyro = np.zeros((len(imu_times), 3))
    accel = np.tile([0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1))
    observations = [
        {
            "accepted": True,
            "first_index": index,
            "second_index": index + 2,
            "metric_displacement_camera_i_m": [0.02, 0.0, 0.0],
            "pnp_inlier_ratio": 0.9,
        }
        for index in range(0, len(visual_times) - 2, 2)
    ]

    refined, quality = fusion.refine_positions_visual_inertial(
        positions,
        rotations,
        observations,
        visual_times,
        imu_times,
        gyro,
        accel,
        np.eye(4),
        td_s=0.0,
        correction_node_indices=np.array([0, 10, 20]),
    )

    before = np.sqrt(np.mean(np.sum((positions - true_positions) ** 2, axis=1)))
    after = np.sqrt(np.mean(np.sum((refined - true_positions) ** 2, axis=1)))
    assert after < before
    assert quality["node_policy"] == "mast3r_keyframes"
    assert quality["nodes"] == 3
    assert quality["stereo_edge_rmse_after_m"] < quality["stereo_edge_rmse_before_m"]
    assert abs(refined[-1, 1]) < 0.8 * abs(positions[-1, 1])


def test_gyro_fusion_preserves_consistent_rotation():
    visual_times = np.array([0.0, 1.0, 2.0])
    visual = Rotation.from_rotvec(
        np.column_stack((np.zeros(3), np.zeros(3), 0.1 * visual_times))
    )
    imu_times = np.linspace(0.0, 2.0, 801)
    gyro = np.tile([0.0, 0.0, 0.1], (len(imu_times), 1))
    corrected, quality = fusion.refine_orientations(
        visual,
        np.arange(3),
        visual_times,
        imu_times,
        gyro,
        Rotation.identity(),
        td_s=0.0,
    )
    assert quality["after_p95_deg"] < 1e-5
    np.testing.assert_allclose(corrected.as_matrix(), visual.as_matrix(), atol=1e-7)


def test_gyro_fusion_bounds_isolated_visual_orientation_outlier():
    visual_times = np.linspace(0.0, 6.0, 61)
    visual_rotvecs = np.zeros((len(visual_times), 3))
    visual_rotvecs[len(visual_times) // 2, 2] = np.radians(4.0)
    visual = Rotation.from_rotvec(visual_rotvecs)
    imu_times = np.linspace(0.0, 6.0, 2401)
    gyro = np.zeros((len(imu_times), 3))

    _, quality = fusion.refine_orientations(
        visual,
        np.arange(len(visual_times)),
        visual_times,
        imu_times,
        gyro,
        Rotation.identity(),
        td_s=0.0,
    )

    assert quality["orientation_correction_requested_max_deg"] > 2.0
    assert quality["orientation_correction_max_deg"] <= 2.0 + 1e-9
    assert quality["after_p95_deg"] < 1.0
    assert quality["optimizer_success"] is True


def test_stereo_imu_consensus_downweights_visual_rotation_outlier():
    visual_times = np.array([0.0, 1.0, 2.0])
    visual = Rotation.from_rotvec(
        np.radians(
            [
                [0.0, 0.0, 0.0],
                [0.0, 0.0, 9.0],
                [0.0, 0.0, 10.0],
            ]
        )
    )
    imu_times = np.linspace(0.0, 2.0, 801)
    gyro = np.tile([0.0, 0.0, np.radians(5.0)], (len(imu_times), 1))
    pnp_delta = Rotation.from_euler("z", -5.0, degrees=True).as_quat().tolist()
    stereo = [
        {
            "accepted": True,
            "first_index": index,
            "second_index": index + 1,
            "pnp_rotation_quaternion_xyzw": pnp_delta,
            "pnp_inlier_ratio": 0.9,
        }
        for index in range(2)
    ]

    corrected, quality = fusion.refine_orientations(
        visual,
        np.arange(3),
        visual_times,
        imu_times,
        gyro,
        Rotation.identity(),
        td_s=0.0,
        stereo_observations=stereo,
    )

    corrected_delta_deg = np.degrees(
        (corrected[0].inv() * corrected[1]).magnitude()
    )
    assert corrected_delta_deg < 7.0
    assert quality["stereo_rotation_edges"] == 2
    assert quality["visual_downweighted_nodes"] == 3
    assert quality["stereo_rotation_after_p95_deg"] < 2.0


def test_stereo_report_must_contain_local_displacements():
    with pytest.raises(ValueError, match="predates local displacement"):
        fusion.accepted_stereo_edges(
            {
                "observations": [
                    {
                        "accepted": True,
                        "first_index": index,
                        "second_index": index + 1,
                    }
                    for index in range(4)
                ]
            }
        )

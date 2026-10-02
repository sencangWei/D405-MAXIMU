import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "dual_joint_fusion", ROOT / "scripts/fuse_mast3r_stereo_imu.py"
)
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)


def solve(secondary=None, *, use_visual_position_prior=True, visual_shift=0.0):
    times = np.linspace(0, 2, 21)
    truth = np.column_stack((0.1 * times, np.zeros((21, 2))))
    visual = truth.copy()
    visual[6:15, 1] += 0.015
    visual[6:15, 1] += visual_shift
    imu_times = np.linspace(0, 2, 801)
    stereo = [
        {
            "accepted": True, "first_index": i, "second_index": i + 2,
            "metric_displacement_camera_i_m": [0.02, 0, 0],
            "pnp_inlier_ratio": 0.5,
        }
        for i in range(0, 19, 2)
    ]
    factors = None if secondary is None else [
        {
            "first_index": i, "second_index": i + 6,
            "metric_displacement_world_m": (truth[i + 6] - truth[i]).tolist(),
            "confidence": secondary,
        }
        for i in range(15)
    ]
    refined, report = fusion.refine_positions_visual_inertial(
        visual, Rotation.identity(21), stereo, times, imu_times,
        np.zeros((801, 3)), np.tile([0, 0, 9.80665], (801, 1)),
        np.eye(4), td_s=0, node_stride=2 if use_visual_position_prior else 1,
        max_correction_m=0.02 if use_visual_position_prior else 1.0,
        secondary_visual_factors=factors,
        use_visual_position_prior=use_visual_position_prior,
    )
    return refined, truth, report


def test_secondary_local_factors_reduce_primary_shape_error():
    baseline, truth, _ = solve()
    dual, _, report = solve(1.0)
    assert np.max(np.linalg.norm(dual - truth, axis=1)) < np.max(
        np.linalg.norm(baseline - truth, axis=1)
    )
    assert report["secondary_visual_motion"]["edges"] == 15
    assert report["secondary_visual_motion"]["rmse_after_m"] < report[
        "secondary_visual_motion"
    ]["rmse_before_m"]
    assert report["secondary_visual_motion"]["external_ground_truth_used"] is False


def test_missing_secondary_preserves_existing_solver_and_zero_confidence_has_no_influence():
    baseline, _, baseline_report = solve()
    unavailable, _, report = solve(0.0)
    np.testing.assert_allclose(unavailable, baseline, atol=1e-9)
    assert "secondary_visual_motion" not in baseline_report
    assert report["secondary_visual_motion"]["prior_confidence_median"] == 0


def test_secondary_frontend_is_explicitly_limited_to_joint_left_ir():
    args = SimpleNamespace(
        secondary_right_trajectory=Path("right.csv"),
        stream="infrared_right", position_mode="keyframe-graph",
    )
    with pytest.raises(ValueError, match="left-IR joint"):
        fusion.prepare_secondary_visual_inputs(args, None, None, None, None, None, None, None)


def test_no_secondary_does_not_read_any_additional_sensor_product():
    assert fusion.prepare_secondary_visual_inputs(
        SimpleNamespace(), None, None, None, None, None, None, None
    ) == (None, None)


def test_body_graph_does_not_anchor_to_initial_trajectory_shape():
    neutral, _, report = solve(1.0, use_visual_position_prior=False)
    biased, _, _ = solve(
        1.0, use_visual_position_prior=False, visual_shift=0.03
    )
    np.testing.assert_allclose(neutral, biased, atol=2e-7)
    assert report["visual_position_prior_enabled"] is False
    assert report["correction_smoothness_prior_enabled"] is False


def test_symmetric_shared_graph_recovers_alternating_eye_gaps_and_is_swap_invariant():
    from ego_vio.vio.symmetric_ir_factors import build_symmetric_ir_factors

    times = np.linspace(0, 0.4, 21)
    truth = np.column_stack((0.25 * times, np.zeros((21, 2))))
    tracks = []
    for eye, reliable_half in (("left", 0), ("right", 1)):
        observations = [
            {"accepted": True, "first_index": i, "second_index": i + 1,
             "first_t_sec": times[i], "second_t_sec": times[i + 1],
             "metric_displacement_frame": f"infrared_{eye}_camera_i",
             "metric_displacement_camera_i_m": (truth[i + 1] - truth[i]).tolist()}
            for i in range(20)
        ]
        tracks.append({
            "eye": eye, "times": times, "metric_camera_positions": truth,
            "camera_rotations": Rotation.identity(21).as_matrix(),
            "body_t_camera": np.eye(4), "observations": observations,
            "observation_confidences": [
                float((i >= 10) == bool(reliable_half)) for i in range(20)
            ],
        })

    def solve_tracks(ordered):
        factors, stereo, _ = build_symmetric_ir_factors(
            times, Rotation.identity(21).as_matrix(), ordered
        )
        assert {factor["eye"] for factor in factors} == {"left", "right"}
        imu_times = np.linspace(0, 0.4, 161)
        refined, _ = fusion.refine_positions_visual_inertial(
            np.zeros_like(truth), Rotation.identity(21), stereo, times,
            imu_times, np.zeros((161, 3)), np.tile([0, 0, 9.80665], (161, 1)),
            np.eye(4), td_s=0, node_stride=1, max_correction_m=1,
            secondary_visual_factors=factors, use_visual_position_prior=False,
            stereo_factor_confidences=np.ones(len(stereo)),
        )
        return refined

    direct = solve_tracks(tracks)
    swapped = solve_tracks(tracks[::-1])
    np.testing.assert_allclose(direct, swapped, atol=1e-9)
    np.testing.assert_allclose(direct, truth, atol=1e-7)


def test_symmetric_mode_does_not_count_initializer_twice_as_stationary_consensus():
    times = np.linspace(0, 2, 21)
    positions = np.zeros((21, 3))
    positions[:, 1] = 0.0002 * np.sin(np.pi * times)
    stereo = [{"accepted": True, "first_index": i, "second_index": i + 1,
               "metric_displacement_camera_i_m": [0, 0, 0]} for i in range(20)]
    imu_times = np.linspace(0, 2, 801)
    refined, report = fusion.refine_positions_visual_inertial(
        positions, Rotation.identity(21), stereo, times, imu_times,
        np.zeros((801, 3)), np.tile([0, 0, 9.80665], (801, 1)),
        np.eye(4), td_s=0, node_stride=1, max_correction_m=1,
        use_visual_position_prior=False,
        relative_motion_positions_body=positions,
        relative_motion_valid=np.ones(21, dtype=bool),
    )
    assert np.all(np.isfinite(refined))
    # Here the initializer and relative-motion source are the same odometry.
    # Counting them as independent visual/VINS consensus would be false.
    assert report["stationary_motion_guard"]["protected_frames"] == 0

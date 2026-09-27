import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "scripts" / "fuse_docker2_mast3r_complementary.py"
spec = importlib.util.spec_from_file_location(path.stem, path)
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)


def write_run_report(path, trajectory, *, result, failures, runtime_error=None):
    path.write_text(
        json.dumps(
            {
                "schema": "umi_docker2_run_acceptance_v1",
                "result": result,
                "slam_supervision": False,
                "external_ground_truth_used": False,
                "runtime_error": runtime_error,
                "runtime_watchdog": {"failures": failures},
                "corrected_trajectory": str(trajectory),
            }
        ),
        encoding="utf-8",
    )


def test_metric_scale_ratio_recovers_short_term_scale():
    times = np.arange(0.0, 20.0, 1.0 / 30.0)
    base = np.column_stack(
        (0.05 * times, 0.02 * np.sin(times), np.zeros_like(times))
    )
    metric = 1.03 * base

    ratio, quality = fusion.robust_metric_scale_ratio(
        times, base, metric, horizon_s=1.0
    )

    assert ratio == pytest.approx(1.03, rel=1e-6)
    assert quality["robust_inliers"] >= 50


def test_raw_jump_report_disables_only_local_translation(tmp_path):
    trajectory = tmp_path / "trajectory.csv"
    trajectory.touch()
    report = tmp_path / "run_acceptance.json"
    write_run_report(
        report,
        trajectory,
        result="FAIL",
        failures=["raw_trajectory_jump"],
    )

    policy = fusion.docker2_position_policy(report, trajectory)

    assert policy["local_translation_allowed"] is False
    assert policy["policy"] == (
        "raw_jump_local_translation_disabled_attitude_and_scale_retained"
    )


def test_pass_report_allows_local_translation(tmp_path):
    trajectory = tmp_path / "trajectory.csv"
    trajectory.touch()
    report = tmp_path / "run_acceptance.json"
    write_run_report(report, trajectory, result="PASS", failures=[])

    policy = fusion.docker2_position_policy(report, trajectory)

    assert policy["local_translation_allowed"] is True


def test_unrelated_run_failure_is_rejected(tmp_path):
    trajectory = tmp_path / "trajectory.csv"
    trajectory.touch()
    report = tmp_path / "run_acceptance.json"
    write_run_report(report, trajectory, result="FAIL", failures=["stale_output"])

    with pytest.raises(ValueError, match="not safe"):
        fusion.docker2_position_policy(report, trajectory)


def test_complementary_filter_rejects_constant_frame_offset():
    times = np.arange(0.0, 20.0, 1.0 / 30.0)
    base = np.column_stack((times, np.zeros_like(times), np.zeros_like(times)))
    metric = base + np.asarray([0.2, -0.1, 0.05])

    fused, quality = fusion.complementary_positions(
        times,
        base,
        metric,
        scale_ratio=1.0,
        smoothing_s=4.0,
        local_weight=0.5,
    )

    np.testing.assert_allclose(fused, base, atol=1e-10)
    assert quality["injected_correction_max_mm"] < 1e-6


def test_joint_log_scale_ratio_balances_two_metric_sources():
    ratio, disagreement = fusion.joint_log_scale_ratio(1.0609)

    assert ratio == pytest.approx(1.03)
    assert disagreement < 0.15


def graph_report(*, scale_disagreement=0.015, stereo_rmse_m=0.003):
    return {
        "schema": "umi_mast3r_stereo_imu_fusion_v2",
        "result": "PASS",
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "metric_scale_consistency": {"relative_difference": scale_disagreement},
        "stereo_translation_fusion": {
            "stereo_edge_rmse_after_m": stereo_rmse_m
        },
    }


def test_auto_scale_vote_accepts_consistent_informative_candidate():
    weight, evidence = fusion.select_docker2_scale_weight(
        graph_report(), 1.0435, 0.0365
    )

    assert weight == pytest.approx(0.475)
    assert evidence["enabled"] is True


@pytest.mark.parametrize(
    ("report", "ratio", "disagreement"),
    [
        (graph_report(scale_disagreement=0.04), 1.0435, 0.0365),
        (graph_report(), 1.005, 0.0365),
        (graph_report(), 1.0435, 0.075),
        (graph_report(stereo_rmse_m=0.005), 1.0435, 0.0365),
    ],
)
def test_auto_scale_vote_rejects_unreliable_candidate(
    report, ratio, disagreement
):
    weight, evidence = fusion.select_docker2_scale_weight(
        report, ratio, disagreement
    )

    assert weight == 0.0
    assert evidence["enabled"] is False


def test_adaptive_filter_prefers_smoother_source_at_isolated_spike():
    times = np.arange(0.0, 20.0, 1.0 / 30.0)
    base = np.column_stack((0.02 * times, np.zeros_like(times), np.zeros_like(times)))
    metric = base.copy()
    base[300, 1] = 0.020

    _, quality = fusion.complementary_positions(
        times,
        base,
        metric,
        scale_ratio=1.0,
        smoothing_s=4.0,
        local_weight=0.4,
        adaptive_local_weight=True,
        roughness_threshold_m=0.009,
        adaptive_weight_strength=0.3,
    )

    assert quality["effective_local_weight_max"] > 0.6


def test_adaptive_filter_caps_untrusted_auxiliary_injection():
    times = np.arange(0.0, 20.0, 1.0 / 30.0)
    base = np.column_stack((0.02 * times, np.zeros_like(times), np.zeros_like(times)))
    metric = base.copy()
    metric[300:330, 1] = 0.5

    fused, quality = fusion.complementary_positions(
        times,
        base,
        metric,
        scale_ratio=1.0,
        smoothing_s=4.0,
        local_weight=0.4,
        adaptive_local_weight=True,
        roughness_threshold_m=0.009,
        adaptive_weight_strength=0.3,
    )

    correction = np.linalg.norm(fused - base, axis=1)
    assert np.max(correction) <= 0.009 + 1e-12
    assert quality["injection_clipped_frames"] > 0
    assert quality["injection_limit_mm"] == pytest.approx(9.0)


def test_imu_orientation_prior_applies_calibrated_lever_arm():
    prior = Rotation.from_euler("z", [0.0, 45.0, 90.0], degrees=True)
    body_t_camera = np.eye(4)
    body_t_camera[:3, 3] = [0.10, 0.0, 0.0]
    camera_positions = prior.apply(body_t_camera[:3, 3])

    body_positions, body_rotations, quality = (
        fusion.camera_to_body_with_body_orientation_prior(
            camera_positions,
            prior,
            body_t_camera,
            prior,
        )
    )

    np.testing.assert_allclose(body_positions, 0.0, atol=1e-12)
    np.testing.assert_allclose(body_rotations.as_matrix(), prior.as_matrix())
    assert quality["visual_prior_disagreement_max_deg"] < 1e-10

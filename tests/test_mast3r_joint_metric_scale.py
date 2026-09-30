import importlib.util
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fuse_mast3r_stereo_imu.py"
SPEC = importlib.util.spec_from_file_location(SCRIPT.stem, SCRIPT)
fusion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fusion)


def _load_fusion_inputs(
    *,
    scale_error: float = 1.03,
    lever_m: float = 0.0,
    rotating: bool = False,
    duration_s: float = 3.0,
    frame_count: int = 31,
) -> dict:
    visual_times = np.linspace(0.0, duration_s, frame_count)
    initial_velocity = np.array([0.085, 0.018, 0.0])
    acceleration_world = np.array([0.018, -0.004, 0.0])
    body_positions = (
        initial_velocity[None, :] * visual_times[:, None]
        + 0.5 * acceleration_world[None, :] * visual_times[:, None] ** 2
    )
    if rotating:
        yaw = 0.42 * visual_times
        camera_rotations = Rotation.from_euler("z", yaw)
        gyro_body = None
    else:
        camera_rotations = Rotation.identity(frame_count)
        gyro_body = None

    body_t_camera = np.eye(4)
    body_t_camera[:3, 3] = [lever_m, 0.0, 0.0]
    lever_world = camera_rotations.apply(body_t_camera[:3, 3])
    true_camera_positions = body_positions + lever_world
    measured_camera_positions = (
        true_camera_positions[0]
        + scale_error * (true_camera_positions - true_camera_positions[0])
    )

    observations = []
    for first in range(0, frame_count - 3, 3):
        for hop in (3, 6):
            second = first + hop
            if second >= frame_count:
                continue
            camera_i_delta = camera_rotations[first].inv().apply(
                true_camera_positions[second] - true_camera_positions[first]
            )
            observations.append(
                {
                    "accepted": True,
                    "first_index": first,
                    "second_index": second,
                    "metric_displacement_camera_i_m": camera_i_delta.tolist(),
                    "pnp_inlier_ratio": 0.95,
                }
            )

    imu_times = np.linspace(0.0, duration_s, int(duration_s * 400) + 1)
    if rotating:
        imu_rotations = Rotation.from_euler("z", 0.42 * imu_times)
        gyro = np.tile([0.0, 0.0, 0.42], (len(imu_times), 1))
    else:
        imu_rotations = Rotation.identity(len(imu_times))
        gyro = np.zeros((len(imu_times), 3))
    specific_force_world = acceleration_world + np.array(
        [0.0, 0.0, fusion.STANDARD_GRAVITY]
    )
    accel = imu_rotations.inv().apply(
        np.repeat(specific_force_world[None, :], len(imu_times), axis=0)
    )

    return {
        "visual_times": visual_times,
        "true_body_positions": body_positions,
        "true_camera_positions": true_camera_positions,
        "measured_camera_positions": measured_camera_positions,
        "camera_rotations": camera_rotations,
        "observations": observations,
        "imu_times": imu_times,
        "gyro": gyro if gyro_body is None else gyro_body,
        "accel": accel,
        "body_t_camera": body_t_camera,
    }


def test_joint_metric_scale_corrects_synthetic_three_percent_camera_scale_error():
    data = _load_fusion_inputs(scale_error=1.03)

    refined, quality = fusion.refine_positions_visual_inertial(
        data["measured_camera_positions"],
        data["camera_rotations"],
        data["observations"],
        data["visual_times"],
        data["imu_times"],
        data["gyro"],
        data["accel"],
        data["body_t_camera"],
        td_s=0.0,
        node_stride=3,
        max_correction_m=0.05,
        relative_motion_positions_body=data["true_body_positions"],
        solve_metric_scale=True,
    )

    before = np.sqrt(
        np.mean(
            np.sum(
                (data["measured_camera_positions"] - data["true_camera_positions"])
                ** 2,
                axis=1,
            )
        )
    )
    after = np.sqrt(
        np.mean(np.sum((refined - data["true_camera_positions"]) ** 2, axis=1))
    )
    assert after < 0.35 * before
    assert quality["joint_metric_scale"]["enabled"] is True
    assert quality["joint_metric_scale"]["estimated_ratio"] == pytest.approx(
        1.0 / 1.03, abs=0.006
    )
    assert quality["position_correction_max_m"] < 0.05
    assert quality["correction_clipped_frames"] == 0


def test_joint_metric_scale_does_not_scale_fixed_camera_lever_into_body_origin():
    data = _load_fusion_inputs(scale_error=1.03, lever_m=0.03, rotating=True)

    refined, quality = fusion.refine_positions_visual_inertial(
        data["measured_camera_positions"],
        data["camera_rotations"],
        data["observations"],
        data["visual_times"],
        data["imu_times"],
        data["gyro"],
        data["accel"],
        data["body_t_camera"],
        td_s=0.0,
        node_stride=3,
        max_correction_m=0.05,
        relative_motion_positions_body=data["true_body_positions"],
        solve_metric_scale=True,
    )

    lever_world = data["camera_rotations"].apply(data["body_t_camera"][:3, 3])
    measured_body = data["measured_camera_positions"] - lever_world
    refined_body = refined - lever_world
    measured_body_error = np.linalg.norm(
        measured_body - data["true_body_positions"], axis=1
    )
    refined_body_error = np.linalg.norm(
        refined_body - data["true_body_positions"], axis=1
    )

    assert refined_body_error.max() < 0.35 * measured_body_error.max()
    assert refined_body_error.max() < 0.004
    assert quality["joint_metric_scale"]["estimated_ratio"] == pytest.approx(
        1.0 / 1.03, abs=0.008
    )


def test_joint_metric_scale_requires_translation_excitation_before_estimating_scale():
    frame_count = 12
    visual_times = np.linspace(0.0, 1.1, frame_count)
    positions = np.zeros((frame_count, 3))
    rotations = Rotation.identity(frame_count)
    observations = [
        {
            "accepted": True,
            "first_index": first,
            "second_index": first + 2,
            "metric_displacement_camera_i_m": [0.0, 0.0, 0.0],
            "pnp_inlier_ratio": 0.95,
        }
        for first in range(0, frame_count - 2, 2)
    ]
    imu_times = np.linspace(0.0, 1.1, 441)
    gyro = np.zeros((len(imu_times), 3))
    accel = np.tile([0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1))

    inactive_refined, inactive_quality = fusion.refine_positions_visual_inertial(
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
        max_correction_m=0.05,
        solve_metric_scale=False,
    )
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
        max_correction_m=0.05,
        solve_metric_scale=True,
    )

    np.testing.assert_allclose(refined, positions, atol=1e-8)
    np.testing.assert_allclose(refined, inactive_refined, atol=0.0, rtol=0.0)
    assert inactive_quality["joint_metric_scale"]["enabled"] is False
    assert quality["joint_metric_scale"]["enabled"] is True
    assert quality["joint_metric_scale"]["estimated_ratio"] == pytest.approx(
        1.0, abs=1e-9
    )
    assert quality["joint_metric_scale"]["sufficient_translation"] is False


def test_joint_metric_scale_default_off_matches_explicit_false():
    data = _load_fusion_inputs(scale_error=1.03)

    default_refined, default_quality = fusion.refine_positions_visual_inertial(
        data["measured_camera_positions"],
        data["camera_rotations"],
        data["observations"],
        data["visual_times"],
        data["imu_times"],
        data["gyro"],
        data["accel"],
        data["body_t_camera"],
        td_s=0.0,
        node_stride=3,
        max_correction_m=0.05,
    )
    explicit_refined, explicit_quality = fusion.refine_positions_visual_inertial(
        data["measured_camera_positions"],
        data["camera_rotations"],
        data["observations"],
        data["visual_times"],
        data["imu_times"],
        data["gyro"],
        data["accel"],
        data["body_t_camera"],
        td_s=0.0,
        node_stride=3,
        max_correction_m=0.05,
        solve_metric_scale=False,
    )

    np.testing.assert_allclose(explicit_refined, default_refined, atol=0.0, rtol=0.0)
    assert explicit_quality["joint_metric_scale"]["enabled"] is False
    assert default_quality["joint_metric_scale"]["enabled"] is False
    assert explicit_quality["joint_metric_scale"] == default_quality[
        "joint_metric_scale"
    ]

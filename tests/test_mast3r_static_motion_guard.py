import importlib.util
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fuse_mast3r_stereo_imu.py"
SPEC = importlib.util.spec_from_file_location(SCRIPT.stem, SCRIPT)
fusion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fusion)


FPS = 30.0
IMU_HZ = 400.0
_DEFAULT_RELATIVE_POSITIONS = object()


def _identity_rotations(count: int) -> Rotation:
    return Rotation.from_quat(np.tile([0.0, 0.0, 0.0, 1.0], (count, 1)))


def _synthetic_stream(
    positions: np.ndarray,
    *,
    relative_positions: np.ndarray | None | object = _DEFAULT_RELATIVE_POSITIONS,
    relative_valid: np.ndarray | None = None,
    times: np.ndarray | None = None,
    imu_times: np.ndarray | None = None,
    gyro_body: np.ndarray | None = None,
    accel_body: np.ndarray | None = None,
    rotations: Rotation | None = None,
) -> dict:
    positions = np.asarray(positions, dtype=float)
    sample_count = len(positions)
    if times is None:
        times = np.arange(sample_count, dtype=float) / FPS
    if imu_times is None:
        imu_times = np.arange(0.0, float(times[-1]) + 1.0 / IMU_HZ, 1.0 / IMU_HZ)
    if gyro_body is None:
        gyro_body = np.zeros((len(imu_times), 3), dtype=float)
    if accel_body is None:
        accel_body = np.tile([0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1))
    if rotations is None:
        rotations = _identity_rotations(sample_count)
    if relative_positions is _DEFAULT_RELATIVE_POSITIONS:
        relative_positions = positions.copy()
    if relative_valid is None and relative_positions is not None:
        relative_valid = np.ones(sample_count, dtype=bool)
    return {
        "times": np.asarray(times, dtype=float),
        "body_positions": positions,
        "body_rotations": rotations,
        "imu_times": np.asarray(imu_times, dtype=float),
        "gyro_body": np.asarray(gyro_body, dtype=float),
        "accel_body": np.asarray(accel_body, dtype=float),
        "td_s": 0.0,
        "relative_motion_positions_body": (
            None
            if relative_positions is None
            else np.asarray(relative_positions, dtype=float)
        ),
        "relative_motion_valid": (
            None if relative_valid is None else np.asarray(relative_valid, dtype=bool)
        ),
    }


def _detect(**kwargs):
    return fusion.detect_stationary_segments(**kwargs)


def _stationary_then_motion() -> np.ndarray:
    static = np.zeros((36, 3), dtype=float)
    motion = np.column_stack(
        [
            np.linspace(0.002, 0.040, 24),
            np.zeros(24, dtype=float),
            np.zeros(24, dtype=float),
        ]
    )
    return np.vstack([static, motion])


def _assert_no_segment_crosses_time(
    segments: list[tuple[int, int]], times: np.ndarray, boundary_time: float
) -> None:
    for first, last in segments:
        assert not (times[first] < boundary_time < times[last])


def test_detects_long_static_prefix_without_absorbing_later_motion():
    stream = _synthetic_stream(_stationary_then_motion())

    assert _detect(**stream) == [(0, 35)]


def test_requires_both_visual_and_independent_relative_motion_to_be_quiet():
    positions = np.zeros((45, 3), dtype=float)
    moving = positions.copy()
    moving[:, 0] = np.linspace(0.0, 0.006, len(moving))

    visual_moving = _synthetic_stream(moving, relative_positions=positions)
    relative_moving = _synthetic_stream(positions, relative_positions=moving)

    assert _detect(**visual_moving) == []
    assert _detect(**relative_moving) == []


def test_missing_or_invalid_relative_motion_never_creates_static_segment():
    positions = np.zeros((45, 3), dtype=float)
    missing_relative = _synthetic_stream(
        positions,
        relative_positions=None,
        relative_valid=None,
    )
    all_invalid_relative = _synthetic_stream(
        positions,
        relative_valid=np.zeros(len(positions), dtype=bool),
    )

    assert _detect(**missing_relative) == []
    assert _detect(**all_invalid_relative) == []


def test_camera_timestamp_gap_does_not_bridge_short_static_holds():
    first_hold = np.arange(20, dtype=float) / FPS
    second_hold = 0.80 + np.arange(20, dtype=float) / FPS
    times = np.concatenate([first_hold, second_hold])
    positions = np.zeros((len(times), 3), dtype=float)
    stream = _synthetic_stream(positions, times=times)

    assert _detect(**stream) == []


def test_camera_timestamp_gap_does_not_merge_long_static_holds():
    first_hold = np.arange(36, dtype=float) / FPS
    second_hold = 1.40 + np.arange(36, dtype=float) / FPS
    times = np.concatenate([first_hold, second_hold])
    positions = np.zeros((len(times), 3), dtype=float)
    stream = _synthetic_stream(positions, times=times)

    segments = _detect(**stream)

    assert segments
    _assert_no_segment_crosses_time(segments, times, boundary_time=1.30)
    for first, last in segments:
        assert np.max(np.diff(times[first : last + 1])) <= 0.1 + 1e-9


def test_imu_timestamp_gap_does_not_bridge_static_hold():
    positions = np.zeros((45, 3), dtype=float)
    imu_times = np.concatenate(
        [
            np.arange(0.0, 0.62, 1.0 / IMU_HZ),
            np.arange(0.66, 1.50, 1.0 / IMU_HZ),
        ]
    )
    stream = _synthetic_stream(positions, imu_times=imu_times)

    assert _detect(**stream) == []


def test_imu_timestamp_gap_does_not_merge_long_static_holds():
    positions = np.zeros((90, 3), dtype=float)
    times = np.arange(len(positions), dtype=float) / FPS
    imu_times = np.concatenate(
        [
            np.arange(0.0, 1.17, 1.0 / IMU_HZ),
            np.arange(1.195, float(times[-1]) + 1.0 / IMU_HZ, 1.0 / IMU_HZ),
        ]
    )
    stream = _synthetic_stream(positions, times=times, imu_times=imu_times)

    segments = _detect(**stream)

    assert segments
    _assert_no_segment_crosses_time(segments, times, boundary_time=1.1825)


def test_invalid_relative_motion_sample_splits_static_hold():
    positions = np.zeros((45, 3), dtype=float)
    relative_valid = np.ones(len(positions), dtype=bool)
    relative_valid[22] = False
    stream = _synthetic_stream(positions, relative_valid=relative_valid)

    assert _detect(**stream) == []


def test_brief_gyro_burst_between_camera_samples_splits_static_hold():
    positions = np.zeros((90, 3), dtype=float)
    times = np.arange(len(positions), dtype=float) / FPS
    imu_times = np.arange(0.0, float(times[-1]) + 1.0 / IMU_HZ, 1.0 / IMU_HZ)
    gyro_body = np.zeros((len(imu_times), 3), dtype=float)
    burst_time = 1.205
    burst = (burst_time <= imu_times) & (imu_times <= burst_time + 0.010)
    gyro_body[burst, 2] = np.radians(2.0)
    stream = _synthetic_stream(
        positions,
        times=times,
        imu_times=imu_times,
        gyro_body=gyro_body,
    )

    segments = _detect(**stream)

    assert segments
    _assert_no_segment_crosses_time(segments, times, boundary_time=burst_time)


def test_rejects_constant_velocity_even_with_quiet_accelerometer():
    times = np.arange(45, dtype=float) / FPS
    positions = np.column_stack(
        [
            0.003 * times,
            np.zeros(len(times), dtype=float),
            np.zeros(len(times), dtype=float),
        ]
    )
    stream = _synthetic_stream(positions, times=times)

    assert _detect(**stream) == []


def test_slow_rotation_segments_never_accumulate_more_than_one_degree():
    times = np.arange(181, dtype=float) / FPS
    positions = np.zeros((len(times), 3), dtype=float)
    imu_times = np.arange(0.0, float(times[-1]) + 1.0 / IMU_HZ, 1.0 / IMU_HZ)
    gyro_body = np.tile([0.0, 0.0, np.radians(0.5)], (len(imu_times), 1))
    rotations = Rotation.from_euler("z", np.radians(0.5) * times)
    stream = _synthetic_stream(
        positions,
        times=times,
        imu_times=imu_times,
        gyro_body=gyro_body,
        rotations=rotations,
    )

    segments = _detect(**stream)

    assert segments
    for first, last in segments:
        delta = stream["body_rotations"][first].inv() * stream["body_rotations"][last]
        assert delta.magnitude() <= np.radians(1.0) + 1e-12


def test_rejects_pitch_rotation_even_when_position_is_static():
    positions = np.zeros((45, 3), dtype=float)
    times = np.arange(len(positions), dtype=float) / FPS
    imu_times = np.arange(0.0, float(times[-1]) + 1.0 / IMU_HZ, 1.0 / IMU_HZ)
    gyro_body = np.tile([0.0, np.radians(2.0), 0.0], (len(imu_times), 1))
    rotations = Rotation.from_euler("y", np.radians(2.0) * times)
    stream = _synthetic_stream(
        positions,
        times=times,
        imu_times=imu_times,
        gyro_body=gyro_body,
        rotations=rotations,
    )

    assert _detect(**stream) == []


def test_bounds_slow_accumulated_drift_instead_of_swallowing_entire_motion():
    times = np.arange(133, dtype=float) / FPS
    positions = np.zeros((len(times), 3), dtype=float)
    positions[:, 0] = np.linspace(0.0, 0.0012, len(positions))
    stream = _synthetic_stream(positions, times=times)

    segments = _detect(**stream)

    assert segments
    assert segments != [(0, len(positions) - 1)]
    for first, last in segments:
        assert times[last] - times[first] >= 1.0 - 1e-9
        visual_span = np.ptp(positions[first : last + 1], axis=0)
        vins_span = np.ptp(
            stream["relative_motion_positions_body"][first : last + 1], axis=0
        )
        assert np.linalg.norm(visual_span) <= 0.001 + 1e-12
        assert np.linalg.norm(vins_span) <= 0.001 + 1e-12


def test_rejects_sustained_1p2mm_per_second_translation():
    times = np.arange(60, dtype=float) / FPS
    positions = np.column_stack(
        [
            0.0012 * times,
            np.zeros(len(times), dtype=float),
            np.zeros(len(times), dtype=float),
        ]
    )
    stream = _synthetic_stream(positions, times=times)

    assert _detect(**stream) == []


def test_rejects_noisy_accelerometer_during_static_pose():
    positions = np.zeros((45, 3), dtype=float)
    times = np.arange(len(positions), dtype=float) / FPS
    imu_times = np.arange(0.0, float(times[-1]) + 1.0 / IMU_HZ, 1.0 / IMU_HZ)
    accel_body = np.tile([0.0, 0.0, fusion.STANDARD_GRAVITY], (len(imu_times), 1))
    accel_body[::2, 0] = 0.12
    accel_body[1::2, 0] = -0.12
    stream = _synthetic_stream(
        positions,
        times=times,
        imu_times=imu_times,
        accel_body=accel_body,
    )

    assert _detect(**stream) == []

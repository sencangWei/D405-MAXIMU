import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from evaluate_slam_ground_truth import body_trajectory_to_camera, pose_errors
from analyze_depth_plane_constraint import (
    PlaneObservation,
    apply_temporal_gate,
    fit_plane_ransac,
    load_observations_csv,
    plane_factor_correction,
    transform_plane_to_world,
    write_outputs,
)
from replay_db3_to_ros2 import select_db3
from build_stereo_replay_cache import STEREO_TOPICS, build_cache
from validate_slam_dataset_roles import REQUIRED_MOTIONS, validate_manifest
from validate_slam_product_release import (
    count_unique_hidden_sessions_by_motion,
    validate_benchmark_environment,
    validate_loop_observability,
    validate_plane_factor_safety,
    validate_release,
    validate_frozen_session_inputs,
)
from slam_benchmark_environment import evaluate_environment
from slam_run_health import evaluate_slam_health


def passing_benchmark_environment() -> dict:
    return evaluate_environment(
        {
            "load_average": {"one_minute_per_cpu": 0.1},
            "memory_available_gib": 16.0,
            "pressure": {
                "cpu": {"some": {"avg10": 0.1}},
                "memory": {"full": {"avg10": 0.0}},
                "io": {"full": {"avg10": 0.1}},
            },
            "conflicting_processes": [],
        }
    )


def passing_run_report(variant: str) -> dict:
    report = {
        "variant": variant,
        "result": "PASS",
        "failure_scope": "SLAM",
        "runtime_error": None,
        "runtime_watchdog": {
            "state": "SLAM_HEALTHY",
            "product_usable": True,
        },
        "failures": [],
        "raw_odometry_samples": 100,
        "corrected_odometry_samples": 100,
        "expected_pose_samples_after_skip": 100,
        "pose_coverage": 1.0,
        "loop_input_drop_events": 0,
        "estimator_keyframe_queue_drop_events": 0,
        "automatic_loop_accepts": 0,
        "min_loop_spatial_support": 0.06165,
        "benchmark_environment": passing_benchmark_environment(),
        "pose_graph_health": {"rejected_optimizations": 0},
        "raw_trajectory_diagnostics": {"max_step_m": 0.01, "z_span_m": 0.0},
        "corrected_trajectory_diagnostics": {
            "max_step_m": 0.0,
            "z_span_m": 0.0,
            "endpoint_delta_m": 0.0,
        },
        "z_span_retention_ratio": None,
    }
    report["health"] = evaluate_slam_health(report)
    return report


def test_hidden_motion_repetitions_require_unique_capture_sessions():
    datasets = [
        {
            "id": "hidden-a",
            "role": "hidden_test",
            "motion": "straight_open",
            "session": "recordings/same",
            "acceptance_sha256": "a" * 64,
        },
        {
            "id": "hidden-b",
            "role": "hidden_test",
            "motion": "straight_open",
            "session": "recordings/copied-same-capture",
            "acceptance_sha256": "a" * 64,
        },
        {
            "id": "hidden-c",
            "role": "hidden_test",
            "motion": "straight_open",
            "session": "recordings/independent",
            "acceptance_sha256": "b" * 64,
        },
        {
            "id": "not-measured",
            "role": "hidden_test",
            "motion": "straight_open",
            "session": "recordings/unmeasured",
            "acceptance_sha256": "c" * 64,
        },
    ]

    counts = count_unique_hidden_sessions_by_motion(
        datasets, {"hidden-a", "hidden-b", "hidden-c"}
    )

    assert counts == {"straight_open": 2}


def test_complete_gate_rehashes_frozen_raw_session_inputs(tmp_path: Path):
    session = tmp_path / "recordings" / "hidden"
    (session / "external_imu").mkdir(parents=True)
    paths = {
        "capture_acceptance": session / "acceptance.json",
        "camera_db3": session / "capture.db3",
        "camera_timestamps": session / "d405_frames.csv",
        "imu_samples": session / "external_imu" / "imu.bin",
    }
    for name, path in paths.items():
        path.write_bytes(name.encode())
    frozen = {
        "schema_version": 1,
        "session": str(session.resolve()),
        "frozen_before_slam": True,
        "truth_usage_policy": "withheld_from_slam_until_post_run_scoring",
        "files": {
            name: {
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in paths.items()
        },
    }
    frozen_path = tmp_path / "frozen.json"
    frozen_path.write_text(json.dumps(frozen))
    dataset = {
        "session": str(session),
        "acceptance_sha256": frozen["files"]["capture_acceptance"]["sha256"],
        "session_inputs": str(frozen_path),
        "session_inputs_sha256": hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
    }
    failures = []

    identity = validate_frozen_session_inputs(dataset, "hidden", failures)

    assert failures == []
    assert identity == frozen["files"]["camera_db3"]["sha256"]
    paths["imu_samples"].write_bytes(b"changed")
    failures = []
    changed_identity = validate_frozen_session_inputs(dataset, "hidden", failures)
    assert "hidden: frozen input size changed: imu_samples" in failures
    assert changed_identity is None


def write_passing_pnp_gate_report(path: Path) -> dict:
    report = {
        "result": "PASS",
        "threshold_freeze_allowed": True,
        "selected_threshold": 0.06165,
        "truth_policy": "development_and_validation_only_hidden_forbidden",
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return {
        "report": path.name,
        "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_pose_errors_remove_only_rigid_alignment_not_scale():
    gt = np.column_stack((np.linspace(0, 1, 100), np.zeros(100), np.zeros(100)))
    rotation = Rotation.from_euler("z", 30, degrees=True)
    estimate = (gt * 1.1) @ rotation.as_matrix().T + np.array([2.0, -3.0, 0.4])
    quaternions = np.tile(rotation.as_quat(), (100, 1))
    gt_quaternions = np.tile(Rotation.identity().as_quat(), (100, 1))

    metrics = pose_errors(estimate, quaternions, gt, gt_quaternions, delta=10)

    assert metrics["ate_translation_rmse_m"] > 0.02
    assert metrics["endpoint_drift_percent_of_path"] > 9.0


def test_pose_errors_report_position_and_attitude_alignment_separately():
    gt = np.column_stack(
        (
            np.linspace(0, 1, 100),
            0.2 * np.sin(np.linspace(0, 4, 100)),
            0.1 * np.cos(np.linspace(0, 3, 100)),
        )
    )
    world_offset = Rotation.from_euler("xyz", [3.0, -1.0, 12.0], degrees=True)
    estimate = gt @ world_offset.as_matrix().T + np.array([2.0, -3.0, 0.4])
    gt_attitude = Rotation.from_euler(
        "zyx",
        np.column_stack(
            (
                np.linspace(0, 30, 100),
                np.linspace(-5, 4, 100),
                np.linspace(2, -3, 100),
            )
        ),
        degrees=True,
    )
    estimate_attitude = world_offset * gt_attitude

    metrics = pose_errors(
        estimate,
        estimate_attitude.as_quat(),
        gt,
        gt_attitude.as_quat(),
        delta=10,
    )

    assert metrics["attitude_aligned_ate_rotation_rmse_deg"] < 1e-10
    assert metrics["position_vs_attitude_alignment_rotation_deg"] < 1e-10


def test_body_trajectory_to_camera_applies_rotating_lever_arm():
    positions = np.zeros((2, 3))
    body_rotation = Rotation.from_euler("z", [0.0, 90.0], degrees=True)
    body_t_camera = np.eye(4)
    body_t_camera[0, 3] = 0.1

    camera_positions, camera_quaternions = body_trajectory_to_camera(
        positions, body_rotation.as_quat(), body_t_camera
    )

    np.testing.assert_allclose(camera_positions[0], [0.1, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(camera_positions[1], [0.0, 0.1, 0.0], atol=1e-12)
    np.testing.assert_allclose(
        Rotation.from_quat(camera_quaternions).as_matrix(),
        body_rotation.as_matrix(),
        atol=1e-12,
    )


def test_depth_plane_ransac_rejects_outliers():
    generator = np.random.default_rng(3)
    xy = generator.uniform(-0.4, 0.4, size=(1000, 2))
    z = 0.6 + generator.normal(0.0, 0.001, size=1000)
    plane = np.column_stack((xy, z))
    outliers = generator.uniform(-0.5, 0.5, size=(200, 3))

    normal, offset, inliers = fit_plane_ransac(
        np.vstack((plane, outliers)), threshold_m=0.004
    )

    np.testing.assert_allclose(normal, [0.0, 0.0, 1.0], atol=0.01)
    assert abs(offset + 0.6) < 0.002
    assert inliers.mean() > 0.8


def test_world_plane_stays_fixed_during_real_vertical_motion():
    world_rotation_camera = Rotation.identity()
    world_offsets = []
    camera_distances = []
    for camera_height in (0.2, 0.35, 0.5):
        normal_camera = np.array([0.0, 0.0, 1.0])
        offset_camera = camera_height
        normal_world, offset_world = transform_plane_to_world(
            normal_camera,
            offset_camera,
            world_rotation_camera,
            np.array([0.0, 0.0, camera_height]),
        )
        world_offsets.append(offset_world)
        camera_distances.append(abs(offset_camera))
        np.testing.assert_allclose(normal_world, [0.0, 0.0, 1.0])

    np.testing.assert_allclose(world_offsets, 0.0, atol=1e-12)
    np.testing.assert_allclose(camera_distances, [0.2, 0.35, 0.5])


def test_replay_selects_largest_nonempty_db3(tmp_path):
    (tmp_path / "empty.db3").touch()
    (tmp_path / "small.db3").write_bytes(b"small")
    expected = tmp_path / "recording.db3"
    expected.write_bytes(b"complete recording")

    assert select_db3(tmp_path) == expected


def test_temporal_gate_rejects_stable_wall_for_z_constraint():
    observations = []
    for index in range(10):
        observation = PlaneObservation(
            epoch_s=float(index),
            relative_s=float(index),
            valid_points=500,
            inlier_ratio=0.5,
            median_residual_m=0.001,
            p95_residual_m=0.003,
            normal_camera=[1.0, 0.0, 0.0],
            offset_camera_m=-0.5,
            local_gate_pass=True,
            pose_matched=True,
            normal_world=[1.0, 0.0, 0.0],
            offset_world_m=-0.5,
        )
        observations.append(observation)

    report = apply_temporal_gate(
        observations,
        angle_gate_deg=4.0,
        offset_gate_m=0.025,
        max_horizontal_tilt_deg=12.0,
    )

    assert report["locally_matched_observations"] == 10
    assert report["horizontal_candidates"] == 0
    assert report["accepted_observations"] == 0

    times = np.linspace(0.0, 9.0, 91)
    raw = np.column_stack((np.zeros(91), np.zeros(91), np.linspace(0.0, 0.2, 91)))
    corrected, correction, factor = plane_factor_correction(
        observations,
        times,
        raw,
        gain=0.35,
        max_correction_m=0.03,
        max_gap_s=1.1,
        min_support=5,
        max_slew_mps=0.03,
    )
    np.testing.assert_allclose(corrected, raw)
    np.testing.assert_allclose(correction, 0.0)
    assert factor["status"] == "DISABLED"


def test_depth_plane_observation_csv_round_trip(tmp_path):
    observation = PlaneObservation(
        epoch_s=12.5,
        relative_s=0.5,
        valid_points=1200,
        inlier_ratio=0.81,
        median_residual_m=0.001,
        p95_residual_m=0.003,
        normal_camera=[0.0, 0.0, 1.0],
        offset_camera_m=-0.3,
        local_gate_pass=True,
        pose_matched=True,
        normal_world=[0.01, 0.0, 0.99995],
        offset_world_m=-0.1,
        world_angle_error_deg=0.1,
        world_offset_error_m=0.002,
        temporal_gate_pass=True,
    )
    output_dir = tmp_path / "depth_report"
    write_outputs(output_dir, [observation], {"result": "TEST"})

    loaded = load_observations_csv(output_dir / "depth_plane_frames.csv")

    assert loaded == [observation]


def test_plane_factor_reduces_z_drift_without_flattening_real_elevation():
    times = np.linspace(0.0, 10.0, 301)
    true_z = 0.2 + 0.12 * np.sin(np.pi * times / 10.0)
    drift = 0.02 * times / 10.0
    raw = np.column_stack((0.1 * times, np.zeros_like(times), true_z + drift))
    observations = []
    for timestamp in np.linspace(0.0, 10.0, 51):
        index = int(round(timestamp / 10.0 * (len(times) - 1)))
        observation = PlaneObservation(
            epoch_s=timestamp,
            relative_s=timestamp,
            valid_points=1000,
            inlier_ratio=0.8,
            median_residual_m=0.001,
            p95_residual_m=0.003,
            normal_camera=[0.0, 0.0, 1.0],
            offset_camera_m=float(true_z[index]),
            local_gate_pass=True,
            pose_matched=True,
            normal_world=[0.0, 0.0, 1.0],
            offset_world_m=float(-drift[index]),
            temporal_gate_pass=True,
        )
        observations.append(observation)
    corrected, correction, report = plane_factor_correction(
        observations,
        times,
        raw,
        gain=1.0,
        max_correction_m=0.03,
        max_gap_s=0.25,
        min_support=5,
        max_slew_mps=0.05,
    )

    raw_error = raw[:, 2] - true_z
    corrected_error = corrected[:, 2] - true_z
    assert report["status"] == "ACTIVE"
    assert np.sqrt(np.mean(corrected_error**2)) < np.sqrt(np.mean(raw_error**2))
    assert np.ptp(corrected[:, 2]) > 0.11
    assert report["gravity_axis_span_retention_ratio"] > 0.9
    assert np.max(np.abs(correction)) <= 0.03


def test_plane_factor_releases_to_zero_when_plane_support_disappears():
    times = np.linspace(0.0, 12.0, 361)
    raw = np.column_stack((np.zeros_like(times), np.zeros_like(times), 0.01 * times))
    observations = []
    observation_times = np.linspace(0.0, 4.0, 21)
    for timestamp in observation_times:
        # The first five samples establish the physical plane.  The remaining
        # samples model a gradually growing VIO Z drift while that same plane
        # stays visible.
        drift = max(0.0, 0.02 * (timestamp - 0.8) / 3.2)
        observations.append(
            PlaneObservation(
                epoch_s=timestamp,
                relative_s=timestamp,
                valid_points=1000,
                inlier_ratio=0.8,
                median_residual_m=0.001,
                p95_residual_m=0.003,
                normal_camera=[0.0, 0.0, 1.0],
                offset_camera_m=0.3,
                local_gate_pass=True,
                pose_matched=True,
                normal_world=[0.0, 0.0, 1.0],
                offset_world_m=-0.02 - drift,
                temporal_gate_pass=True,
            )
        )
    corrected, correction, report = plane_factor_correction(
        observations,
        times,
        raw,
        gain=1.0,
        max_correction_m=0.03,
        max_gap_s=0.25,
        min_support=5,
        max_slew_mps=0.02,
    )

    assert report["status"] == "ACTIVE"
    assert correction[np.searchsorted(times, 4.0)] < -0.015
    assert abs(correction[-1]) < 1e-9
    assert corrected[-1, 2] == raw[-1, 2]


def test_plane_factor_is_causal_and_future_observations_do_not_change_past():
    times = np.linspace(0.0, 8.0, 241)
    raw = np.column_stack((np.zeros_like(times), np.zeros_like(times), 0.01 * times))

    def observation(timestamp: float, offset: float) -> PlaneObservation:
        return PlaneObservation(
            epoch_s=timestamp,
            relative_s=timestamp,
            valid_points=1000,
            inlier_ratio=0.8,
            median_residual_m=0.001,
            p95_residual_m=0.003,
            normal_camera=[0.0, 0.0, 1.0],
            offset_camera_m=0.3,
            local_gate_pass=True,
            pose_matched=True,
            normal_world=[0.0, 0.0, 1.0],
            offset_world_m=offset,
            temporal_gate_pass=True,
        )

    past = [observation(timestamp, 0.0) for timestamp in np.linspace(0.0, 3.0, 16)]
    future = [
        observation(timestamp, -0.02)
        for timestamp in np.linspace(5.0, 8.0, 16)
    ]
    _, correction_without_future, _ = plane_factor_correction(
        past,
        times,
        raw,
        gain=1.0,
        max_correction_m=0.03,
        max_gap_s=0.25,
        min_support=5,
        max_slew_mps=0.02,
    )
    _, correction_with_future, report = plane_factor_correction(
        [*past, *future],
        times,
        raw,
        gain=1.0,
        max_correction_m=0.03,
        max_gap_s=0.25,
        min_support=5,
        max_slew_mps=0.02,
    )

    past_end = np.searchsorted(times, 5.0)
    np.testing.assert_allclose(
        correction_with_future[:past_end],
        correction_without_future[:past_end],
        atol=1e-12,
    )
    assert report["causal"] is True


def test_plane_factor_rejects_non_monotonic_trajectory_timestamps():
    with pytest.raises(ValueError, match="strictly increasing"):
        plane_factor_correction(
            [],
            np.array([0.0, 1.0, 0.5]),
            np.zeros((3, 3)),
            gain=0.35,
            max_correction_m=0.03,
            max_gap_s=0.5,
            min_support=5,
            max_slew_mps=0.03,
        )


def test_plane_factor_changes_only_world_gravity_axis_for_tilted_plane():
    times = np.linspace(0.0, 2.0, 61)
    raw = np.column_stack((0.2 * times, -0.1 * times, 0.01 * times))
    normal = np.array([0.1, 0.0, np.sqrt(0.99)])
    observations = []
    for timestamp in np.linspace(0.0, 2.0, 11):
        observations.append(
            PlaneObservation(
                epoch_s=timestamp,
                relative_s=timestamp,
                valid_points=1000,
                inlier_ratio=0.8,
                median_residual_m=0.001,
                p95_residual_m=0.003,
                normal_camera=normal.tolist(),
                offset_camera_m=0.3,
                local_gate_pass=True,
                pose_matched=True,
                normal_world=normal.tolist(),
                offset_world_m=0.01,
                temporal_gate_pass=True,
            )
        )
    corrected, correction, report = plane_factor_correction(
        observations,
        times,
        raw,
        gain=1.0,
        max_correction_m=0.03,
        max_gap_s=0.25,
        min_support=5,
        max_slew_mps=0.1,
    )

    assert report["status"] == "ACTIVE"
    np.testing.assert_allclose(corrected[:, :2], raw[:, :2], atol=1e-12)
    np.testing.assert_allclose(corrected[:, 2] - raw[:, 2], correction)
    assert report["correction_axis_world"] == [0.0, 0.0, 1.0]


def test_plane_factor_release_evidence_rejects_unobservable_active_factor():
    failures = validate_plane_factor_safety(
        {
            "status": "ACTIVE",
            "causal": True,
            "uses_absolute_height": False,
            "uses_endpoint_constraint": False,
        },
        "hidden: depth_plane",
    )

    assert any("active factor lacks sufficient support" in item for item in failures)
    assert any("correction axis" in item for item in failures)


def test_plane_factor_release_evidence_accepts_safe_automatic_disable():
    failures = validate_plane_factor_safety(
        {
            "status": "DISABLED",
            "reason": "no stable gravity-aligned plane",
            "support_observations": 0,
            "active_trajectory_samples": 0,
            "causal": True,
            "uses_absolute_height": False,
            "uses_endpoint_constraint": False,
        },
        "hidden: depth_plane",
    )

    assert failures == []


def test_stereo_replay_cache_preserves_only_required_topics(tmp_path):
    source = tmp_path / "source.db3"
    database = sqlite3.connect(source)
    database.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT NOT NULL,"
        "type TEXT NOT NULL,serialization_format TEXT NOT NULL,"
        "offered_qos_profiles TEXT NOT NULL)"
    )
    database.execute(
        "CREATE TABLE messages(id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL,"
        "timestamp INTEGER NOT NULL,data BLOB NOT NULL)"
    )
    names = [*STEREO_TOPICS, "/device_0/sensor_0/Depth_0/image/data"]
    for topic_id, name in enumerate(names, 1):
        database.execute(
            "INSERT INTO topics VALUES(?,?,?,?,?)",
            (topic_id, name, "test/msg/Test", "cdr", ""),
        )
        database.execute(
            "INSERT INTO messages VALUES(?,?,?,?)",
            (topic_id, topic_id, topic_id * 10, name.encode()),
        )
    database.commit()
    database.close()

    output = tmp_path / "cache.db3"
    report = build_cache(source, output)

    assert report["result"] == "PASS"
    assert set(report["topics"]) == set(STEREO_TOPICS)
    copied = sqlite3.connect(output)
    copied_names = {row[0] for row in copied.execute("SELECT name FROM topics")}
    copied.close()
    assert copied_names == set(STEREO_TOPICS)


def test_stereo_replay_cache_can_limit_duration(tmp_path):
    source = tmp_path / "source.db3"
    database = sqlite3.connect(source)
    database.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT NOT NULL,"
        "type TEXT NOT NULL,serialization_format TEXT NOT NULL,"
        "offered_qos_profiles TEXT NOT NULL)"
    )
    database.execute(
        "CREATE TABLE messages(id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL,"
        "timestamp INTEGER NOT NULL,data BLOB NOT NULL)"
    )
    message_id = 1
    for topic_id, name in enumerate(STEREO_TOPICS, 1):
        database.execute(
            "INSERT INTO topics VALUES(?,?,?,?,?)",
            (topic_id, name, "test/msg/Test", "cdr", ""),
        )
        for second in range(4):
            database.execute(
                "INSERT INTO messages VALUES(?,?,?,?)",
                (message_id, topic_id, second * 1_000_000_000, name.encode()),
            )
            message_id += 1
    database.commit()
    database.close()

    output = tmp_path / "cache.db3"
    report = build_cache(source, output, duration_s=1.1)
    copied = sqlite3.connect(output)
    count = copied.execute("SELECT count(*) FROM messages").fetchone()[0]
    copied.close()

    assert report["duration_limit_s"] == 1.1
    assert count == len(STEREO_TOPICS) * 2


def test_dataset_manifest_detects_missing_hidden_test_without_breaking_dev_validation(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    (session / "acceptance.json").write_text("{}", encoding="utf-8")
    manifest = {
        "datasets": [
            {
                "id": "dev",
                "role": "development",
                "motion": "closed_loop_with_elevation",
                "session": "recordings/session",
                "external_ground_truth": None,
                "acceptance_sha256": hashlib.sha256(b"{}").hexdigest(),
            },
            {
                "id": "val",
                "role": "validation",
                "motion": "closed_loop_horizontal",
                "session": "recordings/session",
                "external_ground_truth": None,
                "acceptance_sha256": hashlib.sha256(b"{}").hexdigest(),
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    old_root = sys.modules[validate_manifest.__module__].ROOT
    sys.modules[validate_manifest.__module__].ROOT = tmp_path
    try:
        assert validate_manifest(manifest_path, False)["result"] == "PASS"
        strict = validate_manifest(manifest_path, True)
    finally:
        sys.modules[validate_manifest.__module__].ROOT = old_root
    assert strict["result"] == "FAIL"
    assert strict["product_test_readiness"] == "MISSING_HIDDEN_TEST"


def test_product_release_gate_rejects_missing_action_matrix(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    report = tmp_path / "run.json"
    report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "pose_coverage": 1.0,
                "loop_input_drop_events": 0,
                "estimator_keyframe_queue_drop_events": 0,
                "automatic_loop_accepts": 0,
            }
        ),
        encoding="utf-8",
    )
    import hashlib

    manifest = {
        "pnp_spatial_gate_evidence": write_passing_pnp_gate_report(
            tmp_path / "pnp_gate.json"
        ),
        "datasets": [
            {
                "id": "only-one-action",
                "role": "validation",
                "motion": "straight_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(acceptance.read_bytes()).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            }
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "FAIL"
    assert "closed_loop_horizontal" in result["dataset_gate"]["missing_motions"]
    assert result["release_readiness"] == "NOT_READY"


def test_candidate_gate_never_claims_customer_release(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    report = tmp_path / "run.json"
    report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "pose_coverage": 1.0,
                "loop_input_drop_events": 0,
                "estimator_keyframe_queue_drop_events": 0,
                "automatic_loop_accepts": 0,
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "datasets": [
            {
                "id": "candidate-development",
                "role": "development",
                "motion": "straight_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
            {
                "id": "candidate-validation",
                "role": "validation",
                "motion": "free_motion_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=False)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "PASS"
    assert result["release_readiness"] == "CANDIDATE_PASS"
    assert result["customer_release_complete"] is False
    assert result["evaluation_scope"] == "candidate_evidence_only"


def test_accepted_loop_requires_pnp_and_pose_graph_observability():
    missing = validate_loop_observability(
        {
            "automatic_loop_accepts": 1,
            "pnp_quality": {"accepted_edges": []},
            "pose_graph_health": {
                "optimizations": 0,
                "usable_optimizations": 0,
                "rejected_optimizations": 0,
            },
        },
        "hidden: auto_loop",
    )
    assert any("accepted PnP quality records" in failure for failure in missing)
    assert any("pose-graph optimization records" in failure for failure in missing)

    complete = validate_loop_observability(
        {
            "automatic_loop_accepts": 1,
            "pnp_quality": {
                "accepted_edges": [
                    {
                        "inliers": 23,
                        "rmse_px": 2.3,
                        "p95_px": 3.6,
                        "current_hull_fraction": 0.16,
                        "old_hull_fraction": 0.15,
                    }
                ]
            },
            "pose_graph_health": {
                "optimizations": 1,
                "usable_optimizations": 1,
                "rejected_optimizations": 0,
            },
        },
        "hidden: auto_loop",
    )
    assert complete == []


def test_release_rejects_missing_or_weakened_benchmark_environment():
    assert validate_benchmark_environment({}, "hidden: auto_loop") == [
        "hidden: auto_loop: missing benchmark environment preflight"
    ]
    environment = passing_benchmark_environment()
    assert validate_benchmark_environment(
        {"benchmark_environment": environment}, "hidden: auto_loop"
    ) == []
    environment["thresholds"]["max_io_pressure_full_avg10_percent"] = 100.0
    assert any(
        "was weakened" in failure
        for failure in validate_benchmark_environment(
            {"benchmark_environment": environment}, "hidden: auto_loop"
        )
    )


def test_hidden_dataset_requires_predeclared_loop_and_hashed_gt_report(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    ground_truth = tmp_path / "truth.csv"
    ground_truth.write_text("t,x,y,z\n", encoding="utf-8")
    report = tmp_path / "run.json"
    report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "pose_coverage": 1.0,
                "loop_input_drop_events": 0,
                "estimator_keyframe_queue_drop_events": 0,
                "automatic_loop_accepts": 0,
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "pnp_spatial_gate_evidence": write_passing_pnp_gate_report(
            tmp_path / "pnp_gate.json"
        ),
        "datasets": [
            {
                "id": "development",
                "role": "development",
                "motion": "free_motion_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
            {
                "id": "validation",
                "role": "validation",
                "motion": "l_shape_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
            {
                "id": "hidden",
                "role": "hidden_test",
                "motion": "straight_open",
                "session": "recordings/session",
                "expected_loop": None,
                "external_ground_truth": "truth.csv",
                "external_ground_truth_sha256": hashlib.sha256(
                    ground_truth.read_bytes()
                ).hexdigest(),
                "acceptance_sha256": hashlib.sha256(
                    acceptance.read_bytes()
                ).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "FAIL"
    assert any("predeclare expected_loop" in item for item in result["failures"])
    assert any("ground-truth evaluation report" in item for item in result["failures"])


def test_complete_release_requires_hashed_three_variant_matrix(tmp_path):
    session = tmp_path / "recordings" / "session"
    session.mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    truth = tmp_path / "truth.csv"
    truth.write_text("t_sec,x,y,z,qw,qx,qy,qz\n", encoding="utf-8")
    run_paths = {}
    trajectory_paths = {}
    for variant in ("raw_vins", "auto_loop", "depth_plane"):
        run_path = tmp_path / f"{variant}_run.json"
        run_path.write_text(
            json.dumps(passing_run_report(variant)),
            encoding="utf-8",
        )
        trajectory_path = tmp_path / f"{variant}_trajectory.csv"
        trajectory_path.write_text(
            "t_sec,x,y,z,qw,qx,qy,qz\n"
            + "".join(
                f"{index / 30.0},0,0,0,1,0,0,0\n" for index in range(100)
            ),
            encoding="utf-8",
        )
        run_paths[variant] = run_path
        trajectory_paths[variant] = trajectory_path
    run = run_paths["auto_loop"]
    ground_truth = tmp_path / "ground_truth.json"
    ground_truth.write_text(
        json.dumps(
            {
                "ate_translation_rmse_m": 0.005,
                "rpe_translation_rmse_m": 0.002,
                "rpe_rotation_rmse_deg": 0.2,
                "z_rmse_m": 0.004,
                "attitude_aligned_ate_rotation_rmse_deg": 0.2,
                "endpoint_drift_percent_of_path": 0.5,
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "release_variant": "auto_loop",
        "pnp_spatial_gate_evidence": write_passing_pnp_gate_report(
            tmp_path / "pnp_gate.json"
        ),
        "datasets": [
            {
                "id": "development",
                "role": "development",
                "motion": "free_motion_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(acceptance.read_bytes()).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(run.read_bytes()).hexdigest(),
            },
            {
                "id": "validation",
                "role": "validation",
                "motion": "l_shape_open",
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(acceptance.read_bytes()).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(run.read_bytes()).hexdigest(),
            },
            {
                "id": "hidden",
                "role": "hidden_test",
                "motion": "straight_open",
                "session": "recordings/session",
                "expected_loop": False,
                "external_ground_truth": "truth.csv",
                "external_ground_truth_sha256": hashlib.sha256(truth.read_bytes()).hexdigest(),
                "acceptance_sha256": hashlib.sha256(acceptance.read_bytes()).hexdigest(),
                "run_report": "run.json",
                "run_report_sha256": hashlib.sha256(run.read_bytes()).hexdigest(),
                "ground_truth_report": "ground_truth.json",
                "ground_truth_report_sha256": hashlib.sha256(
                    ground_truth.read_bytes()
                ).hexdigest(),
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "FAIL"
    assert result["release_variant"] == "auto_loop"
    assert result["variant_report_counts"] == {
        "raw_vins": 0,
        "auto_loop": 0,
        "depth_plane": 0,
    }
    assert any("hidden: raw_vins: missing variant report" in item for item in result["failures"])
    assert any("hidden: auto_loop: missing variant report" in item for item in result["failures"])
    assert any("hidden: depth_plane: missing variant report" in item for item in result["failures"])
    assert "no hidden three-variant evaluation matrix" in result["failures"]


def test_three_variant_gate_applies_precision_thresholds_only_to_release_variant(tmp_path):
    session = tmp_path / "recordings" / "session"
    (session / "external_imu").mkdir(parents=True)
    acceptance = session / "acceptance.json"
    acceptance.write_text("{}", encoding="utf-8")
    session_files = {
        "capture_acceptance": acceptance,
        "camera_db3": session / "capture.db3",
        "camera_timestamps": session / "d405_frames.csv",
        "imu_samples": session / "external_imu" / "imu.bin",
    }
    session_files["camera_db3"].write_bytes(b"camera")
    session_files["camera_timestamps"].write_bytes(b"timestamps")
    session_files["imu_samples"].write_bytes(b"imu")
    frozen_inputs = {
        "schema_version": 1,
        "session": str(session.resolve()),
        "frozen_before_slam": True,
        "truth_usage_policy": "withheld_from_slam_until_post_run_scoring",
        "files": {
            name: {
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in session_files.items()
        },
    }
    frozen_inputs_path = tmp_path / "frozen_inputs.json"
    frozen_inputs_path.write_text(json.dumps(frozen_inputs))
    run_paths = {}
    trajectory_paths = {}
    for variant in ("raw_vins", "auto_loop", "depth_plane"):
        run_path = tmp_path / f"{variant}_run.json"
        run_path.write_text(
            json.dumps(passing_run_report(variant)),
            encoding="utf-8",
        )
        trajectory_path = tmp_path / f"{variant}_trajectory.csv"
        trajectory_path.write_text(
            "t_sec,x,y,z,qw,qx,qy,qz\n"
            + "".join(
                f"{index / 30.0},0,0,0,1,0,0,0\n" for index in range(100)
            ),
            encoding="utf-8",
        )
        run_paths[variant] = run_path
        trajectory_paths[variant] = trajectory_path

    good_metrics = {
        "ate_translation_rmse_m": 0.005,
        "rpe_translation_rmse_m": 0.002,
        "rpe_rotation_rmse_deg": 0.2,
        "z_rmse_m": 0.004,
        "attitude_aligned_ate_rotation_rmse_deg": 0.2,
        "endpoint_drift_percent_of_path": 0.5,
    }
    poor_metrics = {name: 10.0 for name in good_metrics}
    metric_paths = {}
    for variant, metrics in (
        ("raw_vins", poor_metrics),
        ("auto_loop", good_metrics),
        ("depth_plane", poor_metrics),
    ):
        path = tmp_path / f"{variant}_gt.json"
        path.write_text(
            json.dumps(
                {
                    "variant": variant,
                    "estimate": str(trajectory_paths[variant].resolve()),
                    "ground_truth": str((tmp_path / "truth.csv").resolve()),
                    "truth_usage": "post_run_scoring_only",
                    **metrics,
                }
            ),
            encoding="utf-8",
        )
        metric_paths[variant] = path

    variant_reports = {
        variant: {
            "run_report": run_paths[variant].name,
            "run_report_sha256": hashlib.sha256(
                run_paths[variant].read_bytes()
            ).hexdigest(),
            "trajectory": trajectory_paths[variant].name,
            "trajectory_sha256": hashlib.sha256(
                trajectory_paths[variant].read_bytes()
            ).hexdigest(),
            "ground_truth_report": path.name,
            "ground_truth_report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for variant, path in metric_paths.items()
    }
    factor_report = tmp_path / "depth_plane_factor.json"
    factor_report.write_text(
        json.dumps(
            {
                "result": "PASS",
                "scope": "depth_plane_factor_safety_evidence",
                "truth_usage": "none",
                "corrected_trajectory": str(
                    trajectory_paths["depth_plane"].resolve()
                ),
                "plane_factor": {
                    "status": "ACTIVE",
                    "support_observations": 8,
                    "min_support": 5,
                    "activations": 1,
                    "active_trajectory_samples": 80,
                    "correction_axis_world": [0.0, 0.0, 1.0],
                    "max_correction_m": 0.03,
                    "applied_correction_max_abs_m": 0.01,
                    "causal": True,
                    "uses_absolute_height": False,
                    "uses_endpoint_constraint": False,
                },
            }
        ),
        encoding="utf-8",
    )
    variant_reports["depth_plane"].update(
        {
            "factor_report": factor_report.name,
            "factor_report_sha256": hashlib.sha256(
                factor_report.read_bytes()
            ).hexdigest(),
        }
    )
    manifest = {
        "release_variant": "auto_loop",
        "pnp_spatial_gate_evidence": write_passing_pnp_gate_report(
            tmp_path / "pnp_gate.json"
        ),
        "thresholds": {"min_hidden_runs_per_motion": 1},
        "datasets": [],
    }
    for role, motion, dataset_id in (
        ("development", "free_motion_open", "development"),
        ("validation", "l_shape_open", "validation"),
    ):
        manifest["datasets"].append(
            {
                "id": dataset_id,
                "role": role,
                "motion": motion,
                "session": "recordings/session",
                "expected_loop": False,
                "acceptance_sha256": hashlib.sha256(acceptance.read_bytes()).hexdigest(),
                "run_report": run_paths["auto_loop"].name,
                "run_report_sha256": hashlib.sha256(
                    run_paths["auto_loop"].read_bytes()
                ).hexdigest(),
            }
        )
    truth = tmp_path / "truth.csv"
    truth.write_text("t_sec,x,y,z,qw,qx,qy,qz\n", encoding="utf-8")
    for index, motion in enumerate(sorted(REQUIRED_MOTIONS)):
        manifest["datasets"].append(
            {
                "id": f"hidden-{index}",
                "role": "hidden_test",
                "motion": motion,
                "session": "recordings/session",
                "expected_loop": False,
                "external_ground_truth": "truth.csv",
                "external_ground_truth_sha256": hashlib.sha256(truth.read_bytes()).hexdigest(),
                "acceptance_sha256": hashlib.sha256(acceptance.read_bytes()).hexdigest(),
                "session_inputs": frozen_inputs_path.name,
                "session_inputs_sha256": hashlib.sha256(
                    frozen_inputs_path.read_bytes()
                ).hexdigest(),
                "run_report": run_paths["auto_loop"].name,
                "run_report_sha256": hashlib.sha256(
                    run_paths["auto_loop"].read_bytes()
                ).hexdigest(),
                "ground_truth_report": "auto_loop_gt.json",
                "ground_truth_report_sha256": hashlib.sha256(
                    metric_paths["auto_loop"].read_bytes()
                ).hexdigest(),
                "variant_reports": variant_reports,
            }
        )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dataset_module = sys.modules[validate_manifest.__module__]
    release_module = sys.modules[validate_release.__module__]
    old_dataset_root = dataset_module.ROOT
    old_release_root = release_module.ROOT
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        result = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root

    assert result["result"] == "PASS"
    assert result["release_readiness"] == "CUSTOMER_READY"
    assert result["variant_report_counts"] == {
        "raw_vins": len(REQUIRED_MOTIONS),
        "auto_loop": len(REQUIRED_MOTIONS),
        "depth_plane": len(REQUIRED_MOTIONS),
    }

    mismatched_run = json.loads(run_paths["auto_loop"].read_text())
    mismatched_run["min_loop_spatial_support"] = 0.05
    run_paths["auto_loop"].write_text(json.dumps(mismatched_run))
    mismatched_hash = hashlib.sha256(run_paths["auto_loop"].read_bytes()).hexdigest()
    for dataset in manifest["datasets"]:
        if dataset.get("run_report") == run_paths["auto_loop"].name:
            dataset["run_report_sha256"] = mismatched_hash
        if dataset["role"] == "hidden_test":
            dataset["variant_reports"]["auto_loop"][
                "run_report_sha256"
            ] = mismatched_hash
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        mismatched = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root
    assert mismatched["result"] == "FAIL"
    assert any(
        "effective PnP spatial threshold does not match qualified evidence"
        in failure
        for failure in mismatched["failures"]
    )

    mismatched_run["min_loop_spatial_support"] = 0.06165
    run_paths["auto_loop"].write_text(json.dumps(mismatched_run))
    restored_hash = hashlib.sha256(run_paths["auto_loop"].read_bytes()).hexdigest()
    for dataset in manifest["datasets"]:
        if dataset.get("run_report") == run_paths["auto_loop"].name:
            dataset["run_report_sha256"] = restored_hash
        if dataset["role"] == "hidden_test":
            dataset["variant_reports"]["auto_loop"][
                "run_report_sha256"
            ] = restored_hash

    forged_health_run = json.loads(run_paths["auto_loop"].read_text())
    forged_health_run["health"]["state"] = "SLAM_FAILED"
    run_paths["auto_loop"].write_text(json.dumps(forged_health_run))
    forged_health_hash = hashlib.sha256(
        run_paths["auto_loop"].read_bytes()
    ).hexdigest()
    for dataset in manifest["datasets"]:
        if dataset.get("run_report") == run_paths["auto_loop"].name:
            dataset["run_report_sha256"] = forged_health_hash
        if dataset["role"] == "hidden_test":
            dataset["variant_reports"]["auto_loop"][
                "run_report_sha256"
            ] = forged_health_hash
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        forged_health = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root
    assert forged_health["result"] == "FAIL"
    assert any(
        "run health report is missing or inconsistent" in failure
        for failure in forged_health["failures"]
    )

    forged_health_run["health"] = evaluate_slam_health(forged_health_run)
    run_paths["auto_loop"].write_text(json.dumps(forged_health_run))
    restored_hash = hashlib.sha256(run_paths["auto_loop"].read_bytes()).hexdigest()
    for dataset in manifest["datasets"]:
        if dataset.get("run_report") == run_paths["auto_loop"].name:
            dataset["run_report_sha256"] = restored_hash
        if dataset["role"] == "hidden_test":
            dataset["variant_reports"]["auto_loop"][
                "run_report_sha256"
            ] = restored_hash

    original_trajectory = trajectory_paths["auto_loop"].read_bytes()
    trajectory_paths["auto_loop"].write_text(
        "t_sec,x,y,z,qw,qx,qy,qz\n"
        + "".join(
            f"{index / 30.0},{0.1 if index == 50 else 0},0,0,1,0,0,0\n"
            for index in range(100)
        ),
        encoding="utf-8",
    )
    forged_trajectory_hash = hashlib.sha256(
        trajectory_paths["auto_loop"].read_bytes()
    ).hexdigest()
    for dataset in manifest["datasets"]:
        if dataset["role"] == "hidden_test":
            dataset["variant_reports"]["auto_loop"][
                "trajectory_sha256"
            ] = forged_trajectory_hash
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        forged_trajectory = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root
    assert forged_trajectory["result"] == "FAIL"
    assert any(
        "corrected diagnostics do not match trajectory" in failure
        for failure in forged_trajectory["failures"]
    )

    trajectory_paths["auto_loop"].write_bytes(original_trajectory)
    restored_trajectory_hash = hashlib.sha256(original_trajectory).hexdigest()
    for dataset in manifest["datasets"]:
        if dataset["role"] == "hidden_test":
            dataset["variant_reports"]["auto_loop"][
                "trajectory_sha256"
            ] = restored_trajectory_hash

    del variant_reports["depth_plane"]["factor_report"]
    del variant_reports["depth_plane"]["factor_report_sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        unsafe = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root
    assert unsafe["result"] == "FAIL"
    assert any(
        "depth_plane: factor: missing report path" in failure
        for failure in unsafe["failures"]
    )

    forged_metrics = json.loads(metric_paths["auto_loop"].read_text(encoding="utf-8"))
    forged_metrics["estimate"] = str(trajectory_paths["raw_vins"].resolve())
    metric_paths["auto_loop"].write_text(
        json.dumps(forged_metrics), encoding="utf-8"
    )
    variant_reports["auto_loop"]["ground_truth_report_sha256"] = hashlib.sha256(
        metric_paths["auto_loop"].read_bytes()
    ).hexdigest()
    manifest["datasets"][-1]["ground_truth_report_sha256"] = hashlib.sha256(
        metric_paths["auto_loop"].read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    dataset_module.ROOT = tmp_path
    release_module.ROOT = tmp_path
    try:
        forged = validate_release(manifest_path, require_complete=True)
    finally:
        dataset_module.ROOT = old_dataset_root
        release_module.ROOT = old_release_root
    assert forged["result"] == "FAIL"
    assert any(
        "auto_loop: ground truth report scored a different trajectory" in failure
        for failure in forged["failures"]
    )

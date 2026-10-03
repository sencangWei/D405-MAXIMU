import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.probe_independent_ir_timeline_gap as probe


def test_propose_gap_pairs_extend_beyond_both_raw_frontend_ends():
    times = np.arange(0.0, 40.0, 1.0)

    pairs, policy = probe.propose_gap_pairs(
        times,
        left_raw_end_t_sec=19.5,
        right_raw_end_t_sec=22.2,
        max_pairs=4,
        span_frames=2,
    )

    assert policy["both_raw_frontend_end_t_sec"] == 22.2
    assert len(pairs) == 4
    assert all(row["first_t_sec"] > 22.2 for row in pairs)
    assert all(row["second_t_sec"] > 22.2 for row in pairs)
    assert all(row["second_index"] - row["first_index"] == 2 for row in pairs)
    assert pairs[0]["time_source"] == "d405_frames.infrared_left_device_ms/1000"


def test_propose_gap_pairs_uses_full_frame_timeline_not_raw_trajectory_count():
    full_times = np.linspace(100.0, 139.9, 1200)

    pairs, policy = probe.propose_gap_pairs(
        full_times,
        left_raw_end_t_sec=float(full_times[586]),
        right_raw_end_t_sec=float(full_times[587]),
        max_pairs=6,
        span_frames=30,
    )

    assert policy["candidate_first_index_count_after_both_raw_end"] > 0
    assert len(pairs) == 6
    assert min(row["first_index"] for row in pairs) > 587


def test_source_only_summary_flags_do_not_claim_gt_solver_or_rejected_row_recovery(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"records": [{"id": "r1", "session": str(tmp_path / "session")}]}))
    baseline = tmp_path / "baseline"
    output = tmp_path / "out"

    def fake_run_record(record, *, baseline, output, max_pairs):
        path = output / record["id"] / "timeline_gap_probe.json"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n")
        return {"summary": {"pair_count": 1}}

    monkeypatch.setattr(probe, "run_record", fake_run_record)

    assert probe.main(["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(output), "--dataset", "r1"]) == 0
    summary = json.loads((output / "preflight_report.json").read_text())
    assert summary["external_ground_truth_used"] is False
    assert summary["slam_supervision"] is False
    assert summary["backend_launched"] is False
    assert summary["scorer_launched"] is False
    assert summary["not_rejected_row_recovery"] is True


def test_output_refuses_overwrite(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"records": []}))
    output = tmp_path / "out"
    output.mkdir()

    with pytest.raises(ValueError, match="output must be a new directory"):
        probe.main(["--manifest", str(manifest), "--baseline", str(tmp_path), "--output", str(output), "--dataset", "missing"])


def test_bind_vins_to_frame_times_rejects_unbound_timeline(tmp_path):
    vins = tmp_path / "vio_corrected_stream.csv"
    vins.write_text(
        "t_sec,x,y,z,qw,qx,qy,qz\n"
        "10.0,0,0,0,1,0,0,0\n"
        "11.0,1,0,0,1,0,0,0\n"
    )

    with pytest.raises(ValueError, match="VINS/frame binding exceeds"):
        probe.bind_vins_to_frame_times(vins, np.asarray([10.5]))


def test_bind_vins_to_frame_times_checks_only_selected_gap_endpoints(tmp_path):
    vins = tmp_path / "vio_corrected_stream.csv"
    vins.write_text(
        "t_sec,x,y,z,qw,qx,qy,qz\n"
        "10.0,0,0,0,1,0,0,0\n"
        "11.0,1,0,0,1,0,0,0\n"
    )

    positions, rotations, binding = probe.bind_vins_to_frame_times(
        vins,
        np.asarray([0.0, 10.0, 11.0]),
        selected_indices={1, 2},
    )

    assert positions.shape == (3, 3)
    assert len(rotations) == 3
    assert binding["selected_frame_count"] == 2
    assert binding["max_abs_time_delta_s_selected"] == 0.0
    assert binding["unselected_frames_may_precede_vins_start"] is True


def test_load_frame_timeline_preserves_exact_indices_times_and_units(tmp_path):
    frames = tmp_path / "d405_frames.csv"
    frames.write_text(
        "infrared_left_device_ms,infrared_right_device_ms,infrared_left_frame_number,infrared_right_frame_number\n"
        "1000.0,1000.0,30,30\n"
        "1033.0,1033.0,31,31\n"
    )

    times, left_numbers, right_numbers = probe.load_frame_timeline(frames)

    assert times.tolist() == [1.0, 1.033]
    assert left_numbers.tolist() == [30, 31]
    assert right_numbers.tolist() == [30, 31]


def test_body_to_camera_poses_applies_nonzero_rotation_and_lever():
    body_positions = np.asarray([[1.0, 2.0, 3.0]])
    body_rotations = Rotation.from_euler("z", [90.0], degrees=True)
    body_t_camera = np.eye(4)
    body_t_camera[:3, :3] = Rotation.from_euler("x", 90.0, degrees=True).as_matrix()
    body_t_camera[:3, 3] = [0.1, 0.2, 0.3]

    cam_positions, cam_rotations = probe.body_to_camera_poses(body_positions, body_rotations, body_t_camera)

    np.testing.assert_allclose(cam_positions[0], [0.8, 2.1, 3.3], atol=1e-12)
    np.testing.assert_allclose(
        cam_rotations[0].as_matrix(),
        (body_rotations[0] * Rotation.from_matrix(body_t_camera[:3, :3])).as_matrix(),
        atol=1e-12,
    )


def test_body_t_right_from_left_requires_measured_rotation_and_applies_right_lever():
    body_t_left = np.eye(4)
    body_t_left[:3, 3] = [1.0, 2.0, 3.0]
    rotation = Rotation.from_euler("z", 5.0, degrees=True).as_matrix()

    body_t_right = probe.body_t_right_from_left(
        body_t_left,
        {"right_rotation_from_left": rotation.tolist(), "right_translation_from_left_m": [-0.018, 0.0, 0.0]},
    )

    right_t_left = np.eye(4)
    right_t_left[:3, :3] = rotation
    right_t_left[:3, 3] = [-0.018, 0.0, 0.0]
    np.testing.assert_allclose(body_t_right, body_t_left @ np.linalg.inv(right_t_left), atol=1e-12)
    with pytest.raises(ValueError, match="right_rotation_from_left"):
        probe.body_t_right_from_left(body_t_left, {"right_translation_from_left_m": [-0.018, 0.0, 0.0]})


def test_evaluate_pairs_uses_distinct_left_and_right_camera_poses(monkeypatch):
    seen = []

    def fake_left(*args, **kwargs):
        # args: cache, first, second, images..., disp..., positions, rotations, calibration
        seen.append(("left", float(args[-3][args[1], 0])))
        return {"accepted": False, "reason": "left"}

    def fake_right(*args, **kwargs):
        seen.append(("right", float(args[-3][args[1], 0])))
        return {"accepted": False, "reason": "right"}

    monkeypatch.setattr(probe, "_left_motion", fake_left)
    monkeypatch.setattr(probe, "_right_motion", fake_right)
    monkeypatch.setattr(probe.stereo, "stereo_disparity", lambda left, right, num: (np.ones((2, 2)), -np.ones((2, 2))))

    probe.evaluate_pairs(
        [{"first_index": 0, "second_index": 1, "first_t_sec": 1.0, "second_t_sec": 2.0}],
        left_numbers=np.asarray([10, 11]),
        right_numbers=np.asarray([20, 21]),
        left_images={10: np.zeros((2, 2), dtype=np.uint8), 11: np.zeros((2, 2), dtype=np.uint8)},
        right_images={20: np.zeros((2, 2), dtype=np.uint8), 21: np.zeros((2, 2), dtype=np.uint8)},
        positions_left=np.asarray([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        rotations_left=Rotation.identity(2),
        positions_right=np.asarray([[3.0, 0.0, 0.0], [4.0, 0.0, 0.0]]),
        rotations_right=Rotation.identity(2),
        calibration={},
    )

    assert ("left", 1.0) in seen
    assert ("right", 3.0) in seen


def test_load_selected_db3_images_direct_ignores_prepared_dataset_and_uses_exact_selected_frames(monkeypatch, tmp_path):
    calls = {}

    def fake_match(frame_csv, times, trajectory_frame):
        calls["match"] = (frame_csv, times.tolist(), trajectory_frame)
        return np.asarray([100, 101, 102]), np.asarray([200, 201, 202]), {"ok": True}

    def fake_calibration(db3):
        calls["calibration_db3"] = db3
        return {"right_rotation_from_left": np.eye(3).tolist()}

    def fake_images(db3, selected_left, selected_right):
        calls["images"] = (db3, selected_left, selected_right)
        return {101: "left1", 102: "left2"}, {201: "right1", 202: "right2"}

    monkeypatch.setattr(probe.stereo, "match_trajectory_to_stereo_frames", fake_match)
    monkeypatch.setattr(probe.stereo, "load_stereo_calibration", fake_calibration)
    monkeypatch.setattr(probe.stereo, "load_selected_stereo_images", fake_images)

    left_numbers, right_numbers, sync, calibration, left_images, right_images = probe.load_selected_db3_images_direct(
        frame_csv=tmp_path / "d405_frames.csv",
        db3=tmp_path / "source.db3",
        times=np.asarray([1.0, 2.0, 3.0]),
        selected_indices={1, 2},
    )

    assert left_numbers.tolist() == [100, 101, 102]
    assert right_numbers.tolist() == [200, 201, 202]
    assert sync == {"ok": True}
    assert calibration["right_rotation_from_left"] == np.eye(3).tolist()
    assert left_images[101] == "left1"
    assert right_images[202] == "right2"
    assert calls["images"] == (tmp_path / "source.db3", {101, 102}, {201, 202})

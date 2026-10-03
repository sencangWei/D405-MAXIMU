import csv
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
spec = importlib.util.spec_from_file_location(
    "diagnose_fresh_right_stereo", ROOT / "scripts/diagnose_fresh_right_stereo.py"
)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_png(path: Path, value: int, *, shape: tuple[int, int] = (6, 8)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full(shape, value, dtype=np.uint8)
    assert diag.cv2.imwrite(str(path), image)


def fresh_run(tmp_path: Path, *, count: int = 40, eye: str = "right", skew_ms: float = 0.02):
    session = tmp_path / "session"
    run = tmp_path / "right_run"
    dataset = run / "dataset"
    paired = tmp_path / "paired_left"
    rows = []
    traj_rows = []
    d405_rows = []
    for index in range(count):
        name = f"{index:010d}.png"
        t = 1000.0 + index / 30.0
        left_frame = 1000 + index
        right_frame = 7000 + index
        rows.append({"input_index": index, "image": name, "source_frame_number": right_frame, "t_sec": f"{t:.9f}"})
        traj_rows.append({"t_sec": f"{t:.9f}", "x": index * 0.01, "y": 0, "z": 0, "qx": 0, "qy": 0, "qz": 0, "qw": 1})
        d405_rows.append({
            "color_device_ms": f"{(t + 0.004) * 1000.0:.6f}",
            "infrared_left_device_ms": f"{t * 1000.0:.6f}",
            "infrared_right_device_ms": f"{t * 1000.0 + skew_ms:.6f}",
            "infrared_left_frame_number": left_frame,
            "infrared_right_frame_number": right_frame,
        })
        write_png(dataset / name, index % 255)
        write_png(paired / name, (index + 10) % 255)
        write_png(paired / "stereo_right" / name, index % 255)
    write_csv(dataset / "frames.csv", rows)
    write_csv(paired / "frames.csv", [
        {**row, "source_frame_number": 1000 + int(row["input_index"])}
        for row in rows
    ])
    write_csv(run / "trajectory_frames.csv", traj_rows)
    write_csv(session / "d405_frames.csv", d405_rows)
    (dataset / "calibration.yaml").write_text("camera_model: pinhole\n", encoding="utf-8")
    (paired / "calibration.yaml").write_text("camera_model: pinhole\n", encoding="utf-8")
    manifest_common = {
        "schema": "umi_mast3r_dataset_v1",
        "slam_supervision": False,
        "source_session": str(session),
        "source_frames_csv": str(session / "d405_frames.csv"),
        "stream": "infrared_right",
        "frames": count,
    }
    write_json(dataset / "dataset_manifest.json", manifest_common)
    write_json(paired / "dataset_manifest.json", {
        **manifest_common,
        "stream": "infrared_left",
        "stereo_depth_source": {
            "right_directory": "stereo_right",
            "baseline_m": 0.018083254,
            "left_focal_length_px": 120.0,
            "right_camera_info": {
                "width": 8, "height": 6, "fx": 120.0, "fy": 118.0,
                "ppx": 3.5, "ppy": 2.5, "model": "Brown Conrady", "coeffs": [0, 0, 0, 0],
            },
        },
        "camera_info": {
            "width": 8, "height": 6, "fx": 120.0, "fy": 118.0,
            "ppx": 3.5, "ppy": 2.5, "model": "Brown Conrady", "coeffs": [0, 0, 0, 0],
        },
        "source_camera_info": {
            "width": 8, "height": 6, "fx": 120.0, "fy": 118.0,
            "ppx": 3.5, "ppy": 2.5, "model": "Brown Conrady", "coeffs": [0, 0, 0, 0],
        },
    })
    context = {
        "schema": "umi_metric_relative_joint_frontend_context_v1",
        "external_ground_truth_used": False,
        "dataset": str(dataset),
        "paired_left_dataset": str(paired),
        "eye": eye,
        "input_sha256": {},
        "code_sha256": {},
    }
    write_json(run / "context.json", context)
    config = run / "effective_config.yaml"
    checkpoint = run / "checkpoint.pth"
    config.write_text("tracking:\n  metric_relative_joint: true\n", encoding="utf-8")
    checkpoint.write_bytes(b"checkpoint")
    write_json(run / "run_manifest.json", {
        "schema": "umi_mast3r_run_v1",
        "status": "FRONTEND_COMPLETE_NOT_SCORED",
        "eye": eye,
        "source_session": str(session),
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "input_frame_count": count,
        "trajectory_frame_count": count,
        "native_pose_count": count,
        "trajectory_frames_sha256": diag.file_hash(run / "trajectory_frames.csv"),
        "config": str(config),
        "config_sha256": diag.file_hash(config),
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": diag.file_hash(checkpoint),
        "context": str(run / "context.json"),
        "context_sha256": diag.file_hash(run / "context.json"),
    })
    return run, dataset, paired, session


def patch_frontend(monkeypatch):
    def fake_validate(source_run, eye, *, expected_session):
        if eye != "right":
            raise AssertionError("wrong eye")
        manifest = diag.read_json(source_run / "run_manifest.json")
        context = diag.read_json(source_run / "context.json")
        if manifest["status"] != "FRONTEND_COMPLETE_NOT_SCORED" or context["eye"] != "right":
            raise ValueError("wrong frontend identity")
        if Path(diag.read_json(source_run / "dataset" / "dataset_manifest.json")["source_session"]).resolve() != expected_session:
            raise ValueError("session mismatch")
        return {
            "eye": "right",
            "frame_count": int(manifest["native_pose_count"]),
            "config": manifest["config"],
            "checkpoint": manifest["checkpoint"],
            "trajectory_frames_sha256": manifest["trajectory_frames_sha256"],
            "consumed_paths": [
                source_run / "run_manifest.json",
                source_run / "context.json",
                source_run / "trajectory_frames.csv",
            ],
        }

    monkeypatch.setattr(diag, "validate_frontend", fake_validate)


def test_right_timestamp_binds_right_not_color_or_left(monkeypatch, tmp_path):
    run, *_ = fresh_run(tmp_path, count=8)
    patch_frontend(monkeypatch)
    times, *_ = diag.stereo.load_trajectory(run / "trajectory_frames.csv")
    matched, timing = diag.match_right_timeline(times, tmp_path / "session" / "d405_frames.csv")
    assert matched[0]["right_frame_number"] == 7000
    assert timing["time_binding"].endswith("infrared_right_device_ms")
    rows = list(csv.DictReader((tmp_path / "session" / "d405_frames.csv").open()))
    assert float(rows[0]["color_device_ms"]) != pytest.approx(float(rows[0]["infrared_right_device_ms"]))


def test_right_timeline_duplicate_unmatched_nonfinite_fail_closed(tmp_path):
    run, *_ = fresh_run(tmp_path, count=3)
    frame_csv = tmp_path / "session" / "d405_frames.csv"
    times, *_ = diag.stereo.load_trajectory(run / "trajectory_frames.csv")
    rows = list(csv.DictReader(frame_csv.open()))

    duplicate = tmp_path / "duplicate.csv"
    rows[1]["infrared_right_frame_number"] = rows[0]["infrared_right_frame_number"]
    write_csv(duplicate, rows)
    with pytest.raises(ValueError, match="duplicate IR frame numbers"):
        diag.match_right_timeline(times, duplicate)

    rows = list(csv.DictReader(frame_csv.open()))
    nonfinite = tmp_path / "nonfinite.csv"
    rows[1]["infrared_right_device_ms"] = "nan"
    write_csv(nonfinite, rows)
    with pytest.raises(ValueError, match="non-finite"):
        diag.match_right_timeline(times, nonfinite)

    with pytest.raises(ValueError, match="timestamp mismatch"):
        diag.match_right_timeline(times + 100.0, frame_csv)


def test_wrong_eye_or_changed_source_hash_fails_closed(monkeypatch, tmp_path):
    run, *_ = fresh_run(tmp_path, eye="left")
    patch_frontend(monkeypatch)
    with pytest.raises(ValueError, match="wrong frontend identity"):
        diag.run(run, tmp_path / "out", max_pairs=2)


def test_mismatched_paired_right_image_is_rejected(monkeypatch, tmp_path):
    run, _dataset, paired, *_ = fresh_run(tmp_path, count=8)
    patch_frontend(monkeypatch)
    write_png(paired / "stereo_right" / "0000000000.png", 123)
    with pytest.raises(ValueError, match="native RIGHT image differs"):
        diag.run(run, tmp_path / "out", max_pairs=1)


def test_mirror_wrap_restores_nontrivial_right_motion(monkeypatch, tmp_path):
    run, *_ = fresh_run(tmp_path, count=12)
    patch_frontend(monkeypatch)
    captured = {}
    virtual_disp = [-0.03, 0.004, 0.02]
    virtual_rot = Rotation.from_euler("zyx", [-7, 3, 5], degrees=True)

    def fake_pair(left_i, right_i, _left_j, _right_j, pos_i, pos_j, rot_i, rot_j, calib, *_args, **kwargs):
        captured["left_i"] = left_i.copy()
        captured["right_i"] = right_i.copy()
        captured["pos_i"] = pos_i
        captured["pos_j"] = pos_j
        captured["rot_i"] = rot_i
        captured["rot_j"] = rot_j
        captured["calib"] = calib
        captured["trajectory_frame"] = kwargs["trajectory_frame"]
        return {
            "accepted": True,
            "scale": 1.0,
            "metric_distance_m": 0.04,
            "metric_displacement_camera_i_m": virtual_disp,
            "metric_displacement_frame": "infrared_left_camera_i",
            "mast3r_distance": 0.04,
            "direction_cosine": 0.9,
            "rotation_error_deg": 1.0,
            "pnp_inlier_ratio": 1.0,
            "pnp_rotation_quaternion_xyzw": virtual_rot.as_quat().tolist(),
        }

    monkeypatch.setattr(diag.stereo, "estimate_pair_scale", fake_pair)
    monkeypatch.setattr(diag.stereo, "robust_scale", lambda obs, _min: (1.0, {"accepted_observations": len([o for o in obs if o["accepted"]])}))
    monkeypatch.setattr(diag.stereo, "trajectory_step_continuity", lambda *_args: {"result": "PASS", "reason": None})
    report = diag.run(run, tmp_path / "out", max_pairs=1)

    obs = report["observations"][0]
    assert obs["metric_displacement_frame"] == "infrared_right_camera_i"
    np.testing.assert_allclose(obs["metric_displacement_camera_i_m"], [0.03, 0.004, 0.02])
    restored_rot = Rotation.from_quat(obs["pnp_rotation_quaternion_xyzw"])
    expected_rot = Rotation.from_matrix(diag.MIRROR @ virtual_rot.as_matrix() @ diag.MIRROR)
    assert (restored_rot.inv() * expected_rot).magnitude() < 1e-12
    assert captured["trajectory_frame"] == "infrared_left"
    assert captured["calib"]["left"]["cx"] == pytest.approx(8 - 1 - 3.5)
    assert np.all(captured["left_i"][:, 0] == 0)  # mirrored native right image value
    assert np.all(captured["right_i"][:, 0] == 10)  # mirrored paired left image value


def test_wrong_resolution_frame_fails_before_estimator(monkeypatch, tmp_path):
    run, _dataset, paired, *_ = fresh_run(tmp_path, count=12)
    patch_frontend(monkeypatch)
    write_png(paired / "0000000000.png", 10, shape=(5, 8))
    monkeypatch.setattr(
        diag.stereo,
        "estimate_pair_scale",
        lambda *_args, **_kwargs: pytest.fail("estimator must not run on mismatched image shapes"),
    )
    with pytest.raises(ValueError, match="share one resolution"):
        diag.run(run, tmp_path / "out", max_pairs=1)


def test_uniform_pair_coverage_reaches_tail():
    pairs = diag.uniform_pairs(1199, max_pairs=32)
    assert len(pairs) == 32
    assert pairs[0][0] == 0
    assert pairs[-1][1] >= 1190
    assert len({first for first, _second, _hop in pairs}) > 20


def test_existing_output_refused_before_work(monkeypatch, tmp_path):
    run, *_ = fresh_run(tmp_path, count=8)
    patch_frontend(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FileExistsError):
        diag.run(run, out, max_pairs=1)


def test_partial_geometry_failure_keeps_evidence_no_gt_no_production(monkeypatch, tmp_path):
    run, *_ = fresh_run(tmp_path, count=12)
    patch_frontend(monkeypatch)
    monkeypatch.setattr(diag.stereo, "estimate_pair_scale", lambda *_args, **_kwargs: {"accepted": True, "scale": 1.0, "metric_distance_m": 0.01, "pnp_inlier_ratio": 1.0, "metric_displacement_camera_i_m": [-0.01, 0, 0], "pnp_rotation_quaternion_xyzw": [0, 0, 0, 1]})
    monkeypatch.setattr(diag.stereo, "robust_scale", lambda obs, _min: (1.0, {"accepted_observations": len(obs)}))
    monkeypatch.setattr(diag.stereo, "trajectory_step_continuity", lambda *_args: {"result": "FAIL", "reason": "isolated_position_step_jump"})
    report = diag.run(run, tmp_path / "out", max_pairs=2)
    json.dumps(report, allow_nan=False)
    saved = diag.read_json(tmp_path / "out" / "fresh_right_primary_stereo_diagnostic.json")

    assert report["result"] == "FAIL"
    assert "isolated_position_step_jump" in report["failures"]
    assert saved["external_ground_truth_used"] is False
    assert saved["production_promoted"] is False
    assert saved["factors_emitted"] == 0
    assert len(saved["observations"]) == 2
    assert all(isinstance(path, str) for path in saved["frontend"]["consumed_paths"])
    for key in ("diagnostic_script", "right_helper", "stereo_module", "frontend_validation_module", "context_validation_module"):
        assert key in saved["consumed_source_sha256_before"]
        assert saved["consumed_source_sha256_after"][key] == saved["consumed_source_sha256_before"][key]

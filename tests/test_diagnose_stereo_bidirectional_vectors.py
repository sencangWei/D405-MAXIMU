import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "diagnose_stereo_bidirectional_vectors",
    ROOT / "scripts/diagnose_stereo_bidirectional_vectors.py",
)
diag = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diag
spec.loader.exec_module(diag)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def synthetic_combine():
    forward_rotation = Rotation.from_euler("zyx", [35, 7, -11], degrees=True)
    reverse_rotation = forward_rotation.inv()
    forward_displacement = np.array([0.3, -0.2, 0.5])
    reverse_displacement = -forward_rotation.apply(forward_displacement)
    forward = {
        "accepted": True,
        "scale": 1.0,
        "metric_distance_m": 1.0,
        "metric_displacement_camera_i_m": forward_displacement.tolist(),
        "pnp_rotation_quaternion_xyzw": forward_rotation.as_quat().tolist(),
        "pnp_inlier_ratio": 1.0,
        "rotation_error_deg": 0.0,
    }
    reverse = {
        "accepted": True,
        "scale": 1.0,
        "metric_distance_m": 1.0,
        "metric_displacement_camera_i_m": reverse_displacement.tolist(),
        "pnp_rotation_quaternion_xyzw": reverse_rotation.as_quat().tolist(),
        "pnp_inlier_ratio": 1.0,
        "rotation_error_deg": 0.0,
    }
    return diag.stereo.combine_bidirectional_scale(forward, reverse)


def base_report(tmp_path, observations):
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text("traj\n", encoding="utf-8")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "session": str(session.resolve()),
        "trajectory": str(trajectory.resolve()),
        "db3": str((session / "bag.db3").resolve()),
        "prepared_dataset": str(dataset.resolve()),
        "motion_estimator": "pnp",
        "correspondence_estimator": "classical",
        "pnp_rotation_mode": "free",
        "trajectory_frame": "infrared_left",
        "observations": observations,
    }
    path = tmp_path / "report.json"
    write_json(path, report)
    return path


def test_profile_captures_combine_and_restores_previous_profiler():
    prior_events = []

    def prior_profiler(frame, event, arg):
        if event == "return":
            prior_events.append(frame.f_code.co_name)

    sys.setprofile(prior_profiler)
    try:
        result, capture = diag.profile_combine_call(
            synthetic_combine, diag.stereo.combine_bidirectional_scale.__code__
        )
        assert sys.getprofile() is prior_profiler
    finally:
        sys.setprofile(None)

    assert result["accepted"] is True
    assert capture["vector_closure_m"] < 1e-12
    assert capture["direction_cosine"] > 0.999
    assert capture["rclosure_angle_deg"] < 1e-12
    assert capture["scalar_agreement"]["relative_disagreement"] == 0.0
    assert "combine_bidirectional_scale" in prior_events


def test_noncommuting_inverse_uses_reverse_rotation_transpose():
    forward_rotation = Rotation.from_euler("zyx", [35, 7, -11], degrees=True)
    reverse_rotation = forward_rotation.inv()
    forward_displacement = np.array([0.3, -0.2, 0.5])
    reverse_displacement = -forward_rotation.apply(forward_displacement)
    forward = {
        "metric_displacement_camera_i_m": forward_displacement.tolist(),
        "pnp_rotation_quaternion_xyzw": forward_rotation.as_quat().tolist(),
        "scale": 1.0,
    }
    reverse = {
        "metric_displacement_camera_i_m": reverse_displacement.tolist(),
        "pnp_rotation_quaternion_xyzw": reverse_rotation.as_quat().tolist(),
        "scale": 1.0,
    }
    closure = diag.vector_closure(forward, reverse)
    wrong = -reverse_rotation.inv().apply(reverse_displacement)
    assert closure["vector_closure_m"] < 1e-12
    assert closure["direct_reverse_delta_from_forward_m"] < 1e-12
    assert np.linalg.norm(np.asarray(wrong) - forward_displacement) > 0.05


def test_uniform_selection_only_accepted_bidirectional_records(tmp_path):
    observations = []
    for index in range(10):
        observations.append(
            {
                "accepted": True,
                "scale_estimator": "bidirectional_pnp_weighted_mean",
                "first_index": index * 10,
                "second_index": index * 10 + 5,
            }
        )
    observations.append({"accepted": True, "scale_estimator": "strict_forward_mast3r_pnp", "first_index": 999, "second_index": 1000})
    report = json.loads(base_report(tmp_path, observations).read_text(encoding="utf-8"))
    selected = diag.select_pairs(report, max_pairs=4)
    assert [(p.first_index, p.second_index) for p in selected] == [(0, 5), (30, 35), (60, 65), (90, 95)]


def test_run_no_matching_pairs_not_pass(tmp_path):
    report_path = base_report(
        tmp_path,
        [{"accepted": False, "reason": "pnp_failed", "first_index": 0, "second_index": 5}],
    )
    output = tmp_path / "out.json"
    args = diag.argument_parser().parse_args(
        ["--report", str(report_path), "--output", str(output), "--max-depth-m", "0.6"]
    )
    result = diag.run(args)
    assert result["result"] == "NO_MATCHING_PAIRS"
    assert result["selection"]["selected_pairs"] == 0
    assert result["emitted_factor_count"] == 0


def test_run_refuses_overwrite(tmp_path):
    report_path = base_report(tmp_path, [])
    output = tmp_path / "out.json"
    output.write_text("existing\n", encoding="utf-8")
    args = diag.argument_parser().parse_args(
        ["--report", str(report_path), "--output", str(output), "--max-depth-m", "0.6"]
    )
    try:
        diag.run(args)
    except FileExistsError as error:
        assert str(output) in str(error)
    else:
        raise AssertionError("expected no-overwrite guard")


def test_run_binds_inputs_and_fails_when_profiler_missing(monkeypatch, tmp_path):
    observations = [
        {
            "accepted": True,
            "scale_estimator": "bidirectional_pnp_weighted_mean",
            "first_index": 1,
            "second_index": 2,
            "first_t_sec": 1.0,
            "second_t_sec": 2.0,
            "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
            "scale": 1.0,
        }
    ]
    report_path = base_report(tmp_path, observations)
    monkeypatch.setattr(
        diag.stereo,
        "load_trajectory",
        lambda _path: (
            np.array([0.0, 1.0, 2.0]),
            np.zeros((3, 3)),
            np.tile([0.0, 0.0, 0.0, 1.0], (3, 1)),
            [],
        ),
    )
    monkeypatch.setattr(
        diag.lowdiag,
        "_load_images",
        lambda *_args, **_kwargs: (
            np.array([10, 11, 12]),
            np.array([20, 21, 22]),
            {"trajectory_frame": "infrared_left"},
            {"baseline_m": 0.018},
            {11: "li", 12: "lj"},
            {21: "ri", 22: "rj"},
        ),
    )
    monkeypatch.setattr(
        diag,
        "profile_combine_call",
        lambda *_args, **_kwargs: (
            {
                "accepted": True,
                "scale": 1.0,
                "scale_estimator": "bidirectional_pnp_weighted_mean",
                "metric_displacement_camera_i_m": [0.1, 0.0, 0.0],
            },
            None,
        ),
    )
    args = diag.argument_parser().parse_args(
        ["--report", str(report_path), "--output", str(tmp_path / "out.json"), "--max-depth-m", "0.6"]
    )
    result = diag.run(args)
    assert result["result"] == "FAIL"
    assert "profiler missed 1" in result["failures"][0]
    assert result["diagnostics"][0]["fresh_forward_vs_saved"]["within_tolerance"] is True


def test_run_rejects_mast3r_gpu_path(tmp_path):
    report_path = base_report(tmp_path, [])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["correspondence_estimator"] = "mast3r"
    write_json(report_path, report)
    args = diag.argument_parser().parse_args(
        ["--report", str(report_path), "--output", str(tmp_path / "out.json"), "--max-depth-m", "0.6"]
    )
    try:
        diag.run(args)
    except ValueError as error:
        assert "MASt3R model" in str(error)
    else:
        raise AssertionError("expected GPU refusal")


def test_profiler_observer_does_not_change_reverse_failed_return_and_marks_incomplete():
    def reverse_failed_combine():
        forward = {
            "accepted": True,
            "scale": 1.0,
            "metric_distance_m": 1.0,
            "metric_displacement_camera_i_m": [1.0, 0.0, 0.0],
            "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        }
        reverse = {"accepted": False, "reason": "pnp_failed"}
        return diag.stereo.combine_bidirectional_scale(forward, reverse)

    def prior_profiler(_frame, _event, _arg):
        return None

    sys.setprofile(prior_profiler)
    try:
        result, capture = diag.profile_combine_call(
            reverse_failed_combine, diag.stereo.combine_bidirectional_scale.__code__
        )
        assert sys.getprofile() is prior_profiler
    finally:
        sys.setprofile(None)

    assert result == {
        "accepted": False,
        "scale": 1.0,
        "metric_distance_m": 1.0,
        "metric_displacement_camera_i_m": [1.0, 0.0, 0.0],
        "pnp_rotation_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
        "reason": "reverse_motion_failed",
        "reverse_failure_reason": "pnp_failed",
    }
    assert capture["geometry_complete"] is False
    assert capture["reverse_status"]["accepted"] is False
    assert "not both accepted" in capture["capture_error"]

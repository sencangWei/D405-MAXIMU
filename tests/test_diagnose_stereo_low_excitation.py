import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "diagnose_stereo_low_excitation",
    ROOT / "scripts/diagnose_stereo_low_excitation.py",
)
diag = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = diag
spec.loader.exec_module(diag)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def synthetic_low_excitation():
    def unrelated_nested_return():
        metric_distance = 99.0
        return {"accepted": False, "reason": "translation_excitation_low"}

    unrelated_nested_return()
    object_points = np.array([[0.0, 0.0, 0.3], [0.01, 0.0, 0.3]], dtype=float)
    inliers = np.array([[0], [1]], dtype=np.int32)
    inlier_object_points = object_points
    reprojection_before = np.array([0.25, 0.75], dtype=float)
    z = np.array([0.3, 0.31], dtype=float)
    pnp_rotation = Rotation.from_rotvec([0.0, 0.0, 0.01])
    pnp_translation = np.array([-0.002, 0.0, 0.0], dtype=float)
    camera_displacement_i = np.array([0.002, 0.0, 0.0], dtype=float)
    mast3r_delta_i = np.array([0.0, 0.0, 0.0], dtype=float)
    metric_distance = float(np.linalg.norm(camera_displacement_i))
    mast3r_distance = 0.0
    inlier_ratio = 1.0
    free_rotation_delta_deg = 0.0
    pnp_refined = False
    pnp_rotation_constrained = False
    method = "lk"
    pnp_rotation_mode = "free"
    return {"accepted": False, "reason": "translation_excitation_low", "method": "lk"}


def test_profile_captures_only_target_return_and_restores_previous_profiler():
    prior_events = []

    def prior_profiler(frame, event, arg):
        if event == "return":
            prior_events.append(frame.f_code.co_name)

    sys.setprofile(prior_profiler)
    try:
        result, capture = diag.profile_return_locals(
            synthetic_low_excitation, synthetic_low_excitation.__code__
        )
        assert sys.getprofile() is prior_profiler
    finally:
        sys.setprofile(None)

    assert result["reason"] == "translation_excitation_low"
    assert capture["metric_distance_m"] == 0.002
    assert capture["metric_below_3mm"] is True
    assert capture["mast3r_below_1e-4"] is True
    assert capture["both_low"] is True
    assert capture["tracked_points"] == 2
    assert capture["pnp_inliers"] == 2
    assert capture["pnp_reprojection_median_px"] == 0.5
    assert capture["pnp_rotation_quaternion_xyzw"]
    assert any(name == "unrelated_nested_return" for name in prior_events)


def test_profile_restores_after_exception():
    def boom():
        metric_distance = 0.0
        raise RuntimeError("boom")

    def prior_profiler(_frame, _event, _arg):
        return None

    sys.setprofile(prior_profiler)
    try:
        try:
            diag.profile_return_locals(boom, boom.__code__)
        except RuntimeError as error:
            assert str(error) == "boom"
        assert sys.getprofile() is prior_profiler
    finally:
        sys.setprofile(None)


def test_select_low_excitation_pairs_inside_index_window_and_cap(tmp_path):
    report_path = tmp_path / "stereo_report.json"
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "session": str((tmp_path / "session").resolve()),
        "trajectory": str((tmp_path / "trajectory.csv").resolve()),
        "db3": str((tmp_path / "session" / "bag.db3").resolve()),
        "prepared_dataset": str((tmp_path / "dataset").resolve()),
        "motion_estimator": "pnp",
        "correspondence_estimator": "classical",
        "trajectory_frame": "infrared_left",
        "pnp_rotation_mode": "free",
        "num_disparities": 128,
        "observations": [
            {"accepted": False, "reason": "translation_excitation_low", "first_index": 320, "second_index": 325},
            {"accepted": False, "reason": "pnp_failed", "first_index": 323, "second_index": 328},
            {"accepted": False, "reason": "translation_excitation_low", "first_index": 323, "second_index": 328},
            {"accepted": False, "reason": "translation_excitation_low", "first_index": 380, "second_index": 385},
            {"accepted": False, "reason": "translation_excitation_low", "first_index": 382, "second_index": 387},
        ],
    }
    write_json(report_path, report)
    loaded = diag.load_source_report(report_path)
    selected = diag.select_low_excitation_pairs(loaded, 323, 385, max_pairs=1)
    assert [(pair.first_index, pair.second_index) for pair in selected] == [(323, 328)]


def test_run_rejects_mast3r_correspondence_to_avoid_model_gpu(tmp_path):
    report_path = tmp_path / "report.json"
    write_json(
        report_path,
        {
            "schema": "umi_mast3r_stereo_scale_v2",
            "session": str(tmp_path),
            "trajectory": str(tmp_path / "trajectory.csv"),
            "db3": str(tmp_path / "bag.db3"),
            "correspondence_estimator": "mast3r",
            "motion_estimator": "pnp",
            "observations": [],
        },
    )
    args = diag.argument_parser().parse_args(
        ["--report", str(report_path), "--output", str(tmp_path / "out.json")]
    )
    try:
        diag.run(args)
    except ValueError as error:
        assert "would require the MASt3R model" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_missing_max_depth_requires_explicit_historical_value(tmp_path):
    report_path = tmp_path / "report.json"
    write_json(
        report_path,
        {
            "schema": "umi_mast3r_stereo_scale_v2",
            "session": str(tmp_path),
            "trajectory": str(tmp_path / "trajectory.csv"),
            "db3": str(tmp_path / "bag.db3"),
            "correspondence_estimator": "classical",
            "motion_estimator": "pnp",
            "observations": [],
        },
    )
    args = diag.argument_parser().parse_args(
        ["--report", str(report_path), "--output", str(tmp_path / "out.json")]
    )
    try:
        diag.run(args)
    except ValueError as error:
        assert "source report does not record max_depth_m" in str(error)
    else:
        raise AssertionError("expected missing max-depth guard")


def test_run_refuses_to_overwrite_output(tmp_path):
    report_path = tmp_path / "report.json"
    output = tmp_path / "out.json"
    output.write_text("existing\n", encoding="utf-8")
    write_json(
        report_path,
        {
            "schema": "umi_mast3r_stereo_scale_v2",
            "session": str(tmp_path),
            "trajectory": str(tmp_path / "trajectory.csv"),
            "db3": str(tmp_path / "bag.db3"),
            "correspondence_estimator": "classical",
            "motion_estimator": "pnp",
            "observations": [],
        },
    )
    args = diag.argument_parser().parse_args(
        ["--report", str(report_path), "--output", str(output)]
    )
    try:
        diag.run(args)
    except FileExistsError as error:
        assert str(output) in str(error)
    else:
        raise AssertionError("expected no-overwrite guard")


def test_empty_selected_window_reports_no_matching_pairs(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text(
        "t_sec,x,y,z,qw,qx,qy,qz\n"
        "0.000000000,0,0,0,1,0,0,0\n"
        "1.000000000,0,0,0,1,0,0,0\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    output = tmp_path / "diag.json"
    write_json(
        report_path,
        {
            "schema": "umi_mast3r_stereo_scale_v2",
            "session": str(session.resolve()),
            "trajectory": str(trajectory.resolve()),
            "db3": str(session / "bag.db3"),
            "motion_estimator": "pnp",
            "correspondence_estimator": "classical",
            "trajectory_frame": "infrared_left",
            "observations": [
                {
                    "accepted": False,
                    "reason": "translation_excitation_low",
                    "first_index": 0,
                    "second_index": 1,
                    "first_t_sec": 0.0,
                    "second_t_sec": 1.0,
                }
            ],
        },
    )
    args = diag.argument_parser().parse_args(
        [
            "--report",
            str(report_path),
            "--output",
            str(output),
            "--index-start",
            "10",
            "--index-end",
            "20",
            "--max-depth-m",
            "0.6",
        ]
    )
    result = diag.run(args)
    assert result["result"] == "NO_MATCHING_PAIRS"
    assert result["pair_filter"]["selected_pairs"] == 0
    assert result["diagnostics"] == []
    assert result["replayed_accepted_count"] == 0
    assert result["rejected_replayed_count"] == 0
    assert result["emitted_factor_count"] == 0
    assert result["failures"] == []


def test_run_uses_existing_report_binding_without_changing_acceptance(monkeypatch, tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text("traj\n", encoding="utf-8")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    report_path = tmp_path / "report.json"
    output = tmp_path / "diag.json"
    write_json(
        report_path,
        {
            "schema": "umi_mast3r_stereo_scale_v2",
            "session": str(session.resolve()),
            "trajectory": str(trajectory.resolve()),
            "db3": None,
            "prepared_dataset": str(dataset.resolve()),
            "motion_estimator": "pnp",
            "correspondence_estimator": "classical",
            "trajectory_frame": "infrared_left",
            "pnp_rotation_mode": "free",
            "observations": [
                {
                    "accepted": False,
                    "reason": "translation_excitation_low",
                    "first_index": 323,
                    "second_index": 328,
                    "first_t_sec": 323.0,
                    "second_t_sec": 328.0,
                    "sample_hop": 1,
                }
            ],
        },
    )
    monkeypatch.setattr(
        diag.stereo,
        "load_trajectory",
        lambda _path: (
            np.array([float(i) for i in range(400)]),
            np.zeros((400, 3)),
            np.tile([0.0, 0.0, 0.0, 1.0], (400, 1)),
            [],
        ),
    )
    monkeypatch.setattr(
        diag.stereo,
        "match_trajectory_to_stereo_frames",
        lambda *_args, **_kwargs: (
            np.arange(1000, 1400),
            np.arange(2000, 2400),
            {"trajectory_frame": "infrared_left"},
        ),
    )
    monkeypatch.setattr(
        diag.stereo,
        "load_stereo_calibration_from_prepared_dataset",
        lambda _path: {"left": {}, "baseline_m": 0.018},
    )
    monkeypatch.setattr(
        diag.stereo,
        "load_selected_prepared_stereo_images",
        lambda *_args, **_kwargs: ({1323: "left-a", 1328: "left-b"}, {2323: "right-a", 2328: "right-b"}),
    )

    def fake_estimate_pair_scale(*_args, **_kwargs):
        return {"accepted": False, "reason": "translation_excitation_low", "method": "lk"}

    monkeypatch.setattr(diag.stereo, "estimate_pair_scale", fake_estimate_pair_scale)
    monkeypatch.setattr(
        diag,
        "profile_return_locals",
        lambda func, code, *args, **kwargs: (
            func(*args, **kwargs),
            {"metric_distance_m": 0.002, "mast3r_distance": 0.0, "diagnostic_finite": True},
        ),
    )

    args = diag.argument_parser().parse_args(
        [
            "--report",
            str(report_path),
            "--output",
            str(output),
            "--index-start",
            "323",
            "--index-end",
            "385",
            "--max-pairs",
            "8",
            "--max-depth-m",
            "0.6",
        ]
    )
    result = diag.run(args)
    assert result["replayed_accepted_count"] == 0
    assert result["emitted_factor_count"] == 0
    assert result["no_accepted_factors_emitted"] is True
    diag_row = result["diagnostics"][0]
    assert diag_row["first_left_frame_number"] == 1323
    assert diag_row["second_right_frame_number"] == 2328
    assert diag_row["first_t_sec"] == 323.0
    assert diag_row["time_binding"]["first_time_delta_s"] == 0.0
    assert diag_row["replayed_result"]["reason"] == "translation_excitation_low"
    assert diag_row["ransac_repro_status"] == "reproduced_low_excitation"
    assert result["rejected_replayed_count"] == 1
    assert result["profiler_missed_count"] == 0
    assert result["source_function_identity"]["function"] == "estimate_motion_from_correspondences"
    assert result["parameters"]["max_depth_m"] == 0.6
    assert result["parameters"]["parameter_provenance"]["max_depth_m"]["source"] == "cli_explicit_override"
    assert result["historical_source_hash_verified"] is False
    assert output.is_file()


def test_reproduced_low_without_profiler_capture_fails_diagnostic(monkeypatch, tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "d405_frames.csv").write_text("frames\n", encoding="utf-8")
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text("traj\n", encoding="utf-8")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    report_path = tmp_path / "report.json"
    write_json(
        report_path,
        {
            "schema": "umi_mast3r_stereo_scale_v2",
            "session": str(session.resolve()),
            "trajectory": str(trajectory.resolve()),
            "db3": None,
            "prepared_dataset": str(dataset.resolve()),
            "motion_estimator": "pnp",
            "correspondence_estimator": "classical",
            "trajectory_frame": "infrared_left",
            "observations": [
                {
                    "accepted": False,
                    "reason": "translation_excitation_low",
                    "first_index": 1,
                    "second_index": 2,
                    "first_t_sec": 1.0,
                    "second_t_sec": 2.0,
                }
            ],
        },
    )
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
        diag.stereo,
        "match_trajectory_to_stereo_frames",
        lambda *_args, **_kwargs: (np.array([10, 11, 12]), np.array([20, 21, 22]), {}),
    )
    monkeypatch.setattr(diag.stereo, "load_stereo_calibration_from_prepared_dataset", lambda _path: {})
    monkeypatch.setattr(
        diag.stereo,
        "load_selected_prepared_stereo_images",
        lambda *_args, **_kwargs: ({11: "left-a", 12: "left-b"}, {21: "right-a", 22: "right-b"}),
    )
    monkeypatch.setattr(
        diag.stereo,
        "estimate_pair_scale",
        lambda *_args, **_kwargs: {"accepted": False, "reason": "translation_excitation_low"},
    )
    monkeypatch.setattr(
        diag,
        "profile_return_locals",
        lambda func, code, *args, **kwargs: (func(*args, **kwargs), None),
    )
    args = diag.argument_parser().parse_args(
        [
            "--report",
            str(report_path),
            "--output",
            str(tmp_path / "out.json"),
            "--index-start",
            "1",
            "--index-end",
            "2",
            "--max-depth-m",
            "0.6",
        ]
    )
    result = diag.run(args)
    assert result["result"] == "FAIL"
    assert result["profiler_missed_count"] == 1
    assert "profiler missed 1" in result["failures"][0]


def test_pair_time_binding_rejects_mismatched_report_timestamp():
    pair = diag.Pair(
        first_index=1,
        second_index=2,
        sample_hop=None,
        source_observation={
            "first_t_sec": 1.2,
            "second_t_sec": 2.0,
            "reason": "translation_excitation_low",
        },
    )
    try:
        diag.validate_pair_time_binding(pair, np.array([0.0, 1.0, 2.0]))
    except ValueError as error:
        assert "timestamp mismatch" in str(error)
    else:
        raise AssertionError("expected timestamp binding failure")


def test_source_contract_rejects_declared_alignment_hash_mismatch(tmp_path):
    report = {
        "source_sha256": {str(Path(diag.stereo.__file__).resolve()): "0" * 64}
    }
    try:
        diag.validate_source_contract(report)
    except ValueError as error:
        assert "alignment source hash mismatch" in str(error)
    else:
        raise AssertionError("expected source hash guard failure")

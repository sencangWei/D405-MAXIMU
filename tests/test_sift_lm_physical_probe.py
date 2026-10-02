import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "run_sift_lm_physical_probe",
    ROOT / "scripts/run_sift_lm_physical_probe.py",
)
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def make_fixture(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    record = {"id": "rec1", "session": str(session.resolve())}
    baseline = tmp_path / "baseline"
    artifact = baseline / "rec1" / "both"
    artifact.mkdir(parents=True)
    input_hashes = {}
    eye_reports = {}
    left_report_paths = []
    for eye, main_name, traj_name, frame in (
        ("left", "stereo_scale_bidirectional_report.json", "trajectory_imu_metric.csv", "infrared_left_camera_i"),
        ("right", "stereo_scale_right_report.json", "imu_metric_trajectory.csv", "infrared_right_camera_i"),
    ):
        cache = tmp_path / f"{eye}_cache"
        cache.mkdir()
        trajectory = cache / traj_name
        trajectory.write_text("t_sec,x,y,z,qw,qx,qy,qz\n0,0,0,0,1,0,0,0\n", encoding="utf-8")
        input_hashes[str(trajectory.resolve())] = probe.file_hash(trajectory)
        reports = [main_name] + [
            f"stereo_scale_{kind}{'_right' if eye == 'right' else ''}_report.json"
            for kind in ("long_hops", "dense10hz", "multisecond")
        ]
        eye_reports[eye] = []
        for index, name in enumerate(reports):
            path = cache / name
            observations = []
            if index == 0:
                observations = [
                    {"accepted": True, "method": "sift", "first_index": 0, "second_index": 1},
                    {"accepted": True, "method": "lk", "first_index": 1, "second_index": 2},
                ]
            write_json(
                path,
                {
                    "schema": "umi_mast3r_stereo_scale_v2",
                    "session": str(session.resolve()),
                    "external_ground_truth_used": False,
                    "slam_supervision": False,
                    "observation_frame": frame,
                    "trajectory": str(trajectory.resolve()),
                    "prepared_dataset": str(cache / "dataset"),
                    "observations": observations,
                },
            )
            input_hashes[str(path.resolve())] = probe.file_hash(path)
            eye_reports[eye].append(path)
            if eye == "left":
                left_report_paths.append(path)
        if eye == "left":
            graph_inputs = {
                "stereo_report": str(left_report_paths[0].resolve()),
                "additional_stereo_reports": [str(path.resolve()) for path in left_report_paths[1:]],
                "session": str(session.resolve()),
                "imu_calibration": str((probe.ROOT / "config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml").resolve()),
                "vins_spatiotemporal_calibration": "/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml",
            }
            write_json(
                cache / "graph_fusion_report.json",
                {
                    "inputs": graph_inputs,
                    "camera_extrinsics": {
                        "effective_body_T_trajectory_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
                    },
                },
            )
    candidate = {
        "schema": "umi_dual_ir_symmetric_experiment_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": str(session.resolve()),
        "input_sha256": input_hashes,
        "eye_reports": {
            "left": {
                "effective_body_T_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
            }
        },
        "policy_arguments": probe.base.BASELINE_POLICY_ARGUMENTS,
    }
    graph = {
        "schema": "umi_dual_ir_symmetric_graph_diagnostic_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "output_frame": "body_imu_origin",
        "policy_arguments": probe.base.BASELINE_POLICY_ARGUMENTS,
    }
    write_json(artifact / "candidate_manifest.json", candidate)
    write_json(artifact / "graph_report.json", graph)
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"records": [record]})
    return manifest, baseline, record, eye_reports["left"][0]


def test_preflight_counts_sift_and_reports_legacy_contract_gap(tmp_path):
    manifest, baseline, _record, _left_report = make_fixture(tmp_path)
    args = probe.argument_parser().parse_args(
        ["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]
    )
    result = probe.run(args)
    assert result["status"] == "PREFLIGHT_COMPLETE"
    assert result["record_count"] == 1
    assert result["ready_record_count"] == 1
    assert result["total_accepted_sift"] == 2
    assert result["total_accepted_lk"] == 2
    row = result["records"][0]
    assert row["status"] == "READY_FOR_ADAPTER_DESIGN"
    assert row["original_baseline_input_sha256_preserved"] is True
    assert row["source_override_sha256"] == {}
    assert row["factor_output_count"] == 0
    assert row["accepted_as_candidate"] is False
    assert row["fixed_replay_contract"]["gyro_gate_deg"] == 5.0
    assert row["legacy_contract"]["legacy_refine_reports_callable_without_adapter"] is False
    assert row["legacy_contract"]["scope"] == "left_only"
    assert row["legacy_contract"]["legacy_graph_fusion_reports_found"]
    assert row["legacy_contract"]["missing_prepared_dataset_dirs"]
    assert result["adapter_not_launched"] is True
    assert (tmp_path / "out" / "preflight_report.json").is_file()


def test_preflight_refuses_overwrite(tmp_path):
    manifest, baseline, _record, _left_report = make_fixture(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    args = probe.argument_parser().parse_args(
        ["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(output)]
    )
    try:
        probe.run(args)
    except FileExistsError as error:
        assert str(output) in str(error)
    else:
        raise AssertionError("expected no-overwrite guard")


def test_source_hash_change_is_reported_as_failure(tmp_path):
    manifest, baseline, _record, left_report = make_fixture(tmp_path)
    left_report.write_text(left_report.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    args = probe.argument_parser().parse_args(
        ["--manifest", str(manifest), "--baseline", str(baseline), "--output", str(tmp_path / "out")]
    )
    result = probe.run(args)
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert "source hash changed" in result["failures"][0]["error"]


def test_dataset_filter_limits_records(tmp_path):
    manifest, baseline, record, _left_report = make_fixture(tmp_path)
    other = {"id": "other", "session": record["session"]}
    write_json(manifest, {"records": [record, other]})
    args = probe.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--output",
            str(tmp_path / "out"),
            "--dataset",
            "rec1",
        ]
    )
    result = probe.run(args)
    assert result["record_count"] == 1
    assert result["records"][0]["id"] == "rec1"


def test_run_refine_sources_calls_legacy_only_for_left_contract(monkeypatch, tmp_path):
    manifest, baseline, _record, _left_report = make_fixture(tmp_path)
    for dataset in (tmp_path / "left_cache" / "dataset",):
        dataset.mkdir()
    calls = []

    class Legacy:
        @staticmethod
        def refine_reports(stereo, cached, target):
            calls.append((stereo, cached, target))
            target.mkdir(parents=True)
            out = target / "stereo_scale_bidirectional_report.json"
            out.write_text("{}\n", encoding="utf-8")
            return [str(out)]

    monkeypatch.setattr(probe, "load_legacy_refiner", lambda: Legacy)
    guard = tmp_path / "guard.txt"
    guard.write_text("stable\n", encoding="utf-8")
    monkeypatch.setattr(
        probe,
        "run_guard_paths",
        lambda _record_result: ([guard], {"schema": "test_guard", "prepared_image_path_count": 0}),
    )
    monkeypatch.setattr(
        probe,
        "left_legacy_contract_probe",
        lambda _record, _candidate, _rows: {
            "scope": "left_only",
            "legacy_refine_reports_callable_without_adapter": True,
            "cached_argument": "synthetic/left_cache",
            "legacy_graph_fusion_reports_found": [],
            "missing_legacy_graph_fusion_reports": [],
            "missing_prepared_dataset_dirs": [],
            "graph_inputs_match_baseline_left_raw_reports": True,
            "graph_session_matches_record": True,
            "formal_imu_vins_inputs_match": True,
            "graph_effective_left_extrinsic_matches_candidate": True,
        },
    )
    args = probe.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--output",
            str(tmp_path / "out"),
            "--run",
        ]
    )
    result = probe.run(args)
    assert result["status"] == "PREFLIGHT_COMPLETE"
    assert result["run_refine_sources_requested"] is True
    assert result["source_refine_replay_launched"] is True
    assert result["adapter_not_launched"] is True
    assert result["solver_not_launched"] is True
    assert result["refined_source_count"] == 1
    assert len(calls) == 1
    assert calls[0][1] == "synthetic/left_cache"
    assert result["refined_sources"][0]["source_override_sha256"]
    assert result["refined_sources"][0]["source_guard"]["guarded_after_verified"] is True


def test_run_refine_sources_fails_closed_when_guarded_source_mutates(monkeypatch, tmp_path):
    manifest, baseline, _record, _left_report = make_fixture(tmp_path)
    guard = tmp_path / "guard.txt"
    guard.write_text("before\n", encoding="utf-8")

    class Legacy:
        @staticmethod
        def refine_reports(_stereo, _cached, target):
            guard.write_text("after\n", encoding="utf-8")
            target.mkdir(parents=True)
            out = target / "stereo_scale_bidirectional_report.json"
            out.write_text("{}\n", encoding="utf-8")
            return [str(out)]

    monkeypatch.setattr(probe, "load_legacy_refiner", lambda: Legacy)
    monkeypatch.setattr(
        probe,
        "run_guard_paths",
        lambda _record_result: ([guard], {"schema": "test_guard", "prepared_image_path_count": 0}),
    )
    monkeypatch.setattr(
        probe,
        "left_legacy_contract_probe",
        lambda _record, _candidate, _rows: {
            "scope": "left_only",
            "legacy_refine_reports_callable_without_adapter": True,
            "cached_argument": "synthetic/left_cache",
            "legacy_graph_fusion_reports_found": [],
            "missing_legacy_graph_fusion_reports": [],
            "missing_prepared_dataset_dirs": [],
            "graph_inputs_match_baseline_left_raw_reports": True,
            "graph_session_matches_record": True,
            "formal_imu_vins_inputs_match": True,
            "graph_effective_left_extrinsic_matches_candidate": True,
        },
    )
    args = probe.argument_parser().parse_args(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--output",
            str(tmp_path / "out"),
            "--run",
        ]
    )
    result = probe.run(args)
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert result["refined_sources"] == []
    assert result["refined_source_count"] == 0
    assert "source replay guard failed" in result["failures"][0]["error"]

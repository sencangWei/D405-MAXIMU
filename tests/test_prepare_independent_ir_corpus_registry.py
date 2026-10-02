import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_independent_ir_corpus_registry as registry  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _trajectory(path: Path) -> Path:
    return _write_csv(
        path,
        [
            {"t_sec": "1.0", "x": "0", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"},
            {"t_sec": "2.0", "x": "0.01", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"},
        ],
    )


def _report(path: Path, *, eye: str, trajectory: Path, result: str = "PASS", derived: str | None = None) -> Path:
    obs = {
        "accepted": result == "PASS",
        "first_index": 0,
        "second_index": 1,
        "first_t_sec": 1.0,
        "second_t_sec": 2.0,
        "metric_displacement_camera_i_m": [0.01, 0.0, 0.0],
        "metric_displacement_frame": f"infrared_{eye}_camera_i",
        "scale": 0.5,
        "pnp_inlier_ratio": 0.8,
    }
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": result,
        "session": "/session/a",
        "observation_frame": f"infrared_{eye}_camera_i",
        "trajectory": str(trajectory),
        "scale_m_per_mast3r_unit": 0.5,
        "factory_stereo_calibration": {"baseline_m": 0.018083254},
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "observations": [obs],
    }
    if derived is not None:
        report["derived_from_left_stereo_report"] = derived
    return _write_json(path, report)


def _artifact(path: Path, schema: str, variant: str, session: str = "/session/a") -> dict:
    path.mkdir(parents=True, exist_ok=True)
    graph_schema = schema.replace("_candidate_v1", "_graph_diagnostic_v1").replace("_experiment_v1", "_graph_diagnostic_v1")
    body = _write_csv(path / "body_trajectory_fused.csv", [{"t_sec": "1.0", "x": "0", "y": "0", "z": "0"}])
    policy = {"variant": variant, "optional_stereo_policy": "reject_window", "eyes": "both"}
    if schema != "umi_dual_ir_symmetric_experiment_v1":
        policy["source_policy"] = "both"
    candidate = {
        "schema": schema,
        "status": "EXPERIMENTAL_NOT_ACCEPTED",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "session": session,
        "policy_arguments": policy,
    }
    if schema != "umi_dual_ir_symmetric_experiment_v1":
        candidate["output_estimate_sha256"] = _sha(body)
    files = {
        "candidate_manifest.json": candidate,
        "graph_report.json": {
            "schema": graph_schema,
            "status": "EXPERIMENTAL_NOT_ACCEPTED",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "output_frame": "body_imu_origin",
        },
        "local_motion_factors.json": {"factors": [{"eye": "left"}]},
        "shared_stereo_observations.json": {"observations": [{"first_index": 0, "second_index": 1}]},
    }
    for name, value in files.items():
        _write_json(path / name, value)
    return {name: path / name for name in [*files.keys(), "body_trajectory_fused.csv"]}


def _fixture(
    tmp_path: Path,
    *,
    optional_fail: bool = False,
    duplicate_report: bool = False,
    bad_hash: bool = False,
    bad_time: bool = False,
    bad_nan: bool = False,
    missing_accepted_time: bool = False,
    corrupt_artifact_gt: bool = False,
    bad_baseline_policy: bool = False,
    missing_output_hash: bool = False,
) -> tuple[Path, Path, Path, Path]:
    root = tmp_path
    left_traj = _trajectory(root / "left" / "trajectory_frames.csv")
    right_traj = _trajectory(root / "right" / "trajectory_frames.csv")
    left_metric = _trajectory(root / "left" / "trajectory_imu_metric.csv")
    right_metric = _trajectory(root / "right" / "imu_metric_trajectory.csv")
    names = [
        "stereo_scale_bidirectional_report.json",
        "stereo_scale_long_hops_report.json",
        "stereo_scale_dense10hz_report.json",
        "stereo_scale_multisecond_report.json",
    ]
    left_reports, right_reports = [], []
    for idx, name in enumerate(names):
        result = "FAIL" if optional_fail and idx == 1 else "PASS"
        left_reports.append(_report(root / "left" / name, eye="left", trajectory=left_traj, result=result))
        right_names = [
            "stereo_scale_right_report.json",
            "stereo_scale_long_hops_right_report.json",
            "stereo_scale_dense10hz_right_report.json",
            "stereo_scale_multisecond_right_report.json",
        ]
        right_reports.append(
            _report(
                root / "right" / right_names[idx],
                eye="right",
                trajectory=right_traj,
                result=result,
                derived=str(left_reports[idx]),
            )
        )
    duplicate_extra = None
    if duplicate_report:
        duplicate_extra = _report(
            root / "right_duplicate" / "stereo_scale_right_report.json",
            eye="right",
            trajectory=right_traj,
            derived=str(left_reports[0]),
        )
    if bad_time:
        data = json.loads(left_reports[0].read_text())
        data["observations"][0]["first_t_sec"] = 99.0
        _write_json(left_reports[0], data)
    if bad_nan:
        data = json.loads(left_reports[0].read_text())
        data["observations"][0]["first_t_sec"] = "NaN"
        _write_json(left_reports[0], data)
    if missing_accepted_time:
        data = json.loads(left_reports[0].read_text())
        del data["observations"][0]["first_t_sec"]
        _write_json(left_reports[0], data)
    baseline = root / "batch_adapters_v2"
    both = baseline / "rec1" / "both"
    both.mkdir(parents=True)
    all_sources = [left_metric, right_metric, *left_reports, *right_reports]
    if duplicate_extra is not None:
        all_sources.append(duplicate_extra)
    input_sha = {str(p): _sha(p) for p in all_sources}
    if bad_hash:
        input_sha[str(left_reports[0])] = "0" * 64
    candidate_policy = {"optional_stereo_policy": "reject_window", "eyes": "left" if bad_baseline_policy else "both"}
    _write_json(
        both / "candidate_manifest.json",
        {
            "schema": "umi_dual_ir_symmetric_experiment_v1",
            "status": "EXPERIMENTAL_NOT_ACCEPTED",
            "session": "/session/a",
            "input_sha256": input_sha,
            "policy_arguments": candidate_policy,
            "eye_reports": {
                "left": {
                    "effective_body_T_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                    "factory_stereo_calibration": {"baseline_m": 0.018083254},
                },
                "right": {
                    "effective_body_T_camera": [[1, 0, 0, 0.018], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                    "factory_stereo_calibration": {"baseline_m": 0.018083254},
                },
            },
            "external_ground_truth_used": False,
            "slam_supervision": False,
        },
    )
    _write_json(
        both / "graph_report.json",
        {
            "schema": "umi_dual_ir_symmetric_graph_diagnostic_v1",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "output_frame": "body_imu_origin",
        },
    )
    _write_json(both / "local_motion_factors.json", {"factors": []})
    _write_json(both / "shared_stereo_observations.json", {"observations": []})
    _write_csv(both / "body_trajectory_fused.csv", [{"t_sec": "1.0", "x": "0", "y": "0", "z": "0"}])

    constant = root / "constant"
    combined = root / "combined"
    _artifact(constant / "rec1" / "selected", "umi_constant_ir_gauge_candidate_v1", "selected")
    _artifact(combined / "rec1" / "physical_stereo_constant_gauge", "umi_physical_stereo_lever_candidate_v1", "physical_stereo_constant_gauge")
    if corrupt_artifact_gt:
        data = json.loads((constant / "rec1" / "selected" / "candidate_manifest.json").read_text())
        data["external_ground_truth_used"] = True
        _write_json(constant / "rec1" / "selected" / "candidate_manifest.json", data)
    if missing_output_hash:
        data = json.loads((constant / "rec1" / "selected" / "candidate_manifest.json").read_text())
        data.pop("output_estimate_sha256", None)
        _write_json(constant / "rec1" / "selected" / "candidate_manifest.json", data)
    progress_dir = baseline / "rec_missing"
    progress_dir.mkdir(parents=True)
    _write_json(progress_dir / "progress.json", {"status": "PREPARATION_OR_INPUT_FAILED", "error": "primary_stereo_scale_unobservable"})
    manifest = _write_json(
        root / "manifest.json",
        {
            "schema": "umi_dual_ir_regression_recovery_corpus_v1",
            "records": [
                {"id": "rec1", "session": "/session/a"},
                {
                    "id": "rec_missing",
                    "session": "/session/unobservable",
                    "left_status": "partial_missing_imu_scale",
                    "right_status": "missing",
                    "recovery_audit": {"original_left_failure_preserved": {"failures": ["primary_stereo_scale_unobservable"]}},
                },
            ],
        },
    )
    return manifest, baseline, constant, combined


def test_registry_retains_take05_and_labels_unrefined_baseline_sources(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path)
    report = registry.build_registry(manifest, baseline, constant, combined)

    assert report["schema"] == "umi_independent_ir_corpus_registry_v1"
    assert report["external_ground_truth_used"] is False
    assert report["record_count"] == 2
    rows = {row["id"]: row for row in report["records"]}
    assert rows["rec1"]["status"] == "BASELINE_SOURCE_REGISTRY_READY"
    assert rows["rec1"]["source_context"] == "current_unrefined_baseline_adapter_v2"
    assert rows["rec1"]["right"]["lineage"]["derived_from_left_stereo_report_count"] == 4
    assert rows["rec1"]["right"]["trajectory_role"] == "right_report_bound_raw_frontend_timeline"
    assert rows["rec1"]["left"]["metric_trajectories"][0]["candidate_input_sha256_bound"] is True
    assert rows["rec1"]["left"]["raw_frontend_timeline"]["candidate_input_sha256_bound"] is False
    assert rows["rec_missing"]["status"] == "RETAINED_UNOBSERVABLE_UNSCORED"
    assert rows["rec_missing"]["denominator_retained"] is True


def test_optional_failed_reports_are_hashed_but_not_admitted_as_normal_sources(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path, optional_fail=True)
    report = registry.build_registry(manifest, baseline, constant, combined)
    rec = report["records"][0]

    left = rec["left"]
    assert left["report_count"] == 4
    assert left["normal_pass_report_count"] == 3
    assert left["optional_rejected_report_count"] == 1
    assert any(item["result"] == "FAIL" and item["normal_source_admitted"] is False for item in left["reports"])


@pytest.mark.parametrize("kwargs, needle", [({"duplicate_report": True}, "duplicate"), ({"bad_hash": True}, "sha256 mismatch")])
def test_report_identity_errors_are_per_record_not_whole_registry(tmp_path: Path, kwargs: dict, needle: str) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path, **kwargs)
    report = registry.build_registry(manifest, baseline, constant, combined)
    rows = {row["id"]: row for row in report["records"]}

    assert rows["rec1"]["status"] == "ERROR"
    assert needle in rows["rec1"]["error"]
    assert rows["rec_missing"]["status"] == "RETAINED_UNOBSERVABLE_UNSCORED"
    assert report["status"] == "PREFLIGHT_WITH_RECORD_ERRORS"


def test_timestamp_binding_error_is_reported_before_ready(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path, bad_time=True)
    report = registry.build_registry(manifest, baseline, constant, combined)

    assert report["records"][0]["status"] == "ERROR"
    assert "timestamp mismatch" in report["records"][0]["error"]


@pytest.mark.parametrize("kwargs, needle", [({"bad_nan": True}, "non-finite"), ({"missing_accepted_time": True}, "missing first_t_sec")])
def test_accepted_source_rows_require_finite_explicit_timestamps(tmp_path: Path, kwargs: dict, needle: str) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path, **kwargs)
    report = registry.build_registry(manifest, baseline, constant, combined)

    assert report["records"][0]["status"] == "ERROR"
    assert needle in report["records"][0]["error"]


def test_invalid_rejected_diagnostic_uses_actual_observation_index(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report = {
        "observations": [
            {
                "accepted": False,
                "reason": "valid_rejection",
                "first_index": 0,
                "second_index": 1,
                "first_t_sec": 1.0,
                "second_t_sec": 2.0,
            },
            {
                "accepted": False,
                "reason": "bad_rejection",
                "first_index": 0,
                "second_index": 1,
                "first_t_sec": 1.0,
                "second_t_sec": 20.0,
            },
            {
                "accepted": True,
                "first_index": 0,
                "second_index": 1,
                "first_t_sec": 1.0,
                "second_t_sec": 2.0,
            },
        ]
    }

    counts = registry._validate_observation_times(report, report_path, [1.0, 2.0])

    assert counts["invalid_rejected_observation_count"] == 1
    assert counts["invalid_rejected_observations"][0]["observation_index"] == 1
    assert counts["invalid_rejected_observations"][0]["reason"] == "bad_rejection"


def test_artifact_candidates_must_be_onboard(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path, corrupt_artifact_gt=True)
    report = registry.build_registry(manifest, baseline, constant, combined)

    assert report["records"][0]["status"] == "ERROR"
    assert "used ground truth" in report["records"][0]["error"]


@pytest.mark.parametrize(
    "kwargs, needle",
    [({"bad_baseline_policy": True}, "baseline eyes policy mismatch"), ({"missing_output_hash": True}, "missing output_estimate_sha256")],
)
def test_artifact_policy_and_output_hash_contracts(tmp_path: Path, kwargs: dict, needle: str) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path, **kwargs)
    report = registry.build_registry(manifest, baseline, constant, combined)

    assert report["records"][0]["status"] == "ERROR"
    assert needle in report["records"][0]["error"]


def test_manifest_duplicate_or_missing_ids_fail(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["records"].append(dict(data["records"][0]))
    _write_json(manifest, data)

    with pytest.raises(ValueError, match="duplicate manifest record id"):
        registry.build_registry(manifest, baseline, constant, combined)

    data["records"][1].pop("id")
    data["records"].pop()
    _write_json(manifest, data)
    with pytest.raises(ValueError, match="manifest record missing id"):
        registry.build_registry(manifest, baseline, constant, combined)


def test_cli_writes_new_output_and_refuses_overwrite(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path)
    output = tmp_path / "out"
    registry.main(
        [
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--constant-gauge",
            str(constant),
            "--combined-reference",
            str(combined),
            "--output",
            str(output),
        ]
    )
    assert (output / "preflight_report.json").exists()
    with pytest.raises(FileExistsError):
        registry.main(
            [
                "--manifest",
                str(manifest),
                "--baseline",
                str(baseline),
                "--constant-gauge",
                str(constant),
                "--combined-reference",
                str(combined),
                "--output",
                str(output),
            ]
        )


def test_cli_rejects_symlink_output(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path)
    output = tmp_path / "link_out"
    output.symlink_to(tmp_path / "missing_target", target_is_directory=True)

    with pytest.raises(FileExistsError, match="output already exists or is a symlink"):
        registry.main(
            [
                "--manifest",
                str(manifest),
                "--baseline",
                str(baseline),
                "--constant-gauge",
                str(constant),
                "--combined-reference",
                str(combined),
                "--output",
                str(output),
            ]
        )


def test_cli_subprocess_exits_three_but_writes_metadata_on_record_errors(tmp_path: Path) -> None:
    manifest, baseline, constant, combined = _fixture(tmp_path, bad_hash=True)
    output = tmp_path / "failed_out"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "prepare_independent_ir_corpus_registry.py"),
            "--manifest",
            str(manifest),
            "--baseline",
            str(baseline),
            "--constant-gauge",
            str(constant),
            "--combined-reference",
            str(combined),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    report = json.loads((output / "preflight_report.json").read_text())
    assert report["status"] == "PREFLIGHT_WITH_RECORD_ERRORS"
    assert report["error_record_count"] == 1

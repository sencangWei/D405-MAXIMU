import csv
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_independent_ir_corpus_source_probe as probe  # noqa: E402
import prepare_independent_ir_corpus_registry as registry  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _trajectory(path: Path, *, scale: float = 1.0) -> Path:
    return _write_csv(
        path,
        [
            {"t_sec": "1.0", "x": "0", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"},
            {"t_sec": "2.0", "x": f"{0.01 * scale}", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"},
            {"t_sec": "3.0", "x": f"{0.02 * scale}", "y": "0", "z": "0", "qw": "1", "qx": "0", "qy": "0", "qz": "0"},
        ],
    )


def _observations(*, accepted: bool = True, invalid_rejected: bool = False, invalid_accepted: bool = False) -> list[dict]:
    rows = [
        {
            "accepted": True,
            "first_index": 0,
            "second_index": 1,
            "first_t_sec": 1.0,
            "second_t_sec": 2.0,
            "metric_displacement_camera_i_m": [0.01, 0.0, 0.0],
            "metric_displacement_frame": "infrared_left_camera_i",
            "scale": 0.5,
            "pnp_inlier_ratio": 0.8,
        },
        {
            "accepted": False,
            "reason": "pnp_failed",
            "first_index": 1,
            "second_index": 2,
            "first_t_sec": 2.0,
            "second_t_sec": 3.0,
        },
        {
            "accepted": False,
            "reason": "translation_excitation_low",
            "first_index": 0,
            "second_index": 2,
            "first_t_sec": 1.0,
            "second_t_sec": 3.0,
        },
    ]
    if invalid_accepted:
        rows[0].pop("second_t_sec")
    if invalid_rejected:
        rows[1].pop("second_t_sec")
    return rows if accepted else [
        {
            "accepted": False,
            "reason": "optional_window_failed",
            "first_index": 0,
            "second_index": 1,
            "first_t_sec": 1.0,
            "second_t_sec": 2.0,
        }
    ]


def _report(
    path: Path,
    *,
    session: Path,
    eye: str,
    trajectory: Path,
    result: str = "PASS",
    derived: str | None = None,
    invalid_rejected: bool = False,
    invalid_accepted: bool = False,
) -> Path:
    observations = _observations(accepted=result == "PASS", invalid_rejected=invalid_rejected, invalid_accepted=invalid_accepted)
    for row in observations:
        if row.get("metric_displacement_frame"):
            row["metric_displacement_frame"] = f"infrared_{eye}_camera_i"
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": result,
        "session": str(session.resolve()),
        "observation_frame": f"infrared_{eye}_camera_i",
        "trajectory": str(trajectory.resolve()),
        "scale_m_per_mast3r_unit": 0.5,
        "factory_stereo_calibration": {"baseline_m": 0.018083254},
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "observations": observations,
    }
    if result == "FAIL":
        report["failures"] = ["optional failed"]
    if derived is not None:
        report["derived_from_left_stereo_report"] = str(Path(derived).resolve())
    return _write_json(path, report)


def _graph_schema(schema: str) -> str:
    return {
        "umi_constant_ir_gauge_candidate_v1": "umi_constant_ir_gauge_graph_diagnostic_v1",
        "umi_physical_stereo_lever_candidate_v1": "umi_physical_stereo_lever_graph_diagnostic_v1",
    }.get(schema, "umi_dual_ir_symmetric_graph_diagnostic_v1")


def _artifact(path: Path, schema: str, variant: str, session: Path) -> None:
    body = _write_csv(path / "body_trajectory_fused.csv", [{"t_sec": "1.0", "x": "0", "y": "0", "z": "0"}])
    motion = _write_json(path / "local_motion_factors.json", {"factors": []})
    _write_json(
        path / "candidate_manifest.json",
        {
            "schema": schema,
            "session": str(session.resolve()),
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "policy_arguments": {
                "variant": variant,
                "optional_stereo_policy": "reject_window",
                "eyes": "both",
                "source_policy": "both",
            },
            "output_estimate_sha256": _sha(body),
            "output_motion_factors_sha256": _sha(motion),
        },
    )
    _write_json(
        path / "graph_report.json",
        {"schema": _graph_schema(schema), "output_frame": "body_imu_origin", "external_ground_truth_used": False, "slam_supervision": False},
    )
    _write_json(path / "shared_stereo_observations.json", {"observations": []})


def _fixture(
    tmp_path: Path,
    *,
    optional_fail: bool = False,
    bind_raw_in_candidate: bool = True,
    invalid_rejected: bool = False,
    invalid_accepted: bool = False,
) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    left_raw = _trajectory(tmp_path / "left" / "trajectory_frames.csv")
    right_raw = _trajectory(tmp_path / "right" / "trajectory_frames.csv", scale=2.0)
    left_metric = _trajectory(tmp_path / "left" / "trajectory_imu_metric.csv", scale=3.0)
    right_metric = _trajectory(tmp_path / "right" / "imu_metric_trajectory.csv", scale=4.0)
    left_names = registry.physical.eye_report_names("left")
    right_names = registry.physical.eye_report_names("right")
    left_reports, right_reports = [], []
    for index, (left_name, right_name) in enumerate(zip(left_names, right_names)):
        result = "FAIL" if optional_fail and index == 1 else "PASS"
        left = _report(
            tmp_path / "left" / left_name,
            session=session,
            eye="left",
            trajectory=left_raw,
            result=result,
            invalid_rejected=invalid_rejected and index == 0,
            invalid_accepted=invalid_accepted and index == 0,
        )
        right = _report(
            tmp_path / "right" / right_name,
            session=session,
            eye="right",
            trajectory=right_raw,
            result=result,
            derived=str(left),
            invalid_rejected=invalid_rejected and index == 0,
            invalid_accepted=invalid_accepted and index == 0,
        )
        left_reports.append(left)
        right_reports.append(right)
    baseline = tmp_path / "batch_adapters_v2" / "rec" / "both"
    input_paths = [left_metric, right_metric, *left_reports, *right_reports]
    if bind_raw_in_candidate:
        input_paths.extend([left_raw, right_raw])
    _write_json(
        baseline / "candidate_manifest.json",
        {
            "schema": "umi_dual_ir_symmetric_experiment_v1",
            "status": "EXPERIMENTAL_NOT_ACCEPTED",
            "session": str(session.resolve()),
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "input_sha256": {str(path.resolve()): _sha(path) for path in input_paths},
            "policy_arguments": {"optional_stereo_policy": "reject_window", "eyes": "both"},
            "eye_reports": {
                "left": {"effective_body_T_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "factory_stereo_calibration": {"baseline_m": 0.018083254}},
                "right": {"effective_body_T_camera": [[1, 0, 0, 0.018], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "factory_stereo_calibration": {"baseline_m": 0.018083254}},
            },
        },
    )
    _write_json(
        baseline / "graph_report.json",
        {"schema": "umi_dual_ir_symmetric_graph_diagnostic_v1", "output_frame": "body_imu_origin", "external_ground_truth_used": False, "slam_supervision": False},
    )
    _write_json(baseline / "local_motion_factors.json", {"factors": []})
    _write_json(baseline / "shared_stereo_observations.json", {"observations": []})
    _write_csv(baseline / "body_trajectory_fused.csv", [{"t_sec": "1.0", "x": "0", "y": "0", "z": "0"}])

    constant = tmp_path / "constant" / "rec" / "selected"
    combined = tmp_path / "combined" / "rec" / "physical_stereo_constant_gauge"
    _artifact(constant, "umi_constant_ir_gauge_candidate_v1", "selected", session)
    _artifact(combined, "umi_physical_stereo_lever_candidate_v1", "physical_stereo_constant_gauge", session)
    manifest = _write_json(tmp_path / "manifest.json", {"records": [{"id": "rec", "session": str(session.resolve())}]})
    registry_report = registry.build_registry(manifest, tmp_path / "batch_adapters_v2", tmp_path / "constant", tmp_path / "combined")
    stage = tmp_path / "registry"
    _write_json(stage / "preflight_report.json", registry_report)
    return stage


def test_builds_honest_original_source_preflight_without_refined_aliases(tmp_path: Path) -> None:
    stage = _fixture(tmp_path)
    report = probe.build_source_preflight(stage, datasets=["rec"])
    row = report["records"][0]

    assert report["schema"] == probe.SCHEMA
    assert report["external_ground_truth_used"] is False
    assert row["status"] == "INDEPENDENT_IR_CORPUS_SOURCE_PREFLIGHT_READY"
    assert "refined_left_sources" not in row
    assert "refined_right_sources" not in row
    assert row["source_lineage"] == "original_baseline_corpus_sources"
    assert row["left"]["lineage"]["actual_source"] == "original_baseline_left_reports"
    assert row["right"]["lineage"]["actual_source"] == "original_baseline_derived_right_reports"
    assert row["right"]["accepted_native_geometry_refresh"]["policy"] == "reuse_existing_independent_right_accepted_row_native_geometry_refresh"
    assert row["rejected_recovery"]["policy"] == "unchanged_symmetric_rejected_non_low_excitation_recovery"
    assert row["source_registry"]["path"].endswith("preflight_report.json")
    assert row["source_registry"]["sha256"] == probe.file_hash(stage / "preflight_report.json")
    assert row["right_refresh_proof"]["status"] == "PLANNED_NOT_RUN_METADATA_PREFLIGHT_ONLY"
    assert row["right_refresh_proof"]["path"] is None
    assert row["right_refresh_proof"]["sha256"] is None
    assert len(row["right_refresh_proof"]["report_path_map"]["right"]) == 4
    assert row["recovery_appendix"]["status"] == "PLANNED_NOT_RUN_METADATA_PREFLIGHT_ONLY"
    assert row["recovery_appendix"]["path"] is None
    assert row["recovery_appendix"]["sha256"] is None
    assert set(row["recovery_appendix"]["report_path_map"]) == {"left", "right"}
    assert row["factors_emitted"] == 0
    assert row["backend_launched"] is False


def test_optional_failed_report_is_hashed_but_not_admitted_for_refresh_or_recovery(tmp_path: Path) -> None:
    stage = _fixture(tmp_path, optional_fail=True)
    row = probe.build_source_preflight(stage, datasets=["rec"])["records"][0]

    left_fail = [item for item in row["left"]["source_reports"] if item["result"] == "FAIL"]
    right_fail = [item for item in row["right"]["source_reports"] if item["result"] == "FAIL"]
    assert left_fail and right_fail
    assert all(item["admitted_for_rejected_recovery"] is False for item in left_fail + right_fail)
    assert all(item["admitted_for_accepted_refresh"] is False for item in right_fail)
    assert row["left"]["optional_rejected_report_count"] == 1
    assert row["right"]["optional_rejected_report_count"] == 1


def test_raw_and_metric_trajectories_are_bound_separately(tmp_path: Path) -> None:
    stage = _fixture(tmp_path)
    row = probe.build_source_preflight(stage, datasets=["rec"])["records"][0]

    assert row["right"]["raw_geometry_trajectory"]["path"].endswith("trajectory_frames.csv")
    assert row["right"]["metric_body_trajectory"]["path"].endswith("imu_metric_trajectory.csv")
    assert row["right"]["raw_geometry_trajectory"]["path"] != row["right"]["metric_body_trajectory"]["path"]
    assert row["left"]["raw_geometry_trajectory"]["path"] != row["left"]["metric_body_trajectory"]["path"]


def test_raw_trajectory_may_be_registry_bound_without_candidate_input_binding(tmp_path: Path) -> None:
    stage = _fixture(tmp_path, bind_raw_in_candidate=False)
    row = probe.build_source_preflight(stage, datasets=["rec"])["records"][0]

    assert row["status"] == probe.READY_STATUS
    assert row["right"]["raw_geometry_trajectory"]["path"].endswith("trajectory_frames.csv")
    assert row["right"]["metric_body_trajectory"]["path"].endswith("imu_metric_trajectory.csv")


def test_invalid_rejected_diagnostics_are_excluded_without_failing_record(tmp_path: Path) -> None:
    stage = _fixture(tmp_path, invalid_rejected=True)
    row = probe.build_source_preflight(stage, datasets=["rec"])["records"][0]

    assert row["status"] == probe.READY_STATUS
    assert row["left"]["invalid_rejected_diagnostic_rows_excluded"] == 1
    assert row["right"]["invalid_rejected_diagnostic_rows_excluded"] == 1
    assert row["rejected_recovery"]["invalid_rejected_diagnostic_rows_excluded"] == 2


def test_accepted_missing_binding_fields_fail_closed(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    with pytest.raises(ValueError, match="row missing"):
        probe._classify_observations(
            {"observations": [{"accepted": True, "first_index": 0, "first_t_sec": 1.0, "second_index": 1}]},
            [1.0, 2.0],
            report_path,
        )


def test_raw_metric_timeline_mismatch_fails_record(tmp_path: Path) -> None:
    stage = _fixture(tmp_path)
    preflight = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    metric = Path(preflight["records"][0]["left"]["metric_trajectories"][0]["path"])
    rows = list(csv.DictReader(metric.open(newline="", encoding="utf-8")))
    rows[-1]["t_sec"] = "3.5"
    _write_csv(metric, rows)
    candidate_path = Path(preflight["records"][0]["baseline_adapter_v2"]["files"]["candidate_manifest"]["path"])
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["input_sha256"][str(metric.resolve())] = _sha(metric)
    _write_json(candidate_path, candidate)
    preflight["records"][0]["baseline_adapter_v2"]["files"]["candidate_manifest"]["sha256"] = _sha(candidate_path)
    preflight["records"][0]["baseline_candidate_input_sha256"] = candidate["input_sha256"]
    preflight["records"][0]["left"]["metric_trajectories"][0]["sha256"] = _sha(metric)
    _write_json(stage / "preflight_report.json", preflight)

    row = probe.build_source_preflight(stage, datasets=["rec"])["records"][0]
    assert row["status"] == probe.FAIL_STATUS
    assert "exact timeline" in row["error"]


def test_bad_report_type_and_hash_are_retained_as_per_record_failure(tmp_path: Path) -> None:
    stage = _fixture(tmp_path)
    preflight = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    first = Path(preflight["records"][0]["left"]["source_reports"][0]["path"])
    data = json.loads(first.read_text(encoding="utf-8"))
    data["schema"] = "wrong_schema"
    _write_json(first, data)

    result = probe.build_source_preflight(stage, datasets=["rec"])
    row = result["records"][0]
    assert result["status"] == "PREFLIGHT_WITH_FAILURES"
    assert row["status"] == "SOURCE_PREFLIGHT_FAILED"
    assert "sha256 mismatch" in row["error"] or "schema" in row["error"]


def test_input_registry_is_not_mutated(tmp_path: Path) -> None:
    stage = _fixture(tmp_path)
    registry_doc = probe.load_registry(stage)
    before = deepcopy(registry_doc["raw"])
    probe.prepare_record(registry_doc["records_by_id"]["rec"])
    assert registry_doc["raw"] == before


def test_refuses_existing_output_and_filters_dataset(tmp_path: Path) -> None:
    stage = _fixture(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(FileExistsError):
        probe.run(probe.argument_parser().parse_args(["--registry", str(stage), "--output", str(output), "--dataset", "rec"]))

    result = probe.run(probe.argument_parser().parse_args(["--registry", str(stage), "--output", str(tmp_path / "new"), "--dataset", "rec"]))
    assert result["record_count"] == 1
    assert (tmp_path / "new" / "preflight_report.json").is_file()


def test_underlying_module_aliases_are_not_patched_on_failure(tmp_path: Path) -> None:
    stage = _fixture(tmp_path)
    original = probe.right_refresh.accepted_row_indices
    preflight = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    preflight["external_ground_truth_used"] = True
    _write_json(stage / "preflight_report.json", preflight)
    with pytest.raises(ValueError, match="onboard-only"):
        probe.build_source_preflight(stage)
    assert probe.right_refresh.accepted_row_indices is original

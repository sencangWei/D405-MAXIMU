import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import extract_independent_ir_corpus_sources as producer  # noqa: E402
import prepare_independent_ir_corpus_source_probe as source_meta  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402


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


def _observations(*, result: str = "PASS", invalid_rejected: bool = False, invalid_accepted: bool = False) -> list[dict]:
    if result == "FAIL":
        return [
            {
                "accepted": False,
                "reason": "optional_window_failed",
                "first_index": 0,
                "second_index": 1,
                "first_t_sec": 1.0,
                "second_t_sec": 2.0,
            }
        ]
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
    if invalid_rejected:
        rows[1].pop("second_t_sec")
    if invalid_accepted:
        rows[0].pop("second_t_sec")
    return rows


def _report(path: Path, *, session: Path, eye: str, trajectory: Path, db3: Path, result: str = "PASS", derived: str | None = None, invalid_rejected: bool = False, invalid_accepted: bool = False) -> Path:
    observations = _observations(result=result, invalid_rejected=invalid_rejected, invalid_accepted=invalid_accepted)
    for row in observations:
        if row.get("metric_displacement_frame"):
            row["metric_displacement_frame"] = f"infrared_{eye}_camera_i"
    report = {
        "schema": "umi_mast3r_stereo_scale_v2",
        "result": result,
        "session": str(session.resolve()),
        "db3": str(db3.resolve()),
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


def _report_entry(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    observations = report["observations"]
    accepted = sum(1 for row in observations if row.get("accepted") is True)
    rejected = len(observations) - accepted
    return {
        "path": str(path.resolve()),
        "sha256": _sha(path),
        "result": report["result"],
        "trajectory": {
            "path": report["trajectory"],
            "sha256": _sha(Path(report["trajectory"])),
            "role": f"{report['observation_frame'].replace('infrared_', '').replace('_camera_i', '')}_raw_geometry_trajectory",
        },
        "accepted_observation_count": accepted,
        "rejected_observation_count": rejected,
        "admitted_for_rejected_recovery": report["result"] == "PASS",
        "admitted_for_accepted_refresh": report["result"] == "PASS" and report["observation_frame"] == "infrared_right_camera_i",
    }


def _source_stage(
    tmp_path: Path,
    *,
    record_count: int = 1,
    retained: bool = False,
    optional_fail: bool = False,
    invalid_rejected: bool = False,
    invalid_accepted: bool = False,
    raw_metric_alias: bool = False,
) -> Path:
    db3 = tmp_path / "session" / "record.db3"
    db3.parent.mkdir(parents=True, exist_ok=True)
    db3.write_bytes(b"db3")
    records = []
    registry_records = []
    for idx in range(record_count):
        record_id = f"rec{idx:02d}"
        session = tmp_path / "session" / record_id
        session.mkdir(parents=True)
        if retained and idx == record_count - 1:
            registry_records.append({"id": record_id, "status": source_meta.RETAINED_STATUS})
            records.append(
                {
                    "id": record_id,
                    "session": str(session.resolve()),
                    "status": source_meta.RETAINED_STATUS,
                    "denominator_retained": True,
                    "reason": "primary_stereo_scale_unobservable",
                    "factors_emitted": 0,
                }
            )
            continue
        left_raw = _trajectory(tmp_path / record_id / "left" / "trajectory_frames.csv")
        right_raw = _trajectory(tmp_path / record_id / "right" / "trajectory_frames.csv", scale=2.0)
        left_metric = left_raw if raw_metric_alias else _trajectory(tmp_path / record_id / "left" / "trajectory_imu_metric.csv", scale=3.0)
        right_metric = _trajectory(tmp_path / record_id / "right" / "imu_metric_trajectory.csv", scale=4.0)
        left_paths, right_paths = [], []
        for index, (left_name, right_name) in enumerate(zip(physical.eye_report_names("left"), physical.eye_report_names("right"))):
            result = "FAIL" if optional_fail and index == 1 else "PASS"
            left = _report(
                tmp_path / record_id / "left" / left_name,
                session=session,
                eye="left",
                trajectory=left_raw,
                db3=db3,
                result=result,
                invalid_rejected=invalid_rejected and index == 0,
                invalid_accepted=invalid_accepted and index == 0,
            )
            right = _report(
                tmp_path / record_id / "right" / right_name,
                session=session,
                eye="right",
                trajectory=right_raw,
                db3=db3,
                result=result,
                derived=str(left),
                invalid_rejected=invalid_rejected and index == 0,
                invalid_accepted=invalid_accepted and index == 0,
            )
            left_paths.append(left)
            right_paths.append(right)
        left_entries = [_report_entry(path) for path in left_paths]
        right_entries = [_report_entry(path) for path in right_paths]
        baseline = tmp_path / "baseline" / record_id / "both"
        candidate_inputs = [left_raw, right_raw, left_metric, right_metric, *left_paths, *right_paths]
        d405 = _write_csv(tmp_path / record_id / "d405_frames.csv", [{"t_sec": "1.0", "frame": "0"}])
        candidate_inputs.append(d405)
        candidate = _write_json(
            baseline / "candidate_manifest.json",
            {
                "schema": "umi_dual_ir_symmetric_experiment_v1",
                "session": str(session.resolve()),
                "external_ground_truth_used": False,
                "slam_supervision": False,
                "input_sha256": {str(path.resolve()): _sha(path) for path in candidate_inputs},
            },
        )
        graph = _write_json(baseline / "graph_report.json", {"schema": "umi_dual_ir_symmetric_graph_diagnostic_v1"})
        registry_records.append(
            {
                "id": record_id,
                "status": "BASELINE_SOURCE_REGISTRY_READY",
                "baseline_adapter_v2": {
                    "files": {
                        "candidate_manifest": {"path": str(candidate.resolve()), "sha256": _sha(candidate)},
                        "graph_report": {"path": str(graph.resolve()), "sha256": _sha(graph)},
                    }
                },
                "baseline_candidate_input_sha256": {str(path.resolve()): _sha(path) for path in candidate_inputs},
            }
        )
        records.append(
            {
                "id": record_id,
                "session": str(session.resolve()),
                "status": source_meta.READY_STATUS,
                "denominator_retained": True,
                "ready_for_image_extract": True,
                "source_registry": {},
                "left": {
                    "source_reports": left_entries,
                    "raw_geometry_trajectory": {"path": str(left_raw.resolve()), "sha256": _sha(left_raw), "role": "left_raw_geometry_trajectory"},
                    "metric_body_trajectory": {"path": str(left_metric.resolve()), "sha256": _sha(left_metric), "role": "left_body_metric_trajectory"},
                },
                "right": {
                    "source_reports": right_entries,
                    "raw_geometry_trajectory": {"path": str(right_raw.resolve()), "sha256": _sha(right_raw), "role": "right_raw_geometry_trajectory"},
                    "metric_body_trajectory": {"path": str(right_metric.resolve()), "sha256": _sha(right_metric), "role": "right_body_metric_trajectory"},
                },
            }
        )
    stage = tmp_path / "source_preflight"
    registry_path = tmp_path / "registry" / "preflight_report.json"
    registry_report = {
        "schema": "umi_independent_ir_corpus_registry_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "records": registry_records,
    }
    _write_json(registry_path, registry_report)
    for record in records:
        if record.get("status") == source_meta.READY_STATUS:
            record["source_registry"] = {"path": str(registry_path.resolve()), "sha256": _sha(registry_path)}
    report = {
        "schema": source_meta.SCHEMA,
        "status": "PREFLIGHT_COMPLETE",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "records": records,
    }
    _write_json(stage / "preflight_report.json", report)
    return stage


def _install_lightweight_native(monkeypatch: pytest.MonkeyPatch, *, mutate_path: Path | None = None, captured: dict | None = None) -> None:
    def fake_load_trajectory(_path):
        times = np.asarray([1.0, 2.0, 3.0], dtype=float)
        pos = np.zeros((3, 3), dtype=float)
        quat = np.tile(np.asarray([0.0, 0.0, 0.0, 1.0]), (3, 1))
        return times, pos, quat, {}

    def fake_load_images(*_args, **_kwargs):
        numbers = np.asarray([0, 1, 2], dtype=int)
        images = {0: np.zeros((2, 2), dtype=np.uint8), 1: np.zeros((2, 2), dtype=np.uint8), 2: np.zeros((2, 2), dtype=np.uint8)}
        return numbers, numbers, {"sync": True}, {"calib": True}, images, images

    refresh_calls = []

    def fake_refresh_report(**kwargs):
        source = Path(kwargs["report_path"])
        output = Path(kwargs["output_path"])
        refresh_calls.append(source.name)
        data = json.loads(source.read_text(encoding="utf-8"))
        data.pop("derived_from_left_stereo_report", None)
        data["geometry_refresh_policy"] = {"mock": True}
        _write_json(output, data)
        if mutate_path is not None:
            mutated = json.loads(mutate_path.read_text(encoding="utf-8"))
            mutated["mutated_after_snapshot"] = True
            _write_json(mutate_path, mutated)
        accepted = sum(1 for row in data["observations"] if row.get("accepted") is True)
        rejected = len(data["observations"]) - accepted
        return {
            "source_report": str(source.resolve()),
            "refreshed_report": str(output.resolve()),
            "source_sha256": _sha(source),
            "refreshed_sha256": _sha(output),
            "accepted_input_rows": accepted,
            "independent_replaced_rows": accepted,
            "fallback_preserved_rows": 0,
            "rejected_input_rows_unchanged": rejected,
            "pairs_preserved": True,
            "global_scale_preserved": True,
        }

    def fake_build_eye_appendix(**kwargs):
        eye = kwargs["eye"]
        rows = kwargs["preflight"][2]
        if captured is not None:
            captured.setdefault(eye, []).extend(rows)
        observations = [
            {
                "eye": eye,
                "source_report_path": str(item["path"]),
                "source_observation_index": item["index"],
                "native_observation": {"accepted": True, "first_index": item["first"], "second_index": item["second"]},
            }
            for item in rows
        ]
        context = {"reference_scale": 0.5, "merged_report_paths": [str(path.resolve()) for path in kwargs["paths"]]}
        consumed = {f"{eye}_report_{index}": path for index, path in enumerate(kwargs["paths"])}
        return context, observations, consumed

    monkeypatch.setattr(producer.diag.stereo, "load_trajectory", fake_load_trajectory)
    monkeypatch.setattr(producer.diag.lowdiag, "_load_images", fake_load_images)
    monkeypatch.setattr(producer.right_refresh, "refresh_report", fake_refresh_report)
    monkeypatch.setattr(producer.recovery, "build_eye_appendix", fake_build_eye_appendix)
    if captured is not None:
        captured["refresh_calls"] = refresh_calls


def test_invalid_rejected_rows_are_excluded_from_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path, invalid_rejected=True)
    captured: dict = {}
    _install_lightweight_native(monkeypatch, captured=captured)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])
    row = report["records"][0]

    assert row["status"] == producer.READY_STATUS
    assert row["invalid_rejected_diagnostic_rows_excluded"] == 2
    assert all("second_t_sec" in item["row"] for item in captured["left"] + captured["right"])


def test_accepted_rows_remain_strict_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path, invalid_accepted=True)
    _install_lightweight_native(monkeypatch)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])

    assert report["status"] == "NATIVE_SOURCES_WITH_FAILURES"
    assert report["records"][0]["status"] == producer.FAIL_STATUS
    assert "row missing" in report["records"][0]["error"]


def test_optional_unadmitted_right_report_is_byte_copied_not_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path, optional_fail=True)
    captured: dict = {}
    _install_lightweight_native(monkeypatch, captured=captured)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])
    row = report["records"][0]

    copied = [item for item in row["right_refresh_proof"]["reports"].values() if item["byte_copied_unadmitted_optional"]]
    assert len(copied) == 1
    copied_path = Path(next(path for path, item in row["right_refresh_proof"]["reports"].items() if item["byte_copied_unadmitted_optional"]))
    original = Path(copied[0]["fallback_right_source_path"])
    assert copied_path.read_bytes() == original.read_bytes()
    assert copied_path.name not in captured["refresh_calls"]


def test_original_left_paths_and_onboard_flags_are_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path)
    _install_lightweight_native(monkeypatch)
    before_left = {path: path.read_bytes() for path in (tmp_path / "rec00" / "left").glob("*.json")}

    report = producer.run(producer.argument_parser().parse_args(["--source-preflight", str(stage), "--output", str(tmp_path / "out"), "--dataset", "rec00"]))
    row = report["records"][0]

    assert report["external_ground_truth_used"] is False
    assert row["external_ground_truth_used"] is False
    assert row["backend_launched"] is False
    parent = json.loads(Path(row["right_refresh_source_stage_path"]).read_text(encoding="utf-8"))
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text(encoding="utf-8"))
    assert parent["schema"] == "umi_independent_ir_corpus_right_refresh_record_v1"
    assert parent["right_source_paths"] == row["right_source_paths"]
    assert appendix["source_stage_preflight"] == row["right_refresh_source_stage_path"]
    assert appendix["source_stage_preflight_sha256"] == row["right_refresh_source_stage_sha256"]
    assert appendix["source_preflight"] == row["source_preflight"]["path"]
    assert "refined_left_sources" not in parent
    assert "refined_right_sources" not in parent
    assert row["recovery_appendix_path"] not in row["source_override_sha256"]
    assert row["left_source_paths"] == [str(path.resolve()) for path in sorted(before_left, key=lambda p: physical.eye_report_names("left").index(p.name))]
    assert {path: path.read_bytes() for path in before_left} == before_left


def test_raw_metric_alias_fails_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path, raw_metric_alias=True)
    _install_lightweight_native(monkeypatch)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])

    assert report["records"][0]["status"] == producer.FAIL_STATUS
    assert "raw and metric trajectories must be distinct" in report["records"][0]["error"]


def test_retains_25_denominator_with_index_by_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path, record_count=25, retained=True)
    _install_lightweight_native(monkeypatch)

    report = producer.build_native_sources(stage, tmp_path / "out")

    assert report["record_count"] == 25
    assert report["ready_record_count"] == 24
    assert report["retained_unobservable_count"] == 1
    assert report["records"][24]["status"] == producer.RETAINED_STATUS
    assert report["by_id"]["rec24"] == 24
    assert isinstance(report["by_id"]["rec00"], int)


def test_input_hash_mutation_during_refresh_fails_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path)
    preflight = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    mutate_path = Path(preflight["records"][0]["right"]["source_reports"][0]["path"])
    _install_lightweight_native(monkeypatch, mutate_path=mutate_path)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])

    assert report["records"][0]["status"] == producer.FAIL_STATUS
    assert "consumed source changed during extraction" in report["records"][0]["error"]


def test_source_report_hash_mismatch_fails_before_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path)
    preflight = json.loads((stage / "preflight_report.json").read_text(encoding="utf-8"))
    source_path = Path(preflight["records"][0]["right"]["source_reports"][0]["path"])
    data = json.loads(source_path.read_text(encoding="utf-8"))
    data["unexpected_mutation"] = True
    _write_json(source_path, data)
    _install_lightweight_native(monkeypatch)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])

    assert report["records"][0]["status"] == producer.FAIL_STATUS
    assert "source report sha256 mismatch" in report["records"][0]["error"]


def test_source_admission_flags_are_recomputed_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path)
    preflight_path = stage / "preflight_report.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["records"][0]["right"]["source_reports"][0]["admitted_for_accepted_refresh"] = False
    _write_json(preflight_path, preflight)
    _install_lightweight_native(monkeypatch)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])

    assert report["records"][0]["status"] == producer.FAIL_STATUS
    assert "accepted refresh admission mismatch" in report["records"][0]["error"]


def test_appendix_consumed_paths_must_be_guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path)
    extra = _write_json(tmp_path / "unguarded.json", {"not": "guarded"})
    _install_lightweight_native(monkeypatch)

    def fake_build_eye_appendix(**kwargs):
        context = {"reference_scale": 0.5, "merged_report_paths": [str(path.resolve()) for path in kwargs["paths"]]}
        return context, [], {"unguarded": extra}

    monkeypatch.setattr(producer.recovery, "build_eye_appendix", fake_build_eye_appendix)

    report = producer.build_native_sources(stage, tmp_path / "out", ["rec00"])

    assert report["records"][0]["status"] == producer.FAIL_STATUS
    assert "consumed paths missing from source guard" in report["records"][0]["error"]


def test_refuses_existing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stage = _source_stage(tmp_path)
    _install_lightweight_native(monkeypatch)
    output = tmp_path / "out"
    output.mkdir()

    with pytest.raises(FileExistsError):
        producer.run(producer.argument_parser().parse_args(["--source-preflight", str(stage), "--output", str(output)]))

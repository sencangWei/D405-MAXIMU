import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_independent_ir_corpus_probe as probe  # noqa: E402


def write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return path


def source_file(path: Path, value: dict | None = None) -> Path:
    return write_json(path, {"ok": path.name} if value is None else value)


def stereo_report(path: Path, *, result: str = "PASS", scale: float = 1.0) -> Path:
    return write_json(
        path,
        {
            "schema": "umi_mast3r_stereo_scale_v2",
            "result": result,
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "session": "/session",
            "trajectory": str((path.parent / "right_raw.csv").resolve()),
            "observation_frame": "infrared_right_camera_i",
            "factory_stereo_calibration": {"baseline_m": 0.018},
            "scale_m_per_mast3r_unit": scale,
            "observations": [],
        },
    )


def ready_stage(
    tmp_path: Path,
    *,
    bad_hash: bool = False,
    fake_refined: bool = False,
    duplicate: bool = False,
    extra_override: bool = False,
) -> tuple[Path, dict]:
    left = [source_file(tmp_path / f"left_{index}.json") for index in range(4)]
    raw_right = source_file(tmp_path / "right_raw.csv", {"raw": True})
    right = [stereo_report(tmp_path / f"right_{index}.json") for index in range(4)]
    registry_preflight = write_json(
        tmp_path / "source_registry" / "preflight_report.json",
        {
            "schema": "umi_independent_ir_corpus_source_preflight_v1",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "records": [
                {
                    "id": "rec",
                    "session": "/session",
                    "status": "INDEPENDENT_IR_CORPUS_SOURCE_PREFLIGHT_READY",
                    "ready_for_image_extract": True,
                    "left": {
                        "lineage": {
                            "actual_source": "original_baseline_left_reports",
                            "not_sift_lm_refined_source": True,
                            "not_refined_lineage_alias": True,
                        },
                        "source_reports": [
                            {"path": str(path.resolve()), "sha256": probe.file_hash(path)}
                            for path in left
                        ]
                    },
                    "right": {
                        "raw_geometry_trajectory": {"path": str(raw_right.resolve()), "sha256": probe.file_hash(raw_right)},
                        "source_reports": [
                            {
                                "path": str(path.resolve()),
                                "sha256": probe.file_hash(path),
                                "derived_from_left_stereo_report": str(left[index].resolve()),
                            }
                            for index, path in enumerate(right)
                        ],
                        "lineage": {
                            "actual_source": "original_baseline_derived_right_reports",
                            "not_sift_lm_refined_source": True,
                            "not_refined_lineage_alias": True,
                        },
                    },
                }
            ],
        },
    )
    overrides = {str(path.resolve()): probe.file_hash(path) for path in [*left, *right]}
    if bad_hash:
        overrides[str(left[0].resolve())] = "bad"
    right_refresh_proof = {
        "schema": "umi_independent_right_geometry_refresh_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_preflight": str(registry_preflight.resolve()),
        "source_preflight_sha256": probe.file_hash(registry_preflight),
        "reports": {
            str(path.resolve()): {
                "fallback_right_source_path": str(path.resolve()),
                "fallback_right_source_sha256": probe.file_hash(path),
                "raw_right_geometry_trajectory": str(raw_right.resolve()),
                "raw_right_geometry_trajectory_sha256": probe.file_hash(raw_right),
                "pairs_preserved": True,
                "global_scale_preserved": True,
            }
            for path in right
        },
    }
    parent = write_json(
        tmp_path / "right_refresh_parent" / "right_refresh_source_stage.json",
        {
            "schema": probe.RIGHT_REFRESH_PARENT_SCHEMA,
            "id": "rec",
            "session": "/session",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "source_preflight": str(registry_preflight.resolve()),
            "source_preflight_sha256": probe.file_hash(registry_preflight),
            "left_source_paths": [str(path) for path in left],
            "right_source_paths": [str(path) for path in right],
            "source_override_sha256": overrides,
            "right_refresh_proof": right_refresh_proof,
        },
    )
    appendix = source_file(tmp_path / "appendix.json", {
        "schema": "umi_independent_ir_recovery_appendix_v1",
        "native_source_schema": probe.SOURCE_SCHEMA,
        "source_stage_preflight": str(parent.resolve()),
        "source_stage_preflight_sha256": probe.file_hash(parent),
    })
    row_overrides = dict(overrides)
    if extra_override:
        marker = source_file(tmp_path / "appendix_marker.json", {"not": "a stereo report"})
        row_overrides[str(marker.resolve())] = probe.file_hash(marker)
    row = {
        "id": "rec",
        "session": "/session",
        "status": probe.READY_STATUS,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "ready_for_consumer": True,
        "source_preflight": {"path": str(registry_preflight.resolve()), "sha256": probe.file_hash(registry_preflight)},
        "left_source_paths": [str(path) for path in left],
        "right_source_paths": [str(path) for path in right],
        "source_override_sha256": row_overrides,
        "right_refresh_proof": right_refresh_proof,
        "recovery_appendix_path": str(appendix),
        "recovery_appendix_sha256": probe.file_hash(appendix),
    }
    if fake_refined:
        row["refined_left_sources"] = list(row["left_source_paths"])
    records = [
        row,
        {"id": "retained", "session": "/session2", "status": "RETAINED_UNOBSERVABLE_UNSCORED"},
        {"id": "technical", "session": "/session3", "status": "INDEPENDENT_IR_CORPUS_NATIVE_SOURCE_FAILED", "error": "source failed"},
    ]
    if duplicate:
        records.append(dict(records[0]))
    report = {
        "schema": probe.SOURCE_SCHEMA,
        "status": "NATIVE_SOURCES_COMPLETE",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "record_count": len(records),
        "records": records,
    }
    stage = tmp_path / "stage"
    write_json(stage / "preflight_report.json", report)
    return stage, row


def test_load_source_stage_validates_honest_schema_and_denominator_rows(tmp_path: Path) -> None:
    stage, row = ready_stage(tmp_path)

    loaded = probe.load_source_stage(stage)

    assert loaded["ready_record_count"] == 1
    assert loaded["retained_record_count"] == 1
    assert loaded["technical_failed_record_count"] == 1
    ready = probe.validate_source_stage_record("rec", loaded)
    assert ready["left_source_paths"] == [str(Path(path).resolve()) for path in row["left_source_paths"]]
    assert probe.adapt_ready_record_for_paired_runner(ready)["refined_left_sources"] == ready["left_source_paths"]
    with pytest.raises(ValueError, match="not ready"):
        probe.validate_source_stage_record("retained", loaded)


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"bad_hash": True}, "source hash changed"),
        ({"fake_refined": True}, "fake refined"),
        ({"duplicate": True}, "duplicate"),
    ],
)
def test_load_source_stage_fails_closed_on_binding_or_identity_errors(tmp_path: Path, kwargs: dict, match: str) -> None:
    stage, _ = ready_stage(tmp_path, **kwargs)
    with pytest.raises(ValueError, match=match):
        probe.load_source_stage(stage)


def test_load_source_stage_rejects_gt_or_wrong_schema(tmp_path: Path) -> None:
    stage, _ = ready_stage(tmp_path)
    report = json.loads((stage / "preflight_report.json").read_text())
    report["external_ground_truth_used"] = True
    write_json(stage / "preflight_report.json", report)
    with pytest.raises(ValueError, match="ground truth"):
        probe.load_source_stage(stage)
    report["external_ground_truth_used"] = False
    report["schema"] = "old_fake_schema"
    write_json(stage / "preflight_report.json", report)
    with pytest.raises(ValueError, match="schema mismatch"):
        probe.load_source_stage(stage)


def test_validate_recovery_appendix_requires_typed_parent_stage(tmp_path: Path, monkeypatch) -> None:
    stage, row = ready_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text())
    other = write_json(tmp_path / "other.json", {"schema": probe.RIGHT_REFRESH_PARENT_SCHEMA})
    appendix["source_stage_preflight"] = str(other)
    appendix["source_stage_preflight_sha256"] = "bad"
    write_json(Path(row["recovery_appendix_path"]), appendix)

    with pytest.raises(ValueError, match="source_stage_preflight"):
        probe.validate_recovery_appendix(
            {"id": "rec", "session": "/session"},
            loaded["by_id"]["rec"],
            {},
            [],
            {"left": {}, "right": {}},
            loaded,
        )


def test_validate_recovery_appendix_parent_must_match_ready_row(tmp_path: Path) -> None:
    stage, row = ready_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text())
    parent = Path(appendix["source_stage_preflight"])
    parent_report = json.loads(parent.read_text())
    parent_report["right_source_paths"] = list(reversed(parent_report["right_source_paths"]))
    write_json(parent, parent_report)
    appendix["source_stage_preflight_sha256"] = probe.file_hash(parent)
    write_json(Path(row["recovery_appendix_path"]), appendix)

    with pytest.raises(ValueError, match="right_source_paths mismatch"):
        probe.validate_recovery_appendix(
            {"id": "rec", "session": "/session"},
            loaded["by_id"]["rec"],
            {},
            [],
            {"left": {}, "right": {}},
            loaded,
        )


def test_validate_recovery_appendix_rejects_wrong_source_metadata_schema(tmp_path: Path) -> None:
    stage, row = ready_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    registry = Path(loaded["by_id"]["rec"]["source_preflight"]["path"])
    registry_report = json.loads(registry.read_text())
    registry_report["schema"] = "unexpected_schema"
    write_json(registry, registry_report)
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text())
    parent = Path(appendix["source_stage_preflight"])
    parent_report = json.loads(parent.read_text())
    parent_report["source_preflight_sha256"] = probe.file_hash(registry)
    parent_report["right_refresh_proof"]["source_preflight_sha256"] = probe.file_hash(registry)
    parent_report["right_refresh_proof"]["source_preflight"] = str(registry.resolve())
    write_json(parent, parent_report)
    appendix["source_stage_preflight_sha256"] = probe.file_hash(parent)
    write_json(Path(row["recovery_appendix_path"]), appendix)
    loaded["by_id"]["rec"]["source_preflight"]["sha256"] = probe.file_hash(registry)
    loaded["by_id"]["rec"]["right_refresh_proof"]["source_preflight_sha256"] = probe.file_hash(registry)
    loaded["by_id"]["rec"]["right_refresh_proof"]["source_preflight"] = str(registry.resolve())

    with pytest.raises(ValueError, match="source_preflight schema mismatch"):
        probe.validate_recovery_appendix(
            {"id": "rec", "session": "/session"},
            loaded["by_id"]["rec"],
            {},
            [],
            {"left": {}, "right": {}},
            loaded,
        )


def test_source_metadata_readiness_matches_real_source_preflight_schema(tmp_path: Path) -> None:
    stage, row = ready_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    registry = Path(loaded["by_id"]["rec"]["source_preflight"]["path"])
    report = json.loads(registry.read_text())
    source_row = report["records"][0]

    assert report["schema"] == "umi_independent_ir_corpus_source_preflight_v1"
    assert source_row["status"] == "INDEPENDENT_IR_CORPUS_SOURCE_PREFLIGHT_READY"
    assert source_row["ready_for_image_extract"] is True
    assert "ready_for_source_refresh" not in source_row
    assert source_row["left"]["lineage"] == {
        "actual_source": "original_baseline_left_reports",
        "not_sift_lm_refined_source": True,
        "not_refined_lineage_alias": True,
    }
    assert source_row["right"]["lineage"] == {
        "actual_source": "original_baseline_derived_right_reports",
        "not_sift_lm_refined_source": True,
        "not_refined_lineage_alias": True,
    }

    loaded_report, loaded_row = probe.load_bound_source_preflight(
        registry,
        probe.file_hash(registry),
        "rec",
        "/session",
    )

    assert loaded_report["schema"] == report["schema"]
    assert loaded_row["id"] == "rec"

    source_row["ready_for_image_extract"] = False
    write_json(registry, report)
    with pytest.raises(ValueError, match="not ready"):
        probe.load_bound_source_preflight(registry, probe.file_hash(registry), "rec", "/session")


def test_source_metadata_rejects_registry_style_right_lineage(tmp_path: Path) -> None:
    stage, row = ready_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    registry = Path(loaded["by_id"]["rec"]["source_preflight"]["path"])
    report = json.loads(registry.read_text())
    report["records"][0]["right"]["lineage"] = {
        "derived_from_left_stereo_report_count": 4,
        "derived_from_left_stereo_report_paths": report["records"][0]["source_reports_left"] if "source_reports_left" in report["records"][0] else [],
    }
    write_json(registry, report)
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text())
    parent = Path(appendix["source_stage_preflight"])
    parent_report = json.loads(parent.read_text())
    parent_report["source_preflight_sha256"] = probe.file_hash(registry)
    parent_report["right_refresh_proof"]["source_preflight_sha256"] = probe.file_hash(registry)
    write_json(parent, parent_report)
    appendix["source_stage_preflight_sha256"] = probe.file_hash(parent)
    write_json(Path(row["recovery_appendix_path"]), appendix)
    loaded["by_id"]["rec"]["source_preflight"]["sha256"] = probe.file_hash(registry)
    loaded["by_id"]["rec"]["right_refresh_proof"]["source_preflight_sha256"] = probe.file_hash(registry)

    with pytest.raises(ValueError, match="RIGHT lineage mismatch"):
        probe.validate_recovery_appendix(
            {"id": "rec", "session": "/session"},
            loaded["by_id"]["rec"],
            {},
            [],
            {"left": {}, "right": {}},
            loaded,
        )


def test_validate_recovery_appendix_rejects_left_swap_and_right_fallback_outside_metadata(tmp_path: Path) -> None:
    stage, row = ready_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    registry = Path(loaded["by_id"]["rec"]["source_preflight"]["path"])
    registry_report = json.loads(registry.read_text())
    registry_report["records"][0]["left"]["source_reports"] = list(reversed(registry_report["records"][0]["left"]["source_reports"]))
    write_json(registry, registry_report)
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text())
    parent = Path(appendix["source_stage_preflight"])
    parent_report = json.loads(parent.read_text())
    parent_report["source_preflight_sha256"] = probe.file_hash(registry)
    parent_report["right_refresh_proof"]["source_preflight_sha256"] = probe.file_hash(registry)
    write_json(parent, parent_report)
    appendix["source_stage_preflight_sha256"] = probe.file_hash(parent)
    write_json(Path(row["recovery_appendix_path"]), appendix)
    loaded["by_id"]["rec"]["source_preflight"]["sha256"] = probe.file_hash(registry)
    loaded["by_id"]["rec"]["right_refresh_proof"]["source_preflight_sha256"] = probe.file_hash(registry)

    with pytest.raises(ValueError, match="LEFT report paths mismatch"):
        probe.validate_recovery_appendix(
            {"id": "rec", "session": "/session"},
            loaded["by_id"]["rec"],
            {},
            [],
            {"left": {}, "right": {}},
            loaded,
        )

    stage, row = ready_stage(tmp_path / "outside")
    loaded = probe.load_source_stage(stage)
    outside = source_file(tmp_path / "outside_right.json")
    binding = next(iter(loaded["by_id"]["rec"]["right_refresh_proof"]["reports"].values()))
    binding["fallback_right_source_path"] = str(outside.resolve())
    binding["fallback_right_source_sha256"] = probe.file_hash(outside)
    appendix = json.loads(Path(row["recovery_appendix_path"]).read_text())
    parent = Path(appendix["source_stage_preflight"])
    parent_report = json.loads(parent.read_text())
    parent_report["right_refresh_proof"] = loaded["by_id"]["rec"]["right_refresh_proof"]
    write_json(parent, parent_report)
    appendix["source_stage_preflight_sha256"] = probe.file_hash(parent)
    write_json(Path(row["recovery_appendix_path"]), appendix)

    with pytest.raises(ValueError, match="outside source_preflight metadata"):
        probe.validate_recovery_appendix(
            {"id": "rec", "session": "/session"},
            loaded["by_id"]["rec"],
            {},
            [],
            {"left": {}, "right": {}},
            loaded,
        )


def test_validate_recovery_appendix_reuses_existing_validator_with_inmemory_adapter(tmp_path: Path, monkeypatch) -> None:
    stage, row = ready_stage(tmp_path)
    loaded = probe.load_source_stage(stage)
    captured = {}

    def fake_validate(record, adapted_row, baseline_candidate, reference_times, eye_reports):
        captured["record"] = record
        captured["adapted_row"] = adapted_row
        captured["reference_times"] = reference_times
        captured["eye_reports"] = eye_reports
        return ["candidate"], {"proof": True}, [Path(adapted_row["recovery_appendix_path"])]

    monkeypatch.setattr(probe.recovery, "validate_recovery_appendix", fake_validate)

    result = probe.validate_recovery_appendix(
        {"id": "rec", "session": "/session"},
        loaded["by_id"]["rec"],
        {"baseline": True},
        [0.0, 0.1],
        {"left": {}, "right": {}},
        loaded,
    )

    assert result[0] == ["candidate"]
    assert captured["adapted_row"]["refined_left_sources"] == loaded["by_id"]["rec"]["left_source_paths"]
    assert captured["adapted_row"]["refined_right_sources"] == loaded["by_id"]["rec"]["right_source_paths"]
    assert captured["adapted_row"]["independent_right_geometry_refresh"] == loaded["by_id"]["rec"]["right_refresh_proof"]


def test_ready_record_adapter_filters_non_report_override_hashes(tmp_path: Path, monkeypatch) -> None:
    stage, _ = ready_stage(tmp_path, extra_override=True)
    loaded = probe.load_source_stage(stage)
    ready = loaded["by_id"]["rec"]

    adapter = probe.adapt_ready_record_for_paired_runner(ready)

    report_keys = {str(Path(path).resolve()) for path in [*ready["left_source_paths"], *ready["right_source_paths"]]}
    assert set(adapter["source_override_sha256"]) == report_keys
    assert len(ready["source_override_sha256"]) == len(report_keys) + 1


def test_validate_recovery_appendix_allows_top_row_extra_nonreport_override(tmp_path: Path, monkeypatch) -> None:
    stage, _ = ready_stage(tmp_path, extra_override=True)
    loaded = probe.load_source_stage(stage)
    captured = {}

    def fake_validate(record, adapted_row, baseline_candidate, reference_times, eye_reports):
        captured["overrides"] = adapted_row["source_override_sha256"]
        return [], {}, []

    monkeypatch.setattr(probe.recovery, "validate_recovery_appendix", fake_validate)

    probe.validate_recovery_appendix(
        {"id": "rec", "session": "/session"},
        loaded["by_id"]["rec"],
        {},
        [],
        {"left": {}, "right": {}},
        loaded,
    )

    report_keys = {
        str(Path(path).resolve())
        for path in [*loaded["by_id"]["rec"]["left_source_paths"], *loaded["by_id"]["rec"]["right_source_paths"]]
    }
    assert set(captured["overrides"]) == report_keys


def test_main_load_candidates_passes_typed_stage_record_to_right_refresh_validator(tmp_path: Path, monkeypatch) -> None:
    stage, _ = ready_stage(tmp_path)
    baseline = tmp_path / "baseline"
    shared_dir = baseline / "rec" / probe.paired.physical.BASELINE_POLICY
    write_json(shared_dir / "shared_stereo_observations.json", [])
    captured = {}

    def fake_main(_argv):
        source_stage = probe.paired.load_source_stage(stage)
        return probe.paired.run_record(
            {"id": "rec", "session": "/session"},
            baseline,
            Path("/constant"),
            Path("/combined"),
            Path("/output"),
            source_stage,
            {},
        )["status"]

    def fake_original_run_record(record, baseline_root, *args, **kwargs):
        source_stage = args[3]
        stage_record = probe.paired.source_eval.validate_source_stage_record(record["id"], source_stage)
        candidates, eye_reports, consumed, overrides = probe.paired.load_refined_all_eye_candidates(
            record,
            {"candidate": True},
            [0.0],
            stage_record,
        )
        captured["candidates"] = candidates
        captured["eye_reports"] = eye_reports
        captured["consumed"] = consumed
        captured["overrides"] = overrides
        return {"id": record["id"], "status": 0, "variants": {}}

    def fake_load_reports(record, eye, baseline_candidate, reference_times, paths, *, refined_override_sha256):
        return ([{"eye": eye, "path": str(paths[0])}], {"eye": eye}, [paths[0]])

    def fake_right_refresh(stage_record, right_paths):
        assert "right_refresh_proof" in stage_record
        assert "independent_right_geometry_refresh" not in stage_record
        captured["right_refresh_record_id"] = stage_record["id"]
        return {"validated": True}, [Path(right_paths[0])]

    def fake_recovery(record, row, baseline_candidate, reference_times, eye_reports, source_stage):
        return ([{"recovered": True}], {"appendix": True}, [Path(row["recovery_appendix_path"])])

    def fake_build(reference_times, original_rows, existing, recovered):
        return ([{"template": True}], [{"shared": True}], {"ok": True})

    monkeypatch.setattr(probe.paired, "main", fake_main)
    monkeypatch.setattr(probe.paired, "run_record", fake_original_run_record)
    monkeypatch.setattr(probe.paired, "load_override_eye_candidates_from_reports", fake_load_reports)
    monkeypatch.setattr(probe, "validate_right_geometry_refresh_evidence", fake_right_refresh)
    monkeypatch.setattr(probe, "validate_recovery_appendix", fake_recovery)
    monkeypatch.setattr(probe, "build_recovered_shared_rows", fake_build)

    assert probe.main([]) == 0
    assert captured["right_refresh_record_id"] == "rec"
    assert captured["candidates"] == [{"shared": True}]
    assert captured["eye_reports"]["right"]["independent_right_geometry_refresh"] == {"validated": True}


def test_right_refresh_validation_accepts_byte_copied_unadmitted_optional_without_refresh_validator(tmp_path: Path, monkeypatch) -> None:
    source_preflight = write_json(tmp_path / "source_registry" / "preflight_report.json", {"schema": "registry"})
    raw = write_json(tmp_path / "right_raw.csv", {"raw": True})
    refreshed = []
    reports = {}
    for index in range(4):
        fallback = stereo_report(
            tmp_path / f"fallback_{index}.json",
            result="PASS" if index == 0 else "FAIL",
        )
        out = tmp_path / f"out_{index}.json"
        out.write_bytes(fallback.read_bytes())
        refreshed.append(out.resolve())
        reports[str(out.resolve())] = {
            "fallback_right_source_path": str(fallback.resolve()),
            "fallback_right_source_sha256": probe.file_hash(fallback),
            "raw_right_geometry_trajectory": str(raw.resolve()),
            "raw_right_geometry_trajectory_sha256": probe.file_hash(raw),
            "pairs_preserved": True,
            "global_scale_preserved": True,
            "byte_copied_unadmitted_optional": index != 0,
        }
    called = {}

    def fake_refresh_validator(stage_record, right_paths):
        called["paths"] = [str(path.resolve()) for path in right_paths]
        return {"refreshed_row_count": 0, "fallback_row_count": 0}, list(right_paths)

    monkeypatch.setattr(probe.paired, "validate_right_geometry_refresh_evidence", fake_refresh_validator)
    proof = {
        "schema": "umi_independent_right_geometry_refresh_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_preflight": str(source_preflight.resolve()),
        "source_preflight_sha256": probe.file_hash(source_preflight),
        "reports": reports,
    }

    validated, consumed = probe.validate_right_geometry_refresh_evidence({"right_refresh_proof": proof}, refreshed)

    assert validated["optional_unadmitted_copied_count"] == 3
    assert called["paths"] == [str(refreshed[0])]
    assert set(refreshed) <= set(consumed)


def test_byte_copied_optional_must_remain_identical(tmp_path: Path) -> None:
    source_preflight = write_json(tmp_path / "source_registry" / "preflight_report.json", {"schema": "registry"})
    raw = write_json(tmp_path / "right_raw.csv", {"raw": True})
    fallback = stereo_report(tmp_path / "fallback.json", result="FAIL")
    out = write_json(tmp_path / "out.json", {**json.loads(fallback.read_text()), "changed": True})
    primary = stereo_report(tmp_path / "primary.json", result="PASS")
    primary_out = tmp_path / "primary_out.json"
    primary_out.write_bytes(primary.read_bytes())
    proof = {
        "schema": "umi_independent_right_geometry_refresh_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_preflight": str(source_preflight.resolve()),
        "source_preflight_sha256": probe.file_hash(source_preflight),
        "reports": {
            str(primary_out.resolve()): {
                "fallback_right_source_path": str(primary.resolve()),
                "fallback_right_source_sha256": probe.file_hash(primary),
                "raw_right_geometry_trajectory": str(raw.resolve()),
                "raw_right_geometry_trajectory_sha256": probe.file_hash(raw),
                "byte_copied_unadmitted_optional": False,
                "pairs_preserved": True,
                "global_scale_preserved": True,
            },
            str(out.resolve()): {
                "fallback_right_source_path": str(fallback.resolve()),
                "fallback_right_source_sha256": probe.file_hash(fallback),
                "raw_right_geometry_trajectory": str(raw.resolve()),
                "raw_right_geometry_trajectory_sha256": probe.file_hash(raw),
                "byte_copied_unadmitted_optional": True,
                "pairs_preserved": True,
                "global_scale_preserved": True,
            }
        },
    }

    with pytest.raises(ValueError, match="byte-copied RIGHT optional"):
        probe.validate_right_geometry_refresh_evidence({"right_refresh_proof": proof}, [primary_out, out])


def test_byte_copied_right_report_must_be_unadmitted_by_normal_merge_policy(tmp_path: Path) -> None:
    source_preflight = write_json(tmp_path / "source_registry" / "preflight_report.json", {"schema": "registry"})
    raw = write_json(tmp_path / "right_raw.csv", {"raw": True})
    fallback = stereo_report(tmp_path / "fallback.json", result="PASS")
    out = tmp_path / "out.json"
    out.write_bytes(fallback.read_bytes())
    proof = {
        "schema": "umi_independent_right_geometry_refresh_v1",
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "source_preflight": str(source_preflight.resolve()),
        "source_preflight_sha256": probe.file_hash(source_preflight),
        "reports": {
            str(out.resolve()): {
                "fallback_right_source_path": str(fallback.resolve()),
                "fallback_right_source_sha256": probe.file_hash(fallback),
                "raw_right_geometry_trajectory": str(raw.resolve()),
                "raw_right_geometry_trajectory_sha256": probe.file_hash(raw),
                "byte_copied_unadmitted_optional": True,
                "pairs_preserved": True,
                "global_scale_preserved": True,
            }
        },
    }

    with pytest.raises(ValueError, match="admitted by normal merge policy"):
        probe.validate_right_geometry_refresh_evidence({"right_refresh_proof": proof}, [out])


def test_process_local_patches_restore_on_failure(monkeypatch) -> None:
    names = (
        "load_source_stage",
        "load_refined_all_eye_candidates",
        "refresh_shared_row_confidences",
        "run_record",
        "source_upgrade_scope",
        "frozen_code_paths",
        "SCHEMA",
        "REFINED_VARIANT",
    )
    original = {name: getattr(probe.paired, name) for name in names}
    original_validate = probe.paired.source_eval.validate_source_stage_record

    def fail(_argv):
        assert probe.paired.SCHEMA == probe.SCHEMA
        assert probe.paired.REFINED_VARIANT == probe.REFINED_VARIANT
        raise RuntimeError("boom")

    monkeypatch.setattr(probe.paired, "main", fail)
    with pytest.raises(RuntimeError, match="boom"):
        probe.main([])
    assert all(getattr(probe.paired, name) is original[name] for name in names)
    assert probe.paired.source_eval.validate_source_stage_record is original_validate


def test_frozen_code_paths_uses_captured_original_without_recursion(monkeypatch) -> None:
    original = probe.paired.frozen_code_paths
    calls = []

    def fake_original(path):
        calls.append(path)
        return [Path("/base.py")]

    def fake_main(_argv):
        paths = probe.paired.frozen_code_paths(Path("/stage"))
        assert Path("/base.py") in paths
        assert Path(probe.__file__).resolve() in paths
        return 0

    monkeypatch.setattr(probe.paired, "frozen_code_paths", fake_original)
    monkeypatch.setattr(probe.paired, "main", fake_main)

    assert probe.main([]) == 0
    assert calls == [Path("/stage")]
    assert probe.paired.frozen_code_paths is fake_original
    monkeypatch.setattr(probe.paired, "frozen_code_paths", original)


def test_source_upgrade_scope_uses_corpus_not_sift_lm_labels() -> None:
    scope = probe.source_upgrade_scope({"path": "sha"}, "corpus")
    assert scope["source"] == "independent_ir_corpus_native_sources"
    assert scope["right_accepted_refresh"] is True
    assert scope["symmetric_rejected_nonlow_recovery"] is True
    assert "sift" not in json.dumps(scope).lower()

    control = probe.source_upgrade_scope({}, "control")
    assert control["right_accepted_refresh"] is False


def test_retained_source_record_stays_denominator_without_backend(monkeypatch) -> None:
    source_stage = {"by_id": {"retained": {"id": "retained", "status": "RETAINED_UNOBSERVABLE_UNSCORED"}}}
    captured = {}

    def fake_main(_argv):
        captured["result"] = probe.paired.run_record(
                {"id": "retained", "session": "/session"},
                Path("/baseline"),
                Path("/constant"),
                Path("/combined"),
                Path("/output"),
                source_stage,
                {},
            )
        return 0

    monkeypatch.setattr(probe.paired, "main", fake_main)
    assert probe.main([]) == 0
    assert captured["result"]["denominator_retained"] is True
    assert captured["result"]["variants"] == {}


def test_technical_failed_source_record_stays_denominator_without_backend(monkeypatch) -> None:
    source_stage = {"by_id": {"technical": {"id": "technical", "status": "INDEPENDENT_IR_CORPUS_NATIVE_SOURCE_FAILED", "error": "bad source"}}}
    captured = {}

    def fake_main(_argv):
        captured["result"] = probe.paired.run_record(
            {"id": "technical", "session": "/session"},
            Path("/baseline"),
            Path("/constant"),
            Path("/combined"),
            Path("/output"),
            source_stage,
            {},
        )
        return 0

    monkeypatch.setattr(probe.paired, "main", fake_main)
    assert probe.main([]) == 0
    assert captured["result"]["denominator_retained"] is True
    assert captured["result"]["status"] == "INDEPENDENT_IR_CORPUS_NATIVE_SOURCE_FAILED"
    assert captured["result"]["variants"] == {}

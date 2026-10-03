import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_independent_ir_corpus_probe as corpus  # noqa: E402
import evaluate_sift_lm_dual_combined_probe as paired  # noqa: E402
import run_independent_ir_fast_regression as fast  # noqa: E402


ORIGINAL = paired.ORIGINAL_VARIANT
REFINED = corpus.REFINED_VARIANT


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def _write_text(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _variant(tmp_path: Path, record_id: str, variant: str) -> dict:
    artifact = tmp_path / "artifact" / record_id / variant
    estimate = _write_text(artifact / "body_trajectory_fused.csv", f"{record_id},{variant}\n")
    score = {
        "result": "PASS",
        "failures": [],
        "samples": 4,
        "ate_translation_mean_m": 0.001,
        "ate_translation_p95_m": 0.002,
        "ate_translation_max_m": 0.003,
        "ate_translation_rmse_m": 0.0015,
        "ate_translation_within_10mm_ratio": 1.0,
        "timestamp_overlap_ratio": 1.0,
    }
    _write_json(artifact / "score" / "precision.json", score)
    row = {"artifact_dir": str(artifact.resolve()), "score": score, "estimate_sha256": _sha(estimate)}
    if variant == ORIGINAL:
        row["control_replay_agreement"] = {
            "max_position_delta_m": 0.0,
            "threshold_m": paired.CONTROL_REPLAY_MAX_DELTA_M,
            "max_rotation_error_rad": 0.0,
            "rotation_threshold_rad": paired.ROTATION_REPLAY_TOLERANCE_RAD,
            "sample_count": 4,
        }
    return row


def _consumer_summary(path: Path, *, manifest: Path, source_stage: Path, ids: list[str], context_suffix: str = "") -> None:
    source_preflight = source_stage / "preflight_report.json"
    context_root = manifest.parent / f"context{context_suffix}"
    results = [
        {
            "id": record_id,
            "status": "COMPLETED",
            "variants": {
                ORIGINAL: _variant(path, record_id, ORIGINAL),
                REFINED: _variant(path, record_id, REFINED),
            },
        }
        for record_id in ids
    ]
    _write_json(
        path / "summary.json",
        {
            "schema": corpus.SCHEMA,
            "status": "COMPLETED",
            "development_only": True,
            "blind_test": False,
            "production_promoted": False,
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "dataset_count": len(ids),
            "completed_count": len(ids),
            "manifest_sha256": _sha(manifest),
            "baseline_summary_sha256": "b" * 64,
            "source_stage": str(source_stage.resolve()),
            "source_stage_preflight_sha256": _sha(source_preflight),
            "baseline": str((context_root / "baseline").resolve()),
            "constant_gauge": str((context_root / "constant").resolve()),
            "combined_reference": str((context_root / "combined").resolve()),
            "variants": [ORIGINAL, REFINED],
            "control_replay_required_max_delta_m": paired.CONTROL_REPLAY_MAX_DELTA_M,
            "code_sha256": {str(source_preflight.resolve()): _sha(source_preflight), "/code.py": "c" * 64},
            "results": results,
        },
    )


def _stage(tmp_path: Path, name: str, ids: list[str], *, status: str = "NATIVE_SOURCES_COMPLETE") -> Path:
    stage = tmp_path / name
    _write_json(
        stage / "preflight_report.json",
        {
            "schema": corpus.SOURCE_SCHEMA,
            "status": status,
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "record_count": len(ids),
            "records": [{"id": record_id, "status": corpus.READY_STATUS} for record_id in ids],
        },
    )
    return stage


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    failure = ["f1", "f2"]
    passing = ["p1", "p2"]
    manifest = _write_json(tmp_path / "manifest.json", {"records": [{"id": record_id, "session": f"/s/{record_id}"} for record_id in [*failure, *passing]]})
    combined = _write_json(
        tmp_path / "combined" / "summary.json",
        {
            "schema": "umi_physical_stereo_lever_development_regression_v1",
            "status": "COMPLETED_WITH_FAILURES",
            "results": [
                {"id": record_id, "variants": {"physical_stereo_constant_gauge": {"score": {"result": "FAIL"}}}}
                for record_id in failure
            ] + [
                {"id": record_id, "variants": {"physical_stereo_constant_gauge": {"score": {"result": "PASS"}}}}
                for record_id in passing
            ],
        },
    )
    stage_a = _stage(tmp_path, "stage_a", ["f1", "p1"])
    stage_b = _stage(tmp_path, "stage_b", ["f2", "p2"])
    config = {
        "schema": fast.CONFIG_SCHEMA,
        "cohort_selection_uses_reference_scores": True,
        "external_ground_truth_used_for_selection": True,
        "external_ground_truth_used_by_solver": False,
        "production_selector_uses_ground_truth": False,
        "manifest": str(manifest),
        "baseline": str(tmp_path / "baseline"),
        "constant_gauge": str(tmp_path / "constant"),
        "combined_reference": str(tmp_path / "combined"),
        "combined_reference_summary": str(combined),
        "combined_reference_summary_sha256": _sha(combined),
        "combined_reference_variant": "physical_stereo_constant_gauge",
        "telemetry_wrapper": str(ROOT / "scripts/audit_independent_ir_solver_convergence.py"),
        "source_stages": [str(stage_a), str(stage_b)],
        "failure_records": failure,
        "passing_records": passing,
        "allowed_consumer_exit_codes": [0, 3],
    }
    config_path = _write_json(tmp_path / "fast10.json", config)
    return config_path, config


@pytest.fixture
def source_stage_verifier(monkeypatch):
    def fake_verify(summary: dict) -> dict:
        source_stage = Path(summary["source_stage"]).resolve()
        preflight = source_stage / "preflight_report.json"
        if summary["source_stage_preflight_sha256"] != _sha(preflight):
            raise ValueError("source stage preflight hash mismatch")
        if summary["code_sha256"][str(preflight)] != _sha(preflight):
            raise ValueError("source stage preflight missing from code_sha256")
        report = json.loads(preflight.read_text(encoding="utf-8"))
        return {
            "source_stage": str(source_stage),
            "source_stage_preflight": str(preflight),
            "source_stage_preflight_sha256": summary["source_stage_preflight_sha256"],
            "source_preflight": str((source_stage.parent / "metadata.json").resolve()),
            "source_preflight_sha256": "m" * 64,
            "source_policy": report.get("source_policy", "fixture_policy"),
            "source_record_ids": {row["id"] for row in report["records"]},
        }

    monkeypatch.setattr(fast.merger, "_verify_source_stage", fake_verify)


def test_groups_failures_before_passing_and_invokes_existing_telemetry_wrapper(tmp_path: Path, source_stage_verifier) -> None:
    config_path, _config = _fixture(tmp_path)
    calls = []

    def fake_runner(command, cwd=None):
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        telemetry = Path(command[command.index("--telemetry-output") + 1])
        source_stage = Path(command[command.index("--source-stage") + 1])
        ids = [command[index + 1] for index, value in enumerate(command) if value == "--dataset"]
        _consumer_summary(output, manifest=Path(command[command.index("--manifest") + 1]), source_stage=source_stage, ids=ids)
        _write_json(telemetry, {"schema": "umi_independent_ir_solver_lsqr_telemetry_v1", "external_ground_truth_used": False, "slam_supervision": False, "status": "RECORDED", "call_count": 2})
        return SimpleNamespace(returncode=3)

    report = fast.run(config_path, tmp_path / "out", fake_runner)

    assert report["status"] == "COMPLETED"
    assert [group["phase"] for group in report["groups"]] == ["failure", "failure", "passing", "passing"]
    assert [group["record_ids"] for group in report["groups"]] == [["f1"], ["f2"], ["p1"], ["p2"]]
    assert len(calls) == 4
    assert all("audit_independent_ir_solver_convergence.py" in call[1] for call in calls)
    assert all("--dataset" in call for call in calls)
    assert report["groups"][0]["telemetry_status"] == "RECORDED"
    assert report["groups"][0]["returncode"] == 3


def test_fail_closed_on_duplicate_missing_nonterminal_and_existing_output(tmp_path: Path, source_stage_verifier) -> None:
    config_path, config = _fixture(tmp_path)
    config["passing_records"] = ["p1", "f1"]
    dup = _write_json(tmp_path / "dup.json", config)
    with pytest.raises(ValueError, match="duplicate"):
        fast.run(dup, tmp_path / "dup_out", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))

    config_path, config = _fixture(tmp_path / "missing")
    config["failure_records"] = ["f1", "absent"]
    missing = _write_json(tmp_path / "missing.json", config)
    with pytest.raises(ValueError, match="combined reference missing|selected records missing"):
        fast.run(missing, tmp_path / "missing_out", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))

    config_path, config = _fixture(tmp_path / "nonterminal")
    stage = Path(config["source_stages"][0])
    doc = json.loads((stage / "preflight_report.json").read_text())
    doc["status"] = "RUNNING"
    _write_json(stage / "preflight_report.json", doc)
    with pytest.raises(ValueError, match="not terminal"):
        fast.run(config_path, tmp_path / "nonterminal_out", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))

    config_path, _config = _fixture(tmp_path / "exists")
    out = tmp_path / "already"
    out.mkdir()
    with pytest.raises(FileExistsError):
        fast.run(config_path, out, lambda *_args, **_kwargs: SimpleNamespace(returncode=0))


def test_stops_on_disallowed_return_code_without_continuing(tmp_path: Path) -> None:
    config_path, _config = _fixture(tmp_path)
    calls = []

    def failing_runner(command, cwd=None):
        calls.append(command)
        return SimpleNamespace(returncode=2)

    report = fast.run(config_path, tmp_path / "out", failing_runner)

    assert report["status"] == "STOP_COMMAND_FAILED"
    assert len(calls) == 1
    assert report["groups"][0]["status"] == "COMMAND_FAILED"


def test_rejects_duplicate_selected_record_across_source_stages(tmp_path: Path) -> None:
    config_path, config = _fixture(tmp_path)
    doc = json.loads((Path(config["source_stages"][1]) / "preflight_report.json").read_text())
    doc["records"].append({"id": "f1", "status": corpus.READY_STATUS})
    doc["record_count"] = len(doc["records"])
    _write_json(Path(config["source_stages"][1]) / "preflight_report.json", doc)
    with pytest.raises(ValueError, match="duplicate source-stage ownership"):
        fast.run(config_path, tmp_path / "out", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))


def test_failure_cohort_must_be_strict_fail(tmp_path: Path) -> None:
    config_path, config = _fixture(tmp_path)
    summary = json.loads(Path(config["combined_reference_summary"]).read_text())
    summary["results"][0]["variants"]["physical_stereo_constant_gauge"]["score"]["result"] = "RETAINED_UNOBSERVABLE_UNSCORED"
    _write_json(Path(config["combined_reference_summary"]), summary)
    config["combined_reference_summary_sha256"] = _sha(Path(config["combined_reference_summary"]))
    config_path = _write_json(tmp_path / "strict_fail.json", config)
    with pytest.raises(ValueError, match="combined-reference failure"):
        fast.run(config_path, tmp_path / "out", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))


def test_subset_summary_rejects_tampered_preflight_and_context_mismatch(tmp_path: Path, source_stage_verifier) -> None:
    config_path, _config = _fixture(tmp_path)
    calls = []

    def tampered_runner(command, cwd=None):
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        telemetry = Path(command[command.index("--telemetry-output") + 1])
        source_stage = Path(command[command.index("--source-stage") + 1])
        ids = [command[index + 1] for index, value in enumerate(command) if value == "--dataset"]
        _consumer_summary(output, manifest=Path(command[command.index("--manifest") + 1]), source_stage=source_stage, ids=ids)
        summary = json.loads((output / "summary.json").read_text())
        summary["source_stage_preflight_sha256"] = "0" * 64
        _write_json(output / "summary.json", summary)
        _write_json(telemetry, {"schema": "umi_independent_ir_solver_lsqr_telemetry_v1", "external_ground_truth_used": False, "slam_supervision": False, "status": "RECORDED", "call_count": 1})
        return SimpleNamespace(returncode=0)

    with pytest.raises(ValueError, match="preflight hash mismatch"):
        fast.run(config_path, tmp_path / "tampered", tampered_runner)

    config_path, _config = _fixture(tmp_path / "context")
    group_index = 0

    def context_runner(command, cwd=None):
        nonlocal group_index
        group_index += 1
        output = Path(command[command.index("--output") + 1])
        telemetry = Path(command[command.index("--telemetry-output") + 1])
        source_stage = Path(command[command.index("--source-stage") + 1])
        ids = [command[index + 1] for index, value in enumerate(command) if value == "--dataset"]
        _consumer_summary(
            output,
            manifest=Path(command[command.index("--manifest") + 1]),
            source_stage=source_stage,
            ids=ids,
            context_suffix="" if group_index == 1 else "_changed",
        )
        _write_json(telemetry, {"schema": "umi_independent_ir_solver_lsqr_telemetry_v1", "external_ground_truth_used": False, "slam_supervision": False, "status": "RECORDED", "call_count": 1})
        return SimpleNamespace(returncode=0)

    with pytest.raises(ValueError, match="context mismatch"):
        fast.run(config_path, tmp_path / "context_out", context_runner)


def _progress_rows():
    def score(maximum):
        return {"result": "PASS" if maximum <= .010 else "FAIL", "ate_translation_max_m": maximum,
                "samples": 100, "timestamp_overlap_ratio": 1.}
    return [{"id": name, "baseline_score": score(before), "candidate_score": score(after)}
            for name, before, after in [("f1", .020, .014), ("f2", .018, .013), ("f3", .014, .015),
                                        ("p1", .008, .009), ("p2", .009, .0095)]]


def test_partial_improvement_retained_even_without10mm_completion():
    result = fast.assess_incremental_progress(_progress_rows(), ["f1", "f2", "f3"], ["p1", "p2"])
    assert result["status"] == "RETAIN_INCREMENTAL_PROGRESS"
    assert result["improved_failures"] == ["f1", "f2"]
    assert result["worsened_failures"] == ["f3"]
    assert result["fixed10_all_pass"] is False
    assert result["full25_acceptance"] is False
    assert result["production_promoted"] is False


def test_passing_control_crossing10mm_prevents_adoption_not_direction_rejection():
    rows = _progress_rows()
    rows[-1]["candidate_score"].update(result="FAIL", ate_translation_max_m=.01001)
    result = fast.assess_incremental_progress(rows, ["f1", "f2", "f3"], ["p1", "p2"])
    assert result["status"] == "CONTROL_REGRESSION_DO_NOT_ADOPT"
    assert result["passing_regressions"] == ["p2"]
    assert result["direction_proven_useless"] is False


def test_missing_score_or_coverage_cannot_prove_retention_or_useless_direction():
    rows = _progress_rows()
    rows[0]["candidate_score"]["samples"] = 99
    result = fast.assess_incremental_progress(rows, ["f1", "f2", "f3"], ["p1", "p2"])
    assert result["status"] == "INCOMPLETE_COMPARISON"
    assert result["direction_proven_useless"] is False
    rows = _progress_rows()
    rows[0]["candidate_score"]["ate_translation_max_m"] = float("nan")
    assert fast.assess_incremental_progress(rows, ["f1", "f2", "f3"], ["p1", "p2"])["status"] == "INCOMPLETE_COMPARISON"


def test_mixed_progress_kept_and_fixed_set_full_pass_is_not25_acceptance():
    rows = _progress_rows()
    rows[1]["candidate_score"]["ate_translation_max_m"] = .018
    assert fast.assess_incremental_progress(rows, ["f1", "f2", "f3"], ["p1", "p2"])["status"] == "MIXED_PROGRESS_KEEP_EXPERIMENT"
    for row in rows:
        row["candidate_score"].update(result="PASS", ate_translation_max_m=.010)
    result = fast.assess_incremental_progress(rows, ["f1", "f2", "f3"], ["p1", "p2"])
    assert result["fixed10_all_pass"] is True
    assert result["max_ate_requirement_m"] == .010
    assert result["full25_acceptance"] is False

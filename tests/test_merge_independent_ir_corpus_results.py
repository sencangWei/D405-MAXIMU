import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_independent_ir_corpus_probe as corpus  # noqa: E402
import evaluate_sift_lm_dual_combined_probe as paired  # noqa: E402
import merge_independent_ir_corpus_results as merge  # noqa: E402


ORIGINAL = paired.ORIGINAL_VARIANT
REFINED = corpus.REFINED_VARIANT


@pytest.fixture(autouse=True)
def _minimal_source_stage_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_load_source_stage(path: Path) -> dict:
        report = json.loads((Path(path) / "preflight_report.json").read_text(encoding="utf-8"))
        return {"raw": report, "by_id": {row["id"]: row for row in report.get("records", [])}}

    monkeypatch.setattr(merge.corpus, "load_source_stage", fake_load_source_stage)


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


def _variant(tmp_path: Path, record_id: str, variant: str, *, replay_ok: bool = True) -> dict:
    artifact = tmp_path / "artifact" / record_id / variant
    estimate = _write_text(artifact / "body_trajectory_fused.csv", f"{record_id},{variant}\n")
    score = {
        "result": "PASS",
        "failures": [],
        "samples": 4,
        "ate_translation_mean_m": 0.0005,
        "ate_translation_p95_m": 0.0009,
        "ate_translation_max_m": 0.001,
        "ate_translation_rmse_m": 0.0006,
        "ate_translation_within_10mm_ratio": 1.0,
        "timestamp_overlap_ratio": 1.0,
    }
    _write_json(artifact / "score" / "precision.json", score)
    row = {
        "artifact_dir": str(artifact.resolve()),
        "score": score,
        "estimate_sha256": _sha(estimate),
    }
    if variant == ORIGINAL:
        row["control_replay_agreement"] = {
            "max_position_delta_m": 0.0 if replay_ok else 1.0,
            "threshold_m": 1e-7,
            "max_rotation_error_rad": 0.0,
            "rotation_threshold_rad": 5e-9,
            "sample_count": 4,
        }
    return row


def _fixture(tmp_path: Path, *, ids: list[str] | None = None, split: bool = False) -> tuple[Path, list[Path]]:
    ids = ids or ["a", "b", "c"]
    manifest = _write_json(tmp_path / "manifest.json", {"records": [{"id": item, "session": f"/session/{item}"} for item in ids]})
    base = {
        "schema": corpus.SCHEMA,
        "status": "COMPLETED",
        "development_only": True,
        "blind_test": False,
        "production_promoted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "manifest_sha256": _sha(manifest),
        "baseline_summary_sha256": "b" * 64,
        "baseline": str((tmp_path / "baseline").resolve()),
        "constant_gauge": str((tmp_path / "constant").resolve()),
        "combined_reference": str((tmp_path / "combined").resolve()),
        "variants": [ORIGINAL, REFINED],
        "control_replay_required_max_delta_m": 1e-7,
    }
    groups = [ids[:1], ids[1:]] if split else [ids]
    metadata_preflight = _write_json(
        tmp_path / "metadata_source_preflight" / "preflight_report.json",
        {
            "schema": "umi_independent_ir_corpus_source_preflight_v1",
            "status": "PREFLIGHT_COMPLETE",
            "external_ground_truth_used": False,
            "slam_supervision": False,
            "records": [],
        },
    )
    summaries = []
    for index, group in enumerate(groups):
        source_stage = tmp_path / f"source_stage_{index}"
        source_preflight = _write_json(
            source_stage / "preflight_report.json",
            {
                "schema": "umi_independent_ir_corpus_native_sources_v1",
                "status": "NATIVE_SOURCES_COMPLETE",
                "external_ground_truth_used": False,
                "slam_supervision": False,
                "record_count": 0,
                "source_preflight": str(metadata_preflight.resolve()),
                "source_preflight_sha256": _sha(metadata_preflight),
                "source_policy": {"right_refresh": "mock", "recovery": "mock"},
                "records": [{"id": record_id, "status": "MOCK_SOURCE_READY"} for record_id in group],
            },
        )
        results = [
            {
                "id": record_id,
                "status": "COMPLETED",
                "variants": {
                    ORIGINAL: _variant(tmp_path, record_id, ORIGINAL),
                    REFINED: _variant(tmp_path, record_id, REFINED),
                },
            }
            for record_id in group
        ]
        doc = {
            **base,
            "dataset_count": len(results),
            "completed_count": len(results),
            "source_stage": str(source_stage.resolve()),
            "source_stage_preflight_sha256": _sha(source_preflight),
            "code_sha256": {"/code/a.py": "c" * 64, str(source_preflight.resolve()): _sha(source_preflight)},
            "results": results,
            "aggregates": {},
        }
        summaries.append(_write_json(tmp_path / f"shard{index}" / "summary.json", doc))
    return manifest, summaries


def test_merges_valid_shards_with_provenance_and_aggregate(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path, split=True)
    output = tmp_path / "merged.json"

    report = merge.run(merge.argument_parser().parse_args(["--manifest", str(manifest), "--summary", str(summaries[0]), "--summary", str(summaries[1]), "--output", str(output)]))

    assert output.is_file()
    assert report["schema"] == merge.SCHEMA
    assert report["status"] == "COMPLETED"
    assert report["record_count"] == 3
    assert report["completed_count"] == 3
    assert report["processed_record_count"] == 3
    assert report["scored_record_count"] == 3
    assert report["technical_failure_count"] == 0
    assert report["retained_record_count"] == 0
    assert [row["id"] for row in report["results"]] == ["a", "b", "c"]
    assert report["shards"][0]["sha256"] == _sha(summaries[0])
    assert report["aggregates"][REFINED]["scored_count"] == 3
    assert len(report["source_stages"]) == 2
    assert report["source_stages"][0]["path"] != report["source_stages"][1]["path"]
    assert report["source_stages"][0]["metadata_source_preflight"] == report["source_stages"][1]["metadata_source_preflight"]
    assert "common_code_sha256" in report
    assert report["blind_test"] is False
    assert report["production_promoted"] is False


def test_rejects_running_summary(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path)
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    doc["status"] = "RUNNING"
    _write_json(summaries[0], doc)

    with pytest.raises(ValueError, match="not terminal-completed"):
        merge.merge_summaries(manifest, summaries)


def test_rejects_missing_duplicate_and_extra_coverage(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path)
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    doc["results"].pop()
    doc["dataset_count"] = len(doc["results"])
    doc["completed_count"] = len(doc["results"])
    stage = Path(doc["source_stage"]) / "preflight_report.json"
    stage_doc = json.loads(stage.read_text(encoding="utf-8"))
    stage_doc["records"].pop()
    _write_json(stage, stage_doc)
    doc["source_stage_preflight_sha256"] = _sha(stage)
    doc["code_sha256"][str(stage.resolve())] = _sha(stage)
    _write_json(summaries[0], doc)
    with pytest.raises(ValueError, match="coverage mismatch"):
        merge.merge_summaries(manifest, summaries)

    manifest, summaries = _fixture(tmp_path / "dup", split=True)
    doc = json.loads(summaries[1].read_text(encoding="utf-8"))
    doc["results"][0]["id"] = "a"
    stage = Path(doc["source_stage"]) / "preflight_report.json"
    stage_doc = json.loads(stage.read_text(encoding="utf-8"))
    stage_doc["records"][0]["id"] = "a"
    _write_json(stage, stage_doc)
    doc["source_stage_preflight_sha256"] = _sha(stage)
    doc["code_sha256"][str(stage.resolve())] = _sha(stage)
    _write_json(summaries[1], doc)
    with pytest.raises(ValueError, match="duplicate result id"):
        merge.merge_summaries(manifest, summaries)


def test_rejects_changed_context_or_source_stage_hash(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path, split=True)
    doc = json.loads(summaries[1].read_text(encoding="utf-8"))
    doc["combined_reference"] = "/different"
    _write_json(summaries[1], doc)
    with pytest.raises(ValueError, match="context mismatch"):
        merge.merge_summaries(manifest, summaries)

    manifest, summaries = _fixture(tmp_path / "hash")
    source_preflight = Path(json.loads(summaries[0].read_text())["source_stage"]) / "preflight_report.json"
    _write_json(source_preflight, {"changed": True})
    with pytest.raises(ValueError, match="source stage preflight hash mismatch"):
        merge.merge_summaries(manifest, summaries)


def test_rejects_changed_common_code_but_allows_distinct_stage_code_key(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path, split=True)
    report = merge.merge_summaries(manifest, summaries)
    assert report["status"] == "COMPLETED"

    doc = json.loads(summaries[1].read_text(encoding="utf-8"))
    doc["code_sha256"]["/code/a.py"] = "d" * 64
    _write_json(summaries[1], doc)
    with pytest.raises(ValueError, match="context mismatch"):
        merge.merge_summaries(manifest, summaries)


def test_rejects_changed_estimate_or_bad_control_replay(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path)
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    artifact = Path(doc["results"][0]["variants"][REFINED]["artifact_dir"]) / "body_trajectory_fused.csv"
    artifact.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="estimate artifact sha changed"):
        merge.merge_summaries(manifest, summaries)

    manifest, summaries = _fixture(tmp_path / "replay")
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    doc["results"][0]["variants"][ORIGINAL]["control_replay_agreement"]["max_position_delta_m"] = 1.0
    _write_json(summaries[0], doc)
    with pytest.raises(ValueError, match="control replay position mismatch"):
        merge.merge_summaries(manifest, summaries)


def test_rejects_forged_threshold_nan_counts_and_nonterminal_record(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path)
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    doc["results"][0]["variants"][ORIGINAL]["control_replay_agreement"]["threshold_m"] = float("nan")
    summaries[0].write_text(json.dumps(doc, allow_nan=True), encoding="utf-8")
    with pytest.raises(ValueError, match="threshold mismatch"):
        merge.merge_summaries(manifest, summaries)

    manifest, summaries = _fixture(tmp_path / "counts")
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    doc["completed_count"] = 999
    _write_json(summaries[0], doc)
    with pytest.raises(ValueError, match="completed_count mismatch"):
        merge.merge_summaries(manifest, summaries)

    manifest, summaries = _fixture(tmp_path / "record")
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    doc["results"][0]["status"] = "IN_PROGRESS"
    _write_json(summaries[0], doc)
    with pytest.raises(ValueError, match="unsupported per-record status"):
        merge.merge_summaries(manifest, summaries)


def test_accepts_actual_terminal_non_scored_statuses_and_counts(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path, ids=["done", "take05", "tech"], split=False)
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    doc["results"][1] = {"id": "take05", "status": "PREPARATION_OR_INPUT_FAILED", "source_baseline_status": "PREPARATION_OR_INPUT_FAILED", "variants": {}}
    doc["results"][2] = {"id": "tech", "status": "INCOMPLETE_VARIANTS", "variants": {REFINED: {"artifact_dir": "/failed"}}}
    doc["completed_count"] = len(doc["results"])
    _write_json(summaries[0], doc)

    report = merge.merge_summaries(manifest, summaries)

    assert report["status"] == "COMPLETED_WITH_FAILURES"
    assert report["completed_count"] == 3
    assert report["scored_record_count"] == 1
    assert report["retained_record_count"] == 1
    assert report["technical_failure_count"] == 1


def test_rejects_summary_result_ids_not_matching_source_stage(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path)
    doc = json.loads(summaries[0].read_text(encoding="utf-8"))
    stage = Path(doc["source_stage"]) / "preflight_report.json"
    stage_doc = json.loads(stage.read_text(encoding="utf-8"))
    stage_doc["records"].pop()
    _write_json(stage, stage_doc)
    doc["source_stage_preflight_sha256"] = _sha(stage)
    doc["code_sha256"][str(stage.resolve())] = _sha(stage)
    _write_json(summaries[0], doc)

    with pytest.raises(ValueError, match="result ids do not match source stage ids"):
        merge.merge_summaries(manifest, summaries)


def test_refuses_existing_output(tmp_path: Path) -> None:
    manifest, summaries = _fixture(tmp_path)
    output = _write_json(tmp_path / "merged.json", {"exists": True})

    with pytest.raises(FileExistsError):
        merge.run(merge.argument_parser().parse_args(["--manifest", str(manifest), "--summary", str(summaries[0]), "--output", str(output)]))

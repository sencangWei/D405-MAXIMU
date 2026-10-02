#!/usr/bin/env python3
"""Merge immutable independent-IR corpus paired-evaluator shards.

This only validates and aggregates completed shard summaries.  It does not run
source extraction, a backend, a scorer, GPU work, or any selection logic.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_independent_ir_corpus_probe as corpus  # noqa: E402
import evaluate_sift_lm_dual_combined_probe as paired  # noqa: E402


SCHEMA = "umi_independent_ir_corpus_merged_results_v1"
INPUT_SCHEMA = corpus.SCHEMA
ALLOWED_INPUT_STATUSES = {"COMPLETED", "COMPLETED_WITH_FAILURES"}
ORIGINAL_VARIANT = paired.ORIGINAL_VARIANT
COMPLETED_RECORD_STATUS = "COMPLETED"
RETAINED_RECORD_STATUSES = {"RETAINED_UNOBSERVABLE_UNSCORED", "PREPARATION_OR_INPUT_FAILED"}
TECHNICAL_RECORD_STATUSES = {"INDEPENDENT_IR_CORPUS_NATIVE_SOURCE_FAILED", "INCOMPLETE_VARIANTS"}
ALLOWED_RECORD_STATUSES = {COMPLETED_RECORD_STATUS, *RETAINED_RECORD_STATUSES, *TECHNICAL_RECORD_STATUSES}


def read_json(path: Path) -> Any:
    return paired.read_json(path)


def write_json(path: Path, value: Any) -> None:
    paired.write_json(path, value)


def file_hash(path: Path) -> str:
    return paired.file_hash(path)


def _load_manifest(path: Path) -> tuple[list[str], str]:
    manifest = read_json(path)
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("manifest records missing")
    ids = [row.get("id") for row in records]
    if any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("manifest record id missing")
    if len(ids) != len(set(ids)):
        raise ValueError("manifest duplicate ids")
    return ids, file_hash(path)


def _summary_path(path: Path) -> Path:
    return (path / "summary.json") if path.is_dir() else path


def _require_onboard(summary: dict[str, Any], path: Path) -> None:
    if summary.get("external_ground_truth_used") is not False:
        raise ValueError(f"summary used ground truth: {path}")
    if summary.get("slam_supervision") is not False:
        raise ValueError(f"summary used slam supervision: {path}")


SCORE_SUMMARY_KEYS = (
    "result",
    "failures",
    "samples",
    "ate_translation_mean_m",
    "ate_translation_p95_m",
    "ate_translation_max_m",
    "ate_translation_rmse_m",
    "ate_translation_within_10mm_ratio",
    "timestamp_overlap_ratio",
)


def _context(summary: dict[str, Any], stage_info: dict[str, Any]) -> dict[str, Any]:
    code_sha = summary.get("code_sha256")
    if not isinstance(code_sha, dict) or not code_sha:
        raise ValueError("summary code_sha256 missing")
    stage_preflight = str((Path(summary.get("source_stage", "")).resolve() / "preflight_report.json"))
    common_code = {key: value for key, value in code_sha.items() if str(Path(key).resolve()) != stage_preflight}
    if len(common_code) == len(code_sha):
        raise ValueError("summary code_sha256 does not include source-stage preflight binding")
    return {
        key: summary.get(key)
        for key in (
            "manifest_sha256",
            "baseline_summary_sha256",
            "baseline",
            "constant_gauge",
            "combined_reference",
            "variants",
            "control_replay_required_max_delta_m",
        )
    } | {
        "common_code_sha256": common_code,
        "metadata_source_preflight": stage_info["source_preflight"],
        "metadata_source_preflight_sha256": stage_info["source_preflight_sha256"],
        "source_policy": stage_info.get("source_policy"),
    }


def _verify_source_stage(summary: dict[str, Any]) -> dict[str, Any]:
    source_stage = Path(summary.get("source_stage", "")).resolve()
    preflight = source_stage / "preflight_report.json"
    if not preflight.is_file():
        raise ValueError(f"source stage preflight missing: {preflight}")
    if file_hash(preflight) != summary.get("source_stage_preflight_sha256"):
        raise ValueError(f"source stage preflight hash mismatch: {preflight}")
    code_sha = summary.get("code_sha256")
    if not isinstance(code_sha, dict) or code_sha.get(str(preflight)) != summary.get("source_stage_preflight_sha256"):
        raise ValueError(f"source stage preflight missing from code_sha256: {preflight}")
    loaded = corpus.load_source_stage(source_stage)
    report = loaded["raw"]
    metadata = report.get("source_preflight")
    metadata_sha = report.get("source_preflight_sha256")
    if not metadata or not metadata_sha:
        raise ValueError(f"source stage metadata source_preflight missing: {preflight}")
    metadata_path = Path(metadata).resolve()
    if not metadata_path.is_file() or file_hash(metadata_path) != metadata_sha:
        raise ValueError(f"source stage metadata source_preflight hash mismatch: {metadata_path}")
    return {
        "source_stage": str(source_stage),
        "source_stage_preflight": str(preflight),
        "source_stage_preflight_sha256": summary.get("source_stage_preflight_sha256"),
        "source_preflight": str(metadata_path),
        "source_preflight_sha256": metadata_sha,
        "source_policy": report.get("source_policy"),
        "source_record_ids": set(loaded["by_id"]),
    }


def _verify_precision_score(result: dict[str, Any], variant_name: str, variant: dict[str, Any], artifact_dir: Path) -> None:
    precision = artifact_dir / "score" / "precision.json"
    if not precision.is_file():
        raise ValueError(f"precision.json missing for {result.get('id')} {variant_name}: {precision}")
    precision_score = read_json(precision)
    embedded = variant.get("score")
    if not isinstance(embedded, dict):
        raise ValueError(f"variant score missing for {result.get('id')} {variant_name}")
    for key in SCORE_SUMMARY_KEYS:
        if precision_score.get(key) != embedded.get(key):
            raise ValueError(f"embedded score mismatch for {result.get('id')} {variant_name}: {key}")


def _verify_variant_artifact(result: dict[str, Any], variant_name: str, variant: dict[str, Any]) -> None:
    artifact_dir = Path(variant.get("artifact_dir", "")).resolve()
    estimate = artifact_dir / "body_trajectory_fused.csv"
    if not estimate.is_file():
        raise ValueError(f"estimate artifact missing for {result.get('id')} {variant_name}: {estimate}")
    if file_hash(estimate) != variant.get("estimate_sha256"):
        raise ValueError(f"estimate artifact sha changed for {result.get('id')} {variant_name}")
    _verify_precision_score(result, variant_name, variant, artifact_dir)


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not finite") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{label} is not finite/nonnegative")
    return number


def _verify_completed_result(result: dict[str, Any], variants: list[str]) -> None:
    if result.get("status") != "COMPLETED":
        return
    result_variants = result.get("variants")
    if not isinstance(result_variants, dict):
        raise ValueError(f"completed result variants missing: {result.get('id')}")
    for variant_name in variants:
        variant = result_variants.get(variant_name)
        if not isinstance(variant, dict):
            raise ValueError(f"completed result missing variant {variant_name}: {result.get('id')}")
        _verify_variant_artifact(result, variant_name, variant)
    control = result_variants.get(ORIGINAL_VARIANT, {})
    agreement = control.get("control_replay_agreement")
    if not isinstance(agreement, dict):
        raise ValueError(f"original control replay agreement missing: {result.get('id')}")
    if agreement.get("threshold_m") != paired.CONTROL_REPLAY_MAX_DELTA_M:
        raise ValueError(f"original control replay threshold mismatch: {result.get('id')}")
    if agreement.get("rotation_threshold_rad") != paired.ROTATION_REPLAY_TOLERANCE_RAD:
        raise ValueError(f"original control replay rotation threshold mismatch: {result.get('id')}")
    if _finite_nonnegative(agreement.get("max_position_delta_m"), "max_position_delta_m") > paired.CONTROL_REPLAY_MAX_DELTA_M:
        raise ValueError(f"original control replay position mismatch: {result.get('id')}")
    if _finite_nonnegative(agreement.get("max_rotation_error_rad"), "max_rotation_error_rad") > paired.ROTATION_REPLAY_TOLERANCE_RAD:
        raise ValueError(f"original control replay rotation mismatch: {result.get('id')}")
    sample_count = agreement.get("sample_count")
    if not isinstance(sample_count, int) or sample_count <= 0:
        raise ValueError(f"original control replay sample_count invalid: {result.get('id')}")


def _load_summary(path: Path, manifest_sha256: str) -> dict[str, Any]:
    summary_path = _summary_path(path).resolve()
    summary = read_json(summary_path)
    if summary.get("schema") != INPUT_SCHEMA:
        raise ValueError(f"summary schema mismatch: {summary_path}")
    if summary.get("status") not in ALLOWED_INPUT_STATUSES:
        raise ValueError(f"summary is not terminal-completed: {summary_path} {summary.get('status')}")
    _require_onboard(summary, summary_path)
    if summary.get("manifest_sha256") != manifest_sha256:
        raise ValueError(f"summary manifest_sha256 mismatch: {summary_path}")
    if summary.get("production_promoted") is not False or summary.get("blind_test") is not False:
        raise ValueError(f"summary promotion/blind flags mismatch: {summary_path}")
    stage_info = _verify_source_stage(summary)
    results = summary.get("results")
    if not isinstance(results, list):
        raise ValueError(f"summary results missing: {summary_path}")
    variants = summary.get("variants")
    if not isinstance(variants, list) or ORIGINAL_VARIANT not in variants or len(variants) < 2:
        raise ValueError(f"summary variants invalid: {summary_path}")
    for result in results:
        if result.get("status") not in ALLOWED_RECORD_STATUSES:
            raise ValueError(f"unsupported per-record status: {summary_path} {result.get('id')} {result.get('status')}")
        _verify_completed_result(result, variants)
    result_ids = {row.get("id") for row in results}
    if any(not isinstance(record_id, str) or not record_id for record_id in result_ids):
        raise ValueError(f"summary result id missing: {summary_path}")
    if result_ids != stage_info["source_record_ids"]:
        raise ValueError(f"summary result ids do not match source stage ids: {summary_path}")
    if summary.get("dataset_count") is not None and int(summary["dataset_count"]) != len(results):
        raise ValueError(f"summary dataset_count mismatch: {summary_path}")
    if summary.get("completed_count") is not None:
        if int(summary["completed_count"]) != len(results):
            raise ValueError(f"summary completed_count mismatch: {summary_path}")
    return {"path": summary_path, "sha256": file_hash(summary_path), "raw": summary, "stage_info": stage_info, "context": _context(summary, stage_info)}


def _aggregate(results: list[dict[str, Any]], variants: list[str]) -> dict[str, Any]:
    if len(variants) != 2 or variants[0] != ORIGINAL_VARIANT:
        raise ValueError(f"unsupported variants for paired aggregate reuse: {variants}")
    original_refined = paired.REFINED_VARIANT
    try:
        paired.REFINED_VARIANT = variants[1]
        return paired.aggregate(results)
    finally:
        paired.REFINED_VARIANT = original_refined


def merge_summaries(manifest: Path, summaries: list[Path]) -> dict[str, Any]:
    manifest_ids, manifest_sha256 = _load_manifest(manifest)
    if not summaries:
        raise ValueError("at least one --summary is required")
    loaded = [_load_summary(path, manifest_sha256) for path in summaries]
    first_context = loaded[0]["context"]
    for item in loaded[1:]:
        if item["context"] != first_context:
            raise ValueError(f"summary context mismatch: {item['path']}")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in loaded:
        for result in item["raw"]["results"]:
            record_id = result.get("id")
            if not isinstance(record_id, str) or not record_id:
                raise ValueError(f"summary result missing id: {item['path']}")
            if record_id in seen:
                raise ValueError(f"duplicate result id across shards: {record_id}")
            seen.add(record_id)
            results.append(result)
    missing = [record_id for record_id in manifest_ids if record_id not in seen]
    extra = sorted(seen - set(manifest_ids))
    if missing or extra:
        raise ValueError(f"summary coverage mismatch missing={missing} extra={extra}")
    by_id = {record_id: index for index, record_id in enumerate(manifest_ids)}
    results.sort(key=lambda row: by_id[row["id"]])
    variants = list(first_context["variants"])
    aggregates = _aggregate(results, variants)
    scored_records = [row for row in results if row.get("status") == COMPLETED_RECORD_STATUS]
    retained_records = [row for row in results if row.get("status") in RETAINED_RECORD_STATUSES]
    technical_records = [row for row in results if row.get("status") in TECHNICAL_RECORD_STATUSES]
    failures = [row for row in results if row.get("status") != COMPLETED_RECORD_STATUS]
    return {
        "schema": SCHEMA,
        "status": "COMPLETED_WITH_FAILURES" if failures else "COMPLETED",
        "development_only": True,
        "blind_test": False,
        "production_promoted": False,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "manifest": str(manifest.resolve()),
        "manifest_sha256": manifest_sha256,
        "record_count": len(results),
        "completed_count": len(results),
        "processed_record_count": len(results),
        "scored_record_count": len(scored_records),
        "retained_record_count": len(retained_records),
        "technical_failure_count": len(technical_records),
        "failure_count": len(failures),
        "variants": variants,
        "baseline_summary_sha256": first_context["baseline_summary_sha256"],
        "baseline": first_context["baseline"],
        "constant_gauge": first_context["constant_gauge"],
        "combined_reference": first_context["combined_reference"],
        "common_code_sha256": first_context["common_code_sha256"],
        "source_stages": [
            {
                "path": item["stage_info"]["source_stage"],
                "preflight": item["stage_info"]["source_stage_preflight"],
                "preflight_sha256": item["stage_info"]["source_stage_preflight_sha256"],
                "metadata_source_preflight": item["stage_info"]["source_preflight"],
                "metadata_source_preflight_sha256": item["stage_info"]["source_preflight_sha256"],
                "summary_path": str(item["path"]),
                "summary_sha256": item["sha256"],
            }
            for item in loaded
        ],
        "shards": [
            {
                "path": str(item["path"]),
                "sha256": item["sha256"],
                "record_count": len(item["raw"]["results"]),
                "source_stage": item["raw"]["source_stage"],
                "source_stage_preflight_sha256": item["raw"]["source_stage_preflight_sha256"],
                "code_sha256": item["raw"]["code_sha256"],
            }
            for item in loaded
        ],
        "results": results,
        "aggregates": aggregates,
        "unpromoted_development_result": True,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"output must be new: {args.output}")
    report = merge_summaries(args.manifest, args.summary)
    write_json(args.output, report)
    return report


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--summary", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    report = run(args)
    print(json.dumps({"status": report["status"], "record_count": report["record_count"], "failure_count": report["failure_count"]}, sort_keys=True))
    return 0 if report["status"] in {"COMPLETED", "COMPLETED_WITH_FAILURES"} else 3


if __name__ == "__main__":
    raise SystemExit(main())

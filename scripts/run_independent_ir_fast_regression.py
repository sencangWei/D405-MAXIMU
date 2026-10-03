#!/usr/bin/env python3
"""Run the fixed fast10 independent-IR paired regression subset.

This is a small orchestration wrapper.  It never extracts images and never
changes solver/source policy; it invokes the existing LSQR telemetry wrapper,
which in turn invokes the existing independent-IR corpus consumer with
``--dataset`` filters.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_independent_ir_corpus_probe as corpus  # noqa: E402
import merge_independent_ir_corpus_results as merger  # noqa: E402


CONFIG_SCHEMA = "umi_independent_ir_fast_regression_config_v1"
RUN_SCHEMA = "umi_independent_ir_fast_regression_run_v1"
SOURCE_SCHEMA = corpus.SOURCE_SCHEMA
SOURCE_READY_STATUS = corpus.READY_STATUS
SOURCE_TERMINAL_STATUSES = {"NATIVE_SOURCES_COMPLETE", "NATIVE_SOURCES_WITH_FAILURES"}
SUMMARY_TERMINAL_STATUSES = merger.ALLOWED_INPUT_STATUSES
DEFAULT_CONFIG = ROOT / "config/dual_ir_fast_regression_10_20261003.json"


def read_json(path: Path) -> Any:
    return merger.read_json(path)


def write_json(path: Path, value: Any) -> None:
    merger.write_json(path, value)


def file_hash(path: Path) -> str:
    return merger.file_hash(path)


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _require_new_output_root(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"output root must be new: {path}")


def _load_config(path: Path) -> dict[str, Any]:
    config = read_json(path)
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError("fast regression config schema mismatch")
    if config.get("cohort_selection_uses_reference_scores") is not True:
        raise ValueError("fast regression config must declare reference-score cohort selection")
    if config.get("external_ground_truth_used_by_solver") is not False:
        raise ValueError("fast regression solver must not use ground truth")
    if config.get("production_selector_uses_ground_truth") is not False:
        raise ValueError("fast regression must not be a production GT selector")
    failures = config.get("failure_records")
    passings = config.get("passing_records")
    if not isinstance(failures, list) or not isinstance(passings, list):
        raise ValueError("failure_records and passing_records are required")
    ids = [*failures, *passings]
    if any(not isinstance(record_id, str) or not record_id for record_id in ids):
        raise ValueError("fast regression record id missing")
    if len(ids) != len(set(ids)):
        raise ValueError("fast regression duplicate record id")
    stages = config.get("source_stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("source_stages missing")
    if len(stages) != len({str(_resolve(path)) for path in stages}):
        raise ValueError("duplicate source stage path")
    return config


def _manifest_ids(path: Path) -> tuple[set[str], str]:
    manifest = read_json(path)
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("manifest records missing")
    ids = [row.get("id") for row in records]
    if any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("manifest record id missing")
    if len(ids) != len(set(ids)):
        raise ValueError("manifest duplicate ids")
    return set(ids), file_hash(path)


def _validate_combined_reference(config: dict[str, Any], selected_ids: Sequence[str]) -> None:
    summary_path = _resolve(config["combined_reference_summary"])
    if file_hash(summary_path) != config.get("combined_reference_summary_sha256"):
        raise ValueError("combined reference summary hash mismatch")
    summary = read_json(summary_path)
    if summary.get("schema") != "umi_physical_stereo_lever_development_regression_v1":
        raise ValueError("combined reference summary schema mismatch")
    if summary.get("status") not in {"COMPLETED", "COMPLETED_WITH_FAILURES"}:
        raise ValueError("combined reference summary is not terminal")
    variant = config.get("combined_reference_variant")
    by_id = {row.get("id"): row for row in summary.get("results", [])}
    missing = [record_id for record_id in selected_ids if record_id not in by_id]
    if missing:
        raise ValueError(f"combined reference missing selected ids: {missing}")
    for record_id in config["failure_records"]:
        score = by_id[record_id].get("variants", {}).get(variant, {}).get("score")
        if not isinstance(score, dict) or score.get("result") != "FAIL":
            raise ValueError(f"failure cohort record is not a combined-reference failure: {record_id}")
    for record_id in config["passing_records"]:
        score = by_id[record_id].get("variants", {}).get(variant, {}).get("score")
        if not isinstance(score, dict) or score.get("result") != "PASS":
            raise ValueError(f"passing cohort record is not a combined-reference PASS: {record_id}")


def _load_source_stage(path: Path) -> dict[str, Any]:
    preflight = path / "preflight_report.json"
    if not preflight.is_file():
        raise FileNotFoundError(f"source stage preflight missing: {preflight}")
    report = read_json(preflight)
    if report.get("schema") != SOURCE_SCHEMA:
        raise ValueError(f"source stage schema mismatch: {preflight}")
    if report.get("status") not in SOURCE_TERMINAL_STATUSES:
        raise ValueError(f"source stage is not terminal: {preflight} {report.get('status')}")
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError(f"source stage is not source-only: {preflight}")
    records = report.get("records")
    if not isinstance(records, list):
        raise ValueError(f"source stage records missing: {preflight}")
    by_id = {}
    for row in records:
        record_id = row.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"source stage record id missing: {preflight}")
        if record_id in by_id:
            raise ValueError(f"source stage duplicate record id: {record_id}")
        by_id[record_id] = row
    return {
        "path": path,
        "preflight": preflight,
        "sha256": file_hash(preflight),
        "raw": report,
        "by_id": by_id,
    }


def _plan_groups(config: dict[str, Any], stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    used: set[str] = set()
    selected = set(config["failure_records"]) | set(config["passing_records"])
    owners: dict[str, list[Path]] = {record_id: [] for record_id in selected}
    for stage in stages:
        for record_id, row in stage["by_id"].items():
            if record_id not in owners:
                continue
            if row.get("status") != SOURCE_READY_STATUS:
                raise ValueError(f"selected record is not source-ready: {record_id} {row.get('status')}")
            owners[record_id].append(stage["path"])
    duplicate_owners = {record_id: paths for record_id, paths in owners.items() if len(paths) > 1}
    if duplicate_owners:
        raise ValueError(f"selected record has duplicate source-stage ownership: {duplicate_owners}")
    for phase_name, ids in (("failure", config["failure_records"]), ("passing", config["passing_records"])):
        phase_used: set[str] = set()
        for stage in stages:
            group_ids = []
            for record_id in ids:
                row = stage["by_id"].get(record_id)
                if row is None:
                    continue
                if row.get("status") != SOURCE_READY_STATUS:
                    raise ValueError(f"selected record is not source-ready: {record_id} {row.get('status')}")
                group_ids.append(record_id)
            if group_ids:
                phase_used.update(group_ids)
                used.update(group_ids)
                groups.append({
                    "phase": phase_name,
                    "source_stage": stage["path"],
                    "source_stage_preflight": stage["preflight"],
                    "source_stage_preflight_sha256": stage["sha256"],
                    "record_ids": group_ids,
                })
        missing = [record_id for record_id in ids if record_id not in phase_used]
        if missing:
            raise ValueError(f"selected records missing usable terminal source: {missing}")
    expected = set(config["failure_records"]) | set(config["passing_records"])
    if used != expected:
        raise ValueError(f"fast regression source coverage mismatch: missing={sorted(expected-used)} extra={sorted(used-expected)}")
    return groups


def _group_name(index: int, group: dict[str, Any]) -> str:
    return f"{index:02d}_{group['phase']}_{Path(group['source_stage']).name}"


def _command_for_group(config: dict[str, Any], group: dict[str, Any], group_dir: Path) -> list[str]:
    output_dir = group_dir / "paired_output"
    telemetry = group_dir / "lsqr_telemetry.json"
    command = [
        sys.executable,
        str(_resolve(config["telemetry_wrapper"])),
        "--telemetry-output",
        str(telemetry),
        "--manifest",
        str(_resolve(config["manifest"])),
        "--baseline",
        str(_resolve(config["baseline"])),
        "--constant-gauge",
        str(_resolve(config["constant_gauge"])),
        "--combined-reference",
        str(_resolve(config["combined_reference"])),
        "--source-stage",
        str(group["source_stage"]),
        "--output",
        str(output_dir),
    ]
    for record_id in group["record_ids"]:
        command.extend(["--dataset", record_id])
    return command


def _summary_path(path: Path) -> Path:
    return path / "summary.json"


def assess_incremental_progress(rows: list[dict[str, Any]], failure_ids: list[str], passing_ids: list[str]) -> dict[str, Any]:
    """Evaluation-only retention advice; never a solver/production selector.

    Incomplete comparisons do not prove a direction is useless. A candidate can
    improve a majority of historical failures without meeting final10mm yet.
    """
    expected = [*failure_ids, *passing_ids]
    by_id = {row.get("id"): row for row in rows}
    incomplete = []
    if len(by_id) != len(rows) or set(by_id) != set(expected):
        incomplete.append("cohort_missing_extra_or_duplicate")
    improved, worsened, passing_regressions, unresolved = [], [], [], []
    for record_id in expected:
        row = by_id.get(record_id, {})
        before, after = row.get("baseline_score", {}), row.get("candidate_score", {})
        values = [score.get("ate_translation_max_m") for score in (before, after)]
        if (any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in values)
                or any(score.get("result") not in {"PASS", "FAIL"} for score in (before, after))):
            incomplete.append(record_id)
            continue
        samples = [score.get("samples") for score in (before, after)]
        overlaps = [score.get("timestamp_overlap_ratio") for score in (before, after)]
        if (any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in samples)
                or samples[0] != samples[1]
                or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 1 for v in overlaps)
                or overlaps[1] < overlaps[0]):
            incomplete.append(record_id + ":coverage_changed_or_missing")
            continue
        if after["result"] != "PASS" or values[1] > .010:
            unresolved.append(record_id)
        if record_id in passing_ids:
            if before["result"] != "PASS" or values[0] > .010:
                incomplete.append(record_id + ":passing_baseline_not_pass")
            if after["result"] != "PASS" or values[1] > .010:
                passing_regressions.append(record_id)
        elif values[1] < values[0]:
            improved.append(record_id)
        elif values[1] > values[0]:
            worsened.append(record_id)
    if passing_regressions:
        status = "CONTROL_REGRESSION_DO_NOT_ADOPT"
    elif incomplete:
        status = "INCOMPLETE_COMPARISON"
    elif len(improved) > len(failure_ids) / 2:
        status = "RETAIN_INCREMENTAL_PROGRESS"
    elif improved:
        status = "MIXED_PROGRESS_KEEP_EXPERIMENT"
    else:
        status = "NO_MEASURED_IMPROVEMENT"
    return {"status": status, "development_evaluation_only": True,
            "external_ground_truth_used_for_evaluation": True, "production_promoted": False,
            "direction_proven_useless": False, "improved_failures": improved,
            "worsened_failures": worsened, "passing_regressions": passing_regressions,
            "unresolved_records": unresolved, "incomplete": incomplete,
            "fixed10_all_pass": not incomplete and not unresolved and len(rows) == len(expected),
            "full25_acceptance": False, "max_ate_requirement_m": .010}


def _validate_subset_summary(
    summary_path: Path,
    *,
    manifest_sha256: str,
    expected_ids: list[str],
    expected_source_stage: Path,
    expected_context: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = read_json(summary_path)
    if summary.get("schema") != corpus.SCHEMA:
        raise ValueError(f"consumer summary schema mismatch: {summary_path}")
    if summary.get("status") not in SUMMARY_TERMINAL_STATUSES:
        raise ValueError(f"consumer summary is not terminal: {summary_path} {summary.get('status')}")
    merger._require_onboard(summary, summary_path)
    if summary.get("production_promoted") is not False or summary.get("blind_test") is not False:
        raise ValueError(f"consumer summary promotion/blind flags mismatch: {summary_path}")
    if summary.get("manifest_sha256") != manifest_sha256:
        raise ValueError(f"consumer summary manifest hash mismatch: {summary_path}")
    if _resolve(summary.get("source_stage", "")) != expected_source_stage.resolve():
        raise ValueError(f"consumer summary source stage mismatch: {summary_path}")
    stage_info = merger._verify_source_stage(summary)
    if not set(expected_ids).issubset(stage_info["source_record_ids"]):
        raise ValueError(f"consumer summary ids not owned by source stage: {summary_path}")
    context = merger._context(summary, stage_info)
    if expected_context is not None and context != expected_context:
        raise ValueError(f"consumer summary context mismatch: {summary_path}")
    variants = summary.get("variants")
    if not isinstance(variants, list) or merger.ORIGINAL_VARIANT not in variants or len(variants) < 2:
        raise ValueError(f"consumer summary variants invalid: {summary_path}")
    results = summary.get("results")
    if not isinstance(results, list):
        raise ValueError(f"consumer summary results missing: {summary_path}")
    result_ids = [row.get("id") for row in results]
    if result_ids != expected_ids:
        raise ValueError(f"consumer summary result ids mismatch: {summary_path} {result_ids} != {expected_ids}")
    if int(summary.get("dataset_count", -1)) != len(expected_ids):
        raise ValueError(f"consumer summary dataset_count mismatch: {summary_path}")
    if int(summary.get("completed_count", -1)) != len(expected_ids):
        raise ValueError(f"consumer summary completed_count mismatch: {summary_path}")
    for result in results:
        if result.get("status") not in merger.ALLOWED_RECORD_STATUSES:
            raise ValueError(f"unsupported consumer record status: {result.get('id')} {result.get('status')}")
        merger._verify_completed_result(result, variants)
    return {"raw": summary, "context": context}


def _validate_telemetry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"telemetry output missing: {path}")
    report = read_json(path)
    if report.get("schema") != "umi_independent_ir_solver_lsqr_telemetry_v1":
        raise ValueError(f"telemetry schema mismatch: {path}")
    if report.get("external_ground_truth_used") is not False or report.get("slam_supervision") is not False:
        raise ValueError(f"telemetry flags mismatch: {path}")
    return report


def run(config_path: Path, output_root: Path, command_runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    _require_new_output_root(output_root)
    config = _load_config(config_path)
    selected_ids = [*config["failure_records"], *config["passing_records"]]
    manifest_ids, manifest_sha = _manifest_ids(_resolve(config["manifest"]))
    missing_manifest = [record_id for record_id in selected_ids if record_id not in manifest_ids]
    if missing_manifest:
        raise ValueError(f"selected records missing from manifest: {missing_manifest}")
    _validate_combined_reference(config, selected_ids)
    stages = [_load_source_stage(_resolve(path)) for path in config["source_stages"]]
    groups = _plan_groups(config, stages)
    output_root.mkdir(parents=True)
    summary = {
        "schema": RUN_SCHEMA,
        "status": "RUNNING",
        "development_only": True,
        "blind_test": False,
        "production_promoted": False,
        "cohort_selection_uses_reference_scores": True,
        "external_ground_truth_used": False,
        "external_ground_truth_used_by_solver": False,
        "production_selector_uses_ground_truth": False,
        "slam_supervision": False,
        "config": str(config_path.resolve()),
        "config_sha256": file_hash(config_path),
        "runner": str(Path(__file__).resolve()),
        "runner_sha256": file_hash(Path(__file__).resolve()),
        "manifest": str(_resolve(config["manifest"])),
        "manifest_sha256": manifest_sha,
        "combined_reference_summary": str(_resolve(config["combined_reference_summary"])),
        "combined_reference_summary_sha256": config["combined_reference_summary_sha256"],
        "record_count": len(selected_ids),
        "failure_records": list(config["failure_records"]),
        "passing_records": list(config["passing_records"]),
        "retained_full25_quality_notes": config.get("retained_full25_quality_notes", []),
        "groups": [],
    }
    write_json(output_root / "fast_regression_summary.json", summary)
    allowed = set(int(code) for code in config.get("allowed_consumer_exit_codes", [0, 3]))
    expected_context: dict[str, Any] | None = None
    comparison_rows: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        group_name = _group_name(index, group)
        group_dir = output_root / group_name
        if group_dir.exists() or group_dir.is_symlink():
            raise FileExistsError(f"group output already exists: {group_dir}")
        group_dir.mkdir()
        command = _command_for_group(config, group, group_dir)
        group_record = {
            **{key: (str(value) if isinstance(value, Path) else value) for key, value in group.items() if key != "record_ids"},
            "record_ids": list(group["record_ids"]),
            "group_dir": str(group_dir.resolve()),
            "paired_output": str((group_dir / "paired_output").resolve()),
            "telemetry_output": str((group_dir / "lsqr_telemetry.json").resolve()),
            "command": command,
            "status": "RUNNING",
        }
        summary["groups"].append(group_record)
        write_json(output_root / "fast_regression_summary.json", summary)
        started = time.monotonic()
        completed = command_runner(command, cwd=str(ROOT))
        returncode = int(getattr(completed, "returncode", completed))
        group_record["returncode"] = returncode
        group_record["elapsed_s"] = time.monotonic() - started
        if returncode not in allowed:
            group_record["status"] = "COMMAND_FAILED"
            summary["status"] = "STOP_COMMAND_FAILED"
            write_json(output_root / "fast_regression_summary.json", summary)
            return summary
        consumer_summary = _validate_subset_summary(
            _summary_path(group_dir / "paired_output"),
            manifest_sha256=manifest_sha,
            expected_ids=list(group["record_ids"]),
            expected_source_stage=Path(group["source_stage"]),
            expected_context=expected_context,
        )
        if expected_context is None:
            expected_context = consumer_summary["context"]
        for row in consumer_summary["raw"]["results"]:
            variants = row.get("variants", {})
            comparison_rows.append({"id": row["id"],
                "baseline_score": variants.get(corpus.paired.ORIGINAL_VARIANT, {}).get("score", {}),
                "candidate_score": variants.get(corpus.REFINED_VARIANT, {}).get("score", {})})
        telemetry = _validate_telemetry(group_dir / "lsqr_telemetry.json")
        group_record["status"] = "COMPLETED"
        group_record["consumer_status"] = consumer_summary["raw"]["status"]
        group_record["telemetry_status"] = telemetry["status"]
        group_record["telemetry_call_count"] = telemetry["call_count"]
        group_record["summary_sha256"] = file_hash(group_dir / "paired_output" / "summary.json")
        group_record["telemetry_sha256"] = file_hash(group_dir / "lsqr_telemetry.json")
        write_json(output_root / "fast_regression_summary.json", summary)
    summary["status"] = "COMPLETED"
    summary["completed_group_count"] = len(groups)
    summary["incremental_progress"] = assess_incremental_progress(
        comparison_rows, config["failure_records"], config["passing_records"])
    write_json(output_root / "fast_regression_summary.json", summary)
    return summary


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    report = run(args.config, args.output_root)
    print(json.dumps({
        "status": report["status"],
        "record_count": report["record_count"],
        "group_count": len(report["groups"]),
    }, sort_keys=True))
    return 0 if report["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    raise SystemExit(main())

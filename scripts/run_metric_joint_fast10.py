#!/usr/bin/env python3
"""Serial dry-run/runner for the fixed fast10 metric-joint frontend queue."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_metric_joint_frontend import FRONTEND_READY_STATUS, read_json, validate_frontend  # noqa: E402
from experimental_mast3r_metric_joint_adapter import CODE_PATHS, TOOL, context_for_source  # noqa: E402
from run_experimental_metric_joint_frontend import complete_source, freeze_config  # noqa: E402
FAST10 = ROOT / "config/dual_ir_fast_regression_10_20261003.json"
SOURCE_CONFIG = ROOT / "config/mast3r_slam_d405_offline.yaml"
CHECKPOINT = TOOL / "checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
BATCH_V1 = ROOT / ".planning/dual_ir_regression_25_20261002/batch_v1"
OK_FRONTEND = {FRONTEND_READY_STATUS, "FRONTEND_FAILED"}
def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
def load_queue(config: Path, subset: set[str] | None) -> tuple[Path, list[str]]:
    fast = read_json(config)
    if fast.get("external_ground_truth_used_by_solver") is not False:
        raise ValueError("fast10 config must keep solver GT disabled")
    order = list(fast["failure_records"]) + list(fast["passing_records"])
    if fast.get("run_policy", {}).get("phase_order") != ["failure", "passing"]:
        raise ValueError("unexpected fast10 phase order")
    if subset:
        missing = sorted(subset - set(order))
        if missing:
            raise ValueError(f"subset ids are not in fast10: {missing}")
        order = [rid for rid in order if rid in subset]
    return Path(fast["manifest"]).resolve(), order
def find_record(manifest: dict[str, Any], rid: str) -> dict[str, Any]:
    matches = [r for r in manifest["records"] if r.get("id") == rid]
    if len(matches) != 1:
        raise ValueError(f"record id not unique: {rid}")
    return dict(matches[0])
def datasets(record: dict[str, Any]) -> tuple[Path, Path]:
    left = Path(record["left_dir"]).resolve() / "dataset"
    right_base = Path(record["right_dir"]).resolve() if record.get("right_dir") else None
    right = right_base / "dataset" if right_base and (right_base / "dataset").is_dir() else BATCH_V1 / record["id"] / "right_cache/dataset"
    return left, right.resolve()
def frontend_cmd(dataset: Path, paired: Path, eye: str, output: Path, source_config: Path, checkpoint: Path) -> list[str]:
    return [
        sys.executable, str(ROOT / "scripts/run_experimental_metric_joint_frontend.py"),
        "--dataset", str(dataset), "--paired-left-dataset", str(paired), "--eye", eye,
        "--config", str(source_config), "--checkpoint", str(checkpoint), "--output", str(output),
    ]
def eval_cmd(manifest: Path, rid: str, left: Path, right: Path, output: Path) -> list[str]:
    return [
        sys.executable, str(ROOT / "scripts/evaluate_metric_joint_frontend.py"),
        "--manifest", str(manifest), "--record-id", rid,
        "--left-frontend-dir", str(left), "--right-frontend-dir", str(right), "--output", str(output),
    ]
def validate_reuse(path: Path, eye: str, record: dict[str, Any], source_config: Path, checkpoint: Path) -> str:
    manifest_path = path / "run_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"reuse missing run_manifest: {path}")
    manifest = read_json(manifest_path)
    status = manifest.get("status")
    if status == "RUNNING":
        raise RuntimeError(f"refusing active RUNNING producer: {path}")
    if status not in OK_FRONTEND:
        raise ValueError(f"reuse is not terminal frontend artifact: {path} status={status}")
    if manifest.get("source_config_sha256") != sha(source_config):
        raise ValueError(f"reuse source config hash mismatch: {path}")
    if manifest.get("checkpoint_sha256") != sha(checkpoint):
        raise ValueError(f"reuse checkpoint hash mismatch: {path}")
    if manifest.get("runner_sha256") != sha(ROOT / "scripts/run_experimental_metric_joint_frontend.py"):
        raise ValueError(f"reuse runner hash mismatch: {path}")
    left_ds, right_ds = datasets(record)
    expected_dataset = left_ds if eye == "left" else right_ds
    expected_paired = left_ds
    source, count = complete_source(expected_dataset, expected_paired, eye)
    if count != 1199 or int(manifest.get("input_frame_count", -1)) != count:
        raise ValueError(f"reuse frame count mismatch: {path}")
    if manifest.get("eye") != eye or Path(manifest["source_session"]).resolve() != Path(record["session"]).resolve():
        raise ValueError(f"reuse manifest source binding mismatch: {path}")
    context = read_json(path / "context.json")
    expected_context = context_for_source(expected_dataset, expected_paired, eye)
    if context != expected_context or manifest.get("code_sha256") != context.get("code_sha256"):
        raise ValueError(f"reuse context/code binding mismatch: {path}")
    if source.get("source_session") != str(Path(record["session"]).resolve()):
        raise ValueError(f"reuse cached source session mismatch: {path}")
    if status == FRONTEND_READY_STATUS:
        validate_frontend(path, eye, expected_session=Path(record["session"]).resolve())
    return str(status)
def run_step(command: list[str], log: Path, execute: bool) -> int | None:
    if not execute:
        return None
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("$ " + shlex.join(command) + "\n")
    with log.open("a", encoding="utf-8") as stream:
        return subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT).returncode
def terminal_status(path: Path, eye: str, record: dict[str, Any]) -> str:
    try:
        validate_frontend(path, eye, expected_session=Path(record["session"]).resolve())
        return FRONTEND_READY_STATUS
    except Exception:
        manifest = path / "run_manifest.json"
        if manifest.is_file() and read_json(manifest).get("status") == "FRONTEND_FAILED":
            return "FRONTEND_FAILED"
        raise
def guard_changed(before: dict[str, str]) -> bool:
    return any(sha(Path(path)) != digest for path, digest in before.items())
def stage_error(stage: str, error: BaseException) -> dict[str, str]:
    return {"stage": stage, "status": "ERROR", "error_type": type(error).__name__, "error": str(error)}
def guarded_paths(config: Path, manifest_path: Path, source_config: Path, checkpoint: Path, records: list[dict[str, Any]]) -> list[Path]:
    paths = [config, manifest_path, source_config, checkpoint, ROOT / "scripts/run_experimental_metric_joint_frontend.py", ROOT / "scripts/evaluate_metric_joint_frontend.py", *CODE_PATHS.values()]
    for record in records:
        for dataset in datasets(record):
            paths.extend([dataset / "dataset_manifest.json", dataset / "frames.csv", dataset / "calibration.yaml"])
    return list(dict.fromkeys(path.resolve(strict=True) for path in paths))
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="store_true", help="execute; default only writes/prints a dry-run plan")
    p.add_argument("--config", type=Path, default=FAST10)
    p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--record-id", action="append", dest="records")
    p.add_argument("--reuse-root", type=Path)
    p.add_argument("--source-config", type=Path, default=SOURCE_CONFIG)
    p.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    args = p.parse_args(argv)
    output_root = args.output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(f"output root must be new: {output_root}")
    args.source_config.resolve(strict=True); args.checkpoint.resolve(strict=True)
    freeze_config(args.source_config.resolve())
    manifest_path, order = load_queue(args.config.resolve(strict=True), set(args.records or []) or None)
    manifest = read_json(manifest_path)
    selected_records = [find_record(manifest, rid) for rid in order]
    guarded = guarded_paths(args.config.resolve(), manifest_path, args.source_config.resolve(), args.checkpoint.resolve(), selected_records)
    before = {str(p): sha(p) for p in guarded}
    summary: dict[str, Any] = {"schema": "umi_metric_joint_fast10_queue_v1", "status": "DRY_RUN" if not args.run else "RUNNING",
        "development_only": True, "production_promoted": False, "not_full25": True, "external_ground_truth_used_by_solver": False,
        "source_manifest": str(manifest_path), "records": [], "guarded_source_sha256": before}
    reuse_status: dict[tuple[str, str], str] = {}
    reuse_path: dict[tuple[str, str], Path] = {}
    for rid in order:
        record = find_record(manifest, rid); left_ds, right_ds = datasets(record)
        complete_source(left_ds, left_ds, "left"); complete_source(right_ds, left_ds, "right")
        if args.reuse_root:
            for eye in ("left", "right"):
                reused = args.reuse_root / rid / eye
                if reused.exists():
                    reuse_status[(rid, eye)] = validate_reuse(reused, eye, record, args.source_config, args.checkpoint)
                    reuse_path[(rid, eye)] = reused.resolve()
    if args.run:
        output_root.mkdir(parents=True)
    for rid in order:
        record = find_record(manifest, rid); left_ds, right_ds = datasets(record)
        complete_source(left_ds, left_ds, "left"); complete_source(right_ds, left_ds, "right")
        rec_dir = output_root / rid
        left_out, right_out, eval_out = rec_dir / "left", rec_dir / "right", rec_dir / "eval"
        row: dict[str, Any] = {"id": rid, "status": "PLANNED", "left_dataset": str(left_ds), "right_dataset": str(right_ds), "stages": []}
        known_failed = next((eye for eye in ("left", "right") if reuse_status.get((rid, eye)) == "FRONTEND_FAILED"), None)
        if known_failed:
            for eye in ("left", "right"):
                if (rid, eye) in reuse_status:
                    run_manifest = reuse_path[(rid, eye)] / "run_manifest.json"
                    row["stages"].append({"stage": f"{eye}_frontend", "status": reuse_status[(rid, eye)],
                        "reused": str(reuse_path[(rid, eye)]), "run_manifest_sha256": sha(run_manifest)})
                else:
                    row["stages"].append({"stage": f"{eye}_frontend", "status": "SKIPPED_AFTER_KNOWN_REUSED_FAILURE"})
            row["status"] = "COVERAGE_FAILED"
            summary["records"].append(row)
            if args.run:
                write_json(output_root / "summary.json", summary)
            continue
        for eye, ds, paired, out in (("left", left_ds, left_ds, left_out), ("right", right_ds, left_ds, right_out)):
            if row.get("status") in {"COVERAGE_FAILED", "FRONTEND_INVALID"}:
                row["stages"].append({"stage": f"{eye}_frontend", "status": "SKIPPED_AFTER_PRIOR_FRONTEND_FAILURE"})
                continue
            if args.reuse_root and (args.reuse_root / rid / eye).exists():
                status = reuse_status[(rid, eye)]
                row["stages"].append({"stage": f"{eye}_frontend", "status": status, "reused": str(reuse_path[(rid, eye)])})
                out = reuse_path[(rid, eye)]
            else:
                cmd = frontend_cmd(ds, paired, eye, out, args.source_config, args.checkpoint)
                if args.run and guard_changed(before):
                    row["stages"].append({"stage": f"{eye}_frontend", "status": "QUEUE_ABORTED_SOURCE_CHANGED"})
                    row["status"] = summary["status"] = "QUEUE_ERROR_SOURCE_CHANGED"
                    break
                rc = run_step(cmd, rec_dir / "logs" / f"{eye}_frontend.log", args.run)
                try:
                    status = "PLANNED" if rc is None else terminal_status(out, eye, record)
                    row["stages"].append({"stage": f"{eye}_frontend", "returncode": rc, "status": status, "command": cmd})
                except Exception as error:
                    status = "FRONTEND_INVALID"
                    row["stages"].append(stage_error(f"{eye}_frontend", error) | {"returncode": rc, "command": cmd})
            if status == "FRONTEND_FAILED":
                row["status"] = "COVERAGE_FAILED"
            elif status != FRONTEND_READY_STATUS and args.run:
                row["status"] = "FRONTEND_INVALID"
        if row.get("status") not in {"COVERAGE_FAILED", "FRONTEND_INVALID", "QUEUE_ERROR_SOURCE_CHANGED"}:
            cmd = eval_cmd(manifest_path, rid, left_out if not args.reuse_root else Path(row["stages"][0].get("reused", left_out)), right_out if not args.reuse_root else Path(row["stages"][1].get("reused", right_out)), eval_out)
            if args.run and guard_changed(before):
                row["stages"].append({"stage": "evaluate", "status": "QUEUE_ABORTED_SOURCE_CHANGED"})
                row["status"] = summary["status"] = "QUEUE_ERROR_SOURCE_CHANGED"
            else:
                rc = run_step(cmd, rec_dir / "logs/eval.log", args.run)
                try:
                    estatus = "PLANNED" if rc is None else read_json(eval_out / "summary.json").get("status", "MISSING_SUMMARY")
                    row["stages"].append({"stage": "evaluate", "returncode": rc, "status": estatus, "command": cmd})
                    row["status"] = estatus
                except Exception as error:
                    row["stages"].append(stage_error("evaluate", error) | {"returncode": rc, "command": cmd})
                    row["status"] = "EVALUATION_FAILED"
        summary["records"].append(row)
        if args.run:
            write_json(output_root / "summary.json", summary)
        if summary["status"] == "QUEUE_ERROR_SOURCE_CHANGED":
            break
    after = {str(p): sha(Path(p)) for p in before}
    summary["guarded_source_unchanged"] = (after == before)
    if summary["status"] != "QUEUE_ERROR_SOURCE_CHANGED":
        summary["status"] = "DRY_RUN_COMPLETE" if not args.run else "QUEUE_COMPLETED"
    if args.run:
        write_json(output_root / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())

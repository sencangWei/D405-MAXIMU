#!/usr/bin/env python3
"""Frozen development corpus: prepare onboard caches, solve, then score separately.

No accuracy-based exclusions or per-recording policy selection. Failed items
remain in the denominator. Production artifacts are read-only.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

import numpy as np

import fuse_mast3r_dual_ir_symmetric as symmetric
from prepare_dual_ir_eye_cache import (
    CHECKPOINT, OFFLINE_CONFIG, IMU_CALIBRATION, STEREO_REPORTS, VINS_CONFIG, _run_command, prepare,
)

ROOT = Path(__file__).resolve().parents[1]
POLICIES = {
    "both": [],
    "left": ["--eyes", "left"],
    "right": ["--eyes", "right"],
    "stereo_only": ["--disable-learned-motion"],
    "residual_aware": ["--stereo-weight-policy", "residual-aware"],
    "consistent_10mm": ["--learned-motion-consistency-mm", "10"],
    "consistent_15mm": ["--learned-motion-consistency-mm", "15"],
    "consistent_25mm": ["--learned-motion-consistency-mm", "25"],
}
CAPS_MM = (10, 25, 40, 100)
OFFICIAL_CHECKPOINT_SHA256 = "e28f91b488554653e2b46ddae9c78c1143e0bcb2e27d3e26cdb0b717f1568eb2"
FROZEN_CONFIG_SHA256 = "ac2577063dce6f017307f2bbe45485cb272f84b152eec86d9cb31962f9d4d84a"


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def validate_corpus(manifest):
    records = manifest["records"]
    for field in ("id", "session", "capture_dir"):
        values = [record[field] for record in records]
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate corpus {field}")
    for record in records:
        if not record["id"] or Path(record["id"]).name != record["id"]:
            raise ValueError("record ID must be a single path component")
        capture = read_json(Path(record["capture_dir"]) / "capture_manifest.json")
        if capture["status"] != "PASS_CAPTURE_ONLY_NOT_CALIBRATED":
            raise ValueError("raw capture is not accepted")
        if Path(capture["d405_session"]).resolve() != Path(record["session"]).resolve():
            raise ValueError("capture/session mismatch")
        if (Path(record["capture_dir"]) / "EXCLUDED_FROM_DYNAMIC_VALIDATION.md").exists():
            raise ValueError("explicitly excluded no-motion recording")
    return records


def complete_eye(directory, eye):
    if directory is None:
        return False
    directory = Path(directory)
    names = [right if eye == "right" else left for left, right in STEREO_REPORTS]
    names += ["imu_scale_report.json", "imu_metric_trajectory.csv" if eye == "right" else "trajectory_imu_metric.csv"]
    return all((directory / name).is_file() for name in names)


@lru_cache(maxsize=8)
def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_reused_right_cache(record, directory):
    """Require frozen frontend provenance, not just a directory of CSVs."""
    directory = Path(directory)
    run = read_json(directory / "run_manifest.json")
    dataset = read_json(directory / "dataset/dataset_manifest.json")
    if run.get("schema") != "umi_mast3r_run_v1" or run.get("slam_supervision") is not False:
        raise ValueError("right cache is not an unsupervised MASt3R run")
    for key, path, expected in (
        ("config", OFFLINE_CONFIG, FROZEN_CONFIG_SHA256),
        ("checkpoint", CHECKPOINT, OFFICIAL_CHECKPOINT_SHA256),
    ):
        if Path(run.get(key, "")).resolve() != path.resolve():
            raise ValueError(f"right cache {key} path mismatch")
        if run.get(key + "_sha256") != expected or file_hash(path) != expected:
            raise ValueError(f"right cache {key} hash mismatch")
    if run.get("stereo_descriptor_recovery") or run.get("spatial_pointmap_recovery"):
        raise ValueError("right frontend recovery configuration mismatch")
    if dataset.get("stream") != "infrared_right" or dataset.get("slam_supervision") is not False:
        raise ValueError("right cache dataset stream/supervision mismatch")
    if Path(dataset.get("source_session", "")).resolve() != Path(record["session"]).resolve():
        raise ValueError("right cache dataset session mismatch")
    frames = Path(record["session"]) / "d405_frames.csv"
    if dataset.get("source_frames_csv_sha256") != file_hash(frames):
        raise ValueError("right cache source frame hash mismatch")
    preprocessing = dataset.get("image_preprocessing", {})
    if preprocessing.get("crop_bottom_px") != 0 or preprocessing.get("mask_fixed_self_occlusion") is not False:
        raise ValueError("right cache image preprocessing mismatch")
    for key, expected in record.get("right_input_sha256", {}).items():
        if key == "elapsed_s":
            continue
        actual = dataset.get(key) if key == "source_frames_csv_sha256" else run.get(key)
        if actual != expected:
            raise ValueError(f"right cache declared provenance mismatch: {key}")


def ensure_left(record, work):
    source = Path(record.get("alternate_left_dir") or record["left_dir"])
    if record.get("alternate_left_dir"):
        audit = record["alternate_left_audit"]
        if audit.get("external_ground_truth_used") is not False or audit.get("ground_truth_used_for_selection") is not False:
            raise ValueError("recovery cache is not onboard-only")
        expected = {str(source / name): digest for name, digest in audit["stereo_report_hashes"].items()}
        expected[audit["imu_scale_report"]] = audit["imu_scale_report_sha256"]
        expected.update({entry["path"]: entry["sha256"] for entry in audit["raw_trajectory_hashes"].values()})
        if any(file_hash(Path(path)) != digest for path, digest in expected.items()):
            raise ValueError("recovery cache provenance hash changed")
    imu_path = source / "imu_scale_report.json"
    if complete_eye(source, "left") and read_json(imu_path).get("orientation_trajectory"):
        return source
    # Fill an absent/legacy IMU scale report without altering its source cache.
    output = work / "left_cache"
    output.mkdir()
    for left_name, _ in STEREO_REPORTS:
        shutil.copy2(source / left_name, output / left_name)
    command = [sys.executable, str(ROOT / "scripts/align_mast3r_scale_with_imu.py"),
               "--session", record["session"], "--trajectory", str(source / "trajectory_frames.csv"),
               "--orientation-trajectory", str(Path(record["vins_dir"]) / "vio_corrected_stream.csv"),
               "--stereo-scale-report", str(output / STEREO_REPORTS[0][0]),
               "--stream", "infrared_left", "--body-t-camera-yaml", str(VINS_CONFIG),
               "--imu-calibration", str(IMU_CALIBRATION), "--td-s", "-0.009109323",
               "--node-stride", "10", "--max-hop", "1",
               "--output", str(output / "trajectory_imu_metric.csv"),
               "--report", str(output / "imu_scale_report.json")]
    _run_command(command, stage="left_imu_scale", output=work, timeout_s=600)
    return output


def graph_command(record, left, right, output, policy):
    return [sys.executable, str(ROOT / "scripts/fuse_mast3r_dual_ir_symmetric.py"),
            "--session", record["session"], "--left-dir", str(left),
            "--right-dir", str(right), "--vins-dir", record["vins_dir"],
            "--output-dir", str(output), "--max-correction-mm", "none", *POLICIES[policy]]


def score_frozen(record, estimate, output, work, stage):
    # External-reference paths are confined to this post-freeze scoring command.
    command = [sys.executable, str(ROOT / "scripts/score_steamvr_slam.py"),
               "--capture", record["capture_dir"], "--estimate", str(estimate),
               "--output", str(output), "--reference-manifest", record["reference_manifest"]]
    # rc=3 is an accuracy failure, not a command/infrastructure failure.
    _run_command(command, stage=stage, output=work, timeout_s=180, allowed_returncodes=(0, 3))
    manifest = read_json(output / "workflow_manifest.json")
    if manifest.get("result") != "SCORING_COMPLETED" or not manifest.get("estimate_unchanged"):
        raise ValueError("scorer did not complete with unchanged estimate")
    score = read_json(output / "precision.json")
    return {key: score[key] for key in (
        "result", "failures", "samples", "ate_translation_mean_m",
        "ate_translation_p95_m", "ate_translation_max_m", "ate_translation_rmse_m",
        "ate_translation_within_10mm_ratio", "timestamp_overlap_ratio",
    )}


def project_global_cap(reference, uncapped, maximum_m):
    correction = uncapped - reference
    requested = np.linalg.norm(correction, axis=1)
    scale = min(1.0, maximum_m / max(float(np.max(requested)), 1e-12))
    return reference + scale * correction, {
        "maximum_m": maximum_m, "mode": "global", "scale": scale,
        "requested_max_m": float(np.max(requested)),
        "changed_frames": int(np.count_nonzero(np.linalg.norm((1 - scale) * correction, axis=1) > 1e-12)),
    }


def cap_candidates(record, graph, work):
    fusion = symmetric.fusion
    times, original, rotations, rows = fusion.load_trajectory(Path(record["vins_dir"]) / "vio_corrected_stream.csv")
    times, original, rotations, rows, _, _ = symmetric.bind_body_reference(
        Path(record["session"]) / "d405_frames.csv", times, original, rotations, rows)
    solved_times, solved, _, _ = fusion.load_trajectory(graph / "body_trajectory_fused.csv")
    if len(times) != len(solved_times) or np.max(abs(times - solved_times)) > 1e-6:
        raise ValueError("cap study must use identical, bound camera timestamps")
    result = {}
    for cap_mm in CAPS_MM:
        capped, report = project_global_cap(original, solved, cap_mm / 1000)
        directory = graph / f"cap_{cap_mm}mm"
        directory.mkdir()
        estimate = directory / "body_trajectory_fused.csv"
        fusion.write_trajectory(estimate, rows, capped, rotations)
        report.update(schema="umi_symmetric_post_solve_cap_ablation_v1",
                      uncapped_estimate_sha256=hashlib.sha256((graph / "body_trajectory_fused.csv").read_bytes()).hexdigest(),
                      external_ground_truth_used=False, accepted=False,
                      note="Exact post-solve global cap; serialized input roundoff may be nanometers.")
        write_json(directory / "cap_report.json", report)
        result[f"cap_{cap_mm}mm"] = {
            "cap": report,
            "score": score_frozen(record, estimate, directory / "score", work, f"score_{graph.name}_cap_{cap_mm}"),
        }
    return result


def process_record(record, work, policies):
    work.mkdir()
    result = {"id": record["id"], "status": "IN_PROGRESS", "variants": {}}
    if record.get("alternate_left_dir"):
        result["recovery_audit"] = record["recovery_audit"]
        result["alternate_left_audit"] = record["alternate_left_audit"]
    write_json(work / "progress.json", result)
    try:
        left = ensure_left(record, work)
        if complete_eye(record.get("right_dir"), "right"):
            right = Path(record["right_dir"])
            validate_reused_right_cache(record, right)
        else:
            right = prepare(record["session"], left, work / "right_cache", frontend_timeout_s=1200, stage_timeout_s=300)
            validate_reused_right_cache({"session": record["session"]}, right)
        result["left_cache"], result["right_cache"] = str(left), str(right)
        for policy in policies:
            graph = work / policy
            try:
                _run_command(graph_command(record, left, right, graph, policy),
                             stage=f"solve_{policy}", output=work, timeout_s=600)
                report = read_json(graph / "graph_report.json")["joint_position_solver"]
                # Freeze cap variants from internal output before looking at GT.
                caps = cap_candidates(record, graph, work) if policy == "both" or policy.startswith("consistent_") else {}
                result["variants"][policy] = {
                    "score": score_frozen(record, graph / "body_trajectory_fused.csv", graph / "score", work, f"score_{policy}"),
                    "requested_correction_max_m": report["position_correction_requested_max_m"],
                    "stereo_rmse_after_m": report["stereo_edge_rmse_after_m"],
                    "caps": caps,
                }
            except Exception as error:
                result["variants"][policy] = {"error": f"{type(error).__name__}: {error}"}
            write_json(work / "progress.json", result)
        result["status"] = "COMPLETED" if all("score" in result["variants"][policy] for policy in policies) else "INCOMPLETE_VARIANTS"
    except Exception as error:
        result.update(status="PREPARATION_OR_INPUT_FAILED", error=f"{type(error).__name__}: {error}")
    write_json(work / "progress.json", result)
    return result


def aggregate_results(results, dataset_count, policies):
    """Summarize development results without excluding failures or choosing by GT."""
    aggregates = {}
    for policy in policies:
        for cap_name in ("none", *([f"cap_{cap}mm" for cap in CAPS_MM] if policy == "both" or policy.startswith("consistent_") else [])):
            scores = []
            for result in results:
                variant = result.get("variants", {}).get(policy, {})
                score = variant.get("score") if cap_name == "none" else variant.get("caps", {}).get(cap_name, {}).get("score")
                if score is not None:
                    scores.append(score)
            aggregates[f"{policy}/{cap_name}"] = {
                "dataset_count": dataset_count, "scored_count": len(scores),
                "unscored_count": dataset_count - len(scores),
                "max_within_10mm_count": sum(score["ate_translation_max_m"] <= 0.010 for score in scores),
                "precision_pass_count": sum(score["result"] == "PASS" for score in scores),
                "worst_max_m": max((score["ate_translation_max_m"] for score in scores), default=None),
            }
    return aggregates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)
    records = validate_corpus(read_json(args.manifest))
    if args.dataset:
        if set(args.dataset) - {record["id"] for record in records}:
            parser.error("unknown dataset ID")
        records = [record for record in records if record["id"] in args.dataset]
    records.sort(key=lambda record: (not complete_eye(record.get("right_dir"), "right"), record["id"]))
    if args.audit_only:
        print(json.dumps({"records": len(records), "cached_right": sum(complete_eye(record.get("right_dir"), "right") for record in records)}))
        return 0
    if args.output.exists() or args.output.is_symlink():
        parser.error("output must be new; previous results are never overwritten")
    args.output.resolve().relative_to(ROOT)
    args.output.mkdir(parents=True)
    code = [Path(__file__), ROOT / "scripts/fuse_mast3r_dual_ir_symmetric.py",
            ROOT / "scripts/fuse_mast3r_stereo_imu.py", ROOT / "ego_vio/vio/symmetric_ir_factors.py",
            ROOT / "scripts/prepare_dual_ir_eye_cache.py", ROOT / "ego_vio/vio/dual_ir_factors.py",
            ROOT / "scripts/derive_right_ir_stereo_scale.py", ROOT / "scripts/align_mast3r_scale_with_imu.py",
            ROOT / "scripts/mast3r_slam_precision_workflow.sh", ROOT / "scripts/score_steamvr_slam.py",
            VINS_CONFIG, IMU_CALIBRATION, OFFLINE_CONFIG]
    frozen_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in code}
    summary = {"schema": "umi_dual_ir_development_regression_v1", "status": "RUNNING",
               "blind_test": False, "production_promoted": False,
               "dataset_count": len(records), "completed_count": 0, "results": [],
               "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
               "policies": args.policies, "caps_mm": list(CAPS_MM), "code_sha256": frozen_hashes}
    write_json(args.output / "summary.json", summary)
    for index, record in enumerate(records, 1):
        if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in frozen_hashes.items()):
            summary["status"] = "STOPPED_CODE_CHANGED"
            write_json(args.output / "summary.json", summary)
            return 2
        print(f"[{index}/{len(records)}] {record['id']} started", flush=True)
        started = time.monotonic()
        result = process_record(record, args.output / record["id"], args.policies)
        result["elapsed_s"] = time.monotonic() - started
        summary["results"].append(result)
        summary["completed_count"] = index
        summary["aggregates"] = aggregate_results(summary["results"], len(records), args.policies)
        write_json(args.output / "summary.json", summary)
        print(f"[{index}/{len(records)}] {record['id']} {result['status']} ({result['elapsed_s']:.1f}s)", flush=True)
    summary["status"] = "COMPLETED_WITH_FAILURES" if any(record["status"] != "COMPLETED" for record in summary["results"]) else "COMPLETED"
    write_json(args.output / "summary.json", summary)
    return 0 if summary["status"] == "COMPLETED" else 3


if __name__ == "__main__":
    raise SystemExit(main())

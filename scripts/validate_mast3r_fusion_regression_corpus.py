#!/usr/bin/env python3
"""Validate the frozen multi-session MASt3R fusion development corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/mast3r_fusion_regression_corpus_13.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def validate_manifest(manifest_path: Path) -> dict:
    manifest = load_json(manifest_path)
    failures: list[str] = []
    if manifest.get("schema") != "umi_mast3r_fusion_regression_corpus_v1":
        failures.append("unexpected corpus schema")

    truth_policy = manifest.get("truth_policy", {})
    if truth_policy.get("never_input_to_slam") is not True:
        failures.append("truth must never be passed into SLAM")
    if truth_policy.get("final_acceptance_requires_new_unseen_captures") is not True:
        failures.append("final acceptance must require new unseen captures")

    default_calibration = manifest.get("tracker_body_calibration")

    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        failures.append("corpus has no datasets")
        datasets = []

    ids: set[str] = set()
    sessions: set[Path] = set()
    fold_count = int(manifest.get("cross_validation", {}).get("fold_count", 0))
    folds: Counter[int] = Counter()
    tags: Counter[str] = Counter()
    world_epochs: Counter[str] = Counter()
    runtime_world_configs: Counter[str] = Counter()
    dataset_results = []
    calibration_epochs: Counter[str] = Counter()

    for dataset in datasets:
        dataset_id = dataset.get("id", "")
        item_failures: list[str] = []
        if not dataset_id or dataset_id in ids:
            item_failures.append("dataset id is empty or duplicated")
        ids.add(dataset_id)

        session = Path(dataset.get("session", ""))
        if session in sessions:
            item_failures.append("session is duplicated")
        sessions.add(session)

        fold = dataset.get("fold")
        if not isinstance(fold, int) or not 0 <= fold < fold_count:
            item_failures.append(f"fold must be in [0, {fold_count})")
        else:
            folds[fold] += 1

        required_session_files = [
            session / "acceptance.json",
            session / "d405_frames.csv",
            session / "external_imu" / "imu.bin",
        ]
        db3_files = list(session.glob("*.db3")) if session.is_dir() else []
        if len([path for path in db3_files if path.stat().st_size > 0]) != 1:
            item_failures.append("session must contain exactly one non-empty DB3")
        for path in required_session_files:
            if not path.is_file() or path.stat().st_size == 0:
                item_failures.append(f"required input is missing or empty: {path}")

        acceptance_path = session / "acceptance.json"
        if acceptance_path.is_file():
            if sha256(acceptance_path) != dataset.get("capture_acceptance_sha256"):
                item_failures.append("capture acceptance hash changed")
            elif load_json(acceptance_path).get("result") != "PASS":
                item_failures.append("D405/IMU capture acceptance is not PASS")

        for field in ("tracker_csv", "tracker_integrity", "tracker_d405_overlap"):
            path = Path(dataset.get(field, ""))
            if not path.is_file() or path.stat().st_size == 0:
                item_failures.append(f"{field} is missing or empty: {path}")

        for field in ("tracker_integrity", "tracker_d405_overlap"):
            path = Path(dataset.get(field, ""))
            if path.is_file() and load_json(path).get("status") != "PASS":
                item_failures.append(f"{field} is not PASS")

        epoch = dataset.get("lighthouse_world_epoch_sha256")
        if not isinstance(epoch, str) or len(epoch) != 64:
            item_failures.append("Lighthouse world epoch hash is invalid")
        else:
            world_epochs[epoch] += 1

        runtime_world_config = Path(dataset.get("lighthouse_runtime_config", ""))
        runtime_world_hash = dataset.get("lighthouse_runtime_config_sha256")
        if not runtime_world_config.is_file():
            item_failures.append(
                f"Lighthouse runtime config is missing: {runtime_world_config}"
            )
        elif sha256(runtime_world_config) != runtime_world_hash:
            item_failures.append("Lighthouse runtime config hash changed")
        elif isinstance(runtime_world_hash, str):
            runtime_world_configs[runtime_world_hash] += 1

        calibration = dataset.get("tracker_body_calibration", default_calibration)
        if not isinstance(calibration, dict):
            item_failures.append("tracker/body calibration binding is missing")
        else:
            calibration_path = Path(calibration.get("path", ""))
            expected_hash = calibration.get("sha256")
            if not calibration_path.is_file():
                item_failures.append(
                    f"tracker/body calibration is missing: {calibration_path}"
                )
            elif sha256(calibration_path) != expected_hash:
                item_failures.append("tracker/body calibration hash changed")
            else:
                calibration_report = load_json(calibration_path)
                if calibration_report.get("result") != "PASS_CANDIDATE":
                    item_failures.append(
                        "tracker/body calibration is not PASS_CANDIDATE"
                    )
                if calibration_report.get("slam_supervision") is not False:
                    item_failures.append(
                        "tracker/body calibration does not prove SLAM independence"
                    )
                if isinstance(expected_hash, str):
                    calibration_epochs[expected_hash] += 1

        motion_tags = dataset.get("motion_tags")
        if not isinstance(motion_tags, list) or not motion_tags:
            item_failures.append("motion tags are missing")
        else:
            tags.update(str(tag) for tag in motion_tags)

        dataset_results.append(
            {
                "id": dataset_id,
                "result": "PASS" if not item_failures else "FAIL",
                "failures": item_failures,
            }
        )
        failures.extend(f"{dataset_id}: {failure}" for failure in item_failures)

    if set(folds) != set(range(fold_count)):
        failures.append("not every cross-validation fold has a dataset")
    for required_tag in ("translation", "elevation", "slow_turn", "fast_turn", "low_texture"):
        if tags[required_tag] == 0:
            failures.append(f"required motion/scene coverage is missing: {required_tag}")

    return {
        "schema": "umi_mast3r_fusion_regression_corpus_validation_v1",
        "result": "PASS" if not failures else "FAIL",
        "manifest": str(manifest_path.resolve()),
        "slam_supervision": False,
        "external_ground_truth_usage": "post_slam_scoring_only",
        "dataset_count": len(datasets),
        "fold_sizes": {str(key): folds[key] for key in sorted(folds)},
        "world_epoch_count": len(world_epochs),
        "lighthouse_runtime_config_count": len(runtime_world_configs),
        "tracker_body_calibration_epoch_count": len(calibration_epochs),
        "motion_tag_counts": dict(sorted(tags.items())),
        "datasets": dataset_results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_manifest(args.manifest.resolve())
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

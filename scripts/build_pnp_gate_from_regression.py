#!/usr/bin/env python3
"""Build immutable PnP gate evidence from completed regression runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MINIMUM_REQUIREMENTS = {
    "min_positive_runs": 2,
    "min_negative_runs": 2,
    "min_validation_positive_runs": 1,
    "min_validation_negative_runs": 1,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def build_manifest(
    regression_manifest_path: Path,
    summary_path: Path,
    roles_path: Path,
    output_path: Path,
) -> dict:
    regression_manifest_path = regression_manifest_path.resolve()
    summary_path = summary_path.resolve()
    roles_path = roles_path.resolve()
    project_root = regression_manifest_path.parent.parent
    regression = json.loads(regression_manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    roles_document = json.loads(roles_path.read_text(encoding="utf-8"))
    if summary.get("mode") != "REPEATED_REGRESSION":
        raise ValueError("summary is not a repeated regression report")
    if roles_document.get("schema_version") != 1:
        raise ValueError("unsupported role manifest schema")
    roles = roles_document.get("roles", {})
    dataset_contract = {
        entry["id"]: entry
        for entry in [
            *regression.get("datasets", []),
            *regression.get("safety_controls", []),
        ]
    }
    for dataset_id in dataset_contract:
        if roles.get(dataset_id) not in {"development", "validation"}:
            raise ValueError(f"missing predeclared role: {dataset_id}")

    datasets = []
    for dataset in summary.get("datasets", []):
        dataset_id = dataset.get("id")
        contract = dataset_contract.get(dataset_id)
        if contract is None:
            raise ValueError(f"summary contains unknown dataset: {dataset_id}")
        session = resolve(project_root, contract["session"])
        for run in dataset.get("runs", []):
            report_value = run.get("report")
            if not report_value:
                raise ValueError(f"{dataset_id}: run report is missing")
            report = resolve(project_root, report_value)
            loop_log = report.parent / "auto_loop.log"
            if not report.is_file() or not loop_log.is_file():
                raise ValueError(f"{dataset_id}: run artifacts are incomplete")
            datasets.append(
                {
                    "id": f"{dataset_id}-run-{int(run['repetition']):02d}",
                    "role": roles[dataset_id],
                    "session": str(session),
                    "expected_loop": contract["expected_loop"],
                    "loop_log": str(loop_log.resolve()),
                    "loop_log_sha256": sha256(loop_log),
                    "run_report": str(report.resolve()),
                    "run_report_sha256": sha256(report),
                }
            )
    result = {
        "schema_version": 1,
        "confirmation_frames": 4,
        "requirements": MINIMUM_REQUIREMENTS,
        "minimum_support_margin": 0.02,
        "truth_policy": "post_run_scoring_only_hidden_forbidden",
        "source_regression_summary": str(summary_path),
        "source_regression_summary_sha256": sha256(summary_path),
        "roles_manifest": str(roles_path),
        "roles_manifest_sha256": sha256(roles_path),
        "datasets": datasets,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--regression-manifest", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--roles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(
        args.regression_manifest, args.summary, args.roles, args.output
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

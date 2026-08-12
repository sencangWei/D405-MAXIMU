#!/usr/bin/env python3
"""Validate immutable SLAM dataset roles and report product-test readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def validate_manifest(manifest_path: Path, require_hidden: bool) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    roles: dict[str, int] = {}
    ids: set[str] = set()

    for dataset in manifest.get("datasets", []):
        dataset_id = dataset.get("id", "")
        if not dataset_id or dataset_id in ids:
            failures.append(f"invalid or duplicate dataset id: {dataset_id!r}")
        ids.add(dataset_id)
        role = dataset.get("role")
        if role not in {"development", "validation", "hidden_test"}:
            failures.append(f"{dataset_id}: invalid role {role!r}")
            continue
        roles[role] = roles.get(role, 0) + 1

        session = ROOT / dataset.get("session", "")
        acceptance = session / "acceptance.json"
        if not session.is_dir():
            failures.append(f"{dataset_id}: missing session {session}")
            continue
        if not acceptance.is_file():
            failures.append(f"{dataset_id}: missing acceptance.json")
            continue
        actual_hash = hashlib.sha256(acceptance.read_bytes()).hexdigest()
        if actual_hash != dataset.get("acceptance_sha256"):
            failures.append(
                f"{dataset_id}: acceptance hash changed "
                f"({actual_hash} != {dataset.get('acceptance_sha256')})"
            )
        if role == "hidden_test" and dataset.get("external_ground_truth") is None:
            failures.append(f"{dataset_id}: hidden test lacks external ground truth")

    if not roles.get("development"):
        failures.append("no development dataset")
    if not roles.get("validation"):
        failures.append("no validation dataset")
    readiness = "READY" if roles.get("hidden_test") else "MISSING_HIDDEN_TEST"
    if require_hidden and readiness != "READY":
        failures.append("no immutable hidden-test dataset with external ground truth")

    return {
        "result": "PASS" if not failures else "FAIL",
        "product_test_readiness": readiness,
        "role_counts": roles,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "config/slam_product_datasets.json",
    )
    parser.add_argument("--require-hidden", action="store_true")
    args = parser.parse_args()
    result = validate_manifest(args.manifest, args.require_hidden)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

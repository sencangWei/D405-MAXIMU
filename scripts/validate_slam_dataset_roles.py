#!/usr/bin/env python3
"""Validate immutable SLAM dataset roles and report product-test readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROLES = {"development", "validation", "hidden_test"}
REQUIRED_MOTIONS = {
    "straight_open",
    "l_shape_open",
    "free_motion_open",
    "closed_loop_horizontal",
    "closed_loop_with_elevation",
    "true_elevation_open",
    "in_place_rotation",
    "fast_handheld",
}


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def verify_hash(path: Path, expected_hash: str | None, label: str) -> str | None:
    if not path.is_file():
        return f"{label}: missing file {path}"
    if not expected_hash:
        return f"{label}: missing sha256"
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        return f"{label}: hash changed ({actual_hash} != {expected_hash})"
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hidden_capture_identity(dataset: dict, session: Path) -> str | None:
    frozen_value = dataset.get("session_inputs")
    frozen_hash = dataset.get("session_inputs_sha256")
    if not frozen_value or not frozen_hash:
        return None
    frozen_path = resolve_project_path(frozen_value)
    if not frozen_path.is_file() or sha256_file(frozen_path) != frozen_hash:
        return None
    try:
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if (
        frozen.get("frozen_before_slam") is not True
        or frozen.get("truth_usage_policy")
        != "withheld_from_slam_until_post_run_scoring"
        or Path(frozen.get("session", "")).resolve() != session.resolve()
    ):
        return None
    db3 = frozen.get("files", {}).get("camera_db3", {})
    identity = db3.get("sha256")
    return identity if isinstance(identity, str) and len(identity) == 64 else None


def validate_manifest(manifest_path: Path, require_hidden: bool) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    roles: dict[str, int] = {}
    motions: dict[str, int] = {}
    hidden_motion_identities: dict[str, set[str]] = {}
    ids: set[str] = set()

    for dataset in manifest.get("datasets", []):
        dataset_id = dataset.get("id", "")
        if not dataset_id or dataset_id in ids:
            failures.append(f"invalid or duplicate dataset id: {dataset_id!r}")
        ids.add(dataset_id)
        role = dataset.get("role")
        if role not in ALLOWED_ROLES:
            failures.append(f"{dataset_id}: invalid role {role!r}")
            continue
        roles[role] = roles.get(role, 0) + 1
        motion = dataset.get("motion")
        if not motion:
            failures.append(f"{dataset_id}: missing motion classification")
        else:
            motions[motion] = motions.get(motion, 0) + 1

        session = resolve_project_path(dataset.get("session", ""))
        acceptance = session / "acceptance.json"
        if not session.is_dir():
            failures.append(f"{dataset_id}: missing session {session}")
            continue
        if not acceptance.is_file():
            failures.append(f"{dataset_id}: missing acceptance.json")
            continue
        hash_failure = verify_hash(
            acceptance, dataset.get("acceptance_sha256"), f"{dataset_id}: acceptance"
        )
        if hash_failure:
            failures.append(hash_failure)
        ground_truth = dataset.get("external_ground_truth")
        if role == "hidden_test":
            identity = hidden_capture_identity(dataset, session)
            if identity is None:
                failures.append(
                    f"{dataset_id}: hidden test lacks valid pre-SLAM input freeze"
                )
            elif motion:
                hidden_motion_identities.setdefault(motion, set()).add(identity)
            if not isinstance(dataset.get("expected_loop"), bool):
                failures.append(
                    f"{dataset_id}: hidden test must predeclare expected_loop true/false"
                )
            if not ground_truth:
                failures.append(f"{dataset_id}: hidden test lacks external ground truth")
            else:
                ground_truth_failure = verify_hash(
                    resolve_project_path(ground_truth),
                    dataset.get("external_ground_truth_sha256"),
                    f"{dataset_id}: external ground truth",
                )
                if ground_truth_failure:
                    failures.append(ground_truth_failure)

    if not roles.get("development"):
        failures.append("no development dataset")
    if not roles.get("validation"):
        failures.append("no validation dataset")
    readiness = "READY" if roles.get("hidden_test") else "MISSING_HIDDEN_TEST"
    if require_hidden and readiness != "READY":
        failures.append("no immutable hidden-test dataset with external ground truth")

    missing_motions = sorted(REQUIRED_MOTIONS - motions.keys())
    if require_hidden and missing_motions:
        failures.append("missing required motions: " + ", ".join(missing_motions))

    minimum_hidden = int(
        manifest.get("thresholds", {}).get("min_hidden_runs_per_motion", 3)
    )
    if require_hidden:
        for motion in sorted(REQUIRED_MOTIONS):
            count = len(hidden_motion_identities.get(motion, set()))
            if count < minimum_hidden:
                failures.append(
                    f"hidden motion {motion} has {count} independent frozen captures; "
                    f"need {minimum_hidden}"
                )

    return {
        "result": "PASS" if not failures else "FAIL",
        "product_test_readiness": readiness,
        "role_counts": roles,
        "motion_counts": motions,
        "independent_hidden_motion_counts": {
            motion: len(identities)
            for motion, identities in hidden_motion_identities.items()
        },
        "required_motions": sorted(REQUIRED_MOTIONS),
        "missing_motions": missing_motions,
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

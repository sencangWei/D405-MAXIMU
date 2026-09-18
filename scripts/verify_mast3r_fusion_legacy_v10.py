#!/usr/bin/env python3
"""Verify that the recovered MASt3R fusion v10 implementation is intact."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "config" / "mast3r_fusion_legacy_v10.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(toolchain: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(toolchain), *args], text=True
    ).strip()


def verify(profile_path: Path) -> dict:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    evidence: dict[str, object] = {}

    frontend = profile["frontend"]
    toolchain = Path(frontend["toolchain"])
    if not toolchain.is_dir():
        failures.append("frontend_toolchain_missing")
    else:
        commit = git_output(toolchain, "rev-parse", "HEAD")
        diff = subprocess.check_output(["git", "-C", str(toolchain), "diff"])
        diff_sha256 = hashlib.sha256(diff).hexdigest()
        evidence["frontend_commit"] = commit
        evidence["frontend_dirty_diff_sha256"] = diff_sha256
        if commit != frontend["commit"]:
            failures.append("frontend_commit_mismatch")
        if diff_sha256 != frontend["dirty_diff_sha256"]:
            failures.append("frontend_dirty_diff_mismatch")

    for section, key_prefix in (
        (profile["graph_backend"], "graph_backend"),
        (profile["complementary_fusion"], "complementary_fusion"),
    ):
        path = ROOT / section["script"]
        actual = sha256_file(path) if path.is_file() else None
        evidence[f"{key_prefix}_sha256"] = actual
        if actual is None:
            failures.append(f"{key_prefix}_missing")
        elif actual != section["sha256"]:
            failures.append(f"{key_prefix}_sha256_mismatch")

    for name in ("sparse", "tight"):
        path = ROOT / frontend[f"{name}_config"]
        actual = sha256_file(path) if path.is_file() else None
        evidence[f"{name}_config_sha256"] = actual
        if actual is None:
            failures.append(f"{name}_config_missing")
        elif actual != frontend[f"{name}_config_sha256"]:
            failures.append(f"{name}_config_sha256_mismatch")

    return {
        "schema": "umi_mast3r_fusion_legacy_v10_verification_v1",
        "result": "PASS" if not failures else "FAIL",
        "profile": str(profile_path.resolve()),
        "slam_supervision": False,
        "external_ground_truth_used": False,
        "evidence": evidence,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.profile)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

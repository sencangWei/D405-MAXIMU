#!/usr/bin/env python3
"""Freeze one captured SLAM session before hidden evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_INPUTS = {
    "capture_acceptance": "acceptance.json",
    "camera_timestamps": "d405_frames.csv",
    "imu_samples": "external_imu/imu.bin",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_db3(session: Path) -> Path:
    candidates = [path for path in session.glob("*.db3") if path.stat().st_size > 0]
    if not candidates:
        raise FileNotFoundError(f"no non-empty DB3 in {session}")
    return max(candidates, key=lambda path: path.stat().st_size)


def freeze_session(session: Path, output: Path) -> dict:
    session = session.resolve()
    if output.exists():
        raise FileExistsError(output)
    acceptance_path = session / REQUIRED_INPUTS["capture_acceptance"]
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    if acceptance.get("result") != "PASS":
        raise ValueError("capture acceptance must be PASS before freezing")
    paths = {
        name: session / relative for name, relative in REQUIRED_INPUTS.items()
    }
    paths["camera_db3"] = select_db3(session)
    files = {}
    for name, path in paths.items():
        before = path.stat()
        digest = sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RuntimeError(f"input changed while hashing: {path}")
        files[name] = {
            "path": str(path.resolve()),
            "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "sha256": digest,
        }
    result = {
        "schema_version": 1,
        "session": str(session),
        "frozen_before_slam": True,
        "truth_usage_policy": "withheld_from_slam_until_post_run_scoring",
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="在隐藏评测前封存SLAM原始输入")
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = freeze_session(args.session, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

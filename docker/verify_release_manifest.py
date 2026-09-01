#!/usr/bin/env python3
"""Verify the complete Device2 D405 release file and image closure."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "release-manifest.json"
TRANSIENT_PARTS = {".git", ".pytest_cache", "__pycache__"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_files() -> set[str]:
    result: set[str] = set()
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in TRANSIENT_PARTS for part in path.parts):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative == "release-manifest.json" or relative.endswith(".pyc"):
            continue
        result.add(relative)
    return result


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["files"]
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)):
        raise RuntimeError("release manifest contains duplicate file paths")

    declared = set(paths)
    actual = release_files()
    if declared != actual:
        raise RuntimeError(
            f"release file closure mismatch: missing={sorted(actual - declared)}, "
            f"stale={sorted(declared - actual)}"
        )

    for entry in entries:
        path = ROOT / entry["path"]
        observed = sha256(path)
        if observed != entry["sha256"]:
            raise RuntimeError(
                f"sha256 mismatch for {entry['path']}: "
                f"expected={entry['sha256']} actual={observed}"
            )

    image = manifest["image"]
    observed_id = subprocess.check_output(
        ["docker", "image", "inspect", image["tag"], "--format", "{{.Id}}"],
        text=True,
    ).strip()
    if observed_id != image["id"]:
        raise RuntimeError(
            f"image id mismatch: expected={image['id']} actual={observed_id}"
        )

    print(
        f"PASS: {len(entries)} release files and image {image['id']} verified"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

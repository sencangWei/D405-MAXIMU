#!/usr/bin/env python3
"""Synthetic checksum packaging reproducer for the RK3576 0.2.5 failure mode.

This is not a recovered release candidate and does not use recorder data.  It
only demonstrates the packaging invariant behind the historical failure:

* if ``SHA256SUMS`` is generated inside the release tree and lists itself, a
  strict check fails because writing the list mutates the listed file;
* if the manifest avoids self-hashing and ``SHA256SUMS`` is generated outside
  the tree, then moved in while excluding itself, a strict check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterable


CHECKSUMS = "SHA256SUMS"
MANIFEST = "release-manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_files(root: Path, *, include_checksums: bool) -> list[Path]:
    paths = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if not include_checksums and relative.as_posix() == CHECKSUMS:
            continue
        paths.append(relative)
    return paths


def write_manifest(root: Path, *, version: str = "0.2.5-fixture") -> Path:
    """Write a manifest that hashes payload files, but never hashes itself."""
    manifest_path = root / MANIFEST
    entries = []
    for relative in relative_files(root, include_checksums=False):
        if relative.as_posix() == MANIFEST:
            continue
        path = root / relative
        entries.append(
            {
                "path": relative.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest = {
        "schema": "rk3576_checksum_reproducer_fixture_v1",
        "fixture_only": True,
        "not_a_release_candidate": True,
        "version": version,
        "files": entries,
        "manifest_self_hash_excluded": True,
        "sha256sums_self_entry_excluded": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def checksum_lines(root: Path, relatives: Iterable[Path]) -> list[str]:
    lines = []
    for relative in relatives:
        lines.append(f"{sha256_file(root / relative)}  {relative.as_posix()}\n")
    return lines


def write_broken_in_tree_checksums(root: Path) -> Path:
    """Generate SHA256SUMS inside the tree while listing SHA256SUMS itself."""
    checksums = root / CHECKSUMS
    checksums.write_text("", encoding="utf-8")
    relatives = relative_files(root, include_checksums=True)
    checksums.write_text("".join(checksum_lines(root, relatives)), encoding="utf-8")
    return checksums


def write_fixed_external_checksums(root: Path, scratch_dir: Path) -> Path:
    """Generate SHA256SUMS outside the tree, excluding SHA256SUMS itself."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    external = scratch_dir / CHECKSUMS
    relatives = relative_files(root, include_checksums=False)
    external.write_text("".join(checksum_lines(root, relatives)), encoding="utf-8")
    target = root / CHECKSUMS
    shutil.move(str(external), target)
    return target


def verify_strict(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sha256sum", "--strict", "--check", CHECKSUMS],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def create_fixture(root: Path) -> None:
    (root / "adapter").mkdir(parents=True)
    (root / "web-console/static").mkdir(parents=True)
    (root / "adapter/umi_recorderctl.py").write_text(
        'CONTROLLER_VERSION = "0.2.5-umi"\n',
        encoding="utf-8",
    )
    (root / "web-console/static/clock.js").write_text(
        "exports.value = status => Math.max(0, status.capture_elapsed_s || 0);\n",
        encoding="utf-8",
    )


def reproduce(root: Path) -> dict[str, object]:
    if root.exists() or root.is_symlink():
        raise FileExistsError(f"fixture root must be new: {root}")
    root.mkdir(parents=True)
    create_fixture(root)
    write_manifest(root)
    write_broken_in_tree_checksums(root)
    broken = verify_strict(root)
    (root / CHECKSUMS).unlink()
    with tempfile.TemporaryDirectory(prefix="rk3576-checksums-") as tmp:
        write_fixed_external_checksums(root, Path(tmp))
    fixed = verify_strict(root)
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    checksum_text = (root / CHECKSUMS).read_text(encoding="utf-8")
    return {
        "fixture_only": True,
        "not_a_release_candidate": True,
        "broken_returncode": broken.returncode,
        "broken_stderr": broken.stderr,
        "fixed_returncode": fixed.returncode,
        "fixed_stdout": fixed.stdout,
        "manifest_paths": [entry["path"] for entry in manifest["files"]],
        "sha256sums_lists_manifest": f"  {MANIFEST}\n" in checksum_text,
        "sha256sums_lists_itself": f"  {CHECKSUMS}\n" in checksum_text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="empty/new synthetic fixture directory")
    args = parser.parse_args()
    result = reproduce(args.root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["broken_returncode"] != 0 and result["fixed_returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

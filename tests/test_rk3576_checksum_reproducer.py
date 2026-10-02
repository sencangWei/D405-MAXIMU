from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".planning/rk3576_v025_recovery/checksum_reproducer.py"


def load_module():
    spec = importlib.util.spec_from_file_location("checksum_reproducer", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("checksum_reproducer", None)
    spec.loader.exec_module(module)
    return module


def test_in_tree_sha256sums_self_listing_fails_strict_check(tmp_path: Path) -> None:
    repro = load_module()
    root = tmp_path / "broken"
    root.mkdir()
    repro.create_fixture(root)
    repro.write_manifest(root)

    repro.write_broken_in_tree_checksums(root)
    result = repro.verify_strict(root)

    assert result.returncode != 0
    assert "SHA256SUMS" in result.stdout or "SHA256SUMS" in result.stderr
    assert f"  {repro.CHECKSUMS}\n" in (root / repro.CHECKSUMS).read_text(encoding="utf-8")


def test_external_generation_excludes_checksum_itself_and_passes(tmp_path: Path) -> None:
    repro = load_module()
    root = tmp_path / "fixed"
    scratch = tmp_path / "scratch"
    root.mkdir()
    repro.create_fixture(root)
    repro.write_manifest(root)

    repro.write_fixed_external_checksums(root, scratch)
    result = repro.verify_strict(root)
    manifest = json.loads((root / repro.MANIFEST).read_text(encoding="utf-8"))
    checksum_text = (root / repro.CHECKSUMS).read_text(encoding="utf-8")

    assert result.returncode == 0
    assert f"  {repro.CHECKSUMS}\n" not in checksum_text
    assert f"  {repro.MANIFEST}\n" in checksum_text
    assert repro.MANIFEST not in {entry["path"] for entry in manifest["files"]}
    assert repro.CHECKSUMS not in {entry["path"] for entry in manifest["files"]}
    assert manifest["fixture_only"] is True
    assert manifest["not_a_release_candidate"] is True


def test_cli_reports_fixture_not_candidate_and_expected_failure_then_pass(tmp_path: Path) -> None:
    root = tmp_path / "cli-fixture"

    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(root)],
        text=True,
        capture_output=True,
        check=True,
    )
    report = json.loads(result.stdout)

    assert report["fixture_only"] is True
    assert report["not_a_release_candidate"] is True
    assert report["broken_returncode"] != 0
    assert report["fixed_returncode"] == 0
    assert report["sha256sums_lists_manifest"] is True
    assert report["sha256sums_lists_itself"] is False


def test_reproducer_refuses_existing_directory_without_touching_payload(tmp_path: Path) -> None:
    repro = load_module()
    root = tmp_path / "existing"
    root.mkdir()
    sentinel = root / "recording.bin"
    sentinel.write_bytes(b"existing recording must remain unchanged")
    with pytest.raises(FileExistsError, match="must be new"):
        repro.reproduce(root)
    assert sentinel.read_bytes() == b"existing recording must remain unchanged"
    assert list(root.iterdir()) == [sentinel]


def test_reproducer_refuses_symlink_root(tmp_path: Path) -> None:
    repro = load_module()
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "linked"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(FileExistsError, match="must be new"):
        repro.reproduce(root)
    assert list(target.iterdir()) == []

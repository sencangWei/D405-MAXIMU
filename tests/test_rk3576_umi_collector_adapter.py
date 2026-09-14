from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "rk3576" / "collector" / "adapter"
COLLECTOR = ROOT / "rk3576" / "collector"


@pytest.fixture()
def umi_modules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    for name in ("umi_recorderctl", "umi_publish"):
        sys.modules.pop(name, None)
    publish = importlib.import_module("umi_publish")
    recorderctl = importlib.import_module("umi_recorderctl")
    return recorderctl, publish


def test_explicit_unit_identity_reaches_existing_app_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    umi_modules,
) -> None:
    recorderctl, _ = umi_modules
    stm32 = tmp_path / "serial-by-id"
    monkeypatch.setenv("UMI_DEVICE_ID", "umi-rk3576-161")
    monkeypatch.setenv("UMI_D405_SDK_SERIAL", "sdk-serial-161")
    monkeypatch.setenv("UMI_D405_USB_SERIAL", "usb-serial-161")
    monkeypatch.setenv("UMI_STM32_PORT", str(stm32))
    monkeypatch.setenv("UMI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("UMI_RECORDING_ROOT", str(tmp_path / "recordings"))

    config = recorderctl.Config()
    identity = recorderctl.identity(config)

    assert config.sdk_serial == "sdk-serial-161"
    assert config.usb_serial == "usb-serial-161"
    assert config.stm32_port == str(stm32)
    assert identity["device_id"] == "umi-rk3576-161"
    assert identity["model"] == "UMI-D405-RK3576"
    assert identity["capabilities"] == [
        "capture_timing_v1",
        "preview_v1",
        "preview_shared_media_v1",
        "catalog_v1",
        "resumable_transfer_v1",
    ]


def test_recording_root_cannot_change_device_owner(tmp_path: Path, umi_modules) -> None:
    _, publish = umi_modules
    root = tmp_path / "recordings"

    publish.ensure_recording_root(root, "umi-rk3576-161")
    publish.ensure_recording_root(root, "umi-rk3576-161")

    with pytest.raises(ValueError, match="belongs to another device"):
        publish.ensure_recording_root(root, "umi-rk3576-other")


def test_release_provenance_locks_the_deployed_artifact() -> None:
    provenance = json.loads(
        (COLLECTOR / "RELEASE_PROVENANCE.json").read_text(encoding="utf-8")
    )

    assert provenance["artifact"] == {
        "name": "rk3576-umi-0.2.0.tar.gz",
        "sha256": "7d8bd54d59b70513c90b958e1d4d933fd7e5c6288ab8435aca456a72e1f1e84c",
        "architecture": "aarch64",
        "os": "Ubuntu 24.04",
        "python_abi": "cp312",
    }
    assert provenance["collector"]["native_binary_sha256"] == (
        "29d5e9e8cf15639d87e15ad180311d91e05d2b6ab0624a87b32356fa0cd5addb"
    )
    assert provenance["status"] == "BENCH_OBSERVED"


def test_repository_does_not_track_generated_runtime_or_private_keys() -> None:
    tracked_source = {path.relative_to(COLLECTOR).as_posix() for path in COLLECTOR.rglob("*") if path.is_file()}

    assert not any(path.startswith("runtime/") for path in tracked_source)
    assert not any(path.startswith("vendor/") for path in tracked_source)
    assert not any(path.startswith("native/bin/") for path in tracked_source)
    assert not any(path.endswith((".pem", ".key")) for path in tracked_source)

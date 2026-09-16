"""Identity provisioning and controller registry tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rk3576" / "collector" / "commissioningd"))

import crypto_glue as cg  # noqa: E402
from identity import (  # noqa: E402
    DISCRIMINATOR_PATTERN,
    IDENTITY_PROTOCOL,
    DeviceIdentity,
    generate_identity,
    load_identity,
    save_identity,
)
from registry import ControllerRegistry  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _p256_public_b64u() -> str:
    key = generate_private_key(SECP256R1())
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return cg.b64u_encode(raw)


# ---------------------------------------------------------------------------
# identity


def test_generate_identity_defaults(tmp_path):
    identity = generate_identity("device_rk3576_01", "EGO-RK3576", "0.3.0")
    assert len(identity.oob_secret) == 32
    assert len(identity.device_private_key) == 32
    assert len(identity.device_public_key_b64u) == 43
    assert DISCRIMINATOR_PATTERN.fullmatch(identity.discriminator)
    payload = identity.qr_payload()
    assert payload["protocol"] == "EGO_BLE_COMMISSIONING_QR_V1"
    assert payload["service_uuid"] == cg.SERVICE_UUID
    assert payload["device_id"] == "device_rk3576_01"
    assert len(payload["oob_secret"]) == 43
    assert len(payload["device_public_key_sha256"]) == 64
    assert payload["device_public_key_sha256"].islower() or any(
        c in "abcdef" for c in payload["device_public_key_sha256"]
    )


def _assert_qr_schema(payload: dict) -> None:
    """Manual check of the frozen ble-commissioning-qr-v1 schema rules."""
    import re

    assert set(payload) <= {
        "protocol", "service_uuid", "device_id",
        "discriminator", "oob_secret", "device_public_key_sha256",
    }
    assert payload["protocol"] == "EGO_BLE_COMMISSIONING_QR_V1"
    assert payload["service_uuid"] == "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0001"
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,256}", payload["device_id"])
    assert re.fullmatch(r"[0-9A-F]{3}", payload["discriminator"])
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", payload["oob_secret"])
    assert re.fullmatch(r"[0-9a-f]{64}", payload["device_public_key_sha256"])


def test_qr_payload_matches_schema(tmp_path):
    identity = generate_identity("device_rk3576_01", "EGO-RK3576", "0.3.0")
    _assert_qr_schema(identity.qr_payload())
    with pytest.raises(AssertionError):
        _assert_qr_schema({**identity.qr_payload(), "ip": "192.0.2.1"})
    with pytest.raises(AssertionError):
        _assert_qr_schema({**identity.qr_payload(), "oob_secret": "short"})


def test_identity_roundtrip_file_mode(tmp_path):
    identity = generate_identity("device_rk3576_99", "EGO-RK3576", "0.3.0")
    path = tmp_path / "device-identity.json"
    save_identity(identity, path)
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = load_identity(path)
    assert loaded == identity
    record = json.loads(path.read_text())
    assert record["protocol"] == IDENTITY_PROTOCOL


def test_identity_rejects_bad_device_id():
    with pytest.raises(cg.CommissioningError):
        generate_identity("bad id with spaces", "M", "0")
    with pytest.raises(cg.CommissioningError):
        generate_identity("device_01", "M", "0", discriminator="ZZ1")


def test_identity_json_canonical():
    identity = generate_identity("device_01", "EGO-RK3576", "0.3.0")
    value = json.loads(identity.identity_json())
    assert value == {
        "device_id": "device_01",
        "product_model": "EGO-RK3576",
        "software_version": "0.3.0",
    }


# ---------------------------------------------------------------------------
# registry


def test_registry_register_mints_opaque_id(tmp_path):
    registry = ControllerRegistry(tmp_path / "reg.json", "device_01")
    public_key = _p256_public_b64u()
    entry = registry.register(public_key)
    assert entry.controller_id.startswith("controller_")
    assert len(entry.controller_id) <= 256
    loaded = ControllerRegistry(tmp_path / "reg.json", "device_01")
    assert loaded.entries[0].controller_id == entry.controller_id
    assert loaded.entries[0].active


def test_registry_rejects_duplicate_key(tmp_path):
    registry = ControllerRegistry(tmp_path / "reg.json", "device_01")
    public_key = _p256_public_b64u()
    registry.register(public_key)
    with pytest.raises(cg.CommissioningError):
        registry.register(public_key)


def test_registry_caps_at_eight(tmp_path):
    registry = ControllerRegistry(tmp_path / "reg.json", "device_01")
    for _ in range(8):
        registry.register(_p256_public_b64u())
    with pytest.raises(cg.CommissioningError) as err:
        registry.register(_p256_public_b64u())
    assert err.value.code == "UNAVAILABLE"


def test_registry_rejects_malformed_key(tmp_path):
    registry = ControllerRegistry(tmp_path / "reg.json", "device_01")
    with pytest.raises(cg.CommissioningError):
        registry.register("A" * 86)
    with pytest.raises(cg.CommissioningError):
        registry.register(cg.b64u_encode(b"\x02" + b"x" * 64))  # compressed prefix


def test_registry_fail_closed_on_corrupt_file(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text("{not json")
    with pytest.raises(cg.CommissioningError):
        ControllerRegistry(path, "device_01")


def test_registry_fail_closed_on_ninth_entry_file(tmp_path):
    path = tmp_path / "reg.json"
    entry = {
        "controller_id": "controller_x",
        "public_key": "A" * 87,
        "created_at": "2026-09-01T00:00:00Z",
        "expires_at": "2027-09-01T00:00:00Z",
    }
    record = {
        "protocol": "EGO_DEVICE_AUTH_CONTROLLER_REGISTRY_V2",
        "device_id": "device_01",
        "max_controllers": 8,
        "entries": [dict(entry, controller_id=f"c{i}") for i in range(9)],
    }
    path.write_text(json.dumps(record))
    with pytest.raises(cg.CommissioningError):
        ControllerRegistry(path, "device_01")


def test_registry_revoke_and_reset(tmp_path):
    registry = ControllerRegistry(tmp_path / "reg.json", "device_01")
    e1 = registry.register(_p256_public_b64u())
    e2 = registry.register(_p256_public_b64u())
    registry.revoke(e1.controller_id)
    entries = {e.controller_id: e for e in registry.entries}
    assert not entries[e1.controller_id].active
    assert entries[e2.controller_id].active
    registry.reset_all()  # physical reset revokes everything
    assert all(not e.active for e in registry.entries)
    # revoked entries stay recorded but new controllers may still enroll
    # until the file holds eight entries total
    e3 = registry.register(_p256_public_b64u())
    assert e3.active

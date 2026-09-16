"""Per-device commissioning identity: device_id, discriminator, OOB secret, X25519 keypair.

The identity file is created once at the factory (qr_factory.py) and stored
root-owned at /etc/ego/ble/device-identity.json (mode 0600). The OOB secret is
authorization material: it is never logged, advertised, or sent through IPC.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from crypto_glue import (
    SERVICE_UUID,
    CommissioningError,
    b64u_decode,
    b64u_encode,
)

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
DISCRIMINATOR_PATTERN = re.compile(r"^[0-9A-F]{3}$")
IDENTITY_PROTOCOL = "EGO_DEVICE_IDENTITY_V1"
IDENTITY_PATH = Path("/etc/ego/ble/device-identity.json")
QR_PROTOCOL = "EGO_BLE_COMMISSIONING_QR_V1"


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    discriminator: str
    product_model: str
    software_version: str
    oob_secret: bytes  # 32 bytes; never serialized outside the identity file
    device_private_key: bytes  # X25519 private key, 32 bytes
    device_public_key_b64u: str

    def identity_json(self) -> str:
        """GATT ...0002 DeviceIdentity canonical JSON payload."""
        return json.dumps(
            {
                "device_id": self.device_id,
                "product_model": self.product_model,
                "software_version": self.software_version,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def qr_payload(self) -> dict:
        """Sealed QR card payload, validated by ble-commissioning-qr-v1.schema.json."""
        return {
            "protocol": QR_PROTOCOL,
            "service_uuid": SERVICE_UUID,
            "device_id": self.device_id,
            "discriminator": self.discriminator,
            "oob_secret": b64u_encode(self.oob_secret),
            "device_public_key_sha256": _sha256_b64u_of_key(self.device_public_key_b64u),
        }


def _sha256_b64u_of_key(public_key_b64u: str) -> str:
    import hashlib

    return hashlib.sha256(b64u_decode(public_key_b64u)).hexdigest()


def _new_x25519():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    private = X25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public_bytes = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private_bytes, b64u_encode(public_bytes)


def generate_identity(
    device_id: str,
    product_model: str,
    software_version: str,
    discriminator: str | None = None,
) -> DeviceIdentity:
    if not DEVICE_ID_PATTERN.match(device_id):
        raise CommissioningError("INVALID")
    if discriminator is None:
        discriminator = f"{secrets.randbelow(0x1000):03X}"
    if not DISCRIMINATOR_PATTERN.match(discriminator):
        raise CommissioningError("INVALID")
    private_bytes, public_b64u = _new_x25519()
    return DeviceIdentity(
        device_id=device_id,
        discriminator=discriminator,
        product_model=product_model,
        software_version=software_version,
        oob_secret=secrets.token_bytes(32),
        device_private_key=private_bytes,
        device_public_key_b64u=public_b64u,
    )


def save_identity(identity: DeviceIdentity, path: Path = IDENTITY_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "protocol": IDENTITY_PROTOCOL,
        "device_id": identity.device_id,
        "discriminator": identity.discriminator,
        "product_model": identity.product_model,
        "software_version": identity.software_version,
        "oob_secret": b64u_encode(identity.oob_secret),
        "device_private_key": b64u_encode(identity.device_private_key),
        "device_public_key": identity.device_public_key_b64u,
    }
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(record, handle, separators=(",", ":"))
        handle.write("\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_identity(path: Path = IDENTITY_PATH) -> DeviceIdentity:
    path = Path(path)
    try:
        record = json.loads(path.read_text())
    except OSError as exc:
        raise CommissioningError("UNAVAILABLE") from exc
    if record.get("protocol") != IDENTITY_PROTOCOL:
        raise CommissioningError("INVALID")
    try:
        return DeviceIdentity(
            device_id=record["device_id"],
            discriminator=record["discriminator"],
            product_model=record["product_model"],
            software_version=record["software_version"],
            oob_secret=b64u_decode(record["oob_secret"]),
            device_private_key=b64u_decode(record["device_private_key"]),
            device_public_key_b64u=record["device_public_key"],
        )
    except (KeyError, ValueError) as exc:
        raise CommissioningError("INVALID") from exc

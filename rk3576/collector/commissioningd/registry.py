"""Persistent controller registry (device-auth-controller-lifecycle-v2).

Root-owned JSON at /etc/ego/ble/controller-bindings.json; up to eight opaque
controller_id entries with P-256 public signing keys. No Bluetooth MAC, BlueZ
path, or IP is persisted. Parse failure, duplicates, expired entries, or more
than eight entries fail closed.
"""

from __future__ import annotations

import json
import os
import secrets
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto_glue import MAX_CONTROLLERS, CommissioningError, b64u_decode

REGISTRY_PROTOCOL = "EGO_DEVICE_AUTH_CONTROLLER_REGISTRY_V2"
REGISTRY_PATH = Path("/etc/ego/ble/controller-bindings.json")
ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
P256_PUBLIC_KEY_B64U_LEN = 87  # 65-byte uncompressed point, base64url unpadded
CONTROLLER_TTL = timedelta(days=365)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CommissioningError("INVALID") from exc


@dataclass(frozen=True)
class ControllerEntry:
    controller_id: str
    public_key: str  # base64url uncompressed P-256, 87 chars
    created_at: str
    expires_at: str
    revoked_at: str | None = None

    @property
    def expired(self) -> bool:
        return _utcnow() >= _parse_time(self.expires_at)

    @property
    def active(self) -> bool:
        return self.revoked_at is None and not self.expired


class ControllerRegistry:
    def __init__(self, path: Path = REGISTRY_PATH, device_id: str = ""):
        self._path = Path(path)
        self._device_id = device_id
        self._entries: list[ControllerEntry] = []
        self._load()

    @property
    def entries(self) -> list[ControllerEntry]:
        return list(self._entries)

    def _load(self) -> None:
        if not self._path.exists():
            self._entries = []
            return
        try:
            record = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CommissioningError("UNAVAILABLE") from exc
        if record.get("protocol") != REGISTRY_PROTOCOL:
            raise CommissioningError("INVALID")
        if record.get("max_controllers") != MAX_CONTROLLERS:
            raise CommissioningError("INVALID")
        raw_entries = record.get("entries")
        if not isinstance(raw_entries, list) or len(raw_entries) > MAX_CONTROLLERS:
            raise CommissioningError("INVALID")
        entries = []
        for raw in raw_entries:
            entries.append(
                ControllerEntry(
                    controller_id=raw["controller_id"],
                    public_key=raw["public_key"],
                    created_at=raw["created_at"],
                    expires_at=raw["expires_at"],
                    revoked_at=raw.get("revoked_at"),
                )
            )
        self._validate(entries)
        self._entries = entries

    def _validate(self, entries: list[ControllerEntry]) -> None:
        ids = set()
        keys = set()
        for entry in entries:
            if not ID_PATTERN.match(entry.controller_id):
                raise CommissioningError("INVALID")
            if entry.controller_id in ids or entry.public_key in keys:
                raise CommissioningError("INVALID")  # duplicates fail closed
            ids.add(entry.controller_id)
            keys.add(entry.public_key)
            if len(entry.public_key) != P256_PUBLIC_KEY_B64U_LEN:
                raise CommissioningError("INVALID")
            point = b64u_decode(entry.public_key)
            if len(point) != 65 or point[0] != 0x04:
                raise CommissioningError("INVALID")
            # parse-time validation happens lazily via expired property; force it
            _parse_time(entry.created_at)
            _parse_time(entry.expires_at)
            if entry.revoked_at is not None:
                _parse_time(entry.revoked_at)
        if self._device_id and not ID_PATTERN.match(self._device_id):
            raise CommissioningError("INVALID")

    def register(self, public_key: str) -> ControllerEntry:
        """Mint an opaque controller_id. The caller cannot choose the ID."""
        point = b64u_decode(public_key)
        if len(point) != 65 or point[0] != 0x04:
            raise CommissioningError("INVALID")
        if len(public_key) != P256_PUBLIC_KEY_B64U_LEN:
            raise CommissioningError("INVALID")
        if any(e.public_key == public_key or e.active for e in self._entries if e.public_key == public_key):
            raise CommissioningError("REPLAYED")
        # the registry file holds at most eight entries total; revoked entries
        # stay recorded (v2 schema revoked_at) and still occupy a slot
        if len(self._entries) >= MAX_CONTROLLERS:
            raise CommissioningError("UNAVAILABLE")
        controller_id = f"controller_{secrets.token_urlsafe(18)}"
        if not ID_PATTERN.match(controller_id):  # defensive; token_urlsafe fits
            controller_id = f"controller_{secrets.token_hex(16)}"
        now = _utcnow()
        entry = ControllerEntry(
            controller_id=controller_id,
            public_key=public_key,
            created_at=_fmt(now),
            expires_at=_fmt(now + CONTROLLER_TTL),
        )
        self._entries.append(entry)
        self._save()
        return entry

    def revoke(self, controller_id: str) -> None:
        for entry in self._entries:
            if entry.controller_id == controller_id and entry.revoked_at is None:
                updated = ControllerEntry(
                    entry.controller_id,
                    entry.public_key,
                    entry.created_at,
                    entry.expires_at,
                    revoked_at=_fmt(_utcnow()),
                )
                self._entries[self._entries.index(entry)] = updated
                self._save()
                return
        raise CommissioningError("INVALID")

    def reset_all(self) -> None:
        """Physical reset: revoke every controller binding."""
        now = _fmt(_utcnow())
        self._entries = [
            ControllerEntry(e.controller_id, e.public_key, e.created_at, e.expires_at, now)
            for e in self._entries
        ]
        self._save()

    def _save(self) -> None:
        record = {
            "protocol": REGISTRY_PROTOCOL,
            "device_id": self._device_id,
            "max_controllers": MAX_CONTROLLERS,
            "entries": [
                {
                    "controller_id": e.controller_id,
                    "public_key": e.public_key,
                    "created_at": e.created_at,
                    "expires_at": e.expires_at,
                    **({"revoked_at": e.revoked_at} if e.revoked_at else {}),
                }
                for e in self._entries
            ],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp, self._path)
        os.chmod(self._path, 0o600)

"""EGO BLE Wi-Fi commissioning v1 crypto and framing core.

Implements the FROZEN / V0.8-BASELINE.3 contracts:

- ble-wifi-commissioning-v1: 12-byte little-endian GATT write header,
  fragment reassembly with idempotent-retransmit / fail-closed semantics,
  X25519 + HKDF-SHA256 session key, AES-256-GCM credential envelopes.
- ble-discovery-v1: HMAC-SHA256 binding proof over
  ``EGO_BLE_BINDING_PROOF_V1\0 || device_id || challenge``.

The golden vector in ``tests/fixtures/ble-wifi-commissioning-v1/crypto-vector.json``
(source: JO-ara-dev/ego-contracts, FROZEN baseline.3) locks every byte.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Contract constants (frozen)

SERVICE_UUID = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0001"
CHAR_IDENTITY = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0002"
CHAR_CHALLENGE = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0003"
CHAR_RESPONSE = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0004"
CHAR_CONTROL = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0005"
CHAR_DEVICE_KEY = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0006"
CHAR_CREDENTIALS = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0007"
CHAR_STATUS = "f3e0f8d0-7a11-4c9e-9d4b-45474f4f0008"

MSG_CONTROL = 5
MSG_CREDENTIALS = 7

HEADER_BYTES = 12
MAX_FRAME_BYTES = 256
MAX_FRAGMENT_DATA = 244
MAX_FRAGMENTS = 32
MAX_MESSAGE_BYTES = 4096

WINDOW_SECONDS = 120
K1_HOLD_SECONDS = 5
MAX_CONTROLLERS = 8

HKDF_SALT = b"EGO_BLE_WIFI_COMMISSIONING_V1\x00"
HKDF_INFO_PREFIX = b"EGO_BLE_WIFI_SESSION_KEY_V1\x00"

BINDING_PROOF_PREFIX = b"EGO_BLE_BINDING_PROOF_V1\x00"
# INTERPRETATION (not frozen by machine schema; prose-only in
# ble-wifi-commissioning-v1.md): BEGIN OOB proof domain separation.
COMMISSIONING_PROOF_PREFIX = b"EGO_BLE_COMMISSIONING_PROOF_V1\x00"

SECURITY_MODES = ("OPEN", "WPA2_PSK", "WPA3_SAE")


class CommissioningError(Exception):
    """Sanitized fail-closed error; never carries secret material."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# base64url helpers (unpadded, per contract)


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(value: str) -> bytes:
    if not isinstance(value, str):
        raise CommissioningError("INVALID")
    pad = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + pad)
    except Exception as exc:  # noqa: BLE001 - sanitized
        raise CommissioningError("INVALID") from exc


# ---------------------------------------------------------------------------
# 12-byte framing header


@dataclass(frozen=True)
class FrameHeader:
    version: int
    message: int
    transport_session: int
    sequence: int
    fragment_index: int
    fragment_count: int


def pack_header(header: FrameHeader) -> bytes:
    return struct.pack(
        "<BBBBIHBB",
        header.version,
        header.message,
        0,  # flags
        0,  # reserved
        header.transport_session,
        header.sequence,
        header.fragment_index,
        header.fragment_count,
    )


def parse_header(frame: bytes) -> FrameHeader:
    if len(frame) < HEADER_BYTES:
        raise CommissioningError("INVALID")
    version, message, flags, reserved, ts, seq, idx, count = struct.unpack(
        "<BBBBIHBB", frame[:HEADER_BYTES]
    )
    if version != 1 or flags != 0 or reserved != 0:
        raise CommissioningError("INVALID")
    if message not in (MSG_CONTROL, MSG_CREDENTIALS):
        raise CommissioningError("INVALID")
    if ts == 0 or seq == 0:
        raise CommissioningError("INVALID")
    if not 1 <= count <= MAX_FRAGMENTS or idx >= count:
        raise CommissioningError("INVALID")
    return FrameHeader(version, message, ts, seq, idx, count)


@dataclass
class _PartialMessage:
    message: int
    fragment_count: int
    fragments: dict[int, bytes] = field(default_factory=dict)
    accepted: dict[int, bytes] = field(default_factory=dict)  # seq -> raw frame
    last_sequence: int = 0
    header_zero: FrameHeader | None = None  # logical header with fragment_index=0


class FragmentReassembler:
    """Per-connection reassembler. Identical retransmit is idempotent; a changed
    duplicate, sequence gap, malformed frame, or oversize message fails closed."""

    def __init__(self) -> None:
        self._sessions: dict[int, _PartialMessage] = {}

    def accept(self, frame: bytes) -> tuple[FrameHeader, bytes] | None:
        header = parse_header(frame)
        data = frame[HEADER_BYTES:]
        if len(frame) > MAX_FRAME_BYTES or len(data) > MAX_FRAGMENT_DATA:
            raise CommissioningError("INVALID")
        partial = self._sessions.get(header.transport_session)
        if partial is None:
            if header.fragment_index != 0:
                raise CommissioningError("INVALID")  # gap at session start
            partial = _PartialMessage(header.message, header.fragment_count)
            self._sessions[header.transport_session] = partial
        if (
            partial.message != header.message
            or partial.fragment_count != header.fragment_count
        ):
            raise CommissioningError("INVALID")
        if header.sequence in partial.accepted:
            if partial.accepted[header.sequence] == frame:
                return None  # idempotent retransmit of an accepted fragment
            raise CommissioningError("REPLAYED")  # changed duplicate
        if header.sequence != partial.last_sequence + 1 and partial.accepted:
            raise CommissioningError("INVALID")  # gap or reorder
        # the first observed sequence of a fresh transport session may be any
        # non-zero value; after that, sequences must be contiguous
        partial.accepted[header.sequence] = frame
        partial.last_sequence = header.sequence
        partial.fragments[header.fragment_index] = data
        if header.fragment_index == 0:
            partial.header_zero = header
        total = sum(len(v) for v in partial.fragments.values())
        if total > MAX_MESSAGE_BYTES:
            raise CommissioningError("INVALID")
        if len(partial.fragments) < partial.fragment_count:
            return None
        message = b"".join(
            partial.fragments[i] for i in range(partial.fragment_count)
        )
        del self._sessions[header.transport_session]
        return partial.header_zero or header, message

    def reset(self) -> None:
        self._sessions.clear()


# ---------------------------------------------------------------------------
# Strict JSON helpers


def strict_json_object(payload: bytes, allowed_fields: tuple[str, ...]) -> dict:
    """Strict UTF-8 JSON: single object, no trailing bytes, no unknown fields."""
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CommissioningError("INVALID") from exc
    try:
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(text)
        if text[end:].strip():
            raise CommissioningError("INVALID")  # trailing JSON
    except json.JSONDecodeError as exc:
        raise CommissioningError("INVALID") from exc
    if not isinstance(value, dict):
        raise CommissioningError("INVALID")
    if any(k not in allowed_fields for k in value):
        raise CommissioningError("INVALID")
    return value


# ---------------------------------------------------------------------------
# Binding and commissioning proofs (HMAC-SHA256 with the 32-byte OOB secret)


def binding_proof(oob_secret: bytes, device_id: str, challenge: bytes) -> bytes:
    if not 16 <= len(challenge) <= 32:
        raise CommissioningError("INVALID")
    return hmac.new(
        oob_secret, BINDING_PROOF_PREFIX + device_id.encode() + challenge, hashlib.sha256
    ).digest()


def commissioning_oob_proof(
    oob_secret: bytes, device_id: str, session_id: str, client_public_key_b64u: str
) -> bytes:
    """BEGIN OOB proof. INTERPRETATION: domain-separated HMAC over
    device_id || session_id || client_public_key (prose-only in the contract)."""
    return hmac.new(
        oob_secret,
        COMMISSIONING_PROOF_PREFIX
        + device_id.encode()
        + session_id.encode()
        + client_public_key_b64u.encode(),
        hashlib.sha256,
    ).digest()


def verify_proof(expected: bytes, presented_b64u: str) -> None:
    presented = b64u_decode(presented_b64u)
    if not hmac.compare_digest(expected, presented):
        raise CommissioningError("AUTH_FAILED")


# ---------------------------------------------------------------------------
# Session key and credential envelope


def hkdf_session_key(
    shared_secret: bytes, device_id: str, controller_id: str, session_id: str
) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    info = (
        HKDF_INFO_PREFIX
        + device_id.encode()
        + b"\x00"
        + controller_id.encode()
        + b"\x00"
        + session_id.encode()
    )
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=HKDF_SALT, info=info).derive(
        shared_secret
    )


def build_aad(
    logical_header: FrameHeader, device_id: str, controller_id: str, session_id: str
) -> bytes:
    """AAD = 12-byte logical header (fragment_index=0) || device_id\0controller_id\0session_id."""
    zeroed = FrameHeader(
        logical_header.version,
        logical_header.message,
        logical_header.transport_session,
        logical_header.sequence,
        0,
        logical_header.fragment_count,
    )
    return (
        pack_header(zeroed)
        + device_id.encode()
        + b"\x00"
        + controller_id.encode()
        + b"\x00"
        + session_id.encode()
    )


def aes_gcm_decrypt(
    session_key: bytes, nonce: bytes, ciphertext_and_tag: bytes, aad: bytes
) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        return AESGCM(session_key).decrypt(nonce, ciphertext_and_tag, aad)
    except (InvalidTag, ValueError) as exc:
        raise CommissioningError("AUTH_FAILED") from exc


def parse_credential_plaintext(plaintext: bytes) -> dict:
    value = strict_json_object(plaintext, ("ssid", "passphrase", "security_mode"))
    ssid = value.get("ssid")
    mode = value.get("security_mode")
    passphrase = value.get("passphrase")
    if not isinstance(ssid, str) or not ssid or len(ssid.encode()) > 32:
        raise CommissioningError("INVALID")
    if mode not in SECURITY_MODES:
        raise CommissioningError("INVALID")
    if mode == "OPEN":
        if passphrase is not None:
            raise CommissioningError("INVALID")  # OPEN carries no passphrase
    else:
        # Non-empty passphrase; length/charset bounds are enforced by
        # NetworkManager at apply time (the frozen golden vector itself uses a
        # 6-character test passphrase, so the contract does not pin 8-63 here).
        if not isinstance(passphrase, str) or not passphrase:
            raise CommissioningError("INVALID")
    return value

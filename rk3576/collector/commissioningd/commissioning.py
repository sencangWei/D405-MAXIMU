"""Commissioning session state machine (ble-wifi-commissioning-v1 core).

DBus-free and clock-injected for unit testing. The BLE layer feeds raw ATT
write values in; the state machine exposes the ...0008 status object and
drives the injected network backend.

BEGIN interpretation note: the contract freezes the framing, crypto, physical
gate, and network handoff, but the exact BEGIN plaintext JSON field names are
prose-only. This implementation freezes them locally as:

    {"protocol": "EGO_BLE_WIFI_BEGIN_V1", "device_id", "discriminator",
     "session_id", "client_public_key", "controller_public_key", "oob_proof"}

with session_id as 32 lowercase hex chars, X25519 client key (43-char
base64url), uncompressed P-256 controller key (87-char base64url), and
oob_proof = HMAC-SHA256(oob, "EGO_BLE_COMMISSIONING_PROOF_V1\0" ||
device_id || session_id || client_public_key).
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from crypto_glue import (
    MSG_CONTROL,
    MSG_CREDENTIALS,
    WINDOW_SECONDS,
    CommissioningError,
    FragmentReassembler,
    FrameHeader,
    aes_gcm_decrypt,
    b64u_decode,
    build_aad,
    commissioning_oob_proof,
    hkdf_session_key,
    parse_credential_plaintext,
    strict_json_object,
    verify_proof,
)
from identity import DeviceIdentity
from registry import ControllerRegistry

LOGGER = logging.getLogger("commissioningd.session")

BEGIN_PROTOCOL = "EGO_BLE_WIFI_BEGIN_V1"
NETWORK_READY_PROTOCOL = "EGO_BLE_WIFI_NETWORK_READY_V1"
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
BEGIN_FIELDS = (
    "protocol",
    "device_id",
    "discriminator",
    "session_id",
    "client_public_key",
    "controller_public_key",
    "oob_proof",
)


class NetworkBackend(Protocol):
    """Approved NetworkManager adapter seam. Never logs or persists credentials."""

    def apply_credentials(self, credentials: dict, deadline_seconds: float) -> tuple[str, str]:
        """Apply ssid/passphrase/security_mode; return (https_origin, leaf cert sha256)."""

    def cleanup_failed(self) -> None:
        """Remove the failed temporary connection profile."""


@dataclass
class CommissioningConfig:
    https_origin: str  # configured non-loopback Device API listener origin
    server_certificate_sha256: str  # lowercase hex, 64 chars
    activation_timeout_seconds: float = 120.0


class CommissioningStateMachine:
    IDLE = "IDLE"
    WINDOW_OPEN = "WINDOW_OPEN"  # K1 hold done; awaiting BEGIN
    WAITING_CREDENTIALS = "WAITING_CREDENTIALS"
    APPLYING = "APPLYING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    def __init__(
        self,
        identity: DeviceIdentity,
        registry: ControllerRegistry,
        network: NetworkBackend,
        config: CommissioningConfig,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._identity = identity
        self._registry = registry
        self._network = network
        self._config = config
        self._clock = clock
        self._reassembler = FragmentReassembler()
        self._challenge: bytes | None = None
        self._window_opened_at: float | None = None
        self._session: dict | None = None  # active BEGIN context
        self._final_state: str | None = None
        self._final_error = "NONE"
        self._controller_id: str | None = None
        self._bootstrap_reference: str | None = None
        self._bootstrap_expires_at: str | None = None

    # ------------------------------------------------------------------
    # GATT surface used by ble_gatt.py

    def read_identity(self) -> str:
        return self._identity.identity_json()

    def read_device_key(self) -> str:
        return self._identity.device_public_key_b64u

    def write_challenge(self, data: bytes) -> None:
        """...0003: 16-32 fresh random bytes; a new challenge invalidates the old."""
        if not 16 <= len(data) <= 32:
            raise CommissioningError("INVALID")
        self._challenge = bytes(data)

    def read_response(self) -> bytes:
        """...0004: HMAC proof for the current challenge; none before a challenge."""
        from crypto_glue import binding_proof

        if self._challenge is None:
            raise CommissioningError("UNAVAILABLE")
        return binding_proof(
            self._identity.oob_secret, self._identity.device_id, self._challenge
        )

    def write_control(self, frame: bytes) -> None:
        result = self._reassembler.accept(frame)
        if result is None:
            return
        header, message = result
        if header.message != MSG_CONTROL:
            raise CommissioningError("INVALID")
        self._handle_begin(header, message)

    def write_credentials(self, frame: bytes) -> None:
        result = self._reassembler.accept(frame)
        if result is None:
            return
        header, message = result
        if header.message != MSG_CREDENTIALS:
            raise CommissioningError("INVALID")
        self._handle_credentials(header, message)

    def read_status(self) -> dict:
        status = {
            "protocol": NETWORK_READY_PROTOCOL,
            "state": self._final_state or self.state,
            "error_code": self._final_error,
        }
        if self._controller_id is not None:
            status["controller_id"] = self._controller_id
        if self._bootstrap_reference is not None:
            status["bootstrap_reference"] = self._bootstrap_reference
            status["bootstrap_expires_at"] = self._bootstrap_expires_at
        if status["state"] == self.CONNECTED:
            status["https_origin"] = self._config.https_origin
            status["server_certificate_sha256"] = (
                self._config.server_certificate_sha256
            )
        return status

    # ------------------------------------------------------------------
    # K1 gate and lifecycle

    @property
    def state(self) -> str:
        if self._window_opened_at is None:
            return self.IDLE
        if self._session is not None:
            return self._session["phase"]
        return self.WINDOW_OPEN

    def open_window(self) -> None:
        """K1 COMMISSIONING_HOLD: open one 120-second window."""
        if self._window_opened_at is not None:
            return  # one window per hold; already open
        self._window_opened_at = self._clock()
        self._final_state = None
        self._final_error = "NONE"
        LOGGER.info("commissioning window opened")
    def tick(self) -> None:
        """Called periodically; enforces the 120-second window."""
        if self._window_opened_at is None:
            return
        if self._clock() - self._window_opened_at > WINDOW_SECONDS:
            self._end_session(self.EXPIRED, "TIMEOUT")

    def disconnect(self) -> None:
        """BLE disconnect: end the in-progress session only."""
        if self._window_opened_at is not None or self._session is not None:
            self._end_session(self.CANCELLED, "NONE")

    # ------------------------------------------------------------------
    # BEGIN / credentials

    def _handle_begin(self, header: FrameHeader, message: bytes) -> None:
        if self._window_opened_at is None:
            raise CommissioningError("PHYSICAL_GATE_REQUIRED")
        if self._session is not None:
            raise CommissioningError("UNAVAILABLE")  # one session per window
        begin = strict_json_object(message, BEGIN_FIELDS)
        if begin.get("protocol") != BEGIN_PROTOCOL:
            raise CommissioningError("INVALID")
        if begin.get("device_id") != self._identity.device_id:
            raise CommissioningError("INVALID")
        if begin.get("discriminator") != self._identity.discriminator:
            raise CommissioningError("INVALID")
        session_id = begin.get("session_id")
        if not isinstance(session_id, str) or not SESSION_ID_PATTERN.match(session_id):
            raise CommissioningError("INVALID")
        client_public_key = begin.get("client_public_key")
        controller_public_key = begin.get("controller_public_key")
        try:
            client_key_bytes = b64u_decode(client_public_key)
            controller_key_bytes = b64u_decode(controller_public_key)
        except CommissioningError:
            raise
        if len(client_key_bytes) != 32 or len(controller_key_bytes) != 65:
            raise CommissioningError("INVALID")
        verify_proof(
            commissioning_oob_proof(
                self._identity.oob_secret,
                self._identity.device_id,
                session_id,
                client_public_key,
            ),
            begin.get("oob_proof"),
        )
        # verified: mint the opaque controller identity and derive the session key
        entry = self._registry.register(controller_public_key)
        from cryptography.hazmat.primitives.asymmetric.x25519 import (
            X25519PrivateKey,
            X25519PublicKey,
        )

        private_key = X25519PrivateKey.from_private_bytes(
            self._identity.device_private_key
        )
        shared = private_key.exchange(X25519PublicKey.from_public_bytes(client_key_bytes))
        session_key = hkdf_session_key(
            shared, self._identity.device_id, entry.controller_id, session_id
        )
        self._controller_id = entry.controller_id
        self._bootstrap_reference = self._new_bootstrap_reference()
        from datetime import datetime, timedelta, timezone

        self._bootstrap_expires_at = (
            (datetime.now(timezone.utc) + timedelta(seconds=WINDOW_SECONDS))
            .isoformat()
            .replace("+00:00", "Z")
        )
        self._session = {
            "phase": self.WAITING_CREDENTIALS,
            "session_id": session_id,
            "session_key": session_key,
            "credentials_header": None,
        }
        LOGGER.info("BEGIN verified; controller binding registered")

    def _handle_credentials(self, header: FrameHeader, message: bytes) -> None:
        session = self._session
        if session is None or session["phase"] != self.WAITING_CREDENTIALS:
            raise CommissioningError("UNAVAILABLE")
        if len(message) < 13 + 16:  # 12-byte nonce + 16-byte GCM tag minimum
            raise CommissioningError("INVALID")
        nonce, ciphertext = message[:12], message[12:]
        aad = build_aad(
            header,
            self._identity.device_id,
            self._controller_id,
            session["session_id"],
        )
        try:
            plaintext = aes_gcm_decrypt(session["session_key"], nonce, ciphertext, aad)
        except CommissioningError as exc:
            # crypto failures fail closed: end the session and clear key material
            self._end_session(self.FAILED, exc.code)
            raise
        credentials = parse_credential_plaintext(plaintext)
        session["phase"] = self.APPLYING
        try:
            origin, cert_hash = self._network.apply_credentials(
                credentials, self._config.activation_timeout_seconds
            )
        except CommissioningError as exc:
            self._network.cleanup_failed()
            self._end_session(self.FAILED, exc.code)
            raise
        if not origin.startswith("https://"):
            self._network.cleanup_failed()
            self._end_session(self.FAILED, "NETWORK_FAILED")
            raise CommissioningError("NETWORK_FAILED")
        self._end_session(self.CONNECTED, "NONE")
        LOGGER.info("credentials applied; network ready")

    # ------------------------------------------------------------------

    def _new_bootstrap_reference(self) -> str:
        from crypto_glue import b64u_encode

        return b64u_encode(secrets.token_bytes(32))  # 43 chars, single-use

    def _end_session(self, state: str, error_code: str) -> None:
        self._session = None
        self._challenge = None
        self._window_opened_at = None
        self._reassembler.reset()
        self._final_state = state
        self._final_error = error_code
        if state != self.CONNECTED:
            self._bootstrap_reference = None
            self._bootstrap_expires_at = None
            if state != self.FAILED:  # FAILED keeps controller_id for diagnosis
                self._controller_id = None

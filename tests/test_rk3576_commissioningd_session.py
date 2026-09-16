"""End-to-end commissioning state machine tests with a simulated central.

Covers: binding proof challenge/response, K1 physical gate, BEGIN OOB proof,
encrypted credential handoff, network-ready status object, and fail-closed
paths (no gate, expired window, wrong OOB, tampered ciphertext).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rk3576" / "collector" / "commissioningd"))

import crypto_glue as cg  # noqa: E402
from commissioning import (  # noqa: E402
    BEGIN_PROTOCOL,
    CommissioningConfig,
    CommissioningStateMachine,
)
from identity import DeviceIdentity, generate_identity  # noqa: E402
from registry import ControllerRegistry  # noqa: E402

ORIGIN = "https://192.168.113.161:18443"
CERT_HASH = "ab" * 32


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


class FakeNetwork:
    def __init__(self, result=(ORIGIN, CERT_HASH), fail: cg.CommissioningError | None = None):
        self.result = result
        self.fail = fail
        self.applied: list[dict] = []
        self.cleanups = 0

    def apply_credentials(self, credentials, deadline_seconds):
        self.applied.append(credentials)
        if self.fail:
            raise self.fail
        return self.result

    def cleanup_failed(self):
        self.cleanups += 1


@pytest.fixture()
def harness(tmp_path):
    identity = generate_identity("device_rk3576_01", "EGO-RK3576", "0.3.0")
    registry = ControllerRegistry(tmp_path / "controller-bindings.json", identity.device_id)
    network = FakeNetwork()
    clock = FakeClock()
    config = CommissioningConfig(ORIGIN, CERT_HASH)
    sm = CommissioningStateMachine(identity, registry, network, config, clock=clock)
    return identity, registry, network, clock, sm


class SimulatedCentral:
    """Builds client-side BEGIN and credential frames exactly like the app would."""

    def __init__(self, identity: DeviceIdentity):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ec import (
            SECP256R1,
            EllipticCurvePrivateKey,
        )
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        from cryptography.hazmat.primitives.asymmetric.ec import generate_private_key

        self.identity = identity
        self.x25519 = X25519PrivateKey.generate()
        self.p256 = generate_private_key(SECP256R1())
        self.sequence = 0

    def client_public_b64u(self) -> str:
        from cryptography.hazmat.primitives import serialization

        raw = self.x25519.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cg.b64u_encode(raw)

    def controller_public_b64u(self) -> str:
        from cryptography.hazmat.primitives import serialization

        raw = self.p256.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return cg.b64u_encode(raw)

    def begin_json(self, session_id: str) -> bytes:
        client_pk = self.client_public_b64u()
        proof = cg.commissioning_oob_proof(
            self.identity.oob_secret, self.identity.device_id, session_id, client_pk
        )
        return json.dumps(
            {
                "protocol": BEGIN_PROTOCOL,
                "device_id": self.identity.device_id,
                "discriminator": self.identity.discriminator,
                "session_id": session_id,
                "client_public_key": client_pk,
                "controller_public_key": self.controller_public_b64u(),
                "oob_proof": cg.b64u_encode(proof),
            },
            separators=(",", ":"),
        ).encode()

    def frame(self, message: int, transport_session: int, payload: bytes) -> list[bytes]:
        count = max(1, (len(payload) + cg.MAX_FRAGMENT_DATA - 1) // cg.MAX_FRAGMENT_DATA)
        frames = []
        for index in range(count):
            self.sequence += 1
            chunk = payload[
                index * cg.MAX_FRAGMENT_DATA : (index + 1) * cg.MAX_FRAGMENT_DATA
            ]
            header = cg.FrameHeader(
                1, message, transport_session, self.sequence, index, count
            )
            frames.append(cg.pack_header(header) + chunk)
        return frames

    def session_key(self, controller_id: str, session_id: str) -> bytes:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

        device_public = X25519PublicKey.from_public_bytes(
            cg.b64u_decode(self.identity.device_public_key_b64u)
        )
        shared = self.x25519.exchange(device_public)
        return cg.hkdf_session_key(
            shared, self.identity.device_id, controller_id, session_id
        )

    def credentials_frame(
        self, session_key: bytes,
        device_id: str, controller_id: str, session_id: str, credentials: dict,
    ) -> list[bytes]:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = b"\x03" * 12
        plaintext = json.dumps(credentials, separators=(",", ":")).encode()
        envelope_len = 12 + len(plaintext) + 16
        count = max(1, (envelope_len + cg.MAX_FRAGMENT_DATA - 1) // cg.MAX_FRAGMENT_DATA)
        # the AAD locks the logical header (fragment_index=0) that frame()
        # is about to assign: next sequence, zero-based index, predicted count
        logical = cg.FrameHeader(
            1, cg.MSG_CREDENTIALS, 7, self.sequence + 1, 0, count
        )
        aad = cg.build_aad(logical, device_id, controller_id, session_id)
        envelope = nonce + AESGCM(session_key).encrypt(nonce, plaintext, aad)
        return self.frame(cg.MSG_CREDENTIALS, 7, envelope)


def test_challenge_response_binding_proof(harness):
    identity, _, _, _, sm = harness
    with pytest.raises(cg.CommissioningError):
        sm.read_response()  # no challenge yet
    sm.write_challenge(b"z" * 24)
    response = sm.read_response()
    expected = cg.binding_proof(identity.oob_secret, identity.device_id, b"z" * 24)
    assert response == expected
    # a new challenge invalidates the previous proof
    sm.write_challenge(b"y" * 24)
    assert sm.read_response() != response


def test_begin_without_physical_gate_fails(harness):
    _, _, _, _, sm = harness
    identity = sm._identity
    central = SimulatedCentral(identity)
    frames = central.frame(cg.MSG_CONTROL, 3, central.begin_json("ab" * 16))
    with pytest.raises(cg.CommissioningError) as err:
        for f in frames:
            sm.write_control(f)
    assert err.value.code == "PHYSICAL_GATE_REQUIRED"


def test_full_commissioning_flow(harness):
    identity, registry, network, clock, sm = harness
    central = SimulatedCentral(identity)

    # K1 hold opens the window
    sm.open_window()
    assert sm.read_status()["state"] == "WINDOW_OPEN"

    # BEGIN
    session_id = "0123456789abcdef0123456789abcdef"
    for f in central.frame(cg.MSG_CONTROL, 3, central.begin_json(session_id)):
        sm.write_control(f)
    status = sm.read_status()
    assert status["state"] == "WAITING_CREDENTIALS"
    controller_id = status["controller_id"]
    assert controller_id.startswith("controller_")
    assert len(status["bootstrap_reference"]) == 43

    # credentials: long passphrase forces the envelope across two fragments
    session_key = central.session_key(controller_id, session_id)
    frames = central.credentials_frame(
        session_key,
        identity.device_id,
        controller_id,
        session_id,
        {"ssid": "Ruijie-s6145", "passphrase": "p" * 200, "security_mode": "WPA2_PSK"},
    )
    assert len(frames) == 2
    for f in frames:
        sm.write_credentials(f)

    status = sm.read_status()
    assert status["state"] == "CONNECTED"
    assert status["error_code"] == "NONE"
    assert status["https_origin"] == ORIGIN
    assert status["server_certificate_sha256"] == CERT_HASH
    assert network.applied == [
        {"ssid": "Ruijie-s6145", "passphrase": "p" * 200, "security_mode": "WPA2_PSK"}
    ]

    # registry persisted one active controller
    entries = registry.entries
    assert len(entries) == 1
    assert entries[0].controller_id == controller_id
    assert entries[0].active


def test_wrong_oob_proof_rejected(harness):
    identity, registry, _, _, sm = harness
    sm.open_window()
    rogue = generate_identity("device_rk3576_01", "EGO-RK3576", "0.3.0",
                              discriminator=identity.discriminator)
    central = SimulatedCentral(rogue)  # proofs computed with the WRONG oob secret
    frames = central.frame(cg.MSG_CONTROL, 3, central.begin_json("cd" * 16))
    with pytest.raises(cg.CommissioningError) as err:
        for f in frames:
            sm.write_control(f)
    assert err.value.code == "AUTH_FAILED"
    assert registry.entries == []


def test_tampered_ciphertext_fails_closed(harness):
    identity, registry, network, _, sm = harness
    central = SimulatedCentral(identity)
    sm.open_window()
    session_id = "ef" * 16
    for f in central.frame(cg.MSG_CONTROL, 3, central.begin_json(session_id)):
        sm.write_control(f)
    controller_id = sm.read_status()["controller_id"]
    session_key = central.session_key(controller_id, session_id)
    frames = central.credentials_frame(
        session_key,
        identity.device_id, controller_id, session_id,
        {"ssid": "x", "passphrase": "y", "security_mode": "WPA2_PSK"},
    )
    tampered = bytearray(frames[0])
    tampered[-1] ^= 0x01
    with pytest.raises(cg.CommissioningError) as err:
        sm.write_credentials(bytes(tampered))
    assert err.value.code == "AUTH_FAILED"
    assert network.cleanups == 0  # auth failure ends before network apply
    status = sm.read_status()
    assert status["state"] == "FAILED"
    assert status["error_code"] == "AUTH_FAILED"


def test_network_failure_cleans_up_profile(harness):
    identity, _, network, _, sm = harness
    network.fail = cg.CommissioningError("NETWORK_FAILED")
    central = SimulatedCentral(identity)
    sm.open_window()
    session_id = "aa" * 16
    for f in central.frame(cg.MSG_CONTROL, 3, central.begin_json(session_id)):
        sm.write_control(f)
    controller_id = sm.read_status()["controller_id"]
    session_key = central.session_key(controller_id, session_id)
    frames = central.credentials_frame(
        session_key,
        identity.device_id, controller_id, session_id,
        {"ssid": "x", "passphrase": "y", "security_mode": "WPA2_PSK"},
    )
    with pytest.raises(cg.CommissioningError):
        for f in frames:
            sm.write_credentials(f)
    assert network.cleanups == 1
    status = sm.read_status()
    assert status["state"] == "FAILED"
    assert status["error_code"] == "NETWORK_FAILED"


def test_window_expiry_ends_session(harness):
    _, _, _, clock, sm = harness
    sm.open_window()
    clock.advance(121)
    sm.tick()
    assert sm.read_status()["state"] == "EXPIRED"
    assert sm.read_status()["error_code"] == "TIMEOUT"


def test_disconnect_ends_session_but_keeps_binding(harness):
    identity, registry, _, _, sm = harness
    central = SimulatedCentral(identity)
    sm.open_window()
    for f in central.frame(cg.MSG_CONTROL, 3, central.begin_json("bc" * 16)):
        sm.write_control(f)
    controller_id = sm.read_status()["controller_id"]
    sm.disconnect()
    assert sm.read_status()["state"] == "CANCELLED"
    assert registry.entries[0].controller_id == controller_id
    assert registry.entries[0].active


def test_single_session_per_window(harness):
    identity, _, _, _, sm = harness
    central = SimulatedCentral(identity)
    sm.open_window()
    for f in central.frame(cg.MSG_CONTROL, 3, central.begin_json("de" * 16)):
        sm.write_control(f)
    with pytest.raises(cg.CommissioningError):
        for f in central.frame(cg.MSG_CONTROL, 4, central.begin_json("de" * 16)):
            sm.write_control(f)


def test_status_schema_shape(harness):
    identity, _, _, _, sm = harness
    central = SimulatedCentral(identity)
    sm.open_window()
    for f in central.frame(cg.MSG_CONTROL, 3, central.begin_json("f0" * 16)):
        sm.write_control(f)
    controller_id = sm.read_status()["controller_id"]
    session_key = central.session_key(controller_id, "f0" * 16)
    frames = central.credentials_frame(
        session_key,
        identity.device_id, controller_id, "f0" * 16,
        {"ssid": "x", "security_mode": "OPEN"},
    )
    for f in frames:
        sm.write_credentials(f)
    status = sm.read_status()
    assert status["protocol"] == "EGO_BLE_WIFI_NETWORK_READY_V1"
    assert set(status) <= {
        "protocol", "state", "error_code", "controller_id",
        "bootstrap_reference", "bootstrap_expires_at",
        "https_origin", "server_certificate_sha256",
    }

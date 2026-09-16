"""Golden vector + unit tests for the commissioning crypto core.

The vector fixture is copied verbatim from JO-ara-dev/ego-contracts
(tests/fixtures/ble-wifi-commissioning-v1/crypto-vector.json, FROZEN
V0.8-BASELINE.3) and pins X25519, HKDF-SHA256, logical header, AAD, nonce,
plaintext, and AES-256-GCM ciphertext bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rk3576" / "collector" / "commissioningd"))

import crypto_glue as cg  # noqa: E402

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "ble-wifi-commissioning-v1"
    / "crypto-vector.json"
)


@pytest.fixture()
def vector():
    return json.loads(FIXTURE.read_text())


# ---------------------------------------------------------------------------
# Golden vector (byte-exact, must pass on host AND on the RK3576 board)


def test_x25519_shared_secret_matches_vector(vector):
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )

    device_private = X25519PrivateKey.from_private_bytes(
        cg.b64u_decode(vector["device_private_key"])
    )
    # sanity: derived public key matches the vector's device public key
    from cryptography.hazmat.primitives import serialization

    derived_public = device_private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    assert cg.b64u_encode(derived_public) == vector["device_public_key"]

    client_public = X25519PublicKey.from_public_bytes(
        cg.b64u_decode(vector["client_public_key"])
    )
    shared = device_private.exchange(client_public)
    assert cg.b64u_encode(shared) == vector["shared_secret"]


def test_hkdf_session_key_matches_vector(vector):
    session_key = cg.hkdf_session_key(
        cg.b64u_decode(vector["shared_secret"]),
        vector["device_id"],
        vector["controller_id"],
        vector["session_id"],
    )
    assert cg.b64u_encode(session_key) == vector["session_key"]


def test_aes_gcm_envelope_matches_vector(vector):
    header = cg.parse_header(cg.b64u_decode(vector["logical_header"]))
    aad = cg.build_aad(
        header, vector["device_id"], vector["controller_id"], vector["session_id"]
    )
    assert aad == cg.b64u_decode(vector["aad"])
    plaintext = cg.aes_gcm_decrypt(
        cg.b64u_decode(vector["session_key"]),
        cg.b64u_decode(vector["nonce"]),
        cg.b64u_decode(vector["ciphertext_and_tag"]),
        aad,
    )
    assert plaintext.decode() == vector["plaintext"]
    creds = cg.parse_credential_plaintext(plaintext)
    assert creds == {
        "ssid": "wifi",
        "passphrase": "secret",
        "security_mode": "WPA2_PSK",
    }


# ---------------------------------------------------------------------------
# Binding proof


def test_binding_proof_roundtrip():
    oob = bytes(range(32))
    proof = cg.binding_proof(oob, "device_rk3576_01", b"c" * 24)
    assert len(proof) == 32
    cg.verify_proof(proof, cg.b64u_encode(proof))  # no exception


def test_binding_proof_rejects_wrong_secret():
    proof = cg.binding_proof(bytes(range(32)), "device_rk3576_01", b"c" * 24)
    with pytest.raises(cg.CommissioningError) as err:
        cg.verify_proof(proof, cg.b64u_encode(b"x" * 32))
    assert err.value.code == "AUTH_FAILED"


def test_binding_proof_challenge_bounds():
    with pytest.raises(cg.CommissioningError):
        cg.binding_proof(bytes(32), "d", b"short")


# ---------------------------------------------------------------------------
# Framing header


def test_header_roundtrip():
    header = cg.FrameHeader(1, cg.MSG_CREDENTIALS, 7, 256, 0, 2)
    parsed = cg.parse_header(cg.pack_header(header))
    assert parsed == header


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: cg.FrameHeader(2, h.message, h.transport_session, h.sequence, h.fragment_index, h.fragment_count),
        lambda h: cg.FrameHeader(h.version, 6, h.transport_session, h.sequence, h.fragment_index, h.fragment_count),
        lambda h: cg.FrameHeader(h.version, h.message, 0, h.sequence, h.fragment_index, h.fragment_count),
        lambda h: cg.FrameHeader(h.version, h.message, h.transport_session, 0, h.fragment_index, h.fragment_count),
        lambda h: cg.FrameHeader(h.version, h.message, h.transport_session, h.sequence, h.fragment_index, 0),
        lambda h: cg.FrameHeader(h.version, h.message, h.transport_session, h.sequence, h.fragment_index, 33),
        lambda h: cg.FrameHeader(h.version, h.message, h.transport_session, h.sequence, 2, 2),
    ],
)
def test_header_fail_closed(mutate):
    base = cg.FrameHeader(1, cg.MSG_CONTROL, 1, 1, 0, 2)
    with pytest.raises(cg.CommissioningError):
        cg.parse_header(cg.pack_header(mutate(base)))


def test_header_truncated():
    with pytest.raises(cg.CommissioningError):
        cg.parse_header(b"\x01" * 5)


# ---------------------------------------------------------------------------
# Reassembler semantics


def _frame(message, ts, seq, idx, count, data=b""):
    return cg.pack_header(cg.FrameHeader(1, message, ts, seq, idx, count)) + data


def test_reassembly_two_fragments():
    r = cg.FragmentReassembler()
    assert r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 2, b'{"a"')) is None
    header, message = r.accept(_frame(cg.MSG_CONTROL, 9, 2, 1, 2, b':1}'))
    assert message == b'{"a":1}'
    assert header.fragment_index == 0


def test_identical_retransmit_is_idempotent():
    r = cg.FragmentReassembler()
    f1 = _frame(cg.MSG_CONTROL, 9, 1, 0, 2, b"AA")
    assert r.accept(f1) is None
    assert r.accept(f1) is None  # same bytes: idempotent, no error
    header, message = r.accept(_frame(cg.MSG_CONTROL, 9, 2, 1, 2, b"BB"))
    assert message == b"AABB"


def test_changed_duplicate_fails():
    r = cg.FragmentReassembler()
    r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 2, b"AA"))
    with pytest.raises(cg.CommissioningError) as err:
        r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 2, b"XX"))  # same seq, new bytes
    assert err.value.code == "REPLAYED"


def test_sequence_gap_fails():
    r = cg.FragmentReassembler()
    r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 2, b"AA"))
    with pytest.raises(cg.CommissioningError):
        r.accept(_frame(cg.MSG_CONTROL, 9, 3, 1, 2, b"BB"))  # skipped seq 2


def test_first_sequence_may_start_nonzero():
    r = cg.FragmentReassembler()
    assert r.accept(_frame(cg.MSG_CONTROL, 9, 7, 0, 1, b"A"))[1] == b"A"


def test_missing_first_fragment_fails():
    r = cg.FragmentReassembler()
    with pytest.raises(cg.CommissioningError):
        r.accept(_frame(cg.MSG_CONTROL, 9, 1, 1, 2, b"BB"))


def test_conflicting_fragment_count_fails():
    r = cg.FragmentReassembler()
    r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 2, b"AA"))
    with pytest.raises(cg.CommissioningError):
        r.accept(_frame(cg.MSG_CONTROL, 9, 2, 1, 3, b"BB"))


def test_oversize_frame_fails():
    r = cg.FragmentReassembler()
    with pytest.raises(cg.CommissioningError):
        r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 1, b"x" * 245))


def test_oversize_message_fails():
    r = cg.FragmentReassembler()
    r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 32, b"x" * 244))
    with pytest.raises(cg.CommissioningError):
        for i in range(2, 33):  # 17th fragment pushes the total past 4096 bytes
            r.accept(_frame(cg.MSG_CONTROL, 9, i, i - 1, 32, b"x" * 244))


def test_independent_transport_sessions():
    r = cg.FragmentReassembler()
    _, m9 = r.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 1, b"A"))
    assert m9 == b"A"
    _, m10 = r.accept(_frame(cg.MSG_CONTROL, 10, 1, 0, 1, b"B"))
    assert m10 == b"B"
    # a new session may reuse sequence numbers
    r2 = cg.FragmentReassembler()
    _, m = r2.accept(_frame(cg.MSG_CONTROL, 9, 1, 0, 1, b"C"))
    assert m == b"C"


# ---------------------------------------------------------------------------
# Strict JSON


def test_strict_json_rejects_trailing():
    with pytest.raises(cg.CommissioningError):
        cg.strict_json_object(b'{"ssid":"x"} {}', ("ssid",))


def test_strict_json_rejects_unknown_fields():
    with pytest.raises(cg.CommissioningError):
        cg.strict_json_object(b'{"ssid":"x","ip":"1.2.3.4"}', ("ssid",))


def test_strict_json_rejects_bad_utf8():
    with pytest.raises(cg.CommissioningError):
        cg.strict_json_object(b'{"ssid":"\xff"}', ("ssid",))


# ---------------------------------------------------------------------------
# Credential plaintext policy


def test_open_mode_rejects_passphrase():
    with pytest.raises(cg.CommissioningError):
        cg.parse_credential_plaintext(
            b'{"ssid":"x","security_mode":"OPEN","passphrase":"y"}'
        )


def test_encrypted_mode_requires_passphrase():
    with pytest.raises(cg.CommissioningError):
        cg.parse_credential_plaintext(
            b'{"ssid":"x","security_mode":"WPA2_PSK"}'
        )


@pytest.mark.parametrize("mode", ["WPA2_PSK", "WPA3_SAE"])
def test_encrypted_mode_requires_nonempty_passphrase(mode):
    ok = cg.parse_credential_plaintext(
        ('{"ssid":"x","security_mode":"%s","passphrase":"12345678"}' % mode).encode()
    )
    assert ok["security_mode"] == mode
    with pytest.raises(cg.CommissioningError):
        cg.parse_credential_plaintext(
            ('{"ssid":"x","security_mode":"%s"}' % mode).encode()
        )
    with pytest.raises(cg.CommissioningError):
        cg.parse_credential_plaintext(
            ('{"ssid":"x","security_mode":"%s","passphrase":""}' % mode).encode()
        )


def test_unknown_security_mode_fails():
    with pytest.raises(cg.CommissioningError):
        cg.parse_credential_plaintext(
            b'{"ssid":"x","security_mode":"WEP","passphrase":"12345678"}'
        )

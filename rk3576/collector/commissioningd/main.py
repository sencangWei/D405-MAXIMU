"""ego-commissioningd daemon entry point.

Single BlueZ GATT/advertisement owner for the board. Wires the K1 physical
gate, the commissioning state machine, the NetworkManager backend, the legacy
advertiser, the GATT application, and the binding IPC gateway.

BlueZ registration order (frozen): GATT application first, then the
advertisement. Failure of either reports discovery unavailable and fails
closed; there is no alternate profile fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import queue
import sys
import threading
import time

from crypto_glue import CommissioningError
from commissioning import CommissioningConfig, CommissioningStateMachine
from identity import load_identity
from registry import ControllerRegistry

LOGGER = logging.getLogger("commissioningd")


def _cert_sha256(path: str) -> str:
    import pathlib

    pem = pathlib.Path(path).read_bytes()
    # DER fingerprint: parse PEM if needed
    if b"-----BEGIN" in pem:
        import base64
        import re

        body = re.search(
            b"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", pem, re.S
        )
        if body is None:
            raise CommissioningError("INVALID")
        der = base64.b64decode(b"".join(body.group(1).split()))
    else:
        der = pem
    return hashlib.sha256(der).hexdigest()


def build_state_machine(args) -> CommissioningStateMachine:
    identity = load_identity(args.identity_path)
    registry = ControllerRegistry(args.registry_path, identity.device_id)
    config = CommissioningConfig(
        https_origin=args.https_origin,
        server_certificate_sha256=_cert_sha256(args.cert_path),
        activation_timeout_seconds=args.activation_timeout,
    )
    from network import NetworkManagerBackend

    network = NetworkManagerBackend(
        args.wifi_device, config.https_origin, config.server_certificate_sha256
    )
    return CommissioningStateMachine(identity, registry, network, config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ego-commissioningd")
    parser.add_argument("--identity-path", default="/etc/ego/ble/device-identity.json")
    parser.add_argument("--registry-path", default="/etc/ego/ble/controller-bindings.json")
    parser.add_argument("--adapter", default="/org/bluez/hci0")
    parser.add_argument("--wifi-device", default="wlan0")
    parser.add_argument("--https-origin", required=True,
                        help="explicit non-loopback Device API https origin")
    parser.add_argument("--cert-path", required=True,
                        help="TLS leaf certificate (PEM or DER) for the origin")
    parser.add_argument("--activation-timeout", type=float, default=120.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        sm = build_state_machine(args)
    except CommissioningError as exc:
        LOGGER.error("startup failed: %s", exc.code)
        return 2

    trigger_events: "queue.Queue" = queue.Queue()

    from advertiser import LegacyAdvertiser
    from ble_gatt import CommissioningGattApp
    from ipc_sock import BindingIpcGateway
    from k1_trigger import COMMISSIONING_HOLD, K1Observer, SHORT_PRESS

    gatt = CommissioningGattApp(sm, args.adapter)
    advertiser = LegacyAdvertiser(sm._identity.discriminator, args.adapter)
    gateway = BindingIpcGateway()

    try:
        gatt.register()  # GATT application first (frozen order)
        advertiser.register()
        gateway.start()
    except CommissioningError as exc:
        LOGGER.error("BLE registration failed: %s", exc.code)
        advertiser.unregister()
        gatt.unregister()
        return 3

    observer = K1Observer(trigger_events)
    k1_thread = threading.Thread(target=observer.run, daemon=True)
    k1_thread.start()

    last_status = ""
    try:
        while True:
            # drain physical trigger events
            try:
                while True:
                    event = trigger_events.get_nowait()
                    if event.kind == COMMISSIONING_HOLD:
                        sm.open_window()
                        LOGGER.info("K1 commissioning hold observed")
                    elif event.kind == SHORT_PRESS:
                        # Export confirmation belongs to the Manager side
                        # (physical-confirmation-issuance); 0.3.0 logs only.
                        LOGGER.info("K1 short press observed (no pending export)")
            except queue.Empty:
                pass

            sm.tick()
            status = sm.read_status()
            encoded = status["state"] + status["error_code"]
            if encoded != last_status:
                last_status = encoded
                gatt.notify_status()
                if status["state"] in ("CONNECTED", "FAILED", "EXPIRED"):
                    LOGGER.info("commissioning status: %s/%s",
                                status["state"], status["error_code"])
            time.sleep(0.5)
    except KeyboardInterrupt:
        LOGGER.info("shutting down")
    finally:
        observer.stop()
        advertiser.unregister()
        gatt.unregister()
        gateway.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

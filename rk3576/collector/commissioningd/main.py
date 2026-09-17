"""ego-commissioningd daemon entry point.

Single BlueZ GATT/advertisement owner for the board. Wires the K1 physical
gate, the commissioning state machine, the NetworkManager backend, the legacy
advertiser, the GATT application, and the binding IPC gateway.

BlueZ registration order (frozen): GATT application first, then the
advertisement. Failure of either reports discovery unavailable and fails
closed; there is no alternate profile fallback.

The process runs a GLib main loop: BlueZ calls back into our registered GATT
objects (GetAll/ReadValue/WriteValue) while RegisterApplication is in flight
and during every central interaction, so D-Bus messages must be dispatched
continuously. Registration itself is scheduled inside the loop for the same
reason.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import queue
import signal
import sys
import threading

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


class CommissioningDaemon:
    def __init__(self, args, state_machine):
        self._args = args
        self._sm = state_machine
        self._events: "queue.Queue" = queue.Queue()
        self._loop = None
        self._observer = None
        self._gatt = None
        self._advertiser = None
        self._gateway = None
        self._last_status = ""
        self._registered = False

    # ------------------------------------------------------------------
    def run(self, selftest_seconds: float = 0.0) -> int:
        from gi.repository import GLib

        self._loop = GLib.MainLoop()
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

        # registration and periodic work both run inside the main loop
        GLib.timeout_add(100, self._register_ble)
        GLib.timeout_add(500, self._tick)
        if selftest_seconds:
            GLib.timeout_add(int(selftest_seconds * 1000), self._finish_selftest)

        self._loop.run()
        self._shutdown()
        return 0 if (not selftest_seconds or self._registered) else 3

    def _on_signal(self, *_args):
        if self._loop is not None:
            self._loop.quit()

    def _finish_selftest(self) -> bool:
        LOGGER.info("selftest window elapsed; registered=%s", self._registered)
        if self._loop is not None:
            self._loop.quit()
        return False

    # ------------------------------------------------------------------
    def _register_ble(self) -> bool:
        from advertiser import LegacyAdvertiser
        from ble_gatt import CommissioningGattApp
        from k1_trigger import K1Observer
        from ipc_sock import BindingIpcGateway

        self._gatt = CommissioningGattApp(self._sm, self._args.adapter)
        self._advertiser = LegacyAdvertiser(
            self._sm._identity.discriminator, self._args.adapter
        )
        self._gateway = BindingIpcGateway(self._args.ipc_path)
        try:
            self._gatt.register()  # GATT application first (frozen order)
            self._advertiser.register()
            self._gateway.start()
        except CommissioningError as exc:
            LOGGER.error("BLE registration failed: %s", exc.code)
            self._advertiser.unregister()
            self._gatt.unregister()
            if self._loop is not None:
                self._loop.quit()
            return False
        self._registered = True
        self._observer = K1Observer(self._events)
        try:
            self._observer.run_in_thread()
        except CommissioningError as exc:
            LOGGER.error("K1 observer unavailable: %s", exc.code)
            self._observer = None
        return False  # one-shot

    def _tick(self) -> bool:
        from k1_trigger import COMMISSIONING_HOLD, SHORT_PRESS

        try:
            while True:
                event = self._events.get_nowait()
                if event.kind == COMMISSIONING_HOLD:
                    self._sm.open_window()
                    LOGGER.info("K1 commissioning hold observed")
                elif event.kind == SHORT_PRESS:
                    # Export confirmation belongs to the Manager side
                    # (physical-confirmation-issuance); 0.3.0 logs only.
                    LOGGER.info("K1 short press observed (no pending export)")
        except queue.Empty:
            pass
        self._sm.tick()
        status = self._sm.read_status()
        encoded = status["state"] + status["error_code"]
        if encoded != self._last_status:
            self._last_status = encoded
            if self._gatt is not None:
                self._gatt.notify_status()
            if status["state"] in ("CONNECTED", "FAILED", "EXPIRED"):
                LOGGER.info(
                    "commissioning status: %s/%s", status["state"], status["error_code"]
                )
        return True  # keep the timeout

    def _shutdown(self) -> None:
        if self._observer is not None:
            self._observer.stop()
        if self._advertiser is not None:
            self._advertiser.unregister()
        if self._gatt is not None:
            self._gatt.unregister()
        if self._gateway is not None:
            self._gateway.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ego-commissioningd")
    parser.add_argument("--identity-path", default="/etc/ego/ble/device-identity.json")
    parser.add_argument("--registry-path", default="/etc/ego/ble/controller-bindings.json")
    parser.add_argument("--adapter", default="/org/bluez/hci0")
    parser.add_argument("--wifi-device", default="wlan0")
    parser.add_argument("--ipc-path", default="/run/ego/ble-binding-v1.sock")
    parser.add_argument("--https-origin", required=True,
                        help="explicit non-loopback Device API https origin")
    parser.add_argument("--cert-path", required=True,
                        help="TLS leaf certificate (PEM or DER) for the origin")
    parser.add_argument("--activation-timeout", type=float, default=120.0)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--selftest-seconds", type=float, default=0.0,
                        help="run the loop for N seconds then exit (verification)")
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

    daemon = CommissioningDaemon(args, sm)
    return daemon.run(selftest_seconds=args.selftest_seconds)


if __name__ == "__main__":
    sys.exit(main())

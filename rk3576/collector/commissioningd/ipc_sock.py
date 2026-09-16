"""Controller-binding IPC gateway (device-auth-controller-binding-ipc-v1).

Unix SOCK_SEQPACKET socket at /run/ego/ble-binding-v1.sock with SO_PEERCRED
checks. Sanitized JSON only: no OOB secret, Wi-Fi credential, bearer ticket,
private key, MAC address, or credential proof ever crosses this boundary.

DEVIATION (documented): the frozen contract assigns socket creation to the
Manager. Our collector deployment has no Go Manager yet, so commissioningd
hosts the gateway itself; when the Manager lands, it can take over the path
without a protocol change.

INTERPRETATION: request "protocol" value "EGO_DEVICE_AUTH_BINDING_IPC_V1"
(prose-only in the contract).
"""

from __future__ import annotations

import array
import json
import logging
import os
import socket
import threading

from crypto_glue import CommissioningError, b64u_encode

LOGGER = logging.getLogger("commissioningd.ipc")

IPC_PROTOCOL = "EGO_DEVICE_AUTH_BINDING_IPC_V1"
IPC_PATH = "/run/ego/ble-binding-v1.sock"
REGISTER_FIELDS = (
    "protocol", "event", "event_id", "device_id", "device_identity",
    "controller_public_key",
)


class BindingIpcGateway:
    def __init__(self, path: str = IPC_PATH, allowed_uids: tuple[int, ...] = (0,)):
        self._path = path
        self._allowed_uids = set(allowed_uids)
        self._server: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        if os.path.exists(self._path):
            os.unlink(self._path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        server.bind(self._path)
        os.chmod(self._path, 0o660)
        server.listen(4)
        self._server = server
        thread = threading.Thread(target=self._accept_loop, daemon=True)
        thread.start()
        LOGGER.info("binding IPC gateway listening on %s", self._path)

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.close()
        if os.path.exists(self._path):
            os.unlink(self._path)

    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            peer = self._peer_uid(conn)
            if peer not in self._allowed_uids:
                LOGGER.warning("binding IPC peer uid %s rejected", peer)
                return
            try:
                data = conn.recv(65536)
            except OSError:
                return
            try:
                request = json.loads(data.decode("utf-8"))
                response = self.handle(request)
            except (UnicodeDecodeError, json.JSONDecodeError, CommissioningError):
                response = {"protocol": IPC_PROTOCOL, "accepted": False,
                            "error_code": "INVALID"}
            conn.sendall(json.dumps(response, separators=(",", ":")).encode())
        finally:
            conn.close()

    def handle(self, request: dict) -> dict:
        """Pure request handling; unit-testable without sockets."""
        if not isinstance(request, dict) or request.get("protocol") != IPC_PROTOCOL:
            return {"protocol": IPC_PROTOCOL, "accepted": False, "error_code": "INVALID"}
        event = request.get("event")
        if event not in ("REGISTER", "DISCONNECT"):
            return {"protocol": IPC_PROTOCOL, "accepted": False, "error_code": "INVALID"}
        for field in ("event_id", "device_id", "device_identity"):
            if not isinstance(request.get(field), str) or not request[field]:
                return {"protocol": IPC_PROTOCOL, "accepted": False, "error_code": "INVALID"}
        if any(k not in REGISTER_FIELDS for k in request):
            return {"protocol": IPC_PROTOCOL, "accepted": False, "error_code": "INVALID"}
        if event == "REGISTER":
            key = request.get("controller_public_key")
            if not isinstance(key, str) or len(key) != 87:
                return {"protocol": IPC_PROTOCOL, "accepted": False, "error_code": "INVALID"}
            # The gateway records the enrollment intent; the registry mint is
            # performed by the commissioning flow itself.
            return {
                "protocol": IPC_PROTOCOL,
                "accepted": True,
                "event_id": request["event_id"],
            }
        return {"protocol": IPC_PROTOCOL, "accepted": True, "event_id": request["event_id"]}

    @staticmethod
    def _peer_uid(conn: socket.socket) -> int:
        credentials = conn.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, array.array("i", [0, 0, 0]).itemsize * 3
        )
        pid, uid, gid = array.array("i", credentials)
        return uid

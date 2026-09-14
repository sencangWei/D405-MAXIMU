#!/usr/bin/env python3
"""EGO-compatible loopback MJPEG preview backed by the D405 RGB stream."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import uuid
from urllib.parse import urlsplit


TTL_SECONDS = 15.0
RENEW_SECONDS = 5.0
HANDOFF_SECONDS = 12.0


class State:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.native = Path(os.environ.get("UMI_NATIVE_COLLECTOR", str(root / "native/bin/umi-record-native")))
        self.output_root = Path(os.environ.get("UMI_RECORDING_ROOT", "/home/pi/umi-recordings")) / "preview"
        self.sdk_serial = os.environ.get("UMI_D405_SDK_SERIAL", "260322273737")
        self.usb_serial = os.environ.get("UMI_D405_USB_SERIAL", "260323071293")
        self.source_port = int(os.environ.get("UMI_PREVIEW_SOURCE_PORT", "18081"))
        self.lock = threading.RLock()
        self.session_id: str | None = None
        self.device_id: str | None = None
        self.expires_at = 0.0
        self.process: subprocess.Popen[bytes] | None = None
        self.source_starting = False
        self.external_source_expected_until = 0.0
        self.frames = 0
        self.clients = 0

    def source_listens(self) -> bool:
        """Inspect LISTEN state without consuming the source's first client."""
        expected = f"{self.source_port:04X}"
        try:
            for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
                for line in table.read_text(encoding="ascii").splitlines()[1:]:
                    fields = line.split()
                    if len(fields) >= 4 and fields[1].rsplit(":", 1)[-1] == expected and fields[3] == "0A":
                        return True
        except OSError:
            return False
        return False

    def ensure_source(self) -> None:
        if self.source_listens():
            self.wait_source_frame()
            return
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("preview source is starting but not reachable")
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = (self.output_root / "preview-service.log").open("ab", buffering=0)
        command = [
            str(self.native),
            "--output-root", str(self.output_root),
            "--duration", "1800",
            "--preview-only",
            "--preview-mjpeg-port", str(self.source_port),
            "--d405-sdk-serial", self.sdk_serial,
            "--d405-usb-serial", self.usb_serial,
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            close_fds=True,
        )
        log.close()
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if self.source_listens():
                self.wait_source_frame()
                return
            if self.process.poll() is not None:
                raise RuntimeError(f"preview collector exited with {self.process.returncode}")
            time.sleep(0.05)
        self.stop_owned_source()
        raise RuntimeError("preview source did not become ready")

    def wait_source_frame(self) -> None:
        """Do not advertise a session until one complete JPEG is available."""
        deadline = time.monotonic() + 6.0
        with socket.create_connection(("127.0.0.1", self.source_port), timeout=1.0) as source:
            source.settimeout(1.0)
            payload = bytearray()
            while time.monotonic() < deadline and len(payload) <= 2 * 1024 * 1024:
                try:
                    block = source.recv(64 * 1024)
                except socket.timeout:
                    continue
                if not block:
                    break
                payload.extend(block)
                start = payload.find(b"\xff\xd8")
                if start >= 0 and payload.find(b"\xff\xd9", start + 2) >= 0:
                    return
        raise RuntimeError("preview source produced no complete JPEG")

    def stop_owned_source(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def prepare_recording_handoff(self, device_id: str) -> dict:
        with self.lock:
            if self.session_id is None:
                return {"released": True, "session_id": None}
            if device_id != self.device_id:
                raise RuntimeError("preview session belongs to another device")
            self.external_source_expected_until = time.monotonic() + HANDOFF_SECONDS
            session_id = self.session_id
            self.stop_owned_source()
            return {"released": True, "session_id": session_id}

    def request_owned_source_start(self) -> None:
        with self.lock:
            if self.source_starting or self.source_listens():
                return
            if self.process is not None and self.process.poll() is None:
                return
            self.source_starting = True

        def start() -> None:
            try:
                self.ensure_source()
            except Exception:
                pass
            finally:
                with self.lock:
                    self.source_starting = False

        threading.Thread(target=start, name="umi-preview-source-start", daemon=True).start()

    def expire(self) -> None:
        with self.lock:
            if self.session_id is not None and time.time() >= self.expires_at:
                self.session_id = None
                self.device_id = None
                self.expires_at = 0.0
                self.stop_owned_source()

    def health(self) -> dict:
        self.expire()
        with self.lock:
            return {
                "ok": True,
                "session_id": self.session_id,
                "expires_at": self.expires_at if self.session_id else None,
                "clients": self.clients,
                "frames": self.frames,
                "ttl_seconds": TTL_SECONDS,
                "renew_seconds": RENEW_SECONDS,
            }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> State:
        return self.server.preview_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send_json(200, self.state.health())
            return
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "sessions" or parts[2] != "stereo.mjpg":
            self.send_json(404, {"error": {"code": "NOT_FOUND"}})
            return
        session_id = parts[1]
        self.state.expire()
        with self.state.lock:
            if session_id != self.state.session_id:
                self.send_json(404, {"error": {"code": "PREVIEW_SESSION_NOT_FOUND"}})
                return
            self.state.clients += 1
        upstream = self.connect_source(session_id)
        if upstream is None:
            with self.state.lock:
                self.state.clients -= 1
            self.send_json(503, {"error": {"code": "PREVIEW_SOURCE_UNAVAILABLE"}})
            return
        last_jpeg: bytes | None = None
        jpeg_buffer = bytearray()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                with self.state.lock:
                    if session_id != self.state.session_id or time.time() >= self.state.expires_at:
                        break
                try:
                    block = upstream.recv(64 * 1024)
                except socket.timeout:
                    if last_jpeg is not None:
                        self.write_preview_chunk(self.jpeg_part(last_jpeg))
                        with self.state.lock:
                            self.state.frames += 1
                    continue
                if not block:
                    upstream.close()
                    upstream = self.connect_source(session_id, heartbeat=last_jpeg)
                    if upstream is None:
                        break
                    jpeg_buffer.clear()
                    continue
                jpeg_buffer.extend(block)
                while True:
                    start = jpeg_buffer.find(b"\xff\xd8")
                    end = jpeg_buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start < 0 or end < 0:
                        if len(jpeg_buffer) > 2 * 1024 * 1024:
                            jpeg_buffer.clear()
                        break
                    last_jpeg = bytes(jpeg_buffer[start:end + 2])
                    del jpeg_buffer[:end + 2]
                    self.write_preview_chunk(self.jpeg_part(last_jpeg))
                    with self.state.lock:
                        self.state.frames += 1
                        if self.state.process is None:
                            self.state.external_source_expected_until = 0.0
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            pass
        finally:
            if upstream is not None:
                upstream.close()
            with self.state.lock:
                self.state.clients -= 1
            self.close_connection = True

    def session_is_active(self, session_id: str) -> bool:
        self.state.expire()
        with self.state.lock:
            return session_id == self.state.session_id and time.time() < self.state.expires_at

    def connect_source(self, session_id: str, heartbeat: bytes | None = None) -> socket.socket | None:
        while self.session_is_active(session_id):
            try:
                upstream = socket.create_connection(("127.0.0.1", self.state.source_port), timeout=0.5)
                upstream.settimeout(0.5)
                return upstream
            except OSError:
                pass
            with self.state.lock:
                handoff_pending = time.monotonic() < self.state.external_source_expected_until
            if not handoff_pending and not self.state.source_listens():
                self.state.request_owned_source_start()
            if heartbeat is not None:
                try:
                    self.write_preview_chunk(self.jpeg_part(heartbeat))
                    with self.state.lock:
                        self.state.frames += 1
                except (BrokenPipeError, ConnectionError, OSError):
                    return None
            time.sleep(0.2)
        return None

    @staticmethod
    def jpeg_part(jpeg: bytes) -> bytes:
        return (
            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(jpeg)).encode("ascii")
            + b"\r\n\r\n"
            + jpeg
            + b"\r\n"
        )

    def write_preview_chunk(self, block: bytes) -> None:
        self.wfile.write(f"{len(block):x}\r\n".encode("ascii"))
        self.wfile.write(block)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def do_POST(self) -> None:
        try:
            body = self.read_json()
        except ValueError as error:
            self.send_json(400, {"error": {"code": "INVALID_REQUEST", "message": str(error)}})
            return
        path = urlsplit(self.path).path
        if path == "/internal/recording-handoff":
            device_id = body.get("device_id")
            if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
                self.send_json(400, {"error": {"code": "INVALID_DEVICE_ID"}})
                return
            try:
                result = self.state.prepare_recording_handoff(device_id)
            except RuntimeError as error:
                self.send_json(409, {"error": {"code": "PREVIEW_HANDOFF_FAILED", "message": str(error)}})
                return
            self.send_json(200, result)
            return
        if path == "/sessions":
            device_id = body.get("device_id")
            if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
                self.send_json(400, {"error": {"code": "INVALID_DEVICE_ID"}})
                return
            with self.state.lock:
                self.state.expire()
                if self.state.session_id is not None:
                    self.send_json(409, {"error": {"code": "PREVIEW_BUSY"}})
                    return
                try:
                    self.state.ensure_source()
                except Exception as error:
                    self.send_json(503, {"error": {"code": "PREVIEW_START_FAILED", "message": str(error)}})
                    return
                self.state.session_id = uuid.uuid4().hex
                self.state.device_id = device_id
                self.state.expires_at = time.time() + TTL_SECONDS
                self.send_json(201, self.session_payload())
            return
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "sessions" or parts[2] not in {"renew", "close"}:
            self.send_json(404, {"error": {"code": "NOT_FOUND"}})
            return
        with self.state.lock:
            if parts[1] != self.state.session_id:
                self.send_json(404, {"error": {"code": "PREVIEW_SESSION_NOT_FOUND"}})
                return
            if parts[2] == "renew":
                self.state.expires_at = time.time() + TTL_SECONDS
                self.send_json(200, self.session_payload())
            else:
                closed = self.state.session_id
                self.state.session_id = None
                self.state.device_id = None
                self.state.expires_at = 0.0
                self.send_json(200, {"session_id": closed, "reason": body.get("reason", "CLIENT_CLOSED")})
                self.state.stop_owned_source()

    def session_payload(self) -> dict:
        session_id = self.state.session_id
        return {
            "session_id": session_id,
            "expires_at": self.state.expires_at,
            "left_stream_url": f"/sessions/{session_id}/cam0.mp4",
            "right_stream_url": f"/sessions/{session_id}/cam1.mp4",
            "stereo_stream_url": f"/sessions/{session_id}/stereo.mjpg",
        }

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length is invalid") from error
        if length < 0 or length > 4096:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("request body is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("preview must bind to loopback")
    state = State()
    server = Server((args.host, args.port), Handler)
    server.preview_state = state  # type: ignore[attr-defined]
    def stop_server(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        with state.lock:
            state.stop_owned_source()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""K1 physical trigger observer (physical-trigger-v1).

Resolution rule (frozen): udev ID_PATH=platform-gpio-keys + DT label K1 +
Linux key code 0x101. The device node name is never hard-coded.

One observer serializes both event kinds: SHORT_PRESS (Export confirmation)
and COMMISSIONING_HOLD (>=5 continuous seconds, opens the 120 s window).
"""

from __future__ import annotations

import logging
import queue
import select
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from crypto_glue import K1_HOLD_SECONDS, CommissioningError

LOGGER = logging.getLogger("commissioningd.k1")

KEY_CODE_K1 = 0x101
INPUT_EVENT = struct.Struct("llHHi")  # 64-bit timeval + type/code/value
EV_KEY = 0x01
KEY_PRESS = 1
KEY_RELEASE = 0
DEBOUNCE_SECONDS = 0.05

SHORT_PRESS = "SHORT_PRESS"
COMMISSIONING_HOLD = "COMMISSIONING_HOLD"


@dataclass(frozen=True)
class TriggerEvent:
    kind: str
    observed_at: float


def classify_press(press_seconds: float) -> str | None:
    """Map one debounced press duration to an event kind (None = too short)."""
    if press_seconds >= K1_HOLD_SECONDS:
        return COMMISSIONING_HOLD
    if press_seconds >= DEBOUNCE_SECONDS:
        return SHORT_PRESS
    return None


def resolve_k1_device() -> Path:
    """Find the K1 evdev node without hard-coding an event number."""
    for event in sorted(Path("/dev/input").glob("event*")):
        try:
            info = subprocess.run(
                ["udevadm", "info", str(event)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if "ID_PATH=platform-gpio-keys" not in info:
            continue
        if not _reports_key_code(event, KEY_CODE_K1):
            continue
        label = _device_tree_label()
        if label is not None and label != "K1":
            continue
        return event
    raise CommissioningError("UNAVAILABLE")


def _reports_key_code(event: Path, code: int) -> bool:
    import array
    import fcntl

    try:
        bitmap = array.array("B", [0] * 96)
        fcntl.ioctl(open(event, "rb"), 0x80604521, bitmap, True)  # EVIOCGBIT(EV_KEY)
    except OSError:
        return False
    return bool(bitmap[code >> 3] >> (code & 7) & 1)


def _device_tree_label() -> str | None:
    """DT label of the gpio-keys child, when resolvable."""
    for base in (
        Path("/sys/firmware/devicetree/base/gpio-keys"),
        Path("/proc/device-tree/gpio-keys"),
    ):
        for child in sorted(base.glob("button*")):
            label = child / "label"
            try:
                return label.read_bytes().decode("utf-8", "strict").rstrip("\x00")
            except OSError:
                continue
    return None


class K1Observer:
    """Blocking observer; emits TriggerEvent objects into ``events``."""

    def __init__(self, events: "queue.Queue[TriggerEvent]", device: Path | None = None):
        self._events = events
        self._device = device
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        device = self._device or resolve_k1_device()
        LOGGER.info("K1 observer using %s", device)
        fd = open(device, "rb", buffering=0)
        try:
            self._loop(fd)
        finally:
            fd.close()

    def _loop(self, fd) -> None:
        pressed_at: float | None = None
        hold_reported = False
        while not self._stop.is_set():
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                # periodic check so a continuing hold fires at the 5 s mark
                # even without further evdev traffic
                now = time.monotonic()
                if (
                    pressed_at is not None
                    and not hold_reported
                    and now - pressed_at >= K1_HOLD_SECONDS
                ):
                    hold_reported = True
                    self._events.put(TriggerEvent(COMMISSIONING_HOLD, time.time()))
                continue
            raw = fd.read(INPUT_EVENT.size)
            if raw is None or len(raw) < INPUT_EVENT.size:
                continue
            _, _, ev_type, code, value = INPUT_EVENT.unpack(raw[: INPUT_EVENT.size])
            if ev_type != EV_KEY or code != KEY_CODE_K1:
                continue
            now = time.monotonic()
            if value == KEY_PRESS and pressed_at is None:
                pressed_at = now
                hold_reported = False
            elif value == KEY_RELEASE and pressed_at is not None:
                duration = now - pressed_at
                kind = None if hold_reported else classify_press(duration)
                pressed_at = None
                if kind is not None:
                    self._events.put(TriggerEvent(kind, time.time()))

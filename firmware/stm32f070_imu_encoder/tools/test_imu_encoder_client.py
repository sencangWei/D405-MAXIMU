import collections
import threading
import time
import unittest
from unittest import mock

from tools.imu_encoder_client import ImuEncoderClient, ImuEncoderClientError
from tools.test_imu_encoder_protocol import make_packet


class FakeSerial:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = collections.deque(chunks or [])
        self._lock = threading.Lock()
        self.closed = False
        self.read_sizes: list[int] = []

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._chunks[0]) if self._chunks else 0

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        with self._lock:
            if self.closed:
                return b""
            if self._chunks:
                return self._chunks.popleft()
        time.sleep(0.002)
        return b""

    def close(self) -> None:
        self.closed = True


class FailingSerial(FakeSerial):
    def read(self, _size: int) -> bytes:
        raise OSError("serial cable removed")


def wait_for_frames(client: ImuEncoderClient, count: int, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.stats.frames_received >= count:
            return
        time.sleep(0.002)
    raise AssertionError(f"timed out waiting for {count} frames")


class ImuEncoderClientTests(unittest.TestCase):
    def test_rejects_non_positive_queue_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "queue_size must be positive"):
            ImuEncoderClient("TEST", queue_size=0)

    def test_context_manager_reads_split_packet_and_closes_serial(self) -> None:
        packet = make_packet(sequence=7)
        fake = FakeSerial([packet[:11], packet[11:]])

        with ImuEncoderClient("TEST", serial_factory=lambda **_: fake) as client:
            sample = client.read(timeout=1.0)
            self.assertIsNotNone(sample)
            self.assertEqual(7, sample.sequence)
            self.assertEqual(sample, client.latest())

        self.assertTrue(fake.closed)

    def test_read_returns_none_on_timeout(self) -> None:
        fake = FakeSerial()
        client = ImuEncoderClient("TEST", serial_factory=lambda **_: fake)
        client.start()
        try:
            self.assertIsNone(client.read(timeout=0.02))
        finally:
            client.close()

    def test_reads_available_bytes_without_waiting_for_a_large_block(self) -> None:
        packet = make_packet(sequence=12)
        fake = FakeSerial([packet])

        with ImuEncoderClient("TEST", serial_factory=lambda **_: fake) as client:
            sample = client.read(timeout=1.0)

        self.assertEqual(12, sample.sequence)
        self.assertEqual(len(packet), fake.read_sizes[0])

    def test_queue_full_discards_oldest_sample(self) -> None:
        fake = FakeSerial(
            [
                make_packet(sequence=1)
                + make_packet(sequence=2)
                + make_packet(sequence=3)
            ]
        )
        client = ImuEncoderClient(
            "TEST", queue_size=2, serial_factory=lambda **_: fake
        )
        client.start()
        try:
            wait_for_frames(client, 3)
            first = client.read(timeout=0.1)
            second = client.read(timeout=0.1)

            self.assertEqual([2, 3], [first.sequence, second.sequence])
            self.assertEqual(1, client.stats.consumer_queue_drops)
        finally:
            client.close()

    def test_receive_error_is_reported_after_queued_samples(self) -> None:
        fake = FailingSerial()
        client = ImuEncoderClient("TEST", serial_factory=lambda **_: fake)
        client.start()
        try:
            with self.assertRaisesRegex(ImuEncoderClientError, "serial cable removed"):
                client.read(timeout=0.2)
        finally:
            client.close()

    def test_close_is_idempotent(self) -> None:
        fake = FakeSerial()
        client = ImuEncoderClient("TEST", serial_factory=lambda **_: fake)
        client.start()

        client.close()
        client.close()

        self.assertTrue(fake.closed)

    def test_close_ends_unbounded_read_after_a_full_queue_is_drained(self) -> None:
        fake = FakeSerial([make_packet(sequence=9)])
        client = ImuEncoderClient(
            "TEST", queue_size=1, serial_factory=lambda **_: fake
        )
        client.start()
        wait_for_frames(client, 1)
        client.close()
        self.assertEqual(9, client.read(timeout=0).sequence)

        result: list[object] = []
        reader = threading.Thread(target=lambda: result.append(client.read()), daemon=True)
        reader.start()
        reader.join(timeout=0.1)

        self.assertFalse(reader.is_alive(), "read() remained blocked after close()")
        self.assertEqual([None], result)

    def test_each_sample_gets_its_own_pc_parse_timestamp(self) -> None:
        fake = FakeSerial([make_packet(sequence=10) + make_packet(sequence=11)])

        with mock.patch(
            "tools.imu_encoder_protocol.time.time_ns", side_effect=[100, 200]
        ):
            with ImuEncoderClient("TEST", serial_factory=lambda **_: fake) as client:
                first = client.read(timeout=1.0)
                second = client.read(timeout=1.0)

        self.assertEqual([100, 200], [first.pc_unix_ns, second.pc_unix_ns])

    def test_same_client_can_start_a_fresh_session_after_close(self) -> None:
        serial_ports = collections.deque(
            [FakeSerial([make_packet(sequence=1)]), FakeSerial([make_packet(sequence=2)])]
        )
        client = ImuEncoderClient(
            "TEST", serial_factory=lambda **_: serial_ports.popleft()
        )

        client.start()
        self.assertEqual(1, client.read(timeout=1.0).sequence)
        client.close()
        client.start()
        try:
            self.assertEqual(2, client.read(timeout=1.0).sequence)
            self.assertEqual(1, client.stats.frames_received)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()

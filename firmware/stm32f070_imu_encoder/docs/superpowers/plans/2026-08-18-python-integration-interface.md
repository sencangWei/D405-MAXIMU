# Python Integration Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable Python API that reads the STM32F070 63-byte IMU + AS5047P stream and returns validated, structured samples without depending on an APP framework.

**Architecture:** A pure protocol module owns binary framing, CRC, data types, flags, and timer rollover. A separate serial client owns the background reader and a bounded consumer queue. The existing command-line capture tool imports the protocol module so the wire format has one implementation.

**Tech Stack:** Python 3.10+, standard library (`dataclasses`, `enum`, `queue`, `threading`, `struct`), pyserial at runtime, unittest for tests.

## Global Constraints

- Do not modify or depend on the downstream APP or a GUI framework.
- Default serial settings are 921600 baud, 8N1, no flow control.
- Preserve the fixed protocol: sync `A5 5A`, version 1, total length 63, little-endian fields, CRC-16/CCITT-FALSE over bytes 0 through 60.
- Return valid IMU samples even when encoder flags report invalid data.
- A slow consumer must not block the serial receive thread; discard the oldest queued sample and count the drop.
- Keep `tools/combined_capture.py` CLI behavior compatible.

---

### Task 1: Extract and strengthen the protocol module

**Files:**
- Create: `firmware/stm32f070_imu_encoder/tools/imu_encoder_protocol.py`
- Create: `firmware/stm32f070_imu_encoder/tools/test_imu_encoder_protocol.py`
- Modify: `firmware/stm32f070_imu_encoder/tools/combined_capture.py`
- Modify: `firmware/stm32f070_imu_encoder/tools/test_combined_capture.py`

**Interfaces:**
- Produces: `PacketFlag`, `ImuData`, `CombinedSample`, `PacketParser`, `TimerUnwrapper`, `crc16_ccitt_false()`, `delta_u32()`.
- `PacketParser.feed(data: bytes, pc_unix_ns: int | None = None) -> list[CombinedSample]`.

- [ ] **Step 1: Write failing protocol tests**

Add tests that construct a valid 63-byte packet and assert named IMU fields, all flag properties, raw angle, degree conversion, PC timestamp, split input, concatenated packets, CRC recovery, unsupported header recovery, and timer rollover.

```python
sample = PacketParser().feed(make_packet(), pc_unix_ns=123456789)[0]
self.assertEqual(123456789, sample.pc_unix_ns)
self.assertEqual(1.0, sample.imu.gx)
self.assertTrue(sample.imu_valid)
self.assertAlmostEqual(0x1234 * 360.0 / 16384.0, sample.encoder_angle_deg)
```

- [ ] **Step 2: Verify the new tests fail**

Run `python -m unittest tools.test_imu_encoder_protocol -v`.

Expected: import failure because `tools.imu_encoder_protocol` does not exist.

- [ ] **Step 3: Implement the pure protocol module**

Define the stable types and parser without importing pyserial:

```python
class PacketFlag(enum.IntFlag):
    IMU_VALID = 1 << 0
    ENCODER_VALID = 1 << 1
    ENCODER_ERROR = 1 << 2
    ENCODER_PARITY_ERROR = 1 << 3
    IMU_COUNTER_GAP = 1 << 4
    IMU_QUEUE_OVERFLOW = 1 << 5
    PC_TX_QUEUE_OVERFLOW = 1 << 6

@dataclasses.dataclass(frozen=True)
class ImuData:
    gx: float
    gy: float
    gz: float
    ax: float
    ay: float
    az: float
    temperature_c: float

@dataclasses.dataclass(frozen=True)
class CombinedSample:
    pc_unix_ns: int
    flags: PacketFlag
    sequence: int
    imu_first_byte_rx_us: int
    encoder_read_us: int
    imu_counter: int
    encoder_response: int
    imu: ImuData
    imu_frame: bytes
    raw_packet: bytes
```

Add properties for `imu_valid`, `encoder_valid`, every error flag, `encoder_raw`, `encoder_angle_deg`, `sensor_gap_us`, and the legacy aliases `encoder_degrees` and `imu_values` needed by the CLI.

- [ ] **Step 4: Replace duplicate protocol definitions in the CLI**

Import the shared names in `combined_capture.py` and retain `capture_stream()`, CSV formatting, argument parsing, and `main()` in that file. Change CSV access only where required by the new type.

- [ ] **Step 5: Run protocol and legacy CLI tests**

Run `python -m unittest tools.test_imu_encoder_protocol tools.test_combined_capture -v`.

Expected: all protocol and existing capture tests pass.

### Task 2: Add the background serial client

**Files:**
- Create: `firmware/stm32f070_imu_encoder/tools/imu_encoder_client.py`
- Create: `firmware/stm32f070_imu_encoder/tools/test_imu_encoder_client.py`

**Interfaces:**
- Consumes: `PacketParser`, `CombinedSample` from Task 1.
- Produces: `ClientStats`, `ImuEncoderClient`, `ImuEncoderClientError`.
- `start() -> None`, `read(timeout: float | None = None) -> CombinedSample | None`, `latest() -> CombinedSample | None`, `close() -> None`.

- [ ] **Step 1: Write failing client tests with a fake serial object**

The fake returns controlled byte chunks and records whether `close()` was called. Tests assert normal background reading, `None` on timeout, latest sample, oldest-item eviction at queue capacity, exception propagation after receive failure, idempotent close, and context-manager cleanup.

```python
with ImuEncoderClient("TEST", serial_factory=lambda **_: fake) as client:
    sample = client.read(timeout=1.0)
    self.assertEqual(7, sample.sequence)
self.assertTrue(fake.closed)
```

- [ ] **Step 2: Verify client tests fail**

Run `python -m unittest tools.test_imu_encoder_client -v`.

Expected: import failure because `tools.imu_encoder_client` does not exist.

- [ ] **Step 3: Implement the minimal client**

Use lazy pyserial import in the default serial factory, one daemon receive thread, `threading.Event`, and `queue.Queue(maxsize=...)`. On queue full, remove one oldest sample, increment `consumer_queue_drops`, then enqueue the newest sample. Store the first read exception and raise `ImuEncoderClientError` from `read()` once no already-decoded sample remains.

```python
client = ImuEncoderClient(port="COM7", baudrate=921600, queue_size=2048)
client.start()
sample = client.read(timeout=1.0)
client.close()
```

- [ ] **Step 4: Run client and protocol tests**

Run `python -m unittest tools.test_imu_encoder_client tools.test_imu_encoder_protocol tools.test_combined_capture -v`.

Expected: all tests pass without a physical serial device.

### Task 3: Add the handoff example and interface document

**Files:**
- Create: `firmware/stm32f070_imu_encoder/tools/example_imu_encoder.py`
- Create: `firmware/stm32f070_imu_encoder/docs/IMU_ENCODER_PYTHON_INTERFACE.md`
- Modify: `firmware/stm32f070_imu_encoder/README.md`

**Interfaces:**
- Consumes: `ImuEncoderClient` and `CombinedSample` from Tasks 1–2.
- Produces: a copyable integration example and language-neutral wire protocol reference.

- [ ] **Step 1: Write the executable example**

Parse required `--port` and optional `--baud`; open the client with a context manager; print at 10 Hz while consuming all samples; display angle only when `encoder_valid`; print flags and counters; stop cleanly on `Ctrl+C`.

- [ ] **Step 2: Write the downstream integration document**

Document installation (`pip install pyserial`), USB/COM identification, constructor and method contracts, threading rule, minimal use, all 63 byte offsets, little-endian encoding, CRC parameters, IMU raw frame offsets, flag meanings, timestamp semantics and rollover, angle conversion, disconnect behavior, and recommended stored fields.

- [ ] **Step 3: Link the interface from the firmware README**

Add a short “Python 接口模块” section linking the new document and example without replacing the existing CLI instructions.

- [ ] **Step 4: Verify example imports and help output**

Run `python tools/example_imu_encoder.py --help`.

Expected: exit code 0 and help containing required `--port`.

### Task 4: Full regression verification and handoff update

**Files:**
- Modify: `D:/semg.claude/imu_carrier_v4_backup/HANDOFF_TO_CODEX.md`

**Interfaces:**
- Consumes: all outputs from Tasks 1–3.
- Produces: reproducible verification evidence and updated project handoff.

- [ ] **Step 1: Run all Python tests**

Run `python -m unittest discover -s tools -p "test_*.py" -v`.

Expected: all tests pass.

- [ ] **Step 2: Run firmware native tests and target build**

Run `pio test -e native` and `pio run -e stm32f070f6p6`.

Expected: native protocol/pipeline tests pass and STM32 target build succeeds.

- [ ] **Step 3: Inspect the focused diff**

Run `git diff --check` and `git status --short`.

Expected: no whitespace errors; only the Python interface, tests, docs, README, design/plan, and project handoff are changed.

- [ ] **Step 4: Update the cross-session handoff**

Record the Python module paths, public API, test commands, software-only verification result, and remaining physical-board acceptance requirement. Do not claim hardware validation before a CP2102N board is connected.

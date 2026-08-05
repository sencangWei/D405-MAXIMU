from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ego_vio.config import AppConfig, CameraConfig, IMUConfig, UnitConfig, load_config
from ego_vio.imu.calibration import IMUCalibration
from ego_vio.imu.imu_reader import ImuSample
from ego_vio.runtime import Runtime, UnitRuntime


def test_calibration_changes_values_but_preserves_sample_identity(tmp_path):
    calibration_path = tmp_path / "imu.yaml"
    calibration_path.write_text(
        """
calibration_id: test-calibration
accelerometer:
  matrix: [[2, 0, 0], [0, 3, 0], [0, 0, 4]]
  offset_g: [0.1, 0.2, 0.3]
gyroscope:
  matrix: [[5, 0, 0], [0, 6, 0], [0, 0, 7]]
  bias_deg_s: [1, 2, 3]
""",
        encoding="utf-8",
    )
    calibration = IMUCalibration.load(calibration_path)
    raw = ImuSample(
        ts=12.5,
        rx_time=12.6,
        counter=42,
        gx=2.0,
        gy=4.0,
        gz=6.0,
        ax=1.0,
        ay=2.0,
        az=3.0,
        temp=24.0,
    )

    corrected = calibration.apply(raw)

    assert np.allclose([corrected.ax, corrected.ay, corrected.az], [2.1, 6.2, 12.3])
    assert np.allclose([corrected.gx, corrected.gy, corrected.gz], [5.0, 12.0, 21.0])
    assert (corrected.ts, corrected.rx_time, corrected.counter, corrected.temp) == (
        raw.ts,
        raw.rx_time,
        raw.counter,
        raw.temp,
    )
    assert corrected is not raw


def test_config_resolves_relative_calibration_path(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "devices.yaml"
    config_path.write_text(
        """
units:
  - name: left
    role: realtime_vio
    imu:
      port: /dev/test
      calibration: imu.yaml
    camera: {}
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.units[0].imu.calibration == str((config_dir / "imu.yaml").resolve())


def test_runtime_records_raw_and_feeds_corrected_sample():
    calibration = IMUCalibration(
        calibration_id="runtime-test",
        accel_matrix=np.eye(3) * 2.0,
        accel_offset_g=np.zeros(3),
        gyro_matrix=np.eye(3) * 3.0,
        gyro_bias_deg_s=np.ones(3),
    )
    raw = ImuSample(1.0, 5, 2.0, 3.0, 4.0, 0.1, 0.2, 0.3, 24.0, 1.1)

    class Sink:
        def __init__(self):
            self.sample = None

        def put_imu(self, sample):
            self.sample = sample

        def feed_imu(self, sample):
            self.sample = sample
            return None

    class Recorder:
        def __init__(self, sink):
            self.sink = sink

        def get(self, _unit):
            return self.sink

    raw_sink = Sink()
    vio_sink = Sink()
    unit_config = UnitConfig(
        name="left",
        role="realtime_vio",
        imu=IMUConfig(port="/dev/test"),
        camera=CameraConfig(),
    )
    runtime = Runtime(AppConfig())
    runtime.recorder = Recorder(raw_sink)
    runtime.units["left"] = UnitRuntime(
        cfg=unit_config,
        vio=vio_sink,
        imu_calibration=calibration,
    )

    runtime._on_imu("left", raw)

    assert raw_sink.sample is raw
    assert np.allclose(
        [vio_sink.sample.ax, vio_sink.sample.ay, vio_sink.sample.az],
        [0.2, 0.4, 0.6],
    )
    assert np.allclose(
        [vio_sink.sample.gx, vio_sink.sample.gy, vio_sink.sample.gz],
        [3.0, 6.0, 9.0],
    )

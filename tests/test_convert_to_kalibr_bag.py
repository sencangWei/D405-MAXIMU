import csv

import numpy as np

from scripts.convert_to_kalibr_bag import (
    read_camera_ts,
    read_imu_arrival_timestamps,
    select_imu_timestamp_fit,
)


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_legacy_wall_timestamps_are_aligned_to_camera_monotonic_clock(tmp_path):
    camera_csv = tmp_path / "camera.csv"
    imu_csv = tmp_path / "imu.csv"
    _write_csv(
        camera_csv,
        ["ts_mono", "ts_wall"],
        [
            {"ts_mono": 10.0, "ts_wall": 1010.0},
            {"ts_mono": 11.0, "ts_wall": 1011.0},
        ],
    )
    _write_csv(
        imu_csv,
        ["counter", "ts_mono", "ts_wall"],
        [
            {"counter": 1, "ts_mono": 9.5, "ts_wall": 1010.25},
            {"counter": 2, "ts_mono": 9.6, "ts_wall": 1010.50},
        ],
    )

    timestamps, source = read_imu_arrival_timestamps(
        imu_csv, camera_csv, [1, 2]
    )

    assert source == "legacy_wall"
    np.testing.assert_allclose(timestamps, [10.25, 10.50])


def test_legacy_camera_timestamps_preserve_inferred_dropped_frames(tmp_path):
    camera_csv = tmp_path / "camera.csv"
    _write_csv(
        camera_csv,
        ["idx", "ts_mono"],
        [
            {"idx": 1, "ts_mono": 10.000},
            {"idx": 2, "ts_mono": 10.033},
            {"idx": 3, "ts_mono": 10.099},
        ],
    )

    rows = read_camera_ts(camera_csv)

    assert [frame_number for _, frame_number, _ in rows] == [1, 2, 4]


def test_timestamp_fit_prefers_stored_clock_over_jittery_writer_clock():
    samples = [
        {"counter": counter, "ts": 20.0 + index / 400.0}
        for index, counter in enumerate(range(1000, 1060))
    ]
    writer_timestamps = [
        20.0 + index / 400.0 + (0.05 if index % 2 else 0.0)
        for index in range(60)
    ]

    fitted, info, source = select_imu_timestamp_fit(
        samples, writer_timestamps, "legacy_wall"
    )

    assert source == "stored_ts"
    assert info["sigma_ms"] < 1e-6
    np.testing.assert_allclose(np.diff(fitted), 1.0 / 400.0, atol=1e-9)

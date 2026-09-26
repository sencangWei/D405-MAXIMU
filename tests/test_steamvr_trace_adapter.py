import csv
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_SCRIPT = ROOT / "scripts/steamvr_trace_adapter.py"
ADAPTER_SPEC = importlib.util.spec_from_file_location(
    "steamvr_trace_adapter", ADAPTER_SCRIPT
)
ADAPTER = importlib.util.module_from_spec(ADAPTER_SPEC)
assert ADAPTER_SPEC.loader is not None
ADAPTER_SPEC.loader.exec_module(ADAPTER)

CALIB_SCRIPT = ROOT / "scripts/calibrate_lighthouse_umi.py"
CALIB_SPEC = importlib.util.spec_from_file_location(
    "calibrate_lighthouse_umi", CALIB_SCRIPT
)
CALIB = importlib.util.module_from_spec(CALIB_SPEC)
assert CALIB_SPEC.loader is not None
CALIB_SPEC.loader.exec_module(CALIB)


RAW_FIELDS = [
    "sequence",
    "query_monotonic_begin_ns",
    "query_monotonic_end_ns",
    "host_realtime_ns",
    "index",
    "connected",
    "pose_valid",
    "tracking_result",
    "m00",
    "m01",
    "m02",
    "px",
    "m10",
    "m11",
    "m12",
    "py",
    "m20",
    "m21",
    "m22",
    "pz",
    "vx",
    "vy",
    "vz",
    "wx",
    "wy",
    "wz",
]


def raw_row(
    sequence: int,
    rotation: np.ndarray | None = None,
    translation: tuple[float, float, float] = (1.25, -2.5, 0.75),
    begin_ns: int | None = None,
    duration_ns: int = 2_000_000,
    realtime_offset_ns: int = 10_000_000_000,
    connected: str = "1",
    pose_valid: str = "1",
    tracking_result: int = 200,
) -> dict[str, object]:
    begin = 1_000_000_000 + sequence * 10_000_000 if begin_ns is None else begin_ns
    end = begin + duration_ns
    rot = np.eye(3) if rotation is None else rotation
    return {
        "sequence": sequence,
        "query_monotonic_begin_ns": begin,
        "query_monotonic_end_ns": end,
        "host_realtime_ns": realtime_offset_ns + begin,
        "index": 0,
        "connected": connected,
        "pose_valid": pose_valid,
        "tracking_result": tracking_result,
        "m00": rot[0, 0],
        "m01": rot[0, 1],
        "m02": rot[0, 2],
        "px": translation[0],
        "m10": rot[1, 0],
        "m11": rot[1, 1],
        "m12": rot[1, 2],
        "py": translation[1],
        "m20": rot[2, 0],
        "m21": rot[2, 1],
        "m22": rot[2, 2],
        "pz": translation[2],
        "vx": 0,
        "vy": 0,
        "vz": 0,
        "wx": 0,
        "wy": 0,
        "wz": 0,
    }


def write_raw(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=RAW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_tracker(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def adapt(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[dict, Path, Path, Path]:
    raw = tmp_path / "raw.csv"
    output = tmp_path / "tracker.csv"
    report = tmp_path / "report.json"
    write_raw(raw, rows)
    result = ADAPTER.adapt_trace(raw, output, report)
    assert json.loads(report.read_text()) == result
    return result, raw, output, report


def test_known_translation_and_90deg_rotation_quaternion(tmp_path: Path) -> None:
    rot = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    result, _, output, _ = adapt(
        tmp_path,
        [
            raw_row(0, rotation=rot, translation=(1.0, 2.0, 3.0)),
            raw_row(1, rotation=rot, translation=(4.0, 5.0, 6.0)),
        ],
    )

    rows = read_tracker(output)
    assert result["result"] == "PASS"
    assert result["status"] == "PASS"
    assert rows[0]["name"] == "WM0"
    assert rows[0]["serial"] == ADAPTER.DEFAULT_SERIAL
    assert rows[0]["device_time_s"] == ""
    assert rows[0]["source"] == "steamvr_official"
    assert rows[0]["timestamp_source"] == "host_query_midpoint_not_sensor"
    assert rows[0]["pose_frame"] == "steamvr_standing_tracker"
    assert [float(rows[0][key]) for key in ("px_m", "py_m", "pz_m")] == [
        1.0,
        2.0,
        3.0,
    ]
    quat_xyzw = [
        float(rows[0]["qx"]),
        float(rows[0]["qy"]),
        float(rows[0]["qz"]),
        float(rows[0]["qw"]),
    ]
    np.testing.assert_allclose(
        np.abs(quat_xyzw),
        np.abs(Rotation.from_euler("z", 90, degrees=True).as_quat()),
        atol=1e-12,
    )


def test_midpoint_and_wall_clock_mapping(tmp_path: Path) -> None:
    result, _, output, _ = adapt(
        tmp_path,
        [raw_row(0, duration_ns=6_000_000), raw_row(1, duration_ns=6_000_000)],
    )

    row = read_tracker(output)[0]
    assert result["validation"]["clock_pair_drift_ns"] == 0
    assert int(row["host_monotonic_ns"]) == 1_003_000_000
    assert int(row["host_realtime_ns"]) == 11_003_000_000


def test_invalid_row_preserves_raw_and_rejects_export_with_permitted_fraction(
    tmp_path: Path,
) -> None:
    rows = [raw_row(i) for i in range(1001)]
    rows[17] = raw_row(17, pose_valid="0")
    raw = tmp_path / "raw.csv"
    output = tmp_path / "tracker.csv"
    report = tmp_path / "report.json"
    write_raw(raw, rows)
    before = raw.read_bytes()

    result = ADAPTER.adapt_trace(raw, output, report)

    assert raw.read_bytes() == before
    assert result["result"] == "PASS"
    assert result["validation"]["input_rows"] == 1001
    assert result["validation"]["exported_rows"] == 1000
    assert result["validation"]["rejected_counts"] == {"pose_invalid": 1}
    assert len(read_tracker(output)) == 1000


def test_long_good_pose_gap_fails_and_leaves_output_absent(tmp_path: Path) -> None:
    result, _, output, _ = adapt(
        tmp_path,
        [
            raw_row(0),
            raw_row(1, connected="0"),
            raw_row(2, connected="0"),
            raw_row(3, connected="0"),
            raw_row(4),
        ],
    )

    assert result["result"] == "FAIL"
    assert result["status"] == "FAIL"
    assert "sample_good_fraction_below_99_9_percent" in result["failures"]
    assert "good_pose_max_gap_gt_30ms" in result["failures"]
    assert not output.exists()


def test_timestamp_regression_fails_and_leaves_output_absent(tmp_path: Path) -> None:
    rows = [raw_row(0), raw_row(1)]
    rows[1]["query_monotonic_begin_ns"] = rows[0]["query_monotonic_begin_ns"]
    rows[1]["query_monotonic_end_ns"] = rows[0]["query_monotonic_end_ns"]
    result, _, output, _ = adapt(tmp_path, rows)

    assert result["result"] == "FAIL"
    assert "query_midpoint_not_strictly_increasing" in result["failures"]
    assert not output.exists()


def test_negative_reflected_matrix_is_rejected_and_fails_small_sample(
    tmp_path: Path,
) -> None:
    reflected = np.diag([1.0, 1.0, -1.0])
    result, _, output, _ = adapt(
        tmp_path,
        [raw_row(0, rotation=reflected), raw_row(1)],
    )

    assert result["result"] == "FAIL"
    assert result["validation"]["rejected_counts"] == {"bad_rotation_matrix": 1}
    assert "sample_good_fraction_below_99_9_percent" in result["failures"]
    assert not output.exists()


def test_existing_output_and_report_are_preserved(tmp_path: Path) -> None:
    raw = tmp_path / "raw.csv"
    output = tmp_path / "tracker.csv"
    report = tmp_path / "report.json"
    write_raw(raw, [raw_row(0), raw_row(1)])
    output.write_text("do not replace\n")
    with pytest.raises(FileExistsError, match="refusing to overwrite existing output"):
        ADAPTER.adapt_trace(raw, output, report)
    assert output.read_text() == "do not replace\n"

    output.unlink()
    report.write_text("do not replace report\n")
    with pytest.raises(FileExistsError, match="refusing to overwrite existing report"):
        ADAPTER.adapt_trace(raw, output, report)
    assert report.read_text() == "do not replace report\n"
    assert not output.exists()


def test_existing_handeye_parser_loads_host_monotonic_output(tmp_path: Path) -> None:
    _, _, output, _ = adapt(tmp_path, [raw_row(i) for i in range(31)])

    times, positions, quaternions, serial = CALIB.load_tracker(output, "host_monotonic")

    assert serial == ADAPTER.DEFAULT_SERIAL
    np.testing.assert_allclose(times[:3], [1.001, 1.011, 1.021])
    assert positions.shape == (31, 3)
    assert quaternions.shape == (31, 4)

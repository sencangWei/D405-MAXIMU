import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from analyze_loop_endpoint_stability import analyze, read_trajectory


def samples(end_offset_m: float) -> list[tuple[float, tuple[float, float, float]]]:
    result = []
    for index in range(301):
        timestamp = index / 30.0
        if timestamp <= 3.0:
            point = (0.0, 0.0, 0.0)
        elif timestamp >= 7.0:
            point = (end_offset_m, 0.0, 0.0)
        else:
            point = (0.2, 0.1, 0.05)
        result.append((timestamp, point))
    return result


def test_stable_windows_confirm_sub_centimeter_endpoint():
    report = analyze(samples(0.009), threshold_m=0.01)

    assert report["trajectory_modified"] is False
    assert report["point_endpoint_sub_centimeter"] is True
    assert report["stable_sub_centimeter_all_windows"] is True
    assert [window["duration_s"] for window in report["windows"]] == [1.0, 2.0, 3.0]
    assert all(window["sub_centimeter"] for window in report["windows"])


def test_window_centers_reject_lucky_last_sample():
    trajectory = samples(0.03)
    trajectory[-1] = (trajectory[-1][0], (0.005, 0.0, 0.0))

    report = analyze(trajectory, threshold_m=0.01)

    assert report["point_endpoint_sub_centimeter"] is True
    assert report["stable_sub_centimeter_all_windows"] is False
    assert all(
        window["methods"]["median"]["center_delta_m"] > 0.025
        for window in report["windows"]
    )


def test_report_exposes_signed_xyz_endpoint_errors():
    trajectory = samples(0.0)
    endpoint = (0.003, -0.004, 0.005)
    for index, (timestamp, point) in enumerate(trajectory):
        if timestamp >= 7.0:
            trajectory[index] = (timestamp, endpoint)

    report = analyze(trajectory, threshold_m=0.01)

    assert report["point_endpoint_delta_xyz_m"] == list(endpoint)
    assert report["windows"][0]["methods"]["mean"][
        "center_delta_xyz_m"
    ] == list(endpoint)


def test_reader_rejects_timestamp_regression(tmp_path: Path):
    path = tmp_path / "trajectory.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["t_sec", "x", "y", "z"])
        writer.writerow([1.0, 0, 0, 0])
        writer.writerow([0.5, 0, 0, 0])

    try:
        read_trajectory(path)
    except ValueError as exc:
        assert "non-increasing" in str(exc)
    else:
        raise AssertionError("timestamp regression was accepted")


def test_dispersion_exposes_unstable_endpoint_window():
    trajectory = samples(0.005)
    for index in range(len(trajectory) - 30, len(trajectory)):
        timestamp, point = trajectory[index]
        trajectory[index] = (
            timestamp,
            (point[0], 0.02 * math.sin(index), 0.0),
        )

    report = analyze(trajectory, windows_s=(1.0,), threshold_m=0.01)

    end_dispersion = report["windows"][0]["methods"]["median"]["end_dispersion"]
    assert end_dispersion["p95_radius_m"] > 0.015

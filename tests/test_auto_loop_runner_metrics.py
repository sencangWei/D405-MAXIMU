import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from test_vins_auto_loop import camera_frame_count, trajectory_diagnostics


def test_trajectory_diagnostics_reports_step_and_vertical_span():
    rows = [
        [0.0, 0.0, 0.0, 0.0],
        [1.0, 0.01, 0.0, 0.02],
        [2.0, 0.01, 0.03, -0.01],
    ]

    metrics = trajectory_diagnostics(rows)

    assert abs(metrics["max_step_m"] - (0.03**2 + 0.03**2) ** 0.5) < 1e-12
    assert abs(metrics["z_span_m"] - 0.03) < 1e-12
    assert abs(metrics["endpoint_delta_m"] - (0.01**2 + 0.03**2 + 0.01**2) ** 0.5) < 1e-12


def test_trajectory_diagnostics_handles_incomplete_trajectory():
    assert trajectory_diagnostics([]) == {
        "max_step_m": None,
        "z_span_m": None,
        "endpoint_delta_m": None,
    }


def test_camera_frame_count_prefers_actual_image_cache(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    with (session / "d405_frames.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frame"])
        writer.writerows([[index] for index in range(1000)])

    cache = tmp_path / "stereo_prefix.db3"
    database = sqlite3.connect(cache)
    database.execute(
        "CREATE TABLE topics(id INTEGER PRIMARY KEY,name TEXT NOT NULL)"
    )
    database.execute(
        "CREATE TABLE messages(id INTEGER PRIMARY KEY,topic_id INTEGER NOT NULL)"
    )
    database.execute(
        "INSERT INTO topics VALUES(1, '/device_0/sensor_0/Infrared_1/image/data')"
    )
    database.executemany(
        "INSERT INTO messages VALUES(?, 1)",
        [(index,) for index in range(1, 451)],
    )
    database.commit()
    database.close()

    assert camera_frame_count(session, cache) == (450, str(cache.resolve()))

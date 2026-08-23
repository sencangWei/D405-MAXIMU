import csv
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from replay_db3_to_ros2 import load_epoch_minus_mono


def _write_frames_csv(session: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    session.mkdir()
    with (session / "d405_frames.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize(
    ("device_field", "mono_field"),
    [
        ("color_device_ms", "color_mono"),
        ("depth_device_ms", "depth_mono"),
        ("infrared_left_device_ms", "infrared_left_mono"),
    ],
)
def test_load_epoch_minus_mono_supports_recording_modes(
    tmp_path: Path, device_field: str, mono_field: str
) -> None:
    session = tmp_path / "session"
    _write_frames_csv(
        session,
        [device_field, mono_field],
        [
            {device_field: "100100.0", mono_field: "1.0"},
            {device_field: "100200.0", mono_field: "1.1"},
            {device_field: "100300.0", mono_field: "1.2"},
        ],
    )

    assert load_epoch_minus_mono(session) == pytest.approx(99.1)


def test_load_epoch_minus_mono_rejects_csv_without_supported_time_pair(
    tmp_path: Path,
) -> None:
    session = tmp_path / "session"
    _write_frames_csv(session, ["set_index"], [{"set_index": "0"}])

    with pytest.raises(RuntimeError, match="color_device_ms/color_mono"):
        load_epoch_minus_mono(session)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from test_vins_auto_loop import trajectory_diagnostics


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

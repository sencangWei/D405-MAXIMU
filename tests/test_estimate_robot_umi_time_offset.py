from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).parents[1] / "scripts" / "estimate_robot_umi_time_offset.py"
SPEC = importlib.util.spec_from_file_location("estimate_robot_umi_time_offset", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

AGGREGATE_SCRIPT = Path(__file__).parents[1] / "scripts" / "aggregate_robot_umi_time_offsets.py"
AGGREGATE_SPEC = importlib.util.spec_from_file_location(
    "aggregate_robot_umi_time_offsets", AGGREGATE_SCRIPT
)
assert AGGREGATE_SPEC and AGGREGATE_SPEC.loader
AGGREGATE = importlib.util.module_from_spec(AGGREGATE_SPEC)
sys.modules[AGGREGATE_SPEC.name] = AGGREGATE
AGGREGATE_SPEC.loader.exec_module(AGGREGATE)


def _motion(t: np.ndarray) -> np.ndarray:
    return np.column_stack(
        (
            0.15 * np.sin(0.7 * t) + 0.04 * np.sin(3.1 * t),
            0.11 * np.cos(0.43 * t + 0.4),
            0.03 * np.sin(1.7 * t + 0.2),
        )
    )


def test_speed_correlation_recovers_robot_query_offset() -> None:
    true_offset = 0.037
    umi_t = np.arange(10.0, 50.0, 1.0 / 30.0)
    robot_t = np.arange(0.0, 60.0, 1.0 / 29.9)
    umi_p = _motion(umi_t)
    robot_p = _motion(robot_t - true_offset)

    result = MODULE.estimate_offset(umi_t, umi_p, robot_t, robot_p)

    assert abs(result["robot_query_offset_ms"] - true_offset * 1000.0) < 2.0
    assert result["correlation"] > 0.99
    assert result["uses_endpoint_constraint"] is False
    assert result["uses_handeye_constraint"] is False


def test_aggregate_rejects_weak_runs_and_uses_median(tmp_path: Path) -> None:
    paths = []
    for index, (offset, corr, width) in enumerate(
        ((16.0, 0.98, 40.0), (17.0, 0.96, 55.0), (15.0, 0.95, 45.0), (120.0, 0.61, 30.0))
    ):
        path = tmp_path / f"offset_{index}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": "robot_umi_clock_offset_report_v1",
                    "robot_query_offset_ms": offset,
                    "correlation": corr,
                    "peak_width_at_delta_corr_0p005_ms": width,
                }
            ),
            encoding="utf-8",
        )
        paths.append(path)
    result = AGGREGATE.aggregate_reports(paths)
    assert result["robot_query_offset_ms"] == 16.0
    assert result["accepted_count"] == 3
    assert result["rejected_count"] == 1

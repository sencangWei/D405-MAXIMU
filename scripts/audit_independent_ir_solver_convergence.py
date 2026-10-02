#!/usr/bin/env python3
"""Record LSQR telemetry for future independent-IR paired diagnostics.

This is intentionally a thin wrapper around the existing corpus consumer.  It
does not change solver inputs, tolerances, weights, caps, source policy, score
outputs, or any existing artifact.  The only process-local change is wrapping
the canonical ``fuse_mast3r_stereo_imu.lsqr`` binding used by
``run_physical_stereo_lever_probe``; the original LSQR return object is returned
unchanged to the solver.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_independent_ir_corpus_probe as consumer  # noqa: E402
import fuse_mast3r_stereo_imu as fusion  # noqa: E402
import run_physical_stereo_lever_probe as physical  # noqa: E402


SCHEMA = "umi_independent_ir_solver_lsqr_telemetry_v1"
FIELD_NAMES = (
    "x",
    "istop",
    "itn",
    "r1norm",
    "r2norm",
    "anorm",
    "acond",
    "arnorm",
    "xnorm",
    "var",
)


def _shape(value: Any) -> list[int] | None:
    shape = getattr(value, "shape", None)
    if shape is None:
        return None
    try:
        return [int(part) for part in shape]
    except TypeError:
        return None


def _json_number(value: Any, field: str, nonfinite: list[str]) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        nonfinite.append(field)
        return None
    if number.is_integer() and field in {"istop", "itn"}:
        return int(number)
    return number


def _call_telemetry(matrix: Any, rhs: Any, kwargs: dict[str, Any], result: Any) -> dict[str, Any]:
    nonfinite: list[str] = []
    entry: dict[str, Any] = {
        "matrix_shape": _shape(matrix),
        "rhs_shape": _shape(rhs),
        "atol": kwargs.get("atol"),
        "btol": kwargs.get("btol"),
        "iter_lim": kwargs.get("iter_lim"),
        "result_type": type(result).__name__,
        "result_length": len(result) if hasattr(result, "__len__") else None,
        "unknown_result_layout": False,
        "nonfinite_fields": nonfinite,
    }
    if not hasattr(result, "__len__") or len(result) < 9:
        entry["unknown_result_layout"] = True
        return entry
    for index, field in enumerate(FIELD_NAMES[1:9], start=1):
        entry[field] = _json_number(result[index], field, nonfinite)
    return entry


@contextmanager
def capture_fusion_lsqr() -> Iterator[list[dict[str, Any]]]:
    """Capture telemetry at the canonical fusion LSQR seam.

    The wrapped function returns exactly the original LSQR result object.  The
    fusion module binding is restored on normal exit and exceptions.
    """

    original = fusion.lsqr
    calls: list[dict[str, Any]] = []

    def wrapped(matrix: Any, rhs: Any, *args: Any, **kwargs: Any) -> Any:
        result = original(matrix, rhs, *args, **kwargs)
        calls.append(_call_telemetry(matrix, rhs, kwargs, result))
        return result

    fusion.lsqr = wrapped
    try:
        yield calls
    finally:
        fusion.lsqr = original


def _status(calls: list[dict[str, Any]], exception: str | None) -> str:
    if exception is not None:
        return "CONSUMER_EXCEPTION_RECORDED"
    if not calls:
        return "NO_LSQR_CALLS"
    if any(call.get("unknown_result_layout") for call in calls):
        return "UNKNOWN_LSQR_RESULT_LAYOUT"
    if any(call.get("nonfinite_fields") for call in calls):
        return "RECORDED_WITH_NONFINITE_VALUES"
    return "RECORDED"


def write_json_new(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"telemetry output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True))


def build_report(
    *,
    calls: list[dict[str, Any]],
    consumer_argv: list[str],
    consumer_exit_code: int | None,
    exception: str | None,
    started_s: float,
    finished_s: float,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "external_ground_truth_used": False,
        "slam_supervision": False,
        "diagnostic_only": True,
        "consumer": "evaluate_independent_ir_corpus_probe.main",
        "canonical_lsqr_module": "fuse_mast3r_stereo_imu",
        "canonical_lsqr_binding_reached_through_physical_runner": physical.fusion is fusion,
        "consumer_argv": consumer_argv,
        "consumer_exit_code": consumer_exit_code,
        "consumer_exception": exception,
        "status": _status(calls, exception),
        "call_count": len(calls),
        "calls": calls,
        "started_monotonic_s": started_s,
        "finished_monotonic_s": finished_s,
        "elapsed_s": finished_s - started_s,
        "no_convergence_or_rank_guarantee": True,
    }


def run_with_telemetry(consumer_argv: list[str], telemetry_output: Path) -> int:
    if telemetry_output.exists() or telemetry_output.is_symlink():
        raise FileExistsError(f"telemetry output already exists: {telemetry_output}")
    started = time.monotonic()
    exit_code: int | None = None
    exception_text: str | None = None
    with capture_fusion_lsqr() as calls:
        try:
            exit_code = consumer.main(consumer_argv)
        except BaseException as exc:
            exception_text = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            finished = time.monotonic()
            report = build_report(
                calls=calls,
                consumer_argv=consumer_argv,
                consumer_exit_code=exit_code,
                exception=exception_text,
                started_s=started,
                finished_s=finished,
            )
            write_json_new(telemetry_output, report)
    return int(exit_code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--telemetry-output", type=Path, required=True)
    args, consumer_argv = parser.parse_known_args(argv)
    try:
        return run_with_telemetry(consumer_argv, args.telemetry_output)
    except FileExistsError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())

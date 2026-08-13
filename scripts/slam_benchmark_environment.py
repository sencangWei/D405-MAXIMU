#!/usr/bin/env python3
"""Qualify a host before running reproducible offline SLAM benchmarks."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_THRESHOLDS = {
    "max_load_1m_per_cpu": 0.75,
    "max_cpu_pressure_some_avg10_percent": 10.0,
    "max_io_pressure_full_avg10_percent": 10.0,
    "max_memory_pressure_full_avg10_percent": 1.0,
    "min_memory_available_gib": 8.0,
    "max_conflicting_processes": 0,
}
CONFLICTING_PROCESS_MARKERS = (
    "vins_fusion_ros2_node",
    "loop_fusion_node",
    "replay_db3_to_ros2.py",
    "replay_db3_cpp",
    "capture_d405_720p_rgb_stereo_ir.py",
    "scripts/train.py",
    "run_pi05_rebot_e2_after_training.py",
)
RESOURCE_GATED_PROCESS_MARKERS = (
    "rebot_rs_trajectory_replay.py",
    "hik_camera_node",
)
CHECK_SPECIFICATIONS = (
    ("load_1m_per_cpu", "max_load_1m_per_cpu", "<="),
    ("cpu_pressure_some_avg10_percent", "max_cpu_pressure_some_avg10_percent", "<="),
    ("io_pressure_full_avg10_percent", "max_io_pressure_full_avg10_percent", "<="),
    (
        "memory_pressure_full_avg10_percent",
        "max_memory_pressure_full_avg10_percent",
        "<=",
    ),
    ("memory_available_gib", "min_memory_available_gib", ">="),
    ("conflicting_processes", "max_conflicting_processes", "<="),
)
EXPECTED_CHECKS = {name for name, _, _ in CHECK_SPECIFICATIONS}


def read_pressure(path: Path) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        result[fields[0]] = {
            key: float(value) for key, value in (field.split("=", 1) for field in fields[1:])
        }
    return result


def process_matches(argv: list[str], markers: tuple[str, ...]) -> bool:
    """Match real entrypoints without treating diagnostic arguments as processes."""

    def token_matches(token: str) -> bool:
        normalized = token.rstrip("/")
        return any(
            normalized == marker or normalized.endswith("/" + marker)
            for marker in markers
        )

    if not argv:
        return False
    executable = Path(argv[0]).name
    if token_matches(argv[0]):
        return True
    if executable.startswith("python"):
        return len(argv) > 1 and token_matches(argv[1])
    if executable == "uv" and len(argv) > 2 and argv[1] == "run":
        return token_matches(argv[2])
    if executable == "rtk" and len(argv) > 3 and argv[1] == "proxy":
        nested = Path(argv[2]).name
        return nested.startswith("python") and token_matches(argv[3])
    if executable == "ros2" and len(argv) > 3 and argv[1] == "run":
        return token_matches(argv[3])
    return False


def find_processes(markers: tuple[str, ...]) -> list[dict[str, int | str]]:
    matches: list[dict[str, int | str]] = []
    own_pid = os.getpid()
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit() or int(directory.name) == own_pid:
            continue
        try:
            argv = [
                token.decode(errors="replace")
                for token in (directory / "cmdline").read_bytes().split(b"\0")
                if token
            ]
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if argv and process_matches(argv, markers):
            matches.append(
                {"pid": int(directory.name), "command": " ".join(argv)}
            )
    return sorted(matches, key=lambda item: int(item["pid"]))


def find_conflicting_processes() -> list[dict[str, int | str]]:
    return find_processes(CONFLICTING_PROCESS_MARKERS)


def capture_environment() -> dict:
    cpu_count = os.cpu_count() or 1
    load_1m, load_5m, load_15m = os.getloadavg()
    memory_kib = 0
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            memory_kib = int(line.split()[1])
            break
    return {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "kernel_release": platform.release(),
        "cpu_count": cpu_count,
        "load_average": {
            "one_minute": load_1m,
            "five_minutes": load_5m,
            "fifteen_minutes": load_15m,
            "one_minute_per_cpu": load_1m / cpu_count,
        },
        "memory_available_gib": memory_kib / 1024 / 1024,
        "pressure": {
            "cpu": read_pressure(Path("/proc/pressure/cpu")),
            "memory": read_pressure(Path("/proc/pressure/memory")),
            "io": read_pressure(Path("/proc/pressure/io")),
        },
        "conflicting_processes": find_conflicting_processes(),
        "resource_gated_processes": find_processes(RESOURCE_GATED_PROCESS_MARKERS),
    }


def environment_values(snapshot: dict) -> dict[str, float | int]:
    return {
        "load_1m_per_cpu": snapshot["load_average"]["one_minute_per_cpu"],
        "cpu_pressure_some_avg10_percent": snapshot["pressure"]["cpu"]["some"]["avg10"],
        "io_pressure_full_avg10_percent": snapshot["pressure"]["io"]["full"]["avg10"],
        "memory_pressure_full_avg10_percent": snapshot["pressure"]["memory"]["full"]["avg10"],
        "memory_available_gib": snapshot["memory_available_gib"],
        "conflicting_processes": len(snapshot["conflicting_processes"]),
    }


def evaluate_environment(
    snapshot: dict, thresholds: dict | None = None
) -> dict:
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    values = environment_values(snapshot)
    checks = []
    failures = []
    for name, threshold_name, operator in CHECK_SPECIFICATIONS:
        value = values[name]
        threshold = limits[threshold_name]
        passed = value <= threshold if operator == "<=" else value >= threshold
        checks.append(
            {
                "name": name,
                "value": value,
                "operator": operator,
                "threshold": threshold,
                "result": "PASS" if passed else "FAIL",
            }
        )
        if not passed:
            failures.append(f"{name} {value:.3f} {operator} {threshold:.3f} failed")
    return {
        "schema_version": 1,
        "result": "PASS" if not failures else "FAIL",
        "thresholds": limits,
        "snapshot": snapshot,
        "checks": checks,
        "failures": failures,
    }


def validate_environment_report(report: dict) -> list[str]:
    failures: list[str] = []
    if report.get("schema_version") != 1:
        failures.append("unsupported benchmark environment schema")
    if report.get("result") != "PASS":
        failures.append("benchmark environment preflight is not PASS")
    thresholds = report.get("thresholds", {})
    for name, expected in DEFAULT_THRESHOLDS.items():
        value = thresholds.get(name)
        if not isinstance(value, (int, float)):
            failures.append(f"benchmark threshold {name} is missing")
        elif name.startswith("max_") and value > expected:
            failures.append(f"benchmark threshold {name} was weakened")
        elif name.startswith("min_") and value < expected:
            failures.append(f"benchmark threshold {name} was weakened")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        failures.append("benchmark environment checks are missing")
    elif len(checks) != len(EXPECTED_CHECKS) or {
        check.get("name") for check in checks
    } != EXPECTED_CHECKS:
        failures.append("benchmark environment check set is incomplete")
    else:
        try:
            values = environment_values(report["snapshot"])
            by_name = {check["name"]: check for check in checks}
            for name, threshold_name, operator in CHECK_SPECIFICATIONS:
                check = by_name[name]
                value = float(check["value"])
                threshold = float(check["threshold"])
                if not math.isfinite(value) or not math.isfinite(threshold):
                    failures.append(f"benchmark check {name} is non-finite")
                    continue
                if not math.isclose(value, float(values[name]), rel_tol=0.0, abs_tol=1e-9):
                    failures.append(f"benchmark check {name} does not match snapshot")
                if check.get("operator") != operator:
                    failures.append(f"benchmark check {name} operator mismatch")
                if not math.isclose(
                    threshold,
                    float(thresholds[threshold_name]),
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    failures.append(f"benchmark check {name} threshold mismatch")
                passed = value <= threshold if operator == "<=" else value >= threshold
                expected_result = "PASS" if passed else "FAIL"
                if check.get("result") != expected_result:
                    failures.append(f"benchmark check {name} result mismatch")
        except (KeyError, TypeError, ValueError):
            failures.append("benchmark environment report is malformed")
    if report.get("failures"):
        failures.append("benchmark environment reports failures")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="离线SLAM基准环境预检")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_environment(capture_environment())
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["result"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

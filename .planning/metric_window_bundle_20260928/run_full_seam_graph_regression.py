"""Run fixed-cost full-seam graph experiment after frozen controls exist."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import subprocess
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "reports/stereo_spatial_repeatability_20260927/sift_lm_gyro_candidate_ten_v1"
WRAPPER = ROOT / "scripts/fuse_mast3r_seam_pair_windows.py"
METRIC_WRAPPER = ROOT / "scripts/fuse_mast3r_metric_windows.py"
NATIVE_FUSION = ROOT / "scripts/fuse_mast3r_stereo_imu.py"
CASES = ["fresh1", "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4", "fresh2", "fresh3", "fresh4"]
SCHEDULE_OFFSETS = (0, 5, 10, 15, 20)


def digest(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_hashes(hashes: dict[str, str]) -> None:
    if not hashes:
        raise ValueError("Frozen hash map is empty")
    for path, expected in hashes.items():
        if digest(path) != expected:
            raise ValueError(f"Frozen source/input changed: {path}")


def add_hash(hashes: dict[str, str], path: Path | str, expected: str | None = None) -> None:
    raw = str(path)
    value = digest(raw) if expected is None else expected
    if raw in hashes and hashes[raw] != value:
        raise ValueError(f"Frozen hash conflict for {raw}")
    hashes[raw] = value


def load_score_helper():
    path = Path(__file__).resolve().with_name("score_full_seam_controls.py")
    spec = importlib.util.spec_from_file_location("score_full_seam_controls", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_full_schedule(summary: dict) -> None:
    cases = summary.get("cases", [])
    if len(cases) != 10 or {case.get("case") for case in cases} != set(CASES):
        raise ValueError("full-seam graph regression requires exact ten cases")
    for case in cases:
        rows = case.get("windows", [])
        if len(rows) != 58:
            raise ValueError(f"expected 58 endpoint rows for {case.get('case')}")
        for row_index, row in enumerate(rows):
            pair = row_index // 2
            part = row_index % 2
            start = 40 * pair + 20 * part
            expected_indices = [start + offset for offset in SCHEDULE_OFFSETS]
            if row.get("window") != row_index + 1:
                raise ValueError("full-seam endpoint window id mismatch")
            if row.get("joint_pair", pair + 1) != pair + 1:
                raise ValueError("full-seam endpoint joint_pair mismatch")
            if row.get("indices") != expected_indices:
                raise ValueError("full-seam endpoint exact schedule mismatch")


def validate_controls(joint_controls: Path, independent_controls: Path) -> tuple[dict, dict, dict[str, str]]:
    helper = load_score_helper()
    joint, joint_hashes = helper.validate_summary(joint_controls, independent=False)
    independent, independent_hashes = helper.validate_summary(independent_controls, independent=True)
    helper.validate_joint_independent_identity(joint, independent)
    validate_full_schedule(joint)
    validate_full_schedule(independent)
    frozen = {}
    for source in (joint_hashes, independent_hashes):
        for raw, expected in source.items():
            add_hash(frozen, raw, expected)
    add_hash(frozen, Path(__file__).resolve())
    add_hash(frozen, Path(__file__).resolve().with_name("score_full_seam_controls.py"))
    add_hash(frozen, WRAPPER)
    add_hash(frozen, METRIC_WRAPPER)
    add_hash(frozen, NATIVE_FUSION)
    return joint, independent, frozen


def remap_command(command: list[str], old: Path, new: Path, *, keep_graph_inputs: bool = False) -> list[str]:
    old_text = str(old)
    frozen_input_flags = {"--trajectory", "--stereo-report", "--additional-stereo-report"}
    out = []
    keep_next = False
    for value in command:
        if keep_next:
            out.append(value)
            keep_next = False
            continue
        out.append(str(new) + value[len(old_text):] if value.startswith(old_text + "/") else value)
        if keep_graph_inputs and value in frozen_input_flags:
            keep_next = True
    return out


def graph_command(command: list[str], old: Path, new: Path, variant: str, controls: Path | None, case: str) -> list[str]:
    out = remap_command(command, old, new, keep_graph_inputs=True)
    if variant == "baseline":
        return out
    out[1] = str(WRAPPER)
    out += [
        "--seam-window-controls", str(controls),
        "--seam-window-case", case,
        "--seam-window-mode", variant,
    ]
    return out


def variant_commands(stages, validated_graph, old: Path, new: Path, variant: str, controls: Path | None, case: str):
    commands = []
    for stage, command in stages:
        base = list(validated_graph if stage == "graph" else command)
        commands.append((stage, graph_command(base, old, new, variant, controls, case) if stage == "graph" else remap_command(base, old, new)))
    return commands


def load_entries(joint_controls: Path, independent_controls: Path):
    _, _, controls_hashes = validate_controls(joint_controls, independent_controls)
    entries = []
    for case in CASES:
        old = BASELINE / case
        manifest_path = old / "manifest.json"
        command_path = old / "validated_graph_command.json"
        manifest = json.loads(manifest_path.read_text())
        graph = json.loads(command_path.read_text())["command"]
        hashes = {}
        add_hash(hashes, manifest_path)
        add_hash(hashes, command_path)
        for raw, expected in manifest["input_sha256"].items():
            add_hash(hashes, raw, expected)
        baseline_source_hashes = {}
        for raw, expected in manifest["source_sha256"].items():
            path = Path(raw)
            add_hash(baseline_source_hashes, path if path.is_absolute() else ROOT / path, expected)
        for flag in ("--stereo-report", "--additional-stereo-report"):
            for index, value in enumerate(graph):
                if value == flag:
                    add_hash(hashes, graph[index + 1])
        verify_hashes(hashes)
        verify_hashes(baseline_source_hashes)
        for variant, controls in (("baseline", None), ("joint", joint_controls), ("independent", independent_controls)):
            source_hashes = {}
            add_hash(source_hashes, Path(__file__).resolve())
            add_hash(source_hashes, WRAPPER)
            for raw, expected in baseline_source_hashes.items():
                add_hash(source_hashes, raw, expected)
            for raw, expected in controls_hashes.items():
                add_hash(source_hashes, raw, expected)
            if controls is not None:
                add_hash(source_hashes, controls)
            entries.append({
                "case": case,
                "variant": variant,
                "old": old,
                "manifest": manifest,
                "validated_graph": graph,
                "input_sha256": hashes,
                "source_sha256": source_hashes,
                "controls": str(controls) if controls is not None else None,
            })
    verify_hashes(controls_hashes)
    return entries


def run_command(command: list[str], cwd: Path, log_path: Path) -> int:
    with log_path.open("w") as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    return int(result.returncode)


def command_value(command: list[str], flag: str) -> Path:
    if flag not in command:
        raise ValueError(f"command missing {flag}")
    return Path(command[command.index(flag) + 1])


def load_csv_times(path: Path) -> np.ndarray:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"trajectory CSV has no header: {path}")
        time_key = "t_sec" if "t_sec" in reader.fieldnames else reader.fieldnames[0]
        return np.asarray([float(row[time_key]) for row in reader], dtype=float)


def verify_graph_output_times(command: list[str]) -> None:
    source = command_value(command, "--trajectory")
    output = command_value(command, "--output")
    source_times = load_csv_times(source)
    output_times = load_csv_times(output)
    if source_times.shape != output_times.shape or not np.allclose(source_times, output_times, atol=1e-9, rtol=0.0):
        raise ValueError("graph output trajectory rows/timestamps changed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--joint-controls", type=Path, required=True)
    parser.add_argument("--independent-controls", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Refuse to overwrite existing seam graph regression")
    entries = load_entries(args.joint_controls, args.independent_controls)
    for entry in entries:
        verify_hashes(entry["input_sha256"])
        verify_hashes(entry["source_sha256"])
    output.mkdir(parents=True)
    records = []
    for entry in entries:
        target = output / entry["variant"] / entry["case"]
        target.mkdir(parents=True)
        commands = variant_commands(
            entry["manifest"]["commands"], entry["validated_graph"], entry["old"], target,
            entry["variant"], Path(entry["controls"]) if entry["controls"] else None, entry["case"]
        )
        record = {
            "case": entry["case"],
            "variant": entry["variant"],
            "baseline": str(entry["old"]),
            "controls": entry["controls"],
            "commands": commands,
            "input_sha256": entry["input_sha256"],
            "source_sha256": entry["source_sha256"],
            "external_reference_used_in_optimization": False,
            "fixed_penalty_m": 0.004,
            "fixed_penalty_role": "regularization_not_stochastic_sigma",
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
            "experiment_status": "research_fixedcost_not_promoted",
            "stages": [],
        }
        (target / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        records.append((target, record))

    for target, record in records:
        verify_hashes(record["input_sha256"])
        verify_hashes(record["source_sha256"])
        graph = next(command for stage, command in record["commands"] if stage == "graph")
        started = time.monotonic()
        rc = run_command(graph, ROOT, target / "graph.log")
        record["stages"].append({"stage": "graph", "returncode": rc, "runtime_s": time.monotonic() - started})
        record["graph_completed"] = rc == 0
        verify_hashes(record["input_sha256"])
        verify_hashes(record["source_sha256"])
        if rc == 0:
            verify_graph_output_times(graph)
        (output / "batch_status.json").write_text(json.dumps({"cases": [r for _, r in records], "gt_scoring_started_after_all_graphs": False}, indent=2) + "\n")

    for _, record in records:
        verify_hashes(record["input_sha256"])
        verify_hashes(record["source_sha256"])
    for target, record in records:
        if not record.get("graph_completed"):
            record["completed"] = False
            record["failure_stage"] = "graph"
            continue
        for stage, command in record["commands"]:
            if stage == "graph":
                continue
            started = time.monotonic()
            rc = run_command(command, ROOT, target / f"{stage}.log")
            record["stages"].append({"stage": stage, "returncode": rc, "runtime_s": time.monotonic() - started})
            verify_hashes(record["input_sha256"])
            verify_hashes(record["source_sha256"])
            if rc != 0 and not (stage in {"quality", "score"} and rc == 3):
                record["completed"] = False
                record["failure_stage"] = stage
                break
        else:
            record["completed"] = True
        verify_hashes(record["input_sha256"])
        verify_hashes(record["source_sha256"])
        (output / "batch_status.json").write_text(json.dumps({"cases": [r for _, r in records], "gt_scoring_started_after_all_graphs": True}, indent=2) + "\n")
    print("ALL_30_GRAPHS_THEN_DOWNSTREAM_FINISHED; no promotion or selection claim", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

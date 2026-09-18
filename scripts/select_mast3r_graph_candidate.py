#!/usr/bin/env python3
"""Select a MASt3R graph trajectory using only its internal quality report."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_candidate(name: str, trajectory: Path, report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    if report.get("external_ground_truth_used") is not False:
        failures.append("external_ground_truth_not_explicitly_excluded")
    if report.get("result") != "PASS":
        failures.append("graph_quality_gate_failed")
    rotation = report.get("rotation_fusion", {})
    after_p95_deg = rotation.get("after_p95_deg")
    stereo_after_p95_deg = rotation.get("stereo_rotation_after_p95_deg")
    if not isinstance(after_p95_deg, (int, float)):
        failures.append("missing_rotation_after_p95")
    if not isinstance(stereo_after_p95_deg, (int, float)):
        failures.append("missing_stereo_rotation_after_p95")
    if not trajectory.is_file():
        failures.append("trajectory_missing")
    return {
        "name": name,
        "trajectory": str(trajectory.resolve()),
        "graph_report": str(report_path.resolve()),
        "eligible": not failures,
        "failures": failures,
        "internal_score": {
            "rotation_after_p95_deg": after_p95_deg,
            "stereo_rotation_after_p95_deg": stereo_after_p95_deg,
        },
    }


def select_candidate(candidates: list[dict]) -> dict:
    eligible = [item for item in candidates if item["eligible"]]
    if not eligible:
        raise ValueError("no candidate passed the internal graph quality gate")
    return min(
        eligible,
        key=lambda item: (
            item["internal_score"]["rotation_after_p95_deg"],
            item["internal_score"]["stereo_rotation_after_p95_deg"],
            item["name"],
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate",
        nargs=3,
        action="append",
        metavar=("NAME", "TRAJECTORY", "GRAPH_REPORT"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    candidates = [
        evaluate_candidate(name, Path(trajectory), Path(report))
        for name, trajectory, report in args.candidate
    ]
    selected = select_candidate(candidates)
    source = Path(selected["trajectory"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, args.output)
    result = {
        "schema": "umi_mast3r_graph_candidate_selection_v1",
        "result": "PASS",
        "selection_supervision": "internal_graph_quality_only",
        "external_ground_truth_used": False,
        "selection_policy": (
            "pass_graph_gate_then_minimize_rotation_after_p95_and_"
            "stereo_rotation_after_p95"
        ),
        "selected": selected,
        "candidates": candidates,
        "output": str(args.output.resolve()),
        "output_sha256": file_sha256(args.output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

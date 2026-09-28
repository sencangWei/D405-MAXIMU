"""Evaluation-only scorer wrapper for frozen full-seam controls."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
OLD_SCORE = Path(__file__).resolve().parent / "score_window_controls.py"
EVAL_SOURCE = ROOT / "scripts" / "evaluate_slam_ground_truth.py"
EXPECTED_CASES = {
    "dev1", "dev2", "heldout1", "heldout2", "heldout3", "heldout4",
    "fresh1", "fresh2", "fresh3", "fresh4",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return dict(count=0, mean_mm=None, median_mm=None, p95_mm=None, max_mm=None)
    if not np.all(np.isfinite(values)):
        raise ValueError("nonfinite scored errors")
    return dict(count=int(len(values)), mean_mm=float(values.mean()), median_mm=float(np.median(values)),
                p95_mm=float(np.quantile(values, 0.95)), max_mm=float(values.max()))


def _validate_hash_map(summary: dict, summary_path: Path) -> dict:
    paths = {str(summary_path): sha(summary_path)}
    if not summary.get("source_sha256"):
        raise ValueError("source_sha256 manifest must be nonempty")
    for raw, expected in summary.get("source_sha256", {}).items():
        path = Path(raw)
        if not path.exists() or sha(path) != expected:
            raise ValueError(f"source digest invalid: {path}")
        paths[str(path)] = expected
    for case in summary.get("cases", []):
        if not case.get("input_sha256"):
            raise ValueError(f"{case.get('case')} input_sha256 manifest must be nonempty")
        for raw, expected in case.get("input_sha256", {}).items():
            path = Path(raw)
            if not path.exists() or sha(path) != expected:
                raise ValueError(f"input digest invalid: {path}")
            paths[str(path)] = expected
    return paths


def _endpoint_identity(case: dict) -> list[tuple]:
    return [(row.get("window"), row.get("indices")) for row in case.get("windows", [])]


def validate_summary(summary_path: Path, *, independent: bool) -> tuple[dict, dict]:
    summary = json.loads(summary_path.read_text())
    if summary.get("external_reference_used") is not False:
        raise ValueError("estimation summary used external reference")
    adapter = summary.get("full_seam_adapter")
    if not isinstance(adapter, dict) or adapter.get("raw_interval_frames") != 40:
        raise ValueError("full_seam_adapter interval40 missing")
    cases = summary.get("cases", [])
    names = [case.get("case") for case in cases]
    if len(cases) != 10 or set(names) != EXPECTED_CASES:
        raise ValueError("exact ten known cases required")
    counts = adapter.get("case_counts", [])
    if len(counts) != 10:
        raise ValueError("full_seam_adapter case_counts missing")
    by_name = {row.get("case"): row for row in counts}
    for case in cases:
        row = by_name.get(case.get("case"))
        if row is None:
            raise ValueError("full_seam_adapter case_counts mismatch")
        if row.get("pair_count") != 29 or row.get("recording_raw_frame_count") is None:
            raise ValueError("full_seam_adapter must record 29 actual pairs and raw counts")
        if row["recording_raw_frame_count"] - 1 - row["last_endpoint_index"] not in (38, 39):
            raise ValueError("full_seam_adapter tail frames must be true 38/39, not zero")
    if adapter.get("correlated_paired_endpoints") is not True or adapter.get("calibrated_covariance") is not False:
        raise ValueError("full_seam_adapter correlation/covariance flags invalid")
    if bool(adapter.get("independent_summary")) != bool(independent):
        raise ValueError("full_seam_adapter independent flag mismatch")
    return summary, _validate_hash_map(summary, summary_path)


def validate_joint_independent_identity(joint: dict, indep: dict) -> None:
    joint_cases = {case["case"]: case for case in joint["cases"]}
    indep_cases = {case["case"]: case for case in indep["cases"]}
    if set(joint_cases) != set(indep_cases) or len(joint_cases) != 10:
        raise ValueError("joint/independent case maps differ")
    for name, joint_case in joint_cases.items():
        indep_case = indep_cases[name]
        if joint_case.get("input_sha256") != indep_case.get("input_sha256"):
            raise ValueError(f"joint/independent input hash maps differ: {name}")
        if joint_case.get("decoded_grayscale_frame_sha256") != indep_case.get("decoded_grayscale_frame_sha256"):
            raise ValueError(f"joint/independent decoded hash maps differ: {name}")
        if _endpoint_identity(joint_case) != _endpoint_identity(indep_case):
            raise ValueError(f"joint/independent endpoint schedule differs: {name}")
        if len(joint_case.get("windows", [])) != 58:
            raise ValueError(f"expected 58 endpoints per case: {name}")
    if joint.get("full_seam_adapter", {}).get("case_counts") != indep.get("full_seam_adapter", {}).get("case_counts"):
        raise ValueError("joint/independent full adapter metadata differ")


def run_old_score(controls: Path, output: Path) -> None:
    subprocess.run(
        [sys.executable, str(OLD_SCORE), "--controls", str(controls), "--output", str(output)],
        check=True,
    )


def summarize_scores(joint_summary: dict, indep_summary: dict, joint_score: dict, indep_score: dict) -> dict:
    for label, score_report in (("joint", joint_score), ("independent", indep_score)):
        if score_report.get("external_reference_used_in_estimation") is not False:
            raise ValueError(f"{label} scorer output must declare external_reference_used_in_estimation false")
        if score_report.get("external_reference_used_in_evaluation") is not True:
            raise ValueError(f"{label} scorer output must declare external_reference_used_in_evaluation true")
    sj = {case["case"]: case for case in joint_score.get("cases", [])}
    si = {case["case"]: case for case in indep_score.get("cases", [])}
    cases = []
    pooled_joint, pooled_indep = [], []
    for joint_case in joint_summary["cases"]:
        name = joint_case["case"]
        indep_case = next(case for case in indep_summary["cases"] if case["case"] == name)
        jw = {row["window"]: row for row in sj[name]["windows"] if row.get("scored")}
        iw = {row["window"]: row for row in si[name]["windows"] if row.get("scored")}
        joint_score_rows = {row["window"]: row for row in sj[name]["windows"]}
        indep_score_rows = {row["window"]: row for row in si[name]["windows"]}
        common = sorted(set(jw) & set(iw))
        joint_errors = [jw[index]["optimized_local_error_mm"] for index in common]
        indep_errors = [iw[index]["optimized_local_error_mm"] for index in common]
        joint_accepted = [row for row in joint_case.get("windows", []) if row.get("accepted", False)]
        indep_accepted = [row for row in indep_case.get("windows", []) if row.get("accepted", False)]
        joint_reason_counts = {}
        for row in joint_case.get("windows", []):
            joint_reason_counts[row.get("reason", "missing")] = joint_reason_counts.get(row.get("reason", "missing"), 0) + 1
        pair_count = next(
            row["pair_count"]
            for row in joint_summary["full_seam_adapter"]["case_counts"]
            if row["case"] == name
        )
        pooled_joint.extend(joint_errors)
        pooled_indep.extend(indep_errors)
        cases.append({
            "case": name,
            "pair_count": pair_count,
            "joint_windows": len(joint_case.get("windows", [])),
            "independent_windows": len(indep_case.get("windows", [])),
            "joint_accepted": int(len(joint_accepted)),
            "independent_accepted": int(len(indep_accepted)),
            "accepted_joint_pairs": int(sum(
                all(row.get("accepted", False) for row in joint_case.get("windows", [])[2 * index: 2 * index + 2])
                for index in range(pair_count)
            )),
            "joint_scored": len(jw),
            "independent_scored": len(iw),
            "mutually_scored": len(common),
            "joint_refusals": int(sum(not row.get("accepted", False) for row in joint_case.get("windows", []))),
            "independent_refusals": int(sum(not row.get("accepted", False) for row in indep_case.get("windows", []))),
            "joint_unscored_endpoints": int(len(joint_case.get("windows", [])) - len(jw)),
            "joint_accepted_but_unscored": int(sum(row["window"] not in jw for row in joint_accepted)),
            "independent_accepted_but_unscored": int(sum(row["window"] not in iw for row in indep_accepted)),
            "joint_score_refusals": int(sum(not row.get("scored", False) for row in joint_score_rows.values())),
            "independent_score_refusals": int(sum(not row.get("scored", False) for row in indep_score_rows.values())),
            "endpoint_reason_counts": joint_reason_counts,
            "paired_joint": stats(joint_errors),
            "paired_independent": stats(indep_errors),
            "paired_improved": int(sum(j < i for j, i in zip(joint_errors, indep_errors))),
        })
    return {
        "cases": cases,
        "paired_joint": stats(pooled_joint),
        "paired_independent": stats(pooled_indep),
        "paired_improved": int(sum(j < i for j, i in zip(pooled_joint, pooled_indep))),
        "coverage": {
            "cases": len(cases),
            "all_ten_cases": len(cases) == 10,
            "joint_scored": int(sum(row["joint_scored"] for row in cases)),
            "independent_scored": int(sum(row["independent_scored"] for row in cases)),
            "mutually_scored": int(sum(row["mutually_scored"] for row in cases)),
            "joint_unscored_endpoints": int(sum(row["joint_unscored_endpoints"] for row in cases)),
            "joint_accepted_but_unscored": int(sum(row["joint_accepted_but_unscored"] for row in cases)),
            "independent_accepted_but_unscored": int(sum(row["independent_accepted_but_unscored"] for row in cases)),
            "joint_refusals": int(sum(row["joint_refusals"] for row in cases)),
            "independent_refusals": int(sum(row["independent_refusals"] for row in cases)),
            "accepted_joint_pairs": int(sum(row["accepted_joint_pairs"] for row in cases)),
            "pair_count": int(sum(row["pair_count"] for row in cases)),
        },
        "adapter_tail_frames": {
            row["case"]: row["uncovered_tail_frames_after_last_endpoint"]
            for row in joint_summary["full_seam_adapter"]["case_counts"]
        },
        "warning": "local displacement evaluation, NOT ATE; correlated paired endpoint groups; no calibrated covariance; no threshold or selector",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", type=Path, required=True)
    args = parser.parse_args(argv)
    controls = args.controls
    joint_path = controls / "summary.json"
    indep_path = controls / "independent_summary.json"
    if not joint_path.exists() or not indep_path.exists():
        raise ValueError("both summary.json and independent_summary.json are required")
    joint_out = controls / "local_joint_evaluation.json"
    indep_dir = controls / "independent_view"
    indep_view = indep_dir / "summary.json"
    indep_out = controls / "local_independent_evaluation.json"
    report_out = controls / "full_seam_local_score_summary.json"
    for path in (joint_out, indep_view, indep_out, report_out):
        if path.exists():
            raise ValueError(f"refuse to overwrite output: {path}")
    joint_summary, joint_hashes = validate_summary(joint_path, independent=False)
    indep_summary, indep_hashes = validate_summary(indep_path, independent=True)
    validate_joint_independent_identity(joint_summary, indep_summary)
    helper_hashes = {str(Path(__file__).resolve()): sha(Path(__file__).resolve()),
                     str(OLD_SCORE.resolve()): sha(OLD_SCORE),
                     str(EVAL_SOURCE.resolve()): sha(EVAL_SOURCE)}
    frozen = {**joint_hashes, **indep_hashes, **helper_hashes}
    run_old_score(controls, joint_out)
    indep_dir.mkdir()
    shutil.copyfile(indep_path, indep_view)
    run_old_score(indep_dir, indep_out)
    joint_score = json.loads(joint_out.read_text())
    indep_score = json.loads(indep_out.read_text())
    score_hashes = {}
    for score_report in (joint_score, indep_score):
        for raw, expected in score_report.get("source_sha256", {}).items():
            path = Path(raw)
            if not path.exists() or sha(path) != expected:
                raise ValueError(f"score source digest invalid: {path}")
            score_hashes[str(path)] = expected
    if sha(indep_view) != sha(indep_path):
        raise ValueError("independent view byte copy changed")
    result = summarize_scores(joint_summary, indep_summary, joint_score, indep_score)
    for raw, expected in frozen.items():
        if sha(Path(raw)) != expected:
            raise ValueError(f"frozen input changed during scoring: {raw}")
    result["source_sha256"] = {**frozen, **score_hashes, str(joint_out): sha(joint_out), str(indep_out): sha(indep_out), str(indep_view): sha(indep_view)}
    report_out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("coverage", "paired_joint", "paired_independent", "paired_improved", "warning")}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

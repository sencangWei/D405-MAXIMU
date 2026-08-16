#!/usr/bin/env python3
"""Audit repeated loop-regression trajectories after SLAM has completed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_loop_endpoint_stability import analyze, read_trajectory


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config/slam_declared_loop_regression.json"


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def build_audit(
    summary_path: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    threshold_m: float = 0.01,
) -> dict:
    summary_path = summary_path.resolve()
    manifest_path = manifest_path.resolve()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if summary.get("mode") != "REPEATED_REGRESSION":
        raise ValueError("summary is not a repeated regression report")
    declared_datasets = [
        *manifest.get("datasets", []),
        *manifest.get("safety_controls", []),
    ]
    expected_by_id = {
        item["id"]: item["expected_loop"] for item in declared_datasets
    }
    summary_by_id = {
        item.get("id"): item for item in summary.get("datasets", [])
    }
    unknown_ids = set(summary_by_id) - set(expected_by_id)
    if unknown_ids:
        raise ValueError(
            "summary contains unknown dataset(s): " + ", ".join(sorted(unknown_ids))
        )
    inferred_repetitions = max(
        (len(item.get("runs", [])) for item in summary.get("datasets", [])),
        default=1,
    )
    repetitions = int(
        summary.get("required_repetitions_per_dataset")
        or manifest.get("thresholds", {}).get("required_repetitions_per_dataset")
        or inferred_repetitions
    )
    if repetitions < 1:
        raise ValueError("required repetitions must be positive")

    datasets = []
    expected_positive_run_count = sum(
        repetitions for item in declared_datasets if item["expected_loop"]
    )
    stable_positive_run_count = 0
    for declared in declared_datasets:
        dataset_id = declared["id"]
        dataset = summary_by_id.get(dataset_id, {})
        expected_loop = expected_by_id[dataset_id]
        runs = []
        runs_by_repetition = {
            run.get("repetition"): run for run in dataset.get("runs", [])
        }
        for repetition in range(1, repetitions + 1):
            run = runs_by_repetition.get(repetition)
            if run is None:
                runs.append(
                    {
                        "repetition": repetition,
                        "expected_loop": expected_loop,
                        "result": "MISSING_REGRESSION_RUN",
                    }
                )
                continue
            report_value = run.get("report")
            if not report_value:
                runs.append(
                    {
                        "repetition": repetition,
                        "expected_loop": expected_loop,
                        "result": "MISSING_RUN_REPORT",
                    }
                )
                continue
            run_report_path = resolve(report_value)
            run_report = json.loads(run_report_path.read_text(encoding="utf-8"))
            trajectory = run_report_path.parent / "vio_corrected_stream.csv"
            automatic_loop_accepted = int(run_report.get("automatic_loop_accepts", 0)) >= 1
            run_report_passed = run_report.get("result") == "PASS"
            try:
                audit = analyze(read_trajectory(trajectory), threshold_m=threshold_m)
            except (FileNotFoundError, ValueError) as exc:
                runs.append(
                    {
                        "repetition": repetition,
                        "run_report": str(run_report_path),
                        "trajectory": str(trajectory),
                        "expected_loop": expected_loop,
                        "automatic_loop_accepted": automatic_loop_accepted,
                        "run_report_passed": run_report_passed,
                        "result": "TRAJECTORY_UNAVAILABLE",
                        "error": str(exc),
                    }
                )
                continue
            item = {
                "repetition": repetition,
                "run_report": str(run_report_path),
                "trajectory": str(trajectory),
                "expected_loop": expected_loop,
                "automatic_loop_accepted": automatic_loop_accepted,
                "run_report_passed": run_report_passed,
                **audit,
            }
            runs.append(item)
            if (
                expected_loop
                and automatic_loop_accepted
                and run_report_passed
                and audit["stable_sub_centimeter_all_windows"]
            ):
                stable_positive_run_count += 1
        datasets.append(
            {"id": dataset_id, "expected_loop": expected_loop, "runs": runs}
        )

    return {
        "schema_version": 1,
        "source_summary": str(summary_path),
        "source_manifest": str(manifest_path),
        "truth_usage": "post_run_audit_only",
        "trajectory_modified": False,
        "threshold_m": threshold_m,
        "required_repetitions_per_dataset": repetitions,
        "expected_loop_run_count": expected_positive_run_count,
        "stable_sub_centimeter_run_count": stable_positive_run_count,
        "all_expected_loops_stable_sub_centimeter": (
            expected_positive_run_count > 0
            and stable_positive_run_count == expected_positive_run_count
        ),
        "datasets": datasets,
    }


def write_markdown(path: Path, audit: dict) -> None:
    lines = [
        "# 自动回环端点稳定性审计",
        "",
        "- 轨迹仅被读取，未修改。",
        "- 末帧以及首尾1/2/3秒窗口的均值和中位数均小于10mm，才标记为稳定通过。",
        "",
        "| 数据 | 轮次 | 自动回环 | 末帧(mm) | 末帧X/Y/Z(mm) | 1秒均值/中位(mm) | 2秒均值/中位(mm) | 3秒均值/中位(mm) | 稳定<1cm |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for dataset in audit["datasets"]:
        for run in dataset["runs"]:
            if "point_endpoint_delta_m" not in run:
                lines.append(
                    f"| {dataset['id']} | {run.get('repetition')} | — | — | — | — | — | — | "
                    f"{run.get('result', '缺报告')} |"
                )
                continue
            window_values = []
            for window in run["windows"]:
                methods = window["methods"]
                window_values.append(
                    f"{1000 * methods['mean']['center_delta_m']:.2f}/"
                    f"{1000 * methods['median']['center_delta_m']:.2f}"
                )
            if run["expected_loop"]:
                verdict = (
                    "PASS"
                    if run["automatic_loop_accepted"]
                    and run["run_report_passed"]
                    and run["stable_sub_centimeter_all_windows"]
                    else "FAIL"
                )
            else:
                verdict = (
                    "误回环FAIL" if run["automatic_loop_accepted"] else "安全样本PASS"
                )
            point_axis_values = "/".join(
                f"{1000 * value:+.2f}"
                for value in run["point_endpoint_delta_xyz_m"]
            )
            lines.append(
                f"| {dataset['id']} | {run['repetition']} | "
                f"{'是' if run['automatic_loop_accepted'] else '否'} | "
                f"{1000 * run['point_endpoint_delta_m']:.2f} | "
                f"{point_axis_values} | "
                f"{' | '.join(window_values)} | "
                f"{verdict} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--threshold-m", type=float, default=0.01)
    args = parser.parse_args()
    audit = build_audit(args.summary, args.manifest, args.threshold_m)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown:
        write_markdown(args.markdown, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

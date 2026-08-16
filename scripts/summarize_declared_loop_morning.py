#!/usr/bin/env python3
"""Create a concise morning handoff from the sealed loop regression."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_optional(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_exit_code(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def summarize(root: Path) -> str:
    summary_path = root / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    stability = load_optional(root / "endpoint_stability.json")
    pnp_gate = load_optional(root / "pnp_gate_analysis.json")
    stability_exit = load_exit_code(root / "endpoint_stability.exit_code")
    pnp_exit = load_exit_code(root / "pnp_gate_analysis.exit_code")
    plots_exit = load_exit_code(root / "plots.exit_code")

    positive_failures = []
    negative_failures = []
    negative_dataset_ids = set()
    expected_loop_by_id = {
        dataset.get("id"): dataset.get("expected_loop")
        for dataset in (stability or {}).get("datasets", [])
    }
    rows = []
    for dataset in summary.get("datasets", []):
        stages = {
            run.get("loop_stage") for run in dataset.get("runs", [])
        }
        is_negative = (
            expected_loop_by_id.get(dataset.get("id")) is False
            or bool(stages & {"SAFETY_CONTROL_CLEAN", "FALSE_LOOP_ACCEPTED"})
        )
        if is_negative:
            negative_dataset_ids.add(dataset["id"])
        for run in dataset.get("runs", []):
            endpoint = run.get("endpoint_error_m")
            endpoint_text = "—" if endpoint is None else f"{1000 * endpoint:.2f}mm"
            rows.append(
                f"| {dataset['id']} | {run['repetition']} | {run['result']} | "
                f"{run.get('loop_stage', '—')} | "
                f"{run.get('automatic_loop_accepts', 0)} | {endpoint_text} | "
                f"{100 * run.get('pose_coverage', 0):.2f}% |"
            )
            failures = run.get("failures", [])
            if failures:
                target = negative_failures if is_negative else positive_failures
                target.append(
                    f"{dataset['id']} run {run['repetition']}: "
                    + "；".join(failures)
                )

    required_data = []
    if pnp_gate is not None and not pnp_gate.get("threshold_freeze_allowed", False):
        required_data.append(
            "至少1条新的相似画面但不闭环负样本；终点离起点至少20cm。"
        )
    elif summary.get("result") == "INFRASTRUCTURE_BLOCKED":
        required_data.append(
            "不补录数据：基础设施未完成本轮回归，资源空闲后继续复跑同一封存历史数据。"
        )
    elif pnp_gate is None:
        required_data.append(
            "暂不补录数据：先修复PnP证据后处理并完成现有封存回归。"
        )
    failed_positive_ids = [
        dataset["id"]
        for dataset in summary.get("datasets", [])
        if dataset.get("result") != "PASS"
        and dataset["id"] not in negative_dataset_ids
    ]
    failed_positive_ids.extend(
        dataset["id"]
        for dataset in (stability or {}).get("datasets", [])
        if dataset.get("expected_loop") is True
        and dataset["id"] not in failed_positive_ids
        and any(
            run.get("result") in {
                "MISSING_REGRESSION_RUN",
                "MISSING_RUN_REPORT",
                "TRAJECTORY_UNAVAILABLE",
            }
            for run in dataset.get("runs", [])
        )
    )
    if summary.get("result") == "PASS" and stability and stability.get(
        "all_expected_loops_stable_sub_centimeter"
    ):
        loop_conclusion = "四组历史闭环的12轮均稳定小于1cm。"
    else:
        loop_conclusion = "历史闭环重复性尚未全部达到稳定小于1cm。"
    stability_text = (
        "未生成"
        if stability is None
        else f"{stability['stable_sub_centimeter_run_count']}/"
        f"{stability['expected_loop_run_count']}轮"
    )
    pnp_text = (
        f"未生成（后处理退出码={pnp_exit}）"
        if pnp_gate is None
        else f"{pnp_gate.get('result')}，冻结阈值="
        f"{pnp_gate.get('selected_threshold')}"
    )
    stability_text = (
        f"未生成（后处理退出码={stability_exit}）"
        if stability is None
        else stability_text
    )
    plots_text = "完成" if plots_exit == 0 else f"失败（退出码={plots_exit}）"
    completed_runs = sum(
        len(dataset.get("runs", [])) for dataset in summary.get("datasets", [])
    )
    planned_runs = None
    if stability is not None:
        planned_runs = len(stability.get("datasets", [])) * int(
            stability.get("required_repetitions_per_dataset", 0)
        )
    if not planned_runs:
        repetitions = int(summary.get("required_repetitions_per_dataset", 0))
        declared_datasets = int(summary.get("positive_dataset_count", 0)) + int(
            summary.get("safety_control_count", 0)
        )
        planned_runs = repetitions * declared_datasets
    run_progress = (
        f"计划{planned_runs}轮，实际完成{completed_runs}轮"
        if planned_runs
        else f"实际完成{completed_runs}轮"
    )
    requests = required_data or ["闭环调试数据暂不需要补录；进入隐藏外部真值产品验收。"]
    certification_motions = (
        "非闭环直线、非闭环L形、开放自由运动、水平闭环、带真实升降闭环、"
        "开放真实升降、原地旋转、快速手持"
    )
    failure_lines = positive_failures + negative_failures or ["无"]
    return "\n".join(
        [
            "# 无提示自动回环明早摘要",
            "",
            f"- 严格回归（{run_progress}）：**{summary.get('result')}**。",
            f"- 稳定闭合窗口：**{stability_text}**。",
            f"- PnP空间门证据：**{pnp_text}**。",
            f"- 三视图：**{plots_text}**。",
            f"- 结论：{loop_conclusion}",
            "",
            "| 数据 | 轮次 | 结果 | 停止阶段 | 自动回环 | 末帧误差 | 覆盖率 |",
            "|---|---:|---|---|---:|---:|---:|",
            *rows,
            "",
            "## 失败定位",
            "",
            *[f"- {line}" for line in failure_lines],
            "",
            "## 明早最少补充数据",
            "",
            *[f"- {line}" for line in requests],
            *(
                [
                    "- 历史闭环失败项不重录：继续用同一封存数据修算法，避免通过换数据掩盖失败："
                    + "、".join(failed_positive_ids)
                    + "。"
                ]
                if failed_positive_ids
                else []
            ),
            "",
            "## 最终客户认证数据（不用于本轮调参）",
            "",
            "- 需要未参与开发、带外部真值的隐藏数据，算法运行前冻结哈希。",
            f"- 动作覆盖：{certification_motions}。",
            "- 每种动作至少3条独立采集；报告ATE、RPE、每米漂移率、Z误差和失败率。",
            "- Z/Depth因子另需含原生Depth的独立隐藏会话；现有RGB＋双IR母版不含Depth，不能用来宣称平面因子已验收。",
            "",
            "本摘要只证明闭环一致性和误回环安全性；没有外部真值时不能把首尾闭合误差冒充绝对ATE/RPE。",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    text = summarize(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

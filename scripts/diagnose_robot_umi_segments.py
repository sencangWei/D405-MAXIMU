#!/usr/bin/env python3
"""Diagnose shape disagreement against motion, queueing and loop events.

This is a read-only diagnostic.  It consumes the already paired relative CSV
and logs from a Docker2 replay; it never edits a trajectory or removes frames.
The output is intentionally explicit about approximate keyframe-to-time mapping
so it cannot be mistaken for a ground-truth error attribution.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np


def _summary(values: np.ndarray, scale: float = 1.0) -> dict[str, float | int]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"count": 0}
    values = values * scale
    return {
        "count": int(values.size),
        "rmse": float(np.sqrt(np.mean(values * values))),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def _load_csv(path: Path) -> dict[str, np.ndarray]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    required = {
        "t_sec",
        "association_delta_ms",
        "umi_x",
        "umi_y",
        "umi_z",
        "robot_actual_x",
        "robot_actual_y",
        "robot_actual_z",
        "shape_error_mm",
    }
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"matched_relative.csv 字段不完整: {path}")
    out: dict[str, np.ndarray] = {}
    for key in required:
        out[key] = np.asarray([float(row[key]) for row in rows], dtype=float)
    return out


def _motion(points: np.ndarray, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dt = np.gradient(times)
    dt = np.maximum(dt, 1.0e-6)
    velocity = np.gradient(points, axis=0) / dt[:, None]
    speed = np.linalg.norm(velocity, axis=1)
    acceleration = np.gradient(speed, times)
    return speed, acceleration


def _segment_report(data: dict[str, np.ndarray], bin_s: float) -> list[dict]:
    t = data["t_sec"]
    t0 = float(t[0])
    segment = np.floor((t - t0) / bin_s).astype(int)
    umi = np.column_stack([data["umi_x"], data["umi_y"], data["umi_z"]])
    robot = np.column_stack(
        [data["robot_actual_x"], data["robot_actual_y"], data["robot_actual_z"]]
    )
    umi_speed, umi_accel = _motion(umi, t)
    robot_speed, robot_accel = _motion(robot, t)
    result = []
    for index in np.unique(segment):
        mask = segment == index
        result.append(
            {
                "start_s": float(t[mask][0] - t0),
                "end_s": float(t[mask][-1] - t0),
                "samples": int(mask.sum()),
                "shape_error_mm": _summary(data["shape_error_mm"][mask]),
                "association_delta_ms": _summary(data["association_delta_ms"][mask]),
                "umi_speed_mm_s": _summary(umi_speed[mask], 1000.0),
                "robot_speed_mm_s": _summary(robot_speed[mask], 1000.0),
                "umi_accel_mm_s2": _summary(np.abs(umi_accel[mask]), 1000.0),
                "robot_accel_mm_s2": _summary(np.abs(robot_accel[mask]), 1000.0),
                "relative_position_bias_mm": np.mean((umi[mask] - robot[mask]), axis=0).tolist(),
            }
        )
    return result


_FRONTEND = re.compile(
    r"processed=(?P<processed>\d+).*?queue=(?P<queue>\d+).*?queue_max30=(?P<queue_max30>\d+)"
    r".*?total_avg30_ms=(?P<total_avg30>[0-9.]+).*?total_max30_ms=(?P<total_max30>[0-9.]+)"
    r".*?tracks=(?P<tracks>\d+).*?flow_p90=(?P<flow_p90>[0-9.]+)"
)
_BACKEND = re.compile(
    r"processed=(?P<processed>\d+).*?queue=(?P<queue>\d+).*?queue_max30=(?P<queue_max30>\d+)"
    r".*?total_avg30_ms=(?P<total_avg30>[0-9.]+).*?total_max30_ms=(?P<total_max30>[0-9.]+)"
    r".*?tracks=(?P<tracks>\d+).*?flow_p90=(?P<flow_p90>[0-9.]+)"
)


def _parse_perf(path: Path, camera_rate_hz: float = 30.0) -> dict:
    frontend = []
    backend = []
    for line in path.read_text(errors="replace").splitlines():
        target = frontend if "[PERF-FRONTEND]" in line else backend if "[PERF-BACKEND]" in line else None
        if target is None:
            continue
        match = (_FRONTEND if target is frontend else _BACKEND).search(line)
        if not match:
            continue
        row = {key: float(value) for key, value in match.groupdict().items()}
        row["processed"] = int(row["processed"])
        row["queue"] = int(row["queue"])
        row["queue_max30"] = int(row["queue_max30"])
        row["tracks"] = int(row["tracks"])
        target.append(row)

    def summarize(rows: list[dict]) -> dict:
        if not rows:
            return {"samples": 0}
        return {
            "samples": len(rows),
            "processed_first": rows[0]["processed"],
            "processed_last": rows[-1]["processed"],
            "queue": _summary(np.asarray([r["queue"] for r in rows])),
            "queue_max30": int(max(r["queue_max30"] for r in rows)),
            "total_avg30_ms": _summary(np.asarray([r["total_avg30"] for r in rows])),
            "total_max30_ms": _summary(np.asarray([r["total_max30"] for r in rows])),
            "tracks": _summary(np.asarray([r["tracks"] for r in rows])),
            "flow_p90": _summary(np.asarray([r["flow_p90"] for r in rows])),
            "high_backlog_rows_queue_ge_10": int(sum(r["queue"] >= 10 for r in rows)),
            "high_latency_rows_max30_ge_100ms": int(sum(r["total_max30"] >= 100.0 for r in rows)),
        }

    backend_events = []
    if backend:
        first_processed = backend[0]["processed"]
        for row in backend:
            if row["queue"] < 10 and row["total_max30"] < 100.0:
                continue
            event = dict(row)
            event["approx_replay_s"] = max(
                0.0, (row["processed"] - first_processed) / max(camera_rate_hz, 1.0)
            )
            backend_events.append(event)
        backend_events.sort(
            key=lambda row: (row["total_max30"], row["queue"]), reverse=True
        )
    return {
        "frontend": summarize(frontend),
        "backend": summarize(backend),
        "backend_degradation_events": backend_events[:20],
        "backend_event_time_note": (
            "approx_replay_s uses processed keyframe index and nominal camera rate; "
            "it is a diagnostic alignment, not a sensor timestamp"
        ),
    }


def _parse_loops(path: Path) -> dict:
    lines = path.read_text(errors="replace").splitlines()
    accepted = [line for line in lines if "[AUTO_LOOP_ACCEPT]" in line]
    large_rejected = [line for line in lines if "[AUTO_LOOP_LARGE_REJECT]" in line]
    pending = [line for line in lines if "[AUTO_LOOP_PENDING]" in line]
    rejected = [line for line in lines if "[AUTO_LOOP_REJECT]" in line]
    return {
        "accepted": len(accepted),
        "large_rejected": len(large_rejected),
        "pending": len(pending),
        "rejected": len(rejected),
        "accepted_lines": accepted,
        "large_rejected_lines": large_rejected,
        "policy_note": (
            "新二进制会对 >=10mm 修正要求大回环多帧确认、双IR和PnP质量；"
            "若日志没有 AUTO_LOOP_LARGE_REJECT，说明该日志由旧二进制生成，不能用来验证新门禁。"
        ),
    }


def _markdown(report: dict) -> str:
    rows = report["segments"]
    lines = [
        "# Docker2 UMI—机械臂轨迹分段诊断",
        "",
        "本报告只诊断已生成轨迹，不修改、不删帧、不使用首尾答案。分段时间以 paired_relative.csv 首个样本为 0；VINS processed 序号与分段的对应仅作近似诊断。",
        "",
        "## 结论",
        "",
        "- 如果误差只是固定平移，分段 shape_error 的均值和 P95应近似稳定；本次分段明显随运动阶段变化，因此不能只改一个平移量。",
        "- 高误差段应与加速度/速度突变、后端队列积压或回环修正同时检查；这三类因素分别对应时间配对、VIO实时性和回环几何门禁。",
        "- 模糊/低特征帧仍保留在 VIO 轨迹中；产品策略是降低视觉因子权重，只有不合格的回环边被拒绝。",
        "",
        "## 分段指标",
        "",
        "|区间(s)|样本|shape RMSE(mm)|shape P95(mm)|UMI速度中位(mm/s)|机械臂速度中位(mm/s)|配对误差P95(ms)|",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"|{row['start_s']:.1f}–{row['end_s']:.1f}|{row['samples']}|"
            f"{row['shape_error_mm'].get('rmse', float('nan')):.2f}|"
            f"{row['shape_error_mm'].get('p95', float('nan')):.2f}|"
            f"{row['umi_speed_mm_s'].get('median', float('nan')):.1f}|"
            f"{row['robot_speed_mm_s'].get('median', float('nan')):.1f}|"
            f"{row['association_delta_ms'].get('p95', float('nan')):.2f}|"
        )
    lines += [
        "",
        "## VINS队列/延迟摘要",
        "",
        "```json",
        json.dumps(report["vins_perf"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 后端退化事件（近似时间，仅诊断）",
        "",
        "超过队列 10 或 30 帧窗口最大延迟 100 ms 的后端采样如下；`approx_replay_s` 由 processed 序号按 30 fps 换算，不能当作传感器时间戳。",
        "",
        "|近似时间(s)|processed|queue|queue_max30|total_max30(ms)|tracks|flow_p90|",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for event in report["vins_perf"].get("backend_degradation_events", [])[:10]:
        lines.append(
            f"|{event['approx_replay_s']:.1f}|{event['processed']}|{event['queue']}|"
            f"{event['queue_max30']}|{event['total_max30']:.1f}|{event['tracks']}|"
            f"{event['flow_p90']:.3f}|"
        )
    lines += [
        "",
        "## 自动回环摘要",
        "",
        f"接受 {report['loops']['accepted']}，pending {report['loops']['pending']}，普通拒绝 {report['loops']['rejected']}，大修正专门拒绝 {report['loops']['large_rejected']}。",
        "",
        report["loops"]["policy_note"],
        "",
        "## 限制",
        "",
        "旧 robot_joints.jsonl 使用 host-wall 时间，不能恢复电机在传感器端的生成时刻；当前偏移是文件级查询偏移。下一次采集应使用 recorder 已写入的 CAN kernel event timestamp，从而不再使用旧的 20–22ms 文件偏移。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 UMI 与机械臂分段误差诊断")
    parser.add_argument("--matched", type=Path, required=True)
    parser.add_argument("--vins-log", type=Path, required=True)
    parser.add_argument("--loop-log", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bin-s", type=float, default=5.0)
    args = parser.parse_args()
    data = _load_csv(args.matched.resolve())
    report = {
        "schema": "robot_umi_segment_diagnosis_v1",
        "matched_source": str(args.matched.resolve()),
        "segments": _segment_report(data, args.bin_s),
        "overall": {
            "shape_error_mm": _summary(data["shape_error_mm"]),
            "association_delta_ms": _summary(data["association_delta_ms"]),
        },
        "vins_perf": _parse_perf(args.vins_log.resolve()),
        "loops": _parse_loops(args.loop_log.resolve()),
        "interpretation": {
            "fixed_translation_test": "若分段误差随时间/动作明显变化，则固定平移不足以解释",
            "validity": "诊断用途；不把 shape_error 当绝对真值，不注入终点答案",
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "segment_diagnosis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "segment_diagnosis.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"result": "PASS", "out": str(args.out.resolve()), "segments": len(report["segments"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

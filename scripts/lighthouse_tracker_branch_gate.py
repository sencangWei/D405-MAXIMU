#!/usr/bin/env python3
"""Lighthouse tracker 位姿分支切换门。

背景：tracker 的求解器偶发会在中途换到另一条位姿分支——位姿在 ~8ms 内跳
6–17mm，之后**不再回到**原分支，于是 tracker.csv 变成两条（或更多）互不
相接的轨迹拼起来的。真值被切成几段后，单一 SE(3) 对齐必须在段与段之间
折中，于是整条 ATE 被抬高到 ~10mm，**与 SLAM 的实际精度无关**。

实测：20260917_233028 有 2 次切换（6.83 / 13.03mm），分段各自对齐是
5.15 / 5.80 / 2.74mm，整条却是 9.98mm；而 0 次切换的 20260908_222027
同一条链给出 2.68mm / 100% 落在 10mm 内。

为什么需要这道门：`tracker_integrity.json` 的 `translation_pose_jump_count`
只报"有一次大步"，不报**这次步进是否永久换了分支**，也不报**影响了多少
相机帧**。235329 的 flag 是"1 次跳变"，真实伤害是 83% 的相机帧被整体偏移
16.9mm。覆盖率门（min-timestamp-overlap-ratio）也不查位姿有效性。两者都
不会拦下这种 take。

本脚本只读，不做任何修改。SLAM 不参与判定（slam_supervision: false）。

用法:
    lighthouse_tracker_branch_gate.py <lighthouse_session_dir> [--d405-session DIR]
                                      [--max-affected-frame-ratio 0.02] [--json OUT]

退出码: 0 = PASS, 3 = REJECT（真值不可用于精度判定）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

# 步进超过 max(绝对下限, P95 的倍数) 才算候选；实际取两者较大值，
# 免得在极静 take 上被噪声触发。
MIN_STEP_M = 0.003
P95_MULTIPLIER = 3.0
# 跳完若不回到原处（回跳距离 > 步长这个比例），判为换分支而非单点毛刺。
BRANCH_SWITCH_RATIO = 0.4
# 受影响相机帧占比超过这个值就 REJECT。单帧级毛刺（落在两个相机帧之间）
# 不构成污染，故不设零容忍。
MAX_AFFECTED_FRAME_RATIO = 0.02


def load_tracker(path: Path) -> tuple[np.ndarray, np.ndarray]:
    t, p = [], []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            t.append(float(row["host_monotonic_ns"]) / 1e9)
            p.append([float(row["px_m"]), float(row["py_m"]), float(row["pz_m"])])
    return np.asarray(t), np.asarray(p)


def load_camera_times(session: Path) -> np.ndarray | None:
    frames = session / "d405_frames.csv"
    if not frames.is_file():
        return None
    with frames.open(newline="") as f:
        return np.asarray(sorted(float(r["infrared_left_mono"]) for r in csv.DictReader(f)))


def find_branch_switches(t: np.ndarray, p: np.ndarray, camera_times: np.ndarray | None) -> dict:
    """返回窗口内的分支切换清单。窗口 = 相机时间跨度（有的话）。"""
    if camera_times is not None and camera_times.size:
        inside = (t >= camera_times[0]) & (t <= camera_times[-1])
        step_idx_offset = int(np.argmax(inside))  # 窗口首个采样在原数组的下标
    else:
        inside = np.ones(t.shape, dtype=bool)
        step_idx_offset = 0
    tw = p[inside]
    tw_t = t[inside]
    if len(tw) < 10:
        return {"switches": [], "step_p95_mm": None, "step_max_mm": None, "samples": len(tw)}

    step = np.linalg.norm(np.diff(tw, axis=0), axis=1)
    p95 = float(np.percentile(step, 95))
    threshold = max(MIN_STEP_M, p95 * P95_MULTIPLIER)

    switches = []
    for i in np.where(step > threshold)[0]:
        recovery = float(np.linalg.norm(tw[i + 1] - tw[i - 1]))
        is_switch = recovery > BRANCH_SWITCH_RATIO * step[i]
        affected = int((camera_times >= tw_t[i + 1]).sum()) if camera_times is not None else None
        ratio = (affected / camera_times.size) if camera_times is not None and camera_times.size else None
        if is_switch:
            switches.append(
                {
                    "host_monotonic_s": float(tw_t[i]),
                    "step_mm": float(step[i] * 1000),
                    "recovery_gap_mm": recovery * 1000,
                    "affected_camera_frames": affected,
                    "affected_frame_ratio": ratio,
                }
            )
    worst = max((s["affected_frame_ratio"] or 0.0) for s in switches) if switches else 0.0
    return {
        "samples": int(len(tw)),
        "step_p95_mm": p95 * 1000,
        "step_max_mm": float(step.max() * 1000),
        "threshold_mm": threshold * 1000,
        "switches": switches,
        "max_affected_frame_ratio": worst,
        "camera_frames": int(camera_times.size) if camera_times is not None else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lighthouse_session", type=Path)
    ap.add_argument("--d405-session", type=Path, default=None,
                    help="默认读 lighthouse_session/d405_session.txt 指向的采集目录")
    ap.add_argument("--max-affected-frame-ratio", type=float, default=MAX_AFFECTED_FRAME_RATIO)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    tracker = args.lighthouse_session / "tracker.csv"
    if not tracker.is_file():
        print(f"缺 tracker.csv: {tracker}", file=sys.stderr)
        return 2

    session = args.d405_session
    if session is None:
        pointer = args.lighthouse_session / "d405_session.txt"
        if pointer.is_file():
            session = Path(pointer.read_text().strip())
    camera_times = load_camera_times(session) if session else None
    if camera_times is None:
        print("[警告] 拿不到 d405_frames.csv，只扫整条 tracker（无法把跳变映射到相机帧）",
              file=sys.stderr)

    t, p = load_tracker(tracker)
    result = find_branch_switches(t, p, camera_times)
    result["schema"] = "lighthouse_tracker_branch_gate_v1"
    result["lighthouse_session"] = str(args.lighthouse_session)
    result["tracker_csv"] = str(tracker)
    result["d405_session"] = str(session) if session else None
    result["slam_supervision"] = False
    result["external_ground_truth_used"] = False
    result["max_affected_frame_ratio_threshold"] = args.max_affected_frame_ratio

    print(f"=== {args.lighthouse_session.name} ===")
    print(f"  窗口内 tracker 采样 {result['samples']}  相机帧 {result['camera_frames']}")
    print(f"  平移步长 P95 {result['step_p95_mm']:.3f} mm  最大 {result['step_max_mm']:.3f} mm"
          f"  判候选阈值 {result['threshold_mm']:.3f} mm")

    if not result["switches"]:
        print("  分支切换: 0 次")
    for s in result["switches"]:
        ratio = s["affected_frame_ratio"]
        print(f"  ★ 分支切换 t={s['host_monotonic_s']:.3f}s  步长 {s['step_mm']:.2f} mm"
              f"  回跳距离 {s['recovery_gap_mm']:.2f} mm"
              f"  影响相机帧 {s['affected_camera_frames']}"
              + (f" ({ratio * 100:.1f}%)" if ratio is not None else ""))

    worst = result["max_affected_frame_ratio"]
    if worst > args.max_affected_frame_ratio:
        result["result"] = "REJECT"
        result["reason"] = (
            f"tracker 位姿换分支，最多影响 {worst * 100:.1f}% 的相机帧"
            f"（阈值 {args.max_affected_frame_ratio * 100:.1f}%）。"
            "真值被切成互不相接的多段，单一 SE(3) 对齐会在段间折中并抬高整条 ATE，"
            "该 take 的精度数字不可用，必须重录。"
        )
        print(f"\n  ⇒ REJECT: {result['reason']}")
        rc = 3
    elif result["switches"]:
        result["result"] = "PASS_WITH_WARNING"
        result["reason"] = (
            f"有 {len(result['switches'])} 次换分支但最多只影响 {worst * 100:.2f}% 的相机帧"
            f"（阈值 {args.max_affected_frame_ratio * 100:.1f}%），判为可用但需留意。"
        )
        print(f"\n  ⇒ PASS_WITH_WARNING: {result['reason']}")
        rc = 0
    else:
        result["result"] = "PASS"
        result["reason"] = "窗口内未检出位姿分支切换。"
        print(f"\n  ⇒ PASS")
        rc = 0

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

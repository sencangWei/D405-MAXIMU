#!/usr/bin/env python3
"""跳变时刻 tracker 转得有多快 —— 只读 tracker.csv, 不依赖 IMU。

背景: IMU 陀螺在高速段的读数是坏的(实测有 ~3300 deg/s 的饱和样), 不能当裁判,
所以换一个只用 tracker.csv 的判据。

**⚠ 判据有陷阱, 读结果前必须看这段。**
`rate[k] = |R_k^-1 R_{k+1}| / dt` 涵盖的正是**跨越跳变的那一步**。跳变本身就是一次
姿态阶跃, 于是它必然在 dR/dt 上造出尖峰 —— 拿 rate[k] 的分位去说明"跳变发生在
转速最高的时刻"是**循环论证**。本脚本最初就是这么写的, 得出"中位 99.6%"的假结论,
已撤回。

正确的读法是看 `rate[k-1]` / `rate[k+1]`(前后相邻步, 都不含跳变本身):
实测 96 次跳变, 跨跳变那步 99.7 分位, 而邻步只有 80.5 / 81.1 分位, 绝对量级中位
17-18 deg/s(各条 P95 的一半), 85/96 的跳变发生在 < 50 deg/s —— **不是"转速最高的
瞬间"**。结论: 触发条件目前仍未找到。

用法: tracker_jump_rate_audit.py [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
from lighthouse_tracker_branch_gate import (  # noqa: E402
    find_branch_switches, load_camera_times, load_tracker,
)

SESSIONS = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_sessions")


def audit(sess: Path):
    if not (sess / "tracker.csv").is_file():
        return None
    t, p = load_tracker(sess / "tracker.csv")
    if len(t) < 50:
        return None
    session = None
    ptr = sess / "d405_session.txt"
    if ptr.is_file():
        session = Path(ptr.read_text().strip())
    cam = load_camera_times(session) if session else None
    res = find_branch_switches(t, p, cam)
    if not res["switches"]:
        return {"session": sess.name, "switches": [], "n_samples": int(len(t))}

    q = np.array([[float(r["qw"]), float(r["qx"]), float(r["qy"]), float(r["qz"])]
                  for r in csv.DictReader((sess / "tracker.csv").open())])
    dt = np.diff(t)
    dR = np.degrees(np.array([
        (Rotation.from_quat(q[k]).inv() * Rotation.from_quat(q[k + 1])).magnitude()
        for k in range(len(t) - 1)]))
    rate = dR / np.maximum(dt, 1e-6)          # deg/s
    step = np.linalg.norm(np.diff(p, axis=0), axis=1) * 1000   # mm

    # 窗口 = 相机时间跨度, 与门保持一致
    if cam is not None and cam.size:
        inside = (t >= cam[0]) & (t <= cam[-1])
    else:
        inside = np.ones(t.shape, dtype=bool)
    rw = rate[inside[:-1] | inside[1:]] if inside.size == rate.size + 1 else rate[inside[:rate.size]]
    sw_info = []
    for s in res["switches"]:
        k = int(np.argmin(np.abs(t - s["host_monotonic_s"])))
        # rate[k] 含跳变本身(尖峰), 只作对照; 判据用前后邻步。
        nb = [rate[j] for j in (k - 1, k + 1) if 0 <= j < len(rate)]
        sw_info.append({
            "t_s": s["host_monotonic_s"],
            "step_mm": s["step_mm"],
            "rate_deg_s": float(rate[k]),
            "rate_percentile": float((rw < rate[k]).mean() * 100),
            "neighbor_rate_deg_s": [float(x) for x in nb],
            "neighbor_rate_percentile": [float((rw < x).mean() * 100) for x in nb],
            "translation_step_mm": float(step[k]),
        })
    return {
        "session": sess.name,
        "n_samples": int(len(t)),
        "rate_p95_deg_s": float(np.percentile(rate, 95)),
        "rate_max_deg_s": float(rate.max()),
        "switches": sw_info,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    out, allpct = [], []
    for d in sorted(x for x in SESSIONS.iterdir() if x.is_dir()):
        r = audit(d)
        if r is None:
            continue
        out.append(r)
        if not r["switches"]:
            continue
        for s in r["switches"]:
            allpct.append(s["rate_percentile"])
            print(f"  {r['session']:<42} 步长 {s['step_mm']:>7.2f}mm  "
                  f"角速率 {s['rate_deg_s']:>8.1f} deg/s  = 该条 {s['rate_percentile']:>5.1f} 分位  "
                  f"(该条 P95 {r['rate_p95_deg_s']:.1f}, 最大 {r['rate_max_deg_s']:.1f})")

    print(f"\n共 {len(out)} 条会话; 有跳变的 {sum(1 for r in out if r['switches'])} 条, "
          f"跳变 {len(allpct)} 次")
    if allpct:
        nb_all = [x for r in out for s in r["switches"] for x in s["neighbor_rate_percentile"]]
        nb_abs = [x for r in out for s in r["switches"] for x in s["neighbor_rate_deg_s"]]
        print(f"\n★ rate[k] 跨跳变那步(含跳变自身的尖峰, 勿用于判定):")
        print(f"    分位中位 {np.median(allpct):.1f}%   ≥90 分位 {sum(1 for x in allpct if x >= 90)}/{len(allpct)}")
        print(f"\n○ 前后邻步 rate[k-1] / rate[k+1] (不含跳变, 这才是判据):")
        print(f"    分位中位 {np.median(nb_all):.1f}%   ≥90 分位 {sum(1 for x in nb_all if x >= 90)}/{len(nb_all)}")
        print(f"    绝对速率中位 {np.median(nb_abs):.1f} deg/s   <50 deg/s 的 {sum(1 for x in nb_abs if x < 50)}/{len(nb_abs)}")
        print(f"\n    邻步分位若明显低于跨跳变那步, 说明'转速最高'是跳变自己的产物。")
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"已写 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

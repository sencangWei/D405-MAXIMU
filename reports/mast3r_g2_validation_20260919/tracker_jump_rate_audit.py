#!/usr/bin/env python3
"""跳变发生在什么条件下 —— 不依赖 IMU 的稳健判据。

背景: IMU 陀螺在 500-1500 deg/s 的快转段本身就吐垃圾(实测有 ~3300 deg/s 的
饱和样), 而跳变恰好都在这个速率区间, 所以 IMU 分不开"真运动"和"解算跳"。
换一个只用 tracker.csv 的判据: 每个被门检出的跳变, 看它落在 tracker 自身
角速率的什么分位上。若跳变系统性地挤在速率分布的最顶端, 说明机制是
"快转把 Lighthouse 解算打崩", 而不是任意的分支歧义。

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
        pct = float((rw < rate[k]).mean() * 100)
        sw_info.append({
            "t_s": s["host_monotonic_s"],
            "step_mm": s["step_mm"],
            "rate_deg_s": float(rate[k]),
            "rate_percentile": pct,
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
        print(f"跳变处角速率分位: 中位 {np.median(allpct):.1f}%  "
              f"最小 {min(allpct):.1f}%  最大 {max(allpct):.1f}%")
        print(f"落在 90 分位以上的: {sum(1 for x in allpct if x >= 90)}/{len(allpct)}")
        print(f"落在 50 分位以上的: {sum(1 for x in allpct if x >= 50)}/{len(allpct)}")
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"已写 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""用 IMU 陀螺判定 tracker 的每一个大步进是"真运动"还是"解算跳"。

原理: tracker.csv 的姿态是 world<-tracker, 相邻两样本之间的相对旋转
R_k^-1 R_{k+1} 与同区间内陀螺积分出的角度在同一个(体)坐标系里, 可以直接比。
陀螺是独立于 Lighthouse 的物理传感器, 所以这是不依赖任何 SLAM 结果的裁判。

必须先去时间偏置: IMU 与 tracker 两路流之间有 ~6-8ms 的系统偏移, 在
1500 deg/s 的快转段上那相当于 10 deg 的假残差, 会淹没真信号。偏置用
"全序列中位|残差| 最小" 扫出来。

判据: 残差 / 全序列残差P95。正常解算该比值 < 3; 跳变处会出现几十倍。

用法: tracker_imu_jump_audit.py [--limit N] [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import struct
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

SESSIONS = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_sessions")
IMU_FMT = "<dI7f"
IMU_SZ = struct.calcsize(IMU_FMT)


def load_imu(d405: Path):
    ts = [float(r["ts_mono"]) for r in csv.DictReader((d405 / "external_imu" / "imu_ts.csv").open())]
    g = []
    with (d405 / "external_imu" / "imu.bin").open("rb") as f:
        while True:
            c = f.read(IMU_SZ)
            if len(c) < IMU_SZ:
                break
            g.append(struct.unpack(IMU_FMT, c)[2:5])
    n = min(len(ts), len(g))
    return np.asarray(ts[:n]), np.asarray(g[:n])


def load_tracker(sess: Path):
    t, p, q = [], [], []
    for r in csv.DictReader((sess / "tracker.csv").open()):
        t.append(float(r["host_monotonic_ns"]) / 1e9)
        p.append([float(r["px_m"]), float(r["py_m"]), float(r["pz_m"])])
        q.append([float(r["qw"]), float(r["qx"]), float(r["qy"]), float(r["qz"])])
    return np.asarray(t), np.asarray(p), np.asarray(q)


def gyro_cumulative(ti, gi):
    """累积转角(rad), 使任意区间积分变成两次二分查找。

    直接对每个区间做掩码求和是 O(n) —— 每会话 61 个偏置 x 1.1 万区间会跑成小时级。
    cum[i] 用梯形法: 第 i 个样本处的累积角。
    """
    rate = np.linalg.norm(gi, axis=1)
    dt = np.diff(ti)
    seg = 0.5 * (rate[:-1] + rate[1:]) * dt
    return np.concatenate([[0.0], np.cumsum(seg)])


def gyro_angle(cum, ti, a, b):
    """区间 [a,b] 内的陀螺积分角。用梯形法在端点插值。"""
    if b <= a or b < ti[0] or a > ti[-1]:
        return np.nan
    a = max(a, ti[0])
    b = min(b, ti[-1])
    ia = np.searchsorted(ti, a)
    ib = np.searchsorted(ti, b)
    if ib - ia < 2:
        return np.nan
    # 端点外插到 a / b
    def at(x, i0, i1):
        if i1 >= len(ti):
            i1 = len(ti) - 1
        if ti[i1] == ti[i0]:
            return cum[i1]
        w = (x - ti[i0]) / (ti[i1] - ti[i0])
        return cum[i0] + w * (cum[i1] - cum[i0])
    va = at(a, ia, ia + 1) if ti[ia] < a else cum[ia]
    vb = at(b, ib, ib + 1) if (ib < len(ti) and ti[ib] < b) else cum[min(ib, len(ti) - 1)]
    return float(vb - va)


def audit(sess: Path):
    d405_ptr = sess / "d405_session.txt"
    if not d405_ptr.is_file():
        return {"session": sess.name, "skip": "无 d405_session.txt"}
    d405 = Path(d405_ptr.read_text().strip())
    if not (d405 / "external_imu" / "imu.bin").is_file():
        return {"session": sess.name, "skip": "无 IMU"}
    if not (sess / "tracker.csv").is_file():
        return {"session": sess.name, "skip": "无 tracker.csv"}

    t, p, q = load_tracker(sess)
    if len(t) < 50:
        return {"session": sess.name, "skip": f"tracker 仅 {len(t)} 样本"}
    ti, gi = load_imu(d405)
    if ti.size < 50:
        return {"session": sess.name, "skip": "IMU 样本不足"}

    dR = np.degrees(np.array([
        (Rotation.from_quat(q[k]).inv() * Rotation.from_quat(q[k + 1])).magnitude()
        for k in range(len(t) - 1)]))
    step = np.linalg.norm(np.diff(p, axis=0), axis=1)
    cum = gyro_cumulative(ti, gi)

    best = None
    for off in np.arange(-0.06, 0.0601, 0.002):
        gI = np.array([gyro_angle(cum, ti, t[k] + off, t[k + 1] + off) for k in range(len(t) - 1)])
        ok = ~np.isnan(gI)
        med = float(np.median(np.abs((dR - gI)[ok])))
        if best is None or med < best[1]:
            best = (float(off), med)
    off = best[0]
    gI = np.array([gyro_angle(cum, ti, t[k] + off, t[k + 1] + off) for k in range(len(t) - 1)])
    ok = ~np.isnan(gI)
    resid = (dR - gI)[ok]
    p95 = float(np.percentile(np.abs(resid), 95))
    idx = np.arange(len(t) - 1)[ok]

    j = int(np.argmax(step))
    ratio = float(abs(dR[j] - gI[j]) / p95) if p95 > 0 else float("inf")
    return {
        "session": sess.name,
        "d405": d405.name,
        "samples": int(len(t)),
        "imu_offset_ms": off * 1000,
        "resid_median_deg": float(np.median(np.abs(resid))),
        "resid_p95_deg": p95,
        "n_gt_10xp95": int((np.abs(resid) > 10 * p95).sum()),
        "max_step_mm": float(step[j] * 1000),
        "max_step_ratio_to_p95": ratio,
        "max_step_tracker_deg": float(dR[j]),
        "max_step_imu_deg": float(gI[j]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()

    out = []
    dirs = sorted(d for d in SESSIONS.iterdir() if d.is_dir())
    if a.limit:
        dirs = dirs[-a.limit:]
    for d in dirs:
        r = audit(d)
        out.append(r)
        if "skip" in r:
            print(f"{r['session']:<44} 跳过: {r['skip']}")
            continue
        flag = "★" if r["max_step_ratio_to_p95"] > 5 else " "
        print(f"{flag} {r['session']:<42} 步长 {r['max_step_mm']:>7.2f}mm  "
              f"残差/ P95 = {r['max_step_ratio_to_p95']:>6.1f}x  "
              f"(tracker {r['max_step_tracker_deg']:>6.3f}° vs IMU {r['max_step_imu_deg']:>6.3f}°)  "
              f"偏置 {r['imu_offset_ms']:+.0f}ms  全序列>10xP95 的点 {r['n_gt_10xp95']}")

    good = [r for r in out if "skip" not in r]
    if good:
        print(f"\n可判定 {len(good)} 条;残差/ P95 > 5 的: "
              f"{sum(1 for r in good if r['max_step_ratio_to_p95'] > 5)} 条")
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(f"已写 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

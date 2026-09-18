#!/usr/bin/env python3
"""全数据组现状盘点: 逐组打分 + 判定失败来自算法还是真值伪影.

只用各 group 目录里已有的产物, 不重跑 SLAM, 不需要 tracker.csv。
判据不依赖 tracker 门(它看不见 freeze-then-catchup):
  A. 超限形态: 单个连续小区间(blip) vs 持续偏差
  B. 峰值窗口速度剖面: 真值 vs 三条独立链路(VINS / MASt3R / 融合)的波动倍数
     —— 真值自身速度暴涨而独立链路一致平稳 => 真值在跳
  C. 姿态误差平坦性: 峰值窗口姿态误差 vs 全轨迹中位
     —— 平移异常 + 姿态平坦 => 纯平移型真值伪影, 不是位姿解算错
  D. 全程一致性: corr(融合速度, 真值速度) vs corr(融合速度, VINS速度)
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
BATCHES = [
    "20260914_validation_v10_batch",
    "20260914_validation_v10_holdout_batch2",
    "20260914_validation_v11_holdout_batch3",
    "20260915_batch5_four_videos",
    "20260915_collective_batch4",
    "20260916_fusion_v11_umi_only_batch",
]
# 门限, 与 evaluate_slam_ground_truth.py 默认一致
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, within10=95.0, rot=2.0)


def speed(t, p):
    return np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(t) * 1000.0  # mm/s


def vol(v):
    """波动倍数: p95/p5, 对孤立尖峰比 max/min 稳健。"""
    lo, hi = np.percentile(v, 5), np.percentile(v, 95)
    return hi / max(lo, 1e-6)


def runs(mask):
    """连续 True 段数 + 最长段长 + 各段起止。"""
    segs, s = [], None
    for i, m in enumerate(mask):
        if m and s is None:
            s = i
        elif not m and s is not None:
            segs.append((s, i - 1)); s = None
    if s is not None:
        segs.append((s, len(mask) - 1))
    return segs


def analyse(gt_p, est_t, est_p, est_q, chains):
    """chains: {name: (t, p)} 独立参考链路"""
    rt, rp, rq = gt_p
    inside, valid, interp, iq = E.interpolate_ground_truth(est_t, rt, rp, rq, 0.1)
    sp, sq, G = est_p[inside][valid], est_q[inside][valid], interp[:, 1:]
    R, tr = E.rigid_align(sp, G)
    ate = np.linalg.norm(sp @ R.T + tr - G, axis=1)
    ang = np.degrees((E.Rotation.from_quat(iq).inv()
                      * (E.Rotation.from_matrix(R) * E.Rotation.from_quat(sq))).magnitude())

    m = dict(n=len(ate),
             rmse=np.sqrt(np.mean(ate ** 2)) * 1000,
             p95=np.percentile(ate, 95) * 1000,
             mx=ate.max() * 1000,
             within10=float(np.mean(ate <= 0.01) * 100),
             rot=np.sqrt(np.mean(ang ** 2)))
    m["gate_fail"] = [k for k in ("rmse", "p95", "mx")
                      if m[k] > LIM[k]] + \
                     (["within10"] if m["within10"] < LIM["within10"] else []) + \
                     (["rot"] if m["rot"] > LIM["rot"] else [])

    over = ate > 0.01
    segs = runs(over)
    m["n_over"] = int(over.sum())
    m["n_runs"] = len(segs)
    m["longest_run"] = max((b - a + 1 for a, b in segs), default=0)
    m["span_ratio"] = m["n_over"] / max(len(ate), 1)

    # 峰值窗口
    k = int(np.argmax(ate))
    t0 = est_t[inside][valid]
    lo, hi = t0[k] - 1.0, t0[k] + 1.0
    grid_sel = (t0 >= lo) & (t0 <= hi)
    m["peak_idx"] = k
    m["peak_rel_s"] = float(t0[k] - t0[0])
    m["peak_ate"] = float(ate[k] * 1000)

    # 各链路在峰值窗内的速度波动
    vv = {}
    eg = t0[grid_sel]
    vv["融合"] = vol(np.interp(eg[1:], (t0[1:] + t0[:-1]) / 2, speed(t0, sp)))
    vv["真值"] = vol(np.interp(eg[1:], (rt[1:] + rt[:-1]) / 2, speed(rt, rp)))
    for nm, (ct, cp) in chains.items():
        vv[nm] = vol(np.interp(eg[1:], (ct[1:] + ct[:-1]) / 2, speed(ct, cp)))
    m["peak_vol"] = vv
    indep = [v for kk, v in vv.items() if kk != "真值"]
    m["indep_vol_max"] = max(indep) if indep else float("nan")
    m["gt_vs_indep"] = vv["真值"] / max(m["indep_vol_max"], 1e-6)

    # 姿态平坦性
    w = slice(max(0, k - 2), min(len(ang), k + 3))
    m["rot_median"] = float(np.median(ang))
    m["rot_at_peak"] = float(ang[w].mean())
    m["rot_flat"] = bool(m["rot_at_peak"] <= m["rot_median"] * 1.5)

    # 全程一致性
    ev = speed(t0, sp)
    tc = (t0[1:] + t0[:-1]) / 2
    gv = np.interp(tc, (rt[1:] + rt[:-1]) / 2, speed(rt, rp))
    m["corr_est_gt"] = float(np.corrcoef(ev, gv)[0, 1])
    for nm, (ct, cp) in chains.items():
        cv = np.interp(tc, (ct[1:] + ct[:-1]) / 2, speed(ct, cp))
        m["corr_est_" + nm] = float(np.corrcoef(ev, cv)[0, 1])
    return m


def classify(m):
    if not m["gate_fail"]:
        return "PASS", "四项全过"
    # 只有 max 挂, 且是短促 blip, 且真值在峰值窗内远比其他链路抖, 且姿态平坦
    only_max = m["gate_fail"] == ["mx"]
    blip = m["n_runs"] <= 3 and m["longest_run"] <= 8
    if only_max and blip and m["gt_vs_indep"] >= 3.0 and m["rot_flat"]:
        return "真值伪影", (f"仅 max 超限({m['peak_ate']:.2f}mm), "
                          f"{m['n_runs']} 段共 {m['n_over']} 点(最长 {m['longest_run']}), "
                          f"峰值窗真值速度波动 {m['gt_vs_indep']:.1f}× 于独立链路, 姿态平坦")
    if m["n_runs"] >= 5 or m["span_ratio"] > 0.02:
        return "算法误差", (f"超限 {m['n_over']} 点分布 {m['n_runs']} 段"
                          f"(占 {m['span_ratio']*100:.1f}%), 持续性偏差")
    return "算法误差", f"超限形态不满足真值伪影特征(见上表)"


def main():
    rows = []
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            fu = g / "fusion" / "trajectory_fused.csv"
            if not (gt.is_file() and fu.is_file()):
                continue
            try:
                et, ep, eq = E.load_trajectory(fu)
                gtp = E.load_trajectory(gt)
                chains = {}
                v = g / "docker2_slam" / "vio_corrected_stream.csv"
                if v.is_file():
                    t, p, _ = E.load_trajectory(v)
                    chains["VINS"] = (t, p)
                for sub in ("sparse", "tight"):
                    ms = g / "fusion" / sub / "mast3r" / "trajectory_imu_metric.csv"
                    if ms.is_file():
                        t, p, _ = E.load_trajectory(ms)
                        chains["MASt3R"] = (t, p)
                        break
                m = analyse(gtp, et, ep, eq, chains)
                m["group"] = f"{b}/{g.name}"
                m["chains"] = list(chains)
                m["verdict"], m["why"] = classify(m)
                rows.append(m)
                print(f"  ✓ {m['group']:<48} {m['verdict']}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {b}/{g.name}: {exc}")
    out = Path("/tmp/claude-1000/stereoab/survey_all.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n" + "=" * 132)
    print(f"{'组':<44}{'RMSE':>7}{'P95':>7}{'Max':>8}{'10mm%':>8}{'姿态°':>7}"
          f"{'超限点':>7}{'段':>4}{'真值抖':>8}  判定")
    print("-" * 132)
    for m in sorted(rows, key=lambda r: -r["mx"]):
        print(f"{m['group']:<44}{m['rmse']:>7.3f}{m['p95']:>7.3f}{m['mx']:>8.3f}"
              f"{m['within10']:>8.3f}{m['rot']:>7.3f}"
              f"{m['n_over']:>7}{m['n_runs']:>4}{m['gt_vs_indep']:>8.2f}  {m['verdict']}")


if __name__ == "__main__":
    main()

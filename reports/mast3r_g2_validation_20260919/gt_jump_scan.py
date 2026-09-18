#!/usr/bin/env python3
"""在真值自身里找台阶跳变, 再用独立链路(VINS / MASt3R)做对照。

lighthouse_tracker_branch_gate.py 需要 tracker.csv(09-14/09-15 批次没有),
但它查的就是"台阶"这件事 —— 而台阶会原封不动传到由 tracker 派生的真值里。
所以直接在真值上查, 再问一句: VINS / MASt3R 在同一时刻有没有同样大的位移?
  GT 跳而两条独立链路都不跳  => 真值伪影(轨迹被切成两段, 单一 SE(3) 折中)
  三者同跳                  => 真实运动, 是我们的 SLAM 真错了
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


def jumps(t, p, k_mad=8.0, floor_mm=1.5):
    """返回单帧位移显著离群的样本 (idx, 时刻, 位移mm)。"""
    d = np.linalg.norm(np.diff(p, axis=0), axis=1) * 1000
    med = np.median(d)
    mad = np.median(np.abs(d - med)) * 1.4826
    thr = max(med + k_mad * max(mad, 1e-6), floor_mm)
    idx = np.flatnonzero(d > thr)
    return [(int(i), float(t[i + 1]), float(d[i])) for i in idx], med, mad, thr


def at_time(t, p, tt, half=0.030):
    """在 tt 附近 half 秒内的位移(mm)。"""
    m = (t >= tt - half) & (t <= tt + half)
    if m.sum() < 2:
        return float("nan")
    q = p[m]
    return float(np.linalg.norm(q[-1] - q[0]) * 1000)


def main():
    rows = []
    for b in BATCHES:
        for g in sorted((ROOT / b).glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            if not gt.is_file():
                continue
            gt_t, gt_p, _ = E.load_trajectory(gt)
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

            cand, med, mad, thr = jumps(gt_t, gt_p)
            # 只保留"独立链路跟不上"的那些 —— 真正可疑的
            sus = []
            for i, tt, dmm in cand:
                ref = max((at_time(ct, cp, tt) for ct, cp in chains.values()),
                          default=float("nan"))
                ratio = dmm / max(ref, 1e-6) if np.isfinite(ref) else float("nan")
                sus.append(dict(t=tt, gt_mm=dmm, ref_mm=ref, ratio=ratio))

            gt_only = [s for s in sus if np.isfinite(s["ratio"]) and s["ratio"] >= 5.0
                       and s["gt_mm"] >= 3.0]
            rows.append(dict(
                group=f"{b}/{g.name}", chains=list(chains),
                n_cand=len(cand), med_step_mm=float(med), mad_mm=float(mad),
                thr_mm=float(thr), n_gt_only=len(gt_only),
                worst_gt_only_mm=max((s["gt_mm"] for s in gt_only), default=0.0),
                gt_only=gt_only[:8]))
            flag = "⚠ 真值跳" if gt_only else "—"
            print(f"  {b}/{g.name:<12} 台阶候选 {len(cand):>4}  真值独有 {len(gt_only):>3}  {flag}")

    Path("/tmp/claude-1000/stereoab/gt_jump_scan.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))

    print("\n" + "=" * 108)
    print(f"{'组':<46}{'候选台阶':>9}{'中位步长':>9}{'阈值':>7}{'真值独有':>9}{'最大':>8}")
    print("-" * 108)
    for r in sorted(rows, key=lambda r: -r["worst_gt_only_mm"]):
        print(f"{r['group']:<46}{r['n_cand']:>9}{r['med_step_mm']:>9.3f}"
              f"{r['thr_mm']:>7.2f}{r['n_gt_only']:>9}{r['worst_gt_only_mm']:>8.2f}")

    print("\n真值独有跳变明细(位移 mm / 独立链路同期 mm / 倍数):")
    for r in sorted(rows, key=lambda r: -r["worst_gt_only_mm"]):
        if not r["gt_only"]:
            continue
        print(f"\n{r['group']}  独立链路={r['chains']}")
        for s in r["gt_only"]:
            print(f"    t={s['t']:.3f}  真值 {s['gt_mm']:>6.2f}  链路 {s['ref_mm']:>6.2f}"
                  f"  {s['ratio']:>6.1f}×")


if __name__ == "__main__":
    main()

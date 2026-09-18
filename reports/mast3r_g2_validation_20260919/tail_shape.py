#!/usr/bin/env python3
"""尾巴的形态: 是漂移、是回路冲击、还是孤立毛刺? 物理上讲不讲得通?

对每组打印:
  - 峰值出现在轨迹的百分之几处(开头/中段/末段)
  - 峰值前后 15 帧的 ATE 剖面 —— 看是"尖"还是"包"
  - 峰值窗口内的 GT 速度 vs 融合速度 —— 真值同期有没有同样快的运动
  - 超限点是否要求超出平台的加速度
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

SURVEY = json.loads(Path("/tmp/claude-1000/stereoab/survey_all.json").read_text())
ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def sp(t, p):
    return np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(t) * 1000  # mm/s


def main():
    print(f"{'组':<44}{'峰值位置':>9}{'峰宽(帧)':>9}{'尖/包':>7}"
          f"{'真值同期速度':>13}{'融合峰值速度':>13}{'加速度':>9}")
    print("-" * 104)
    detail = []
    for m in sorted(SURVEY, key=lambda r: -r["mx"]):
        g = ROOT / m["group"]
        et, ep, eq = E.load_trajectory(g / "fusion" / "trajectory_fused.csv")
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        inside, valid, interp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
        sp_, sq, G = ep[inside][valid], eq[inside][valid], interp[:, 1:]
        R, tr = E.rigid_align(sp_, G)
        ate = np.linalg.norm(sp_ @ R.T + tr - G, axis=1) * 1000
        t0 = et[inside][valid]
        k = int(np.argmax(ate))
        dur = t0[-1] - t0[0]

        # 峰宽: ATE 高于 max/2 的连续跨度
        half = ate > ate[k] / 2
        segs, s = [], None
        for i, v in enumerate(half):
            if v and s is None:
                s = i
            elif not v and s is not None:
                segs.append((s, i - 1)); s = None
        if s is not None:
            segs.append((s, len(half) - 1))
        w = next((b - a + 1 for a, b in segs if a <= k <= b), 0)

        # 峰值窗口速度
        lo, hi = max(0, k - 15), min(len(ate), k + 16)
        v_f = sp(t0[lo:hi + 1], sp_[lo:hi + 1])
        gv = np.interp((t0[lo:hi] + t0[lo + 1:hi + 1]) / 2,
                       (rt[1:] + rt[:-1]) / 2, sp(rt, rp))
        # 单帧加速度(峰值窗口内最大值)
        tm = (t0[lo:hi] + t0[lo + 1:hi + 1]) / 2          # 速度样本对应时刻
        acc = np.abs(np.diff(v_f) / np.diff(tm)) if len(v_f) > 1 else np.array([])
        acc_g = float(np.max(acc) / 9.81) if acc.size else float("nan")

        sharp = "尖" if w <= 3 else ("中" if w <= 12 else "包")
        row = dict(group=m["group"], mx=m["mx"], frac=float(t0[k] - t0[0]) / dur,
                   width=w, sharp=sharp,
                   gt_peak_v=float(gv.max()), est_peak_v=float(v_f.max()),
                   acc_g=acc_g, k=k, n=len(ate))
        detail.append(row)
        print(f"{m['group']:<44}{row['frac']*100:>8.0f}%{w:>9}{sharp:>7}"
              f"{row['gt_peak_v']:>13.0f}{row['est_peak_v']:>13.0f}{acc_g:>8.2f}g")

    Path("/tmp/claude-1000/stereoab/tail_shape.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=1))
    print("\n单位: 速度 mm/s, 加速度 g。")


if __name__ == "__main__":
    main()

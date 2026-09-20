#!/usr/bin/env python3
"""超标帧定点解剖：`ate_translation_max` 卡 10mm 的那些帧到底是什么。

## 为什么

`fusion_v2` 端到端结果里，几条最好的 cell（v10b/g1、v11b3/g1）**只剩 `ate_translation_max`
一条失败**，而 `ate_translation_within_10mm_ratio = 99.885%` ⇒ 1743 帧里只有 **2 帧**超过 10mm。
RMSE 才 2.6mm。所以「这条 cell 不过」= 「2 帧」决定的。

memory 里对这类 A 类的既有判决（`gate-failure-taxonomy-20260919`）：时间滤波逐位无效、
加重平滑更糟 ⇒ 当时判「两条药方都被否」，但**机制没定位**。

本脚本只做一件事：把超标帧一个个列出来，附上该时刻的
真值速度 / 角速度 / 前后邻帧误差 / 时间戳间隔，
用来区分三种情形：

1. **真值伪影** —— 超标帧处真值本身在跳（tracker 换分支、段边界、重采样台阶）；
2. **估计伪影** —— 相邻帧误差正常、只有单帧飞出去（重采样/插值错位）；
3. **真实误差** —— 超标帧前后误差同样大（一段整体偏移，就是 memory 里的「局部偏移块」）。

判读办法写在输出里。只读，不改任何产物。
用法: max_frame_forensics.py <estimate.csv> <ground_truth.csv>
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402


def main(est, gt_path, limit_mm=10.0):
    tg, Pg, Qg = E.load_trajectory(Path(gt_path))
    t, P, Q = E.load_trajectory(Path(est))
    _, _, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
    # 评测器不返回逐帧误差，这里按它同一套对齐（rigid_align + 同样变换）复算
    gt = plt_[:, 1:4]
    R, tt = E.rigid_align(P, gt)
    per = np.linalg.norm(P @ R.T + tt - gt, axis=1)
    if abs(per.max() - m["ate_translation_max_m"]) > 1e-9:
        raise SystemExit("复算的逐帧误差与评测器 max 不一致，对齐口径不同，拒绝继续")
    bad = np.where(per > limit_mm / 1000.0)[0]
    print(f"估计 {est}")
    print(f"  帧数 {len(per)}  RMSE {np.sqrt((per**2).mean())*1000:.3f}mm  "
          f"超过 {limit_mm}mm 的帧: {len(bad)}  ({len(bad)/len(per)*100:.3f}%)")
    if not len(bad):
        print("  ✓ 没有超标帧"); return
    # 真值速度 / 角速度
    dt = np.diff(t, prepend=t[0]); dt[dt <= 0] = np.median(dt[dt > 0])
    gv = np.linalg.norm(np.diff(plt_[:, 1:4], axis=0, prepend=plt_[:1, 1:4]), axis=1) / dt
    print(f"\n{'idx':>6}{'t(s)':>10}{'err(mm)':>10}{'上一帧':>10}{'下一帧':>10}"
          f"{'真值速度':>11}{'世界轴占比 (x,y,z)':>26}{'|err|/GT速度':>13}")
    print("-" * 100)
    for i in bad:
        e = per[i] * 1000
        prev = per[i - 1] * 1000 if i > 0 else float("nan")
        nxt = per[i + 1] * 1000 if i + 1 < len(per) else float("nan")
        d = gt[i] - (P[i] @ R.T + tt)
        frag = f"{d[0]:+.1f},{d[1]:+.1f},{d[2]:+.1f}"
        ratio = gv[i] / max(per[i], 1e-9)
        print(f"{i:>6}{t[i]:>10.3f}{e:>10.2f}{prev:>10.2f}{nxt:>10.2f}"
              f"{gv[i]*1000:>9.1f}mm/s{frag:>26}{ratio:>13.2f}")
    # 判读
    surrounding = [per[i - 1] * 1000 for i in bad if i > 0] + \
                  [per[i + 1] * 1000 for i in bad if i + 1 < len(per)]
    med_sur = np.median(surrounding) if surrounding else float("nan")
    print("\n判读：")
    print(f"  超标帧中位误差 {np.median(per[bad])*1000:.2f}mm，"
          f"其相邻帧中位 {med_sur:.2f}mm")
    if med_sur < 0.4 * np.median(per[bad]) * 1000:
        print("  ⇒ 相邻帧正常、只有单帧/少数帧飞出去 = **孤立尖峰**（重采样或插值错位嫌疑）")
    else:
        print("  ⇒ 相邻帧同样大 = **一段整体偏移**（memory 里的「局部偏移块」），不是尖峰")
    if np.any(gv[bad] * 1000 < 10.0):
        print("  ⚠ 有超标帧发生在真值近乎静止时（真值速度<10mm/s）⇒ 该处门限对时间对齐极敏感，"
              "符合 [[lighthouse-gt-timing-uncertainty]] 的「真值精度与 10mm 门同量级」")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
#!/usr/bin/env python3
"""前端 `imu_rotation_constraint_weight` A/B —— Stage A：**只跑到 [1/8]**。

## 为什么是这条

`tracker.py:479-493` 的 IMU 旋转混合是全盘**唯一从未被变过**的前端自由度
（25 次前端跑全部 `w=0.6 / gate=0.5°`，见 `scan_provenance`）。它的激活条件是
「IMU 与视觉旋转分歧 > 0.5°」⇒ **只在激烈动作段打开**（鼓包 19/19 落在快段），
且它改当前帧旋转并**连带给 `T_CkCf` 的平移分量施加旋转** ⇒
**航向误差把前进位移转成横向位移**（与「87% 误差垂直于航向」同形）。

## 判据（预先定死的尺子，不事后改）

§13/§14 已量到：「只失败在 max」那批格的**缺口中位 2.6mm**。
⇒ **任何候选杠杆必须在那个 30–50 帧宽的鼓包上移动 ≥3mm 才值得评估。**
至今全部后处理杠杆都差一个数量级（§14 结案）。

所以 Stage A 只问一件事：**这个旋钮能不能把鼓包推动 ≥3mm？**

* 前端两个输出同在 `[1/8]` 的同一内框（逐帧差 `Δ = P(w) − P(0.6)` 无需配准，
  只需基准那次的 Sim(3) 尺度把 Δ 换算成 mm）。
* `proj = Δ·(−e)/|e| > 0` ⇒ 往真值推（校正）。

⚠ 口径：这是 **前端 `[1/8]` 轨迹**，不是融合链；绝对 ATE 不可对门限读
（[[mast3r-chain-topology]]），本实验只用**逐帧差**。

用法: imu_rot_stage_a.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
TAGS = ["w0.6", "w0.3", "w0.0", "noimu", "kfoff"]   # 第一个是基准
REF = "w0.6"
WS = TAGS
CELLS = [("20260914_validation_v10_batch", "group1",
          "只失败在max 13.7/12.9"),
         ("20260915_batch5_four_videos", "group3",
          "大失败 20.6/22.4"),
         ("20260914_validation_v11_holdout_batch3", "group2",
          "两臂都PASS 9.8/8.7")]


def load(p):
    t, P, Q = E.load_trajectory(Path(p))
    return t, P, Q


def main():
    rows = []
    print(f"前端 imu_rotation_constraint_weight A/B（基准 w={REF}）\n")
    print(f"{'cell':<34}{'arm':>7}{'w':>6}{'RMSE':>9}{'MAX@帧':>13}"
          f"{'ΔMAX':>8}{'proj@峰':>9}{'|Δ|@峰':>9}{'Δrot°':>8}")
    print("-" * 112)
    for batch, group, note in CELLS:
        tg, Pg, Qg = E.load_trajectory(ROOT / batch / group /
                                       "lighthouse_body_ground_truth.csv")
        for arm in ("tight", "sparse"):
            # ★ 基准产物必须先存在，否则 base/ref_mx 会沿用上一格的陈旧值，
            #   印出假的 ΔMAX（batch5/g3 tight 的 w0.6 那次被编辑撞坏时踩过）。
            if not (HERE / "out" / batch / group / arm / REF / "trajectory_frames.csv").exists():
                print(f"{batch[:32]:<34}{arm:>7}   (无基准 {REF}，整格跳过)")
                continue
            base = None
            for w in WS:
                f = HERE / "out" / batch / group / arm / w / "trajectory_frames.csv"
                if not f.exists():
                    print(f"{batch[:32]:<34}{arm:>7}{w:>6}   (缺产物)")
                    continue
                t, P, Q = load(f)
                ins, val, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
                P, Q, t = P[ins][val], Q[ins][val], t[ins][val]
                gt = plt_[:, 1:4]
                # ★ 必须用 Sim(3) 对齐：前端是 MASt3R 单位（比真值大约 1/0.4441≈2.25×），
                #   rigid_align 会把整个尺度差算成 ~1m 的假残差（上一版就踩了这个）。
                s, R, tt = E.similarity_align(P, gt)
                ev = (s * (P @ R.T) + tt - gt) * 1000.0
                err = np.linalg.norm(ev, axis=1)
                rmse = float(np.sqrt((err ** 2).mean()))
                mx = float(err.max())
                if base is None:
                    base = (P, err, ev, Q)
                    k = int(np.argmax(err))
                n = min(len(P), len(base[0]), len(base[1]))
                D = (P[:n] - base[0][:n]) * s * 1000.0       # 前端内框 ⇒ 同尺度即 mm
                # ★ 去掉「变体之间的全局 Sim(3)」后的**形状**位移：noimu 会整体重塑
                #   轨迹（mag_p95 7.4mm）而 RMSE 只动 0.28 ⇒ 原始 Δ 里混着全局分量。
                sv, Rv, tv = E.similarity_align(P[:n], base[0][:n])
                Ds = (sv * (P[:n] @ Rv.T) + tv - base[0][:n]) * s * 1000.0
                u = base[2][:n] / np.maximum(base[1][:n], 1e-9)[:, None]
                proj = (D * (-u)).sum(axis=1)
                mag = np.linalg.norm(D, axis=1)
                # ★ 这个旋钮**直接改旋转** ⇒ 单独量变体间的相对旋转角（世界系，度）
                drot_rel = float(np.median(2 * np.degrees(np.arccos(np.clip(
                    np.abs((Q[:n] * base[3][:n]).sum(axis=1)), 0, 1)))))
                if w == REF:
                    print(f"{batch[-30:]:<34}{arm:>7}{w:>6}{rmse:>9.2f}"
                          f"{f'{mx:.2f}@{k}':>13}{'—':>8}{'—':>9}{'—':>9}{'—':>8}")
                else:
                    print(f"{batch[-30:]:<34}{arm:>7}{w:>6}{rmse:>9.2f}"
                          f"{f'{mx:.2f}@{k}':>13}{mx - ref_mx:>+8.2f}"
                          f"{proj[k]:>+9.2f}{mag[k]:>9.2f}{drot_rel:>8.3f}")
                rows.append(dict(batch=batch, group=group, arm=arm, w=w,
                                 rmse=rmse, rot=drot_rel, mx=mx, k=k, scale=s, n=n,
                                 d_peak=float(proj[k]), mag_peak=float(mag[k]),
                                 mag_max=float(mag.max()),
                                 mag_p95=float(np.percentile(mag, 95)),
                                 sh_max=float(np.linalg.norm(Ds, axis=1).max()),
                                 sh_p95=float(np.percentile(np.linalg.norm(Ds, axis=1), 95)),
                                 sh_peak=float(np.linalg.norm(Ds[k], ))))
                if w == REF:
                    ref_mx = mx
            print()
    (HERE / "imu_rot_stage_a_rows.json").write_text(json.dumps(rows, indent=1))

    print(f"{'='*112}\n■ 判据：鼓包上必须移动 ≥3mm 才值得评估（缺口中位 2.6mm）")
    for arm in ("tight", "sparse"):
        for w in [x for x in TAGS if x != REF]:
            g = [r for r in rows if r["arm"] == arm and r["w"] == w]
            if not g:
                continue
            ref = {(r["batch"], r["group"]): r["mx"] for r in rows
                   if r["arm"] == arm and r["w"] == REF}
            gm = np.array([r["mag_peak"] for r in g])
            gs = np.array([r["sh_peak"] for r in g])
            gp = np.array([r["d_peak"] for r in g])
            dm = np.array([r["mx"] - ref[(r["batch"], r["group"])] for r in g])
            print(f"  {arm:>6} w={w}  |Δ|@峰 中位 {np.median(gm):6.2f}mm"
                  f"（最大 {gm.max():6.2f}）  proj@峰 中位 {np.median(gp):+6.2f}mm"
                  f"  形状|Δ|@峰 中位 {np.median(gs):6.2f}mm  ΔMAX 中位 {np.median(dm):+6.2f}mm"
                  f"   {'✅ 动了' if np.median(gm) >= 3 else '❌ <3mm'}")


if __name__ == "__main__":
    main()
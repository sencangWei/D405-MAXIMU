#!/usr/bin/env python3
"""把「段间缝合」沿生产链铺开，回答：`batch5/g3` 那 15mm 是**谁造的**。

## 为什么要单独一个脚本

`segment_stitch.json` 只记了产品链。README 四之六 ③ 现在声称
「① 前端原始输出里就已经是 17.69mm / 8.77°」——那个断言必须有可复现的出处。

## 三个必须处理的口径问题（踩过的坑）

1. **单位**：`trajectory_frames.csv` 是 MASt3R 单位，`imu_scale_report.json:scale`
   才是 m/unit。**拿它配不带尺度的 `rigid_align` 做分段对齐会得到荒唐数字**
   （本次曾得尾段 177mm）。① 必须乘 scale。
2. **展布**：两段"各自对齐"若其中一段退化成点/直线，对齐是病态的、结论无意义。
   所以每段都报**空间展布**(P95 距质心) 与**奇异值比**，与真值同段对照。
   本脚本里展布单位是 **m**（不是 mm）。
3. **真值自洽性**：真值按同一切点做同样的缝合应当恒等（=0），这是测量的零点。

**无监督**：切点用**前端自己的开头关键帧空洞**（第一个运动关键帧的时刻），
不挑切点。真值只用于读数。

只读产物 CSV/JSON + GT + 前端日志，不跑管线。
"""
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

CELL = ("20260915_batch5_four_videos", "group3", "tight")
ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
OUT = Path(__file__).parent


def first_motion_keyframe(mast3r_log):
    """第一个运动关键帧的帧号 → 切点秒数（无监督）。"""
    m = re.search(r"Motion keyframe (\d+):", mast3r_log.read_text(errors="replace"))
    return int(m.group(1)) if m else None


def stitch(t_est, P, t_gt, P_gt, cut_s):
    """在 cut_s 切开，两段各自 rigid_align 到真值，返回两个变换的差异。"""
    ins, val, itp, _iq = E.interpolate_ground_truth(t_est, t_gt, P_gt, np.tile(
        [1.0, 0, 0, 0], (len(t_gt), 1)), 0.1)
    ts, Q = t_est[ins][val], itp[:, 1:]
    # val 是对 GT 栅格的掩码；PP 用同一掩码取估计侧
    PP = P[ins][val]
    head = (ts - ts[0]) <= cut_s
    tail = ~head
    if head.sum() < 20 or tail.sum() < 20:
        return None
    solved, ext, sv = {}, {}, {}
    for nm, m in (("head", head), ("tail", tail)):
        R, tr = E.rigid_align(PP[m], Q[m])
        e = np.linalg.norm(PP[m] @ R.T + tr - Q[m], axis=1) * 1000
        solved[nm] = (R, tr)
        X = PP[m] - PP[m].mean(0)
        ext[nm] = float(np.percentile(np.linalg.norm(X, axis=1), 95))
        sv[nm] = (np.linalg.svd(X, compute_uv=False) / np.linalg.svd(X, compute_uv=False)[0])
        solved[nm] = (R, tr, float(np.median(e)))
    (Rh, th, eh), (Rt, tt, et) = solved["head"], solved["tail"]
    c = PP[head].mean(0)
    d = float(np.linalg.norm((c @ Rh.T + th) - (c @ Rt.T + tt)) * 1000)
    ang = float(np.degrees(E.Rotation.from_matrix(Rh.T @ Rt).magnitude()))
    return dict(head_res_mm=eh, tail_res_mm=et, stitch_mm=d, stitch_deg=ang,
                head_ext_m=ext["head"], tail_ext_m=ext["tail"],
                head_sv=[float(x) for x in sv["head"]], tail_sv=[float(x) for x in sv["tail"]])


def main():
    batch, group, sub = CELL
    M = ROOT / batch / group / "fusion" / sub / "mast3r"
    F = M.parent
    GT = ROOT / batch / group / "lighthouse_body_ground_truth.csv"

    kf = first_motion_keyframe(M / "mast3r.log")
    t_gt, P_gt, _ = E.load_trajectory(GT)
    # 帧号 → 秒：用 frames.csv 的时间列，避免假设恒定帧率
    a = np.genfromtxt(M / "trajectory_frames.csv", delimiter=",", names=True)
    t_frames = np.asarray(a["t_sec"], float)
    cut_s = float(t_frames[kf] - t_frames[0])
    print(f"第一个运动关键帧 = 帧 {kf} ⇒ 切点 t-t0 = {cut_s:.4f}s （无监督，取自前端日志）\n")

    scale = json.loads((M / "imu_scale_report.json").read_text())["scale"]
    print(f"scale = {scale:.6f}  (1/scale = {1/scale:.3f}x)\n")

    CHAINS = [("①frames(前端原始)", M / "trajectory_frames.csv", scale),
              ("②imu_metric", M / "trajectory_imu_metric.csv", 1.0),
              ("③graph", M / "trajectory_graph.csv", 1.0),
              ("④fused(产品)", F / "trajectory_fused.csv", 1.0)]

    print(f"{'链':<20}{'头残差':>9}{'尾残差':>9}{'段间互差':>10}{'夹角':>8}"
          f"{'头展布m':>9}{'尾展布m':>9}")
    rows = {}
    for nm, p, mul in CHAINS:
        b = np.genfromtxt(p, delimiter=",", names=True)
        t = np.asarray(b["t_sec"], float)
        P = np.stack([b["x"], b["y"], b["z"]], 1).astype(float) * mul
        r = stitch(t, P, t_gt, P_gt, cut_s)
        rows[nm] = r
        print(f"{nm:<20}{r['head_res_mm']:9.2f}{r['tail_res_mm']:9.2f}"
              f"{r['stitch_mm']:10.2f}{r['stitch_deg']:8.2f}"
              f"{r['head_ext_m']:9.3f}{r['tail_ext_m']:9.3f}")

    # 零点：真值按同一切点做同样的缝合，应当恒等
    z = stitch(t_gt, P_gt, t_gt, P_gt, cut_s)
    print(f"\n真值【自己】同切点缝合（零点，应≈0）: 互差 {z['stitch_mm']:.4f}mm  "
          f"夹角 {z['stitch_deg']:.4f}°")
    print(f"  真值头段奇异值比 {np.round(z['head_sv'], 3)}  (对照 ① 的头段 "
          f"{np.round(rows['①frames(前端原始)']['head_sv'], 3)})")

    # 退化检查：两段都得有三维展布，朝向才受约束
    h = rows["①frames(前端原始)"]
    ok = h["head_sv"][1] > 0.05 and h["tail_sv"][1] > 0.05
    print(f"\n退化检查: 头/尾展布 {h['head_ext_m']:.3f}m / {h['tail_ext_m']:.3f}m, "
          f"第2奇异值比 {h['head_sv'][1]:.3f} / {h['tail_sv'][1]:.3f} "
          f"=> {'不退化，夹角与互差可信' if ok else '⚠ 某段近共线，朝向可能不受约束'}")

    (OUT / "stitch_chain.json").write_text(json.dumps(
        dict(cell="/".join(CELL), cut_s=cut_s, first_motion_keyframe=kf,
             scale=scale, chains=rows, truth_zero_point=z),
        ensure_ascii=False, indent=1, default=float))
    print(f"\n已写 {OUT / 'stitch_chain.json'}")


if __name__ == "__main__":
    main()

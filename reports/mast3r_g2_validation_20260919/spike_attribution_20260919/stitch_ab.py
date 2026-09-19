#!/usr/bin/env python3
"""A/B：收紧 `motion_keyframe_rotation_deg` 5.0 → 1.0，开头那 15mm 动不动。

## 背景

README 四之六 ③ 测得：`batch5/g3` 开头 7.53s 相对后面被挪开 **17.68mm / 8.71°**，
而且**在 ① 前端原始输出里就已经是**（不是 [6/8]/[7/8]/[8/9] 造成的）。
现象定位：第一个运动关键帧在**帧 226（t=7.53s）**，比真值运动起手（t≈4.8s）晚 2.7s。
⇒ 假设：运动起手段整个落在第一个关键帧区间里，没被第二个关键帧约束。

## 为什么动 rotation 而不是 translation

触发时 `translation=0.0245、rotation=5.753deg` ⇒ **rotation 触发**，且 0.0245 本来就
低于任何收紧后的 translation 阈值。收紧 translation 是空转。

## 两个切点口径都报（否则不可比）

- **固定切点** = 基线那次运行的第一关键帧时刻（7.5259s）：同一个物理切点量两次，
  直接回答"这段还是不是被挪开 15mm"。
- **各自切点** = 每次运行**自己**的第一关键帧时刻（无监督）：回答"空洞本身缩小没有"。

`①frames × scale ≡ ②imu_metric`（[6/8] 是纯缩放，见 mast3r-chain-topology），
所以 `①×scale` 上算的门指标可以当**度量链的代理**，不必跑完 [6/8]→[9/9]。

## ⚠ 这里印出来的 rmse/p95/max/w10 是【① 前端原始轨迹】的数

**不是产品链的**，也**不可**拿去对门限读。产品链还要过 [7/8] 图优化与 [8/9] 融合，
融合把残差压掉一大截（本目录 ③ 的 3.75/12.34 → 0.82/4.70 就是实例）。
这四个数在这里**只用于 A/B 之间横向比较**。
"""
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
BATCH, GROUP = "20260915_batch5_four_videos", "group3"
BASE = ROOT / BATCH / GROUP / "fusion" / "tight" / "mast3r"
GT = ROOT / BATCH / GROUP / "lighthouse_body_ground_truth.csv"
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)
OUT = Path(__file__).parent


def first_motion_keyframe(log):
    m = re.search(r"Motion keyframe (\d+):", log.read_text(errors="replace"))
    return int(m.group(1)) if m else None


def load_frames(csv, mul):
    a = np.genfromtxt(csv, delimiter=",", names=True)
    return (np.asarray(a["t_sec"], float),
            np.stack([a["x"], a["y"], a["z"]], 1).astype(float) * mul)


def stitch(t_est, P, t_gt, P_gt, cut_s):
    ins, val, itp, _ = E.interpolate_ground_truth(
        t_est, t_gt, P_gt, np.tile([1.0, 0, 0, 0], (len(t_gt), 1)), 0.1)
    ts, Q, PP = t_est[ins][val], itp[:, 1:], P[ins][val]
    head = (ts - ts[0]) <= cut_s
    tail = ~head
    if head.sum() < 20 or tail.sum() < 20:
        return None
    solved = {}
    for nm, m in (("head", head), ("tail", tail)):
        R, tr = E.rigid_align(PP[m], Q[m])
        e = np.linalg.norm(PP[m] @ R.T + tr - Q[m], axis=1) * 1000
        X = PP[m] - PP[m].mean(0)
        sv = np.linalg.svd(X, compute_uv=False)
        solved[nm] = (R, tr, float(np.median(e)),
                      float(np.percentile(np.linalg.norm(X, axis=1), 95)),
                      sv / sv[0])
    (Rh, th, eh, exh, svh), (Rt, tt, et, ext, svt) = solved["head"], solved["tail"]
    c = PP[head].mean(0)
    return dict(head_res_mm=eh, tail_res_mm=et,
                stitch_mm=float(np.linalg.norm((c @ Rh.T + th) - (c @ Rt.T + tt)) * 1000),
                stitch_deg=float(np.degrees(E.Rotation.from_matrix(Rh.T @ Rt).magnitude())),
                head_ext_m=exh, tail_ext_m=ext,
                head_sv=[float(x) for x in svh], tail_sv=[float(x) for x in svt],
                n_head=int(head.sum()), n_tail=int(tail.sum()))


def gate(t_est, P, t_gt, P_gt):
    ins, val, itp, _ = E.interpolate_ground_truth(
        t_est, t_gt, P_gt, np.tile([1.0, 0, 0, 0], (len(t_gt), 1)), 0.1)
    PP, Q = P[ins][val], itp[:, 1:]
    R, tr = E.rigid_align(PP, Q)
    err = np.linalg.norm(PP @ R.T + tr - Q, axis=1) * 1000
    g = dict(rmse=float(np.sqrt((err ** 2).mean())), p95=float(np.percentile(err, 95)),
             mx=float(err.max()), w10=float((err < 10).mean() * 100))
    return g, err


def main():
    t_gt, P_gt, Q_gt = E.load_trajectory(GT)
    scale = json.loads((BASE / "imu_scale_report.json").read_text())["scale"]

    runs = {}
    runs["基线 rot=5.0"] = dict(frames=BASE / "trajectory_frames.csv",
                             log=BASE / "mast3r.log")
    for p in sys.argv[1:]:
        p = Path(p)
        # ⚠ 用路径末两段做键：只取 parent.name 会在 /a/x 与 /b/x 之间撞名，静默覆盖
        label = "/".join(p.parts[-2:])
        if label in runs:
            raise SystemExit(f"⚠ 标签撞名: {label}（换个父目录名，或改这里的命名）")
        runs[label] = dict(frames=p / "trajectory_frames.csv", log=p / "mast3r.log")

    cuts = {}
    for nm, r in runs.items():
        kf = first_motion_keyframe(r["log"])
        r["t"], r["P"] = load_frames(r["frames"], scale)
        cuts[nm] = dict(kf=kf, cut_s=None if kf is None else float(r["t"][kf] - r["t"][0]))
        if kf is None:
            print(f"{nm:<16} ⚠ 无运动关键帧")
        else:
            print(f"{nm:<16} 第一个运动关键帧 = 帧 {kf}  ⇒ 切点 t-t0 = {cuts[nm]['cut_s']:.4f}s")

    base_cut = cuts["基线 rot=5.0"]["cut_s"]
    print(f"\n基线切点（固定口径）= {base_cut:.4f}s\n")

    z = stitch(t_gt, P_gt, t_gt, P_gt, base_cut)
    print(f"真值自缝零点: 互差 {z['stitch_mm']:.4f}mm 夹角 {z['stitch_deg']:.4f}°\n")

    for cutname in ("固定切点", "各自切点"):
        print(f"=== {cutname} ===")
        print(f"{'运行':<16}{'切点s':>9}{'头残差':>9}{'尾残差':>9}{'段间互差':>10}{'夹角':>8}"
              f"{'rmse①':>8}{'p95①':>8}{'max①':>8}{'w10①':>7}")
        res = {}
        for nm, r in runs.items():
            c = base_cut if cutname == "固定切点" else cuts[nm]["cut_s"]
            s = stitch(r["t"], r["P"], t_gt, P_gt, c)
            g, err = gate(r["t"], r["P"], t_gt, P_gt)
            res[nm] = dict(cut_s=c, stitch=s, gate=g)
            if s is None:
                print(f"{nm:<16}{c:9.2f}  ⚠ 某段样本不足")
                continue
            print(f"{nm:<16}{c:9.2f}{s['head_res_mm']:9.2f}{s['tail_res_mm']:9.2f}"
                  f"{s['stitch_mm']:10.2f}{s['stitch_deg']:8.2f}"
                  f"{g['rmse']:8.2f}{g['p95']:8.2f}{g['mx']:8.2f}{g['w10']:7.1f}")
        print()

    print("退化检查（固定切点，①×scale）:")
    for nm, r in res.items():
        s = r["stitch"]
        if s is None:
            continue
        ok = s["head_sv"][1] > 0.05 and s["tail_sv"][1] > 0.05
        print(f"  {nm:<16} 展布 {s['head_ext_m']:.3f}m/{s['tail_ext_m']:.3f}m  "
              f"第2奇异值比 {s['head_sv'][1]:.3f}/{s['tail_sv'][1]:.3f} "
              f"=> {'可信' if ok else '⚠ 近共线'}")

    print("\n⚠ rmse①/p95①/max①/w10① 是【① 前端原始轨迹】的数，不是产品链的，"
          "只用于横向比较、不可对门限读。")
    print("（产品链牌价参考本目录 ③：① 3.75/12.34 → ④ 0.82/4.70）")
    print("门限:", LIM)
    (OUT / "stitch_ab.json").write_text(json.dumps(
        dict(scale=scale, base_cut_s=base_cut, cuts=cuts, results=res,
             truth_zero=z, lim=LIM), ensure_ascii=False, indent=1, default=float))
    print(f"已写 {OUT / 'stitch_ab.json'}")


if __name__ == "__main__":
    main()

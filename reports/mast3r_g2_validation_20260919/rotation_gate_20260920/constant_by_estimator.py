#!/usr/bin/env python3
"""决定性分辨：那个恒定体轴偏移是**真值侧的**还是**估计侧的**。

## 逻辑

度量只比较估计与 GT，所以「常量在估计里」和「常量在 GT 里」在读数上同形
（`ground_truth_poses = tracker_poses @ tracker_T_target` 是**固定左乘**，
所以 GT 的自洽性 = tracker 自己的自洽性，GT 侧无法靠内部检查发现）。

但可以用**两个互相独立的估计**去对同一个 GT 解同一个常量：

  * 若两个估计解出的 C 一致 ⇒ C 是**它们共享的东西**的属性 ⇒ 指向 GT（或共享的配置）；
  * 若两个估计解出的 C 不同 ⇒ C 是**各自的估计误差** ⇒ 指向估计侧。

这里比的是：
  A. `vins_raw.csv`（VINS 估计器，体轴系）
  B. `trajectory_frames.csv`（MASt3R 前端，**相机**系 → 用配置的 body_T_cam0 转到体轴）
两者不共享估计器（前端只把 IMU 当旋转先验，不用 VINS 的状态）。

⚠ 已知的混淆项：B 转体轴要用 `body_T_cam0`，这个外参本身若错 δ，
   B 的 C 会整体偏 δ。所以「一致」是强证据，「不一致」要再看是不是外参造成的。

## 附带自检

同一估计器换不同配置重跑（融合尾段参数代际）解出的 C 若一致，
说明 C 对该估计是稳定的 —— 进一步支持「C 不是尾段造成的」。
"""
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
VINS_CFG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release"
                "/formal_runtime_calibration/vins_config.yaml")


def body_T_cam0():
    text = VINS_CFG.read_text(encoding="utf-8")
    block = re.search(r"^body_T_cam0\s*:(.*?)(?=^\w|\Z)", text, re.M | re.S)
    vals = [float(x) for x in re.findall(r"-?\d+\.\d+", block.group(1))][:16]
    return np.array(vals).reshape(4, 4)


def load(est, gt):
    te, Pe, Qe = E.load_trajectory(est)
    tg, Pg, Qg = E.load_trajectory(gt)
    inside, valid, ctx, iq = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
    return Pe[inside][valid], Qe[inside][valid], ctx[:, 1:4], iq


def gate(Pe, Qe, Pg, Qg, C=None):
    Q = Qe if C is None else (Rotation.from_quat(Qe) * C).as_quat()
    R, _ = E.rigid_align(Pe, Pg)
    d = Rotation.from_quat(Qg).inv() * (Rotation.from_matrix(R) * Rotation.from_quat(Q))
    return float(np.sqrt(np.mean(np.degrees(d.magnitude()) ** 2)))


def solve_C(Pe, Qe, Pg, Qg):
    f = lambda x: gate(Pe, Qe, Pg, Qg, Rotation.from_rotvec(x))  # noqa: E731
    r = minimize(f, np.zeros(3), method="Nelder-Mead",
                 options=dict(xatol=1e-9, fatol=1e-10, maxiter=20000))
    return Rotation.from_rotvec(r.x), f(r.x)


def main():
    T_bc = body_T_cam0()
    R_bc = Rotation.from_matrix(T_bc[:3, :3])
    print(f"body_T_cam0 旋转角 {np.degrees(R_bc.magnitude()):.2f}°\n")

    groups = [f.parents[2] for f in sorted(
        ROOT.glob("**/fusion_current/tight/trajectory_fused.csv"))]
    print(f"{'cell':<40}{'A: vins_raw':>26}│{'B: 前端(相机→体)':>26}│{'A vs B':>8}")
    print(f"{'':<40}{'门':>8}{'|C|':>8}{'C(x,y,z)':>10}"
          f"│{'门':>8}{'|C|':>8}{'C(x,y,z)':>10}│{'夹角':>8}")
    print("-" * 104)
    rows = []
    for g in groups:
        gt = g / "lighthouse_body_ground_truth.csv"
        vins = g / "docker2_slam/vio_raw.csv"
        frames = g / "fusion/tight/mast3r/trajectory_frames.csv"
        if not (gt.exists() and vins.exists() and frames.exists()):
            continue
        name = str(g).replace(str(ROOT) + "/", "")

        Pe, Qe, Pg, Qg = load(vins, gt)
        Ca, ga = solve_C(Pe, Qe, Pg, Qg)
        base_a = gate(Pe, Qe, Pg, Qg)

        Pc, Qc, Pgc, Qgc = load(frames, gt)
        # 相机系 → 体轴系：T_wb = T_wc · T_cb，T_cb = inv(body_T_cam0)
        T_cb = np.linalg.inv(T_bc)
        R_cb = Rotation.from_matrix(T_cb[:3, :3])
        t_cb = T_cb[:3, 3]
        Rwc = Rotation.from_quat(Qc)
        Pb = Pc + Rwc.apply(t_cb)
        Qb = (Rwc * R_cb).as_quat()
        Cb, gb = solve_C(Pb, Qb, Pgc, Qgc)
        base_b = gate(Pb, Qb, Pgc, Qgc)

        ang = np.degrees((Ca * Cb.inv()).magnitude())
        print(f"{name:<40}{base_a:>8.2f}{np.degrees(Ca.magnitude()):>8.2f}"
              f"{str(np.round(np.degrees(Ca.as_rotvec()), 2)):>10}"
              f"│{base_b:>8.2f}{np.degrees(Cb.magnitude()):>8.2f}"
              f"{str(np.round(np.degrees(Cb.as_rotvec()), 2)):>10}│{ang:>8.2f}")
        rows.append(dict(name=name, Ca=Ca, Cb=Cb,
                         base_a=base_a, base_b=base_b,
                         ga_own=ga, gb_own=gb,
                         ga_cross=gate(Pe, Qe, Pg, Qg, Cb),
                         gb_cross=gate(Pb, Qb, Pgc, Qgc, Ca)))

    if not rows:
        print("没有可比 cell")
        return
    angs = [np.degrees((r["Ca"] * r["Cb"].inv()).magnitude()) for r in rows]
    print(f"\n=== A(vins_raw) 与 B(前端) 解出的常量夹角 ===")
    print(f"  中位 {np.median(angs):.2f}°  范围 {min(angs):.2f}–{max(angs):.2f}°")
    print(f"  A 的 |C| 中位 {np.median([np.degrees(r['Ca'].magnitude()) for r in rows]):.2f}°")
    print(f"  B 的 |C| 中位 {np.median([np.degrees(r['Cb'].magnitude()) for r in rows]):.2f}°")
    for k in ("Ca", "Cb"):
        v = [r[k] for r in rows]
        pw = [np.degrees((x * y.inv()).magnitude()) for i, x in enumerate(v)
              for j, y in enumerate(v) if i < j]
        print(f"  {k} 跨 take 两两夹角：中位 {np.median(pw):.2f}°  p90 {np.percentile(pw, 90):.2f}°")
    print("\n=== 交叉迁移：把对方估计器解出的常量用到自己身上 ===")
    print(f"  {'cell':<40}{'A基线':>8}{'A用C_A':>9}{'A用C_B':>9}│"
          f"{'B基线':>8}{'B用C_B':>9}{'B用C_A':>9}")
    print("  " + "-" * 92)
    for r in rows:
        print(f"  {r['name']:<40}{r['base_a']:>8.2f}{r['ga_own']:>9.2f}"
              f"{r['ga_cross']:>9.2f}│{r['base_b']:>8.2f}{r['gb_own']:>9.2f}"
              f"{r['gb_cross']:>9.2f}")
    import numpy as _np
    ba = _np.array([r["base_a"] for r in rows])
    ao = _np.array([r["ga_own"] for r in rows])
    ax = _np.array([r["ga_cross"] for r in rows])
    bb = _np.array([r["base_b"] for r in rows])
    bo = _np.array([r["gb_own"] for r in rows])
    bx = _np.array([r["gb_cross"] for r in rows])
    print(f"\n  A: 基线中位 {_np.median(ba):.2f} → 用C_A {_np.median(ao):.2f} "
          f"→ 用C_B {_np.median(ax):.2f}   （C_B 迁移改善 {int((ax<ba).sum())}/{len(rows)}）")
    print(f"  B: 基线中位 {_np.median(bb):.2f} → 用C_B {_np.median(bo):.2f} "
          f"→ 用C_A {_np.median(bx):.2f}   （C_A 迁移改善 {int((bx<bb).sum())}/{len(rows)}）")


if __name__ == "__main__":
    main()

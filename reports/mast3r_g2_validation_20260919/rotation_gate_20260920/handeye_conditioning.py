#!/usr/bin/env python3
"""手眼外参在这个数据集上**可辨识吗**。

## 为什么问

`handeye_vs_oracle.py` 里手眼解出来的 R_bc 离配置值 ~89°，而拟合残差只有 ~0.94°。
两个可能：①解落到退化的远处；②配置真的错得离谱。区分方法只有一个——
**看代价函数在配置值附近的形状**：

  - 若代价在配置值附近是**平的**（沿任何方向的曲率都落在噪声里）
    ⇒ 这个问题在此数据上不可辨识，手眼解是噪声，`estimate_extrinsic` 也救不了；
  - 若代价在配置值处**明显高于**最优点、且有明确下降方向
    ⇒ 外参真的可辨识，配置确实偏了，值得改。

相对旋转太小 / 转轴太单一，是这类退化的常见来源。这里同时报：
  * 相对旋转的**幅度分布**与**转轴散布**（激励够不够）
  * 代价沿 3 个切向在 ±2° 内的曲线
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402
from handeye_vs_oracle import read_body_t_cam0  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")


def pairs_for(frames_csv, vins_csv, dts=(0.3, 0.5, 1.0, 2.0)):
    tc, _, Qc = E.load_trajectory(frames_csv)
    tv, _, Qv = E.load_trajectory(vins_csv)
    keep = (tc >= tv[0]) & (tc <= tv[-1])
    t = tc[keep]
    Qv_i = Slerp(tv, Rotation.from_quat(Qv))(t)
    Rc = Rotation.from_quat(Qc[keep])
    dc, db = [], []
    for dt in dts:
        n = max(1, int(round(dt / np.median(np.diff(t)))))
        a = Rc[:-n].inv() * Rc[n:]
        b = Qv_i[:-n].inv() * Qv_i[n:]
        sel = np.degrees(a.magnitude()) > np.percentile(np.degrees(a.magnitude()), 50)
        dc.append(a[sel])
        db.append(b[sel])
    return Rotation.concatenate(dc), Rotation.concatenate(db)


def cost(R_bc, dc, db, side="right"):
    """side=right: ΔR_body = R_bc ΔR_cam R_bc⁻¹ ；side=left: 反过来。"""
    pred = (R_bc * dc * R_bc.inv()) if side == "right" else (R_bc.inv() * dc * R_bc)
    return float(np.mean(np.degrees((db.inv() * pred).magnitude()) ** 2))


def main():
    R_cfg = Rotation.from_matrix(read_body_t_cam0())
    print(f"配置 R_bc 转角 {np.degrees(R_cfg.magnitude()):.2f}°\n")

    groups = [f.parents[2] for f in sorted(
        ROOT.glob("**/fusion_current/tight/trajectory_fused.csv"))]
    print(f"{'take':<44}{'n':>6}{'ΔR中位':>8}{'ΔR p90':>8}"
          f"{'代价@cfg':>10}{'代价@最优':>10}{'最优离cfg':>10}")
    print("-" * 96)
    curves = {}
    for g in groups:
        frames = g / "fusion/tight/mast3r/trajectory_frames.csv"
        vins = g / "docker2_slam/vio_corrected_stream.csv"
        if not (frames.exists() and vins.exists()):
            continue
        name = str(g).replace(str(ROOT) + "/", "")
        dc, db = pairs_for(frames, vins)
        mag = np.degrees(dc.magnitude())
        c_cfg = cost(R_cfg, dc, db)
        # 局部曲率：沿 3 个切向各走 ±2°
        prof = []
        for ax in range(3):
            for eps in np.linspace(-2.0, 2.0, 9):
                e = np.zeros(3)
                e[ax] = np.radians(eps)
                prof.append((eps, cost(R_cfg * Rotation.from_rotvec(e), dc, db)))
        curves[name] = prof
        # 全局最优（粗搜 + 精修）只作参照：看它离配置多远
        best, bx = 1e18, None
        rng = np.random.default_rng(0)
        for _ in range(400):
            R0 = Rotation.random(random_state=rng) * R_cfg
            c = cost(R0, dc, db)
            if c < best:
                best, bx = c, R0
        print(f"{name:<44}{len(dc):>6}{np.median(mag):>8.1f}"
              f"{np.percentile(mag, 90):>8.1f}{c_cfg:>10.2f}{best:>10.2f}"
              f"{np.degrees((bx * R_cfg.inv()).magnitude()):>9.0f}°")

    print("\n=== 代价沿切向的曲线（配置值处为 0°，单位 deg²）===")
    for name, prof in curves.items():
        by_ax = {0: [], 1: [], 2: []}
        for (eps, c), ax in zip(prof, [i // 9 for i in range(len(prof))]):
            by_ax[ax].append(c)
        s = "  ".join(f"轴{ax}: " + "/".join(f"{c:.1f}" for c in by_ax[ax])
                      for ax in range(3))
        print(f"  {name.split('/')[-2]}/{name.split('/')[-1]}")
        print(f"    (ε=-2..+2°) {s}")


if __name__ == "__main__":
    main()

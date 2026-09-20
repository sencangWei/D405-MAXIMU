#!/usr/bin/env python3
"""姿态杠杆 A/B（在**新融合配置**下重验）：`--use-docker2-orientation-for-lever-arm` 要不要留？

## 为什么

`[8/9]` 带这个开关时，`camera_to_body_with_body_orientation_prior` 把
`mast3r_rotations` **重绑成对齐后的 VINS 姿态** ⇒ **MASt3R 视觉姿态被整个丢弃**，
输出四元数 ≡ `常量 × VINS姿态`。不带时走 `camera_to_body`，保留 `[7/8]` 联合
优化出来的姿态（那里 MASt3R 视觉 sigma 0.5° 与陀螺 0.25° 是一起优化的）。

`rotation_gate_20260920/attitude_ab.txt` 曾在 **`lw=0.25 sw=0.475 adaptive`** 下测过：
**门 rot 18/18 改善，中位 −0.088°，ATE 中位 +0.005mm**。

但现役已改成 `lw=0, sw=0.25`（见 `lw35_vs_lw0_ab.py` / `sw_sweep_lw0.py`），
**结论必须在同一配置下复验**，否则又是一个「换配置后结论失效」。

## 做法

同一个 `[7/8]` graph，只切这个开关；两臂都平滑（σ=0.025s，与工作流一致）；
官方评测器。两条臂（tight/sparse）都跑 ⇒ 组数翻倍。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
              "formal_runtime_calibration/vins_config.yaml")
FUSE = Path("/home/robot/ego_vio_humble/scripts/fuse_docker2_mast3r_complementary.py")
SMOOTH = Path("/home/robot/ego_vio_humble/scripts/smooth_pose_trajectory.py")
# ★ 与现役工作流逐字一致（除了本实验要切的那个开关）
FUSE_ARGS = ["--scale-horizon-s", "1", "--smoothing-s", "8",
             "--docker2-local-weight", "0", "--docker2-scale-weight", "0.25",
             "--roughness-threshold-mm", "9", "--adaptive-weight-strength", "0.45"]


def acceptance(cell):
    p = cell / "docker2_slam/run_acceptance.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    f = list(d.get("runtime_watchdog", {}).get("failures", []))
    return p if (d.get("result") == "PASS" and not f) else None


def cells():
    out = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_graph.csv")):
            c = p.parents[3]
            if not (c / "lighthouse_body_ground_truth.csv").exists():
                continue
            if not (c / "docker2_slam/vio_corrected_stream.csv").exists():
                continue
            out.append((c, arm))
    return out


def run(cell, arm, use_flag, td):
    md = cell / f"fusion/{arm}/mast3r"
    raw, rep = td / "raw.csv", td / "rep.json"
    cmd = [sys.executable, str(FUSE),
           "--mast3r", str(md / "trajectory_graph.csv"),
           "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
           "--body-t-camera-yaml", str(CONFIG),
           "--graph-report", str(md / "graph_fusion_report.json"),
           *FUSE_ARGS, "--output", str(raw), "--report", str(rep)]
    acc = acceptance(cell)
    if acc is not None:                      # 验收不过就不传报告（lw=0 时无副作用）
        cmd += ["--docker2-report", str(acc)]
    if use_flag:
        cmd.append("--use-docker2-orientation-for-lever-arm")
    subprocess.run(cmd, check=True, capture_output=True)
    sm, smr = td / "sm.csv", td / "sm.json"
    subprocess.run([sys.executable, str(SMOOTH), "--input", str(raw),
                    "--output", str(sm), "--method", "gaussian",
                    "--gaussian-sigma-s", "0.025", "--report", str(smr)],
                   check=True, capture_output=True)
    return sm


def main():
    cs = cells()
    print(f"姿态杠杆 A/B（lw=0, sw=0.25，与现役一致）；{len(cs)} 组\n")
    print(f"{'cell':<44}{'arm':>7}{'门rot flag→nof':>20}{'ATE flag→nof':>20}{'P95':>18}")
    print("-" * 110)
    rows = []
    for cell, arm in cs:
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        vals = []
        for flag in (True, False):
            with tempfile.TemporaryDirectory() as t:
                try:
                    sm = run(cell, arm, flag, Path(t))
                    t2, P, Q = E.load_trajectory(sm)
                    _, _, plt_, qlt_ = E.interpolate_ground_truth(t2, tg, Pg, Qg, 0.1)
                    m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
                    vals.append((m["ate_rotation_rmse_deg"],
                                 m["ate_translation_rmse_m"] * 1000,
                                 m["ate_translation_p95_m"] * 1000))
                except Exception:                                     # noqa: BLE001
                    vals.append((float("nan"),) * 3)
        a, b = vals
        if np.isnan(a[0]) or np.isnan(b[0]):
            print(f"{str(cell).replace(str(ROOT)+'/', ''):<44}{arm:>7}   求解失败")
            continue
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name:<44}{arm:>7}"
              f"{a[0]:>10.2f}→{b[0]:<9.2f}{a[1]:>10.2f}→{b[1]:<9.2f}"
              f"{a[2]:>9.2f}→{b[2]:<8.2f}")
        rows.append((a, b))
    if not rows:
        return
    A = np.array([[r[0][0], r[1][0], r[0][1], r[1][1]] for r in rows])
    d_rot, d_ate = A[:, 1] - A[:, 0], A[:, 3] - A[:, 2]
    print("\n" + "=" * 110)
    print(f"=== 汇总 {len(rows)} 组 ===")
    print(f"  门rot 改善: {int((d_rot < 0).sum())}/{len(rows)}   "
          f"中位变化 {np.median(d_rot):+.3f}°   (负=去掉开关更好)")
    print(f"  ATE_T 变差>2%: {int((d_ate > 0.02*np.maximum(A[:,2],1e-9)).sum())}/{len(rows)}   "
          f"中位变化 {np.median(d_ate):+.3f} mm")
    print(f"  门rot 中位:  去掉开关 {np.median(A[:,1]):.3f}°  vs  保留 {np.median(A[:,0]):.3f}°")
    print(f"\n判读：若「去掉开关」在多数 cell 上改善门且 ATE 不动 ⇒ 该开关是纯粹的"
          "净损失，应删。")


if __name__ == "__main__":
    main()
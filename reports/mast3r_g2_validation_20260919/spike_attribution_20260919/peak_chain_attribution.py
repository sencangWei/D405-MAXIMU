#!/usr/bin/env python3
"""峰那一帧，误差是【哪条链】造的？沿航向还是垂直？

## 为什么做这个

`v10_batch/group1/tight` 的 `ate_translation_max` 峰值在**帧 469**（t−t0=15.630s）。
先前三条解释**全部被自己的数据否掉**：

1. **"欠走"**（估计单位时间位移不足）—— 峰值处**累计欠走积分 = −7.91mm**（估计反而走多了），
   `corr(累计欠走, 误差) = −0.456`，整条路径比 0.9809。**反相关**。
2. **"过度平滑"** —— 估计的**步进 p95 = 7.08mm ≈ 真值 7.26mm**，它走得动。
3. **"tracker 换分支"** —— `lighthouse_tracker_branch_gate.py` 报 **0 次切换**，PASS。

## 这个脚本问什么

`pose_errors` 的对齐是**整条轨迹一次性全局 `rigid_align`**（那个 `delta=30` 是 RPE 的，
不是对齐窗）。所以 `max` 有可能是全局折中甩出来的假峰。两件事要分开：

- **是不是局部真差异** ⇒ 在峰附近**开窗、窗口内各自重新对齐**，再看窗口内误差。
- **是谁造的** ⇒ ①前端 / ③图优化 / ④融合未平滑 / ⑤融合平滑 / VINS 各自在峰那一帧差多少。

分解口径：残差向量相对**真值自身航向**（`Q[i+k]−Q[i−k]` 归一）投影，
`along` = 沿航向（走快走慢），`perp` = 垂直航向（横向偏）。

⚠ ①前端是 MASt3R 单位，**必须乘 `imu_scale_report.json` 的 scale** 才可比。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

G = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow"
         "/20260914_validation_v10_batch/group1")
FE = G / "fusion/tight/mast3r"
CUR = G / "fusion_current/tight"
VINS = G / "docker2_slam/vio_corrected_stream.csv"
GT = G / "lighthouse_body_ground_truth.csv"

PEAK = 469          # 先前测得的 ate_translation_max 所在下标
HALF = 45           # 归属窗口半宽（帧）
HEAD = 6            # 估算真值航向用的前后帧距


def load(p):
    return E.load_trajectory(Path(p))


def heading(P, i, k=HEAD):
    """真值航向 = 真值**位置**在 i 处的前后位移方向（不是四元数差）。"""
    a, b = max(0, i - k), min(len(P) - 1, i + k)
    v = P[b] - P[a]
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else np.array([1.0, 0.0, 0.0])


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else CUR
    scale = json.loads((FE / "imu_scale_report.json").read_text())["scale"]
    t_gt, P_gt, Q_gt = load(GT)

    chains = {}
    tf, Pf, _ = load(FE / "trajectory_frames.csv")
    chains["①frames(raw前端)×scale"] = (tf, Pf * scale)
    tg, Pg, _ = load(outdir / "mast3r/trajectory_graph.csv")
    chains["③graph [7/8]"] = (tg, Pg)
    tu, Pu, _ = load(outdir / "trajectory_fused_unsmoothed.csv")
    chains["④fused未平滑 [8/9]"] = (tu, Pu)
    ts, Ps, _ = load(outdir / "trajectory_fused.csv")
    chains["⑤fused平滑后 [9/9]"] = (ts, Ps)
    tv, Pv, _ = load(VINS)
    chains["VINS corrected"] = (tv, Pv)

    h = heading(P_gt, PEAK)
    print(f"目录 = {outdir}")
    print(f"scale = {scale:.6f}   GT 航向(帧{PEAK}) = {np.round(h, 4)}\n")
    print(f"{'链':<24}{'方法':<10}{'峰帧残差':>10}{'沿航向':>9}{'垂直':>9}"
          f"{'窗口max':>9}{'窗口内占比垂直':>15}")
    print("-" * 88)

    for name, (t, P) in chains.items():
        for mode in ("全局对齐", "窗口对齐"):
            tt = t
            ins, val, itp, iq = E.interpolate_ground_truth(tt, t_gt, P_gt, Q_gt, 0.1)
            idx = np.where(ins)[0][val]
            PP, QQ = P[idx], itp[:, 1:]
            # 峰在本链上的下标 = 真值帧 469 在有效子集里的位置
            k = int(np.argmin(np.abs(idx - PEAK)))
            if mode == "全局对齐":
                m = np.ones(len(PP), bool)
            else:
                m = np.zeros(len(PP), bool)
                m[max(0, k - HALF):k + HALF + 1] = True
            R, tr = E.rigid_align(PP[m], QQ[m])
            res = (PP[m] @ R.T + tr) - QQ[m]
            e = np.linalg.norm(res, axis=1) * 1000
            km = int(np.argmin(np.abs(np.where(m)[0] - k)))
            r = res[km]
            hh = heading(QQ, k)
            along = float(np.dot(r, hh)) * 1000
            perp = float(np.sqrt(max(0.0, (np.linalg.norm(r) * 1000) ** 2 - along ** 2)))
            frac = ""
            if mode == "窗口对齐":
                frac = f"{100.0 * np.median(np.abs(res - (res @ hh)[:, None] * hh) / (np.linalg.norm(res, axis=1, keepdims=True) + 1e-12)):.0f}%"
            print(f"{name:<24}{mode:<10}{e[km]:10.2f}{along:9.2f}{perp:9.2f}"
                  f"{e.max():9.2f}{frac:>15}")

    # 真值自己：峰附近每帧的步长分解，看真值自己有没有横向异常
    print("\n【真值自检】帧 469 附近真值每帧步长分解（沿其自身航向 / 垂直）")
    for j in range(PEAK - 6, PEAK + 7):
        if j < 1 or j >= len(P_gt):
            continue
        hh = heading(P_gt, j)
        d = P_gt[j] - P_gt[j - 1]
        al = float(np.dot(d, hh)) * 1000
        pe = float(np.sqrt(max(0.0, (np.linalg.norm(d) * 1000) ** 2 - al ** 2)))
        mark = "  ← 峰" if j == PEAK else ""
        print(f"  帧{j:>5}  步长{np.linalg.norm(d)*1000:7.2f}  沿{al:7.2f}  垂直{pe:7.2f}{mark}")


if __name__ == "__main__":
    main()

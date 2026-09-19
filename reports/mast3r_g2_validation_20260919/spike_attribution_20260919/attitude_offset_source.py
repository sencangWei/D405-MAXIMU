#!/usr/bin/env python3
"""那个「每 take 一个常量姿态偏移」到底从哪来。

## 背景（前一步的结论）

`rotation_budget.py` 测出：官方 rot 门 = √(VINS姿态噪声² + 常量偏移²)。
常量偏移中位 1.52°（占 2.0° 门限的 76%），逐 take 一个常量、轴向不一致。
本脚本追它的**来源**。

## 测了什么，得到什么

1. **否掉「尺度伪影」**：Procrustes 里尺度与旋转解析解耦
   （尺度由 ‖A‖ 定，旋转由 A·Bᵀ 的 SVD 定），换 similarity 的 R
   得到**逐位相同**的偏移 ⇒ 偏移与尺度无关。这条能否掉，但在结构上必然如此。

2. **确认是旋转漂移的签名**：把轨迹切两半各自 `rigid_align`，
   两个 R 的夹角 = 半段漂移。VINS 的偏移 ≈ 半段漂移的一半
   （rigid_align 落在漂移中点，姿态不跟着漂，于是差一半）。

3. **融合修掉大半**：VINS 半段漂移 5.52° → fused 1.10°；
   偏移 3.10° → 1.57°。**姿态是逐位相同的（见 rotation_budget part7），
   所以 off 的任何变化都只能来自位置侧** —— 融合的位置修正确实在收这个口子。

4. **★ 残留的 1.57° 落在【位置分辨带】里**：
   轨迹半径中位 15.9cm、位置 RMSE ~2.5mm
   ⇒ 位置数据能分辨的最小旋转 = atan(2.5/159) ≈ 1.52°。
   把位置最优 R 换成姿态最优 R，位置 RMSE 只涨 **+1.1mm**（埋在位置噪声里）。
   ⇒ 这 1.57° 的旋转，位置数据本来就定不出来。

⇒ **rot 门的底 = √(1.2² + 1.5²) ≈ 1.9°，门限 2.0°。**
   16cm 半径的桌面轨迹上，这道门本身就是掷硬币，不是调参问题。

只读产物 CSV + 官方评测器，不跑管线、不改任何东西。
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation as Rot

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
OUT = Path(__file__).parent


def on_gt(path, rt, rp, rq, min_n=20):
    """把一条链的位姿摆到 GT 栅格上，返回 (位置, GT位置, 姿态四元数, GT姿态四元数)。"""
    et, ep, eq = E.load_trajectory(Path(path))
    ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if val.sum() < min_n:
        return None
    return ep[ins][val], itp[:, 1:], eq[ins][val], iq


def attitude_optimal(Pq, Qq):
    """min ‖A − Rc·B‖_F 的最优旋转，A=Rq_gt, B=Rq_est。"""
    A = np.asarray(Rot.from_quat(Qq).as_matrix())
    B = np.asarray(Rot.from_quat(Pq).as_matrix())
    M = (A @ B.transpose(0, 2, 1)).mean(0)
    U, _, Vt = np.linalg.svd(M)
    D = np.eye(3)
    D[2, 2] = np.linalg.det(U @ Vt)
    return U @ D @ Vt


def measure(P, Q, Pq, Qq):
    """一条链的全部量。"""
    R_pos, t = E.rigid_align(P, Q)
    pos_rmse = float(np.linalg.norm(P @ R_pos.T + t - Q, axis=1).mean() * 1000)

    R_att = attitude_optimal(Pq, Qq)
    t2 = Q.mean(0) - R_att @ P.mean(0)
    pos_rmse_att = float(np.linalg.norm(P @ R_att.T + t2 - Q, axis=1).mean() * 1000)

    offset = float(np.degrees(
        (Rot.from_matrix(R_att).inv() * Rot.from_matrix(R_pos)).magnitude()))

    # 半段漂移：前后半段各自对齐，两个 R 的夹角
    h = len(P) // 2
    Ra, _ = E.rigid_align(P[:h], Q[:h])
    Rb, _ = E.rigid_align(P[h:], Q[h:])
    half_drift = float(np.degrees(
        (Rot.from_matrix(Ra).inv() * Rot.from_matrix(Rb)).magnitude()))

    radius = float(np.linalg.norm(P - P.mean(0), axis=1).mean())

    return dict(offset_deg=offset, half_drift_deg=half_drift,
                radius_m=radius, pos_rmse_mm=pos_rmse,
                pos_rmse_with_attitude_R_mm=pos_rmse_att,
                pos_cost_of_offset_mm=pos_rmse_att - pos_rmse,
                # 位置数据能分辨的最小旋转
                resolvable_band_deg=float(np.degrees(np.arctan(pos_rmse / 1000.0 / radius))))


def main():
    rows = []
    for b in sorted(ROOT.glob("2026*")):
        for g in sorted(b.glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            if not gt.is_file():
                continue
            rt, rp, rq = E.load_trajectory(gt)
            for sub in ("sparse", "tight"):
                for chain, path in (
                    ("VINS", g / "docker2_slam" / "vio_corrected_stream.csv"),
                    ("fused", g / "fusion" / sub / "trajectory_fused.csv"),
                ):
                    if not path.is_file():
                        continue
                    d = on_gt(path, rt, rp, rq)
                    if d is None:
                        continue
                    m = measure(*d)
                    m.update(cell=f"{b.name}/{g.name}/{sub}", chain=chain)
                    rows.append(m)

    print("=" * 100)
    print(f"{'cell':<44}{'链':<7}{'偏移':>8}{'半段漂移':>10}{'半径':>8}"
          f"{'位置RMSE':>10}{'换姿态R后':>11}{'代价':>8}{'分辨带':>8}")
    print("-" * 100)
    for r in rows:
        print(f"{r['cell']:<44}{r['chain']:<7}{r['offset_deg']:7.2f}°"
              f"{r['half_drift_deg']:9.2f}°{r['radius_m'] * 100:7.1f}cm"
              f"{r['pos_rmse_mm']:9.2f}mm{r['pos_rmse_with_attitude_R_mm']:10.2f}mm"
              f"{r['pos_cost_of_offset_mm']:+7.2f}mm{r['resolvable_band_deg']:7.2f}°")

    print()
    for tag in ("VINS", "fused"):
        x = [r for r in rows if r["chain"] == tag]
        if not x:
            continue
        off = np.array([r["offset_deg"] for r in x])
        hd = np.array([r["half_drift_deg"] for r in x])
        cost = np.array([r["pos_cost_of_offset_mm"] for r in x])
        band = np.array([r["resolvable_band_deg"] for r in x])
        print(f"{tag:<7} n={len(x)}")
        print(f"    偏移中位 {np.median(off):5.2f}°   半段漂移中位 {np.median(hd):5.2f}°"
              f"   off/(漂移/2) 中位 {np.median(off / np.maximum(hd / 2, 1e-9)):5.2f}")
        print(f"    轨迹半径 {np.median([r['radius_m'] for r in x]) * 100:.1f}cm"
              f"   位置分辨带中位 {np.median(band):.2f}°"
              f"   换姿态R的位置代价中位 {np.median(cost):+.2f}mm")

    fused = [r for r in rows if r["chain"] == "fused"]
    if fused:
        off = np.median([r["offset_deg"] for r in fused])
        band = np.median([r["resolvable_band_deg"] for r in fused])
        r2 = 1.18  # rotation_budget.py ⑤ 的 VINS 真实姿态噪声
        print()
        print(f"⇒ fused 残留偏移 {off:.2f}° ≈ 位置分辨带 {band:.2f}°"
              f" ⇒ **这个旋转位置数据本来就定不出来**")
        print(f"⇒ rot 门的地板 √(r2² + 分辨带²) = √({r2}² + {band:.2f}²)"
              f" = {np.hypot(r2, band):.2f}°   门限 2.0°")

    (OUT / "attitude_offset_source.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))
    print(f"\n已写 {OUT / 'attitude_offset_source.json'}")


if __name__ == "__main__":
    main()

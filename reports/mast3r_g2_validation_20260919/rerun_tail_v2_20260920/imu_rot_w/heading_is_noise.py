#!/usr/bin/env python3
"""§31.4 决定性检验：逐帧航向误差是不是**位置噪声的导数表现**（而非独立缺陷）。

机理推导：逐帧位移 d=|ΔGT|，各向同性位置噪声 σ ⇒ 估计位移方向的角度误差
    θ ≈ atan(√2·σ / d)   ⇒   σ_implied = d·tan(θ)/√2
若在**快段**上实测的 σ_implied ≈ 该臂自己的 ATE（位置噪声的直接度量），
则「方向错」**完全由位置噪声解释**，航向通道没有额外缺陷可修 ——
§25.3「步长对、方向错」的「方向错」就此归因，且**不可以再当作一个独立的修靶**。
（步长对是因为长度对噪声只做二次叠加，一阶免疫；方向对噪声是一阶敏感 ⇒ 同一噪声
 必然表现为「长度对、方向错」。）

对照：用 25mm 窗口算同一件事 ⇒ σ_implied 应降到 ~1/6（窗口把噪声平均掉）。

用法: heading_is_noise.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent


def main():
    cells = [l.split("/") for l in (HERE / ".." / "cells22.txt").resolve().read_text().split()
             if l.count("/") == 2]
    rows = []
    for batch, group, arm in cells:
        gtdir = WF / batch / group
        tg, Pg, Qg = E.load_trajectory(gtdir / "lighthouse_body_ground_truth.csv")
        for tag, rel in (("8_fused", "trajectory_fused.csv"),
                         ("1_frontend", "mast3r/trajectory_frames.csv")):
            p = gtdir / "fusion" / arm / rel
            if not p.exists():
                continue
            t, P, Q = E.load_trajectory(p)
            ins, val, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
            t, P, G = t[ins][val], P[ins][val], plt_[:, 1:4]
            s, R, tt = E.similarity_align(P, G)
            A = s * (P @ R.T) + tt
            d = np.linalg.norm(np.diff(G, axis=0), axis=1)          # 真值逐帧位移
            e = np.linalg.norm(np.diff(A, axis=0), axis=1)          # 估计逐帧位移
            v = A[1:] - A[:-1]
            g = G[1:] - G[:-1]
            c = np.clip((v * g).sum(1) / np.maximum(e * d, 1e-18), -1, 1)
            th = np.degrees(np.arccos(c))
            err = np.linalg.norm(A - G, axis=1) * 1000              # 逐帧 ATE (mm)

            # 只看「位移显著大于噪声」的帧，否则 θ 恒 ~90°（信息量为零）
            fast = d > 3e-3                                         # 位移 >3mm
            if fast.sum() < 30:
                continue
            sig_implied = d[fast] * np.tan(np.radians(np.minimum(th[fast], 80))) / np.sqrt(2)
            # 窗口版：25mm 弧长、≤0.5s
            seg = np.linalg.norm(np.diff(G, axis=0), axis=1)
            arc = np.concatenate([[0.0], np.cumsum(seg)])
            j = np.searchsorted(arc, arc + 0.025, side="left")
            ok = (j < len(arc)) & ((t[np.minimum(j, len(t) - 1)] - t) <= 0.5)
            i = np.arange(len(arc))[ok]; jj = j[ok]
            av, gv = A[jj] - A[i], G[jj] - G[i]
            al, gl = np.linalg.norm(av, axis=1), np.linalg.norm(gv, axis=1)
            cw = np.clip((av * gv).sum(1) / np.maximum(al * gl, 1e-18), -1, 1)
            thw = np.degrees(np.arccos(cw))
            sig_w = gl * np.tan(np.radians(np.minimum(thw, 80))) / np.sqrt(2)
            rows.append(dict(cell=f"{batch}/{group}/{arm}", stage=tag,
                             ate_p50=float(np.median(err)),
                             ate_max=float(err.max()),
                             d_med_mm=float(np.median(d[fast]) * 1000),
                             theta_med=float(np.median(th[fast])),
                             sig_implied_med=float(np.median(sig_implied) * 1000),
                             n_fast=int(fast.sum()),
                             theta_win_med=float(np.median(thw)),
                             sig_win_med=float(np.median(sig_w) * 1000),
                             n_win=int(ok.sum())))

    (HERE / "heading_is_noise.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"{'cell':<44}{'stage':<12}{'ATEp50':>8}{'ATE max':>8}{'逐帧d':>7}"
          f"{'θ逐帧':>7}{'σ推':>7}{'θ窗口':>7}{'σ窗推':>7}{'比值':>7}")
    for r in rows:
        rr = r["sig_implied_med"] / max(r["ate_p50"], 1e-9)
        print(f"{r['cell'][-42:]:<44}{r['stage']:<12}{r['ate_p50']:>8.2f}{r['ate_max']:>8.2f}"
              f"{r['d_med_mm']:>7.2f}{r['theta_med']:>7.1f}{r['sig_implied_med']:>7.2f}"
              f"{r['theta_win_med']:>7.1f}{r['sig_win_med']:>7.2f}{rr:>7.2f}")
    fu = [r for r in rows if r["stage"] == "8_fused"]
    rr = np.array([r["sig_implied_med"] / max(r["ate_p50"], 1e-9) for r in fu])
    print(f"\n融合级 n={len(fu)}  σ推/ATE 中位 {np.median(rr):.2f}  "
          f"范围 {rr.min():.2f}–{rr.max():.2f}")
    print(f"窗口版 σ窗推/ATE 中位 "
          f"{np.median([r['sig_win_med']/max(r['ate_p50'],1e-9) for r in fu]):.2f}")


if __name__ == "__main__":
    main()

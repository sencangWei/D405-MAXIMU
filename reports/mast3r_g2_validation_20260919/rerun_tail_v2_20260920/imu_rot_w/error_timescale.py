#!/usr/bin/env python3
"""§31.5 误差的**时间尺度** —— 决定「位置域滤波整族封死」这个判决还站不站得住。

§31.4 已证：逐帧航向误差推回的位置噪声只有 0.13–0.23mm，而 ATE 是 2.5–5mm
⇒ **误差不是高频抖动，是缓变的块偏移**。
若如此，§25.2 那一族滤波器（中值 3/5/7、Hampel(3)、高斯 σ=2/4 帧）**全部短于块宽**
⇒ 它们「MAX 改动中位 ≈0.00mm」的原因可能只是**尺度不匹配**，而不是「位置域结构性无解」。
本脚本把块宽与频谱量出来，判定那个封死判决是否越界。

量什么（22 臂 × 融合级/前端，零新跑）：
  1. 误差自相关半高宽（帧）——「块」有多宽
  2. 误差序列里高于 1.5×中位的**连续段**长度分布（§25 报道中位 11 帧，复核）
  3. 误差 / 真值运动的**功率谱重心**（Hz）——误差是不是比运动更慢
  4. 结论需要的判据：块宽(帧) 是否 > 已测滤波器窗(7 帧)

用法: error_timescale.py
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent


def runs_above(x, thr):
    m = x > thr
    if not m.any():
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    st = np.where(d == 1)[0]
    en = np.where(d == -1)[0]
    return list(en - st)


def acf_halfwidth(x):
    """自相关首次跌破 0.5 的滞后（帧）；x 已去均值。"""
    x = x - x.mean()
    n = len(x)
    if n < 32 or np.allclose(x, 0):
        return None
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    if ac[0] <= 0:
        return None
    ac = ac / ac[0]
    below = np.where(ac < 0.5)[0]
    return int(below[0]) if len(below) else n


def spec_centroid_hz(x, fs):
    x = x - x.mean()
    w = np.hanning(len(x))
    f = np.fft.rfftfreq(len(x), 1.0 / fs)
    P = np.abs(np.fft.rfft(x * w)) ** 2
    P[0] = 0.0
    return float((f * P).sum() / max(P.sum(), 1e-18))


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
            err = np.linalg.norm(A - G, axis=1) * 1000
            fs = 1.0 / float(np.median(np.diff(t)))
            speed = np.linalg.norm(np.gradient(G, axis=0), axis=1) \
                / np.maximum(np.gradient(t), 1e-9)
            rl = runs_above(err, 1.5 * float(np.median(err)))
            rows.append(dict(
                cell=f"{batch}/{group}/{arm}", stage=tag, fs=float(fs),
                ate_p50=float(np.median(err)), ate_max=float(err.max()),
                acf_half_frames=acf_halfwidth(err),
                RunLen_p50=float(np.median(rl)) if rl else None,
                RunLen_p90=float(np.percentile(rl, 90)) if rl else None,
                n_runs=len(rl),
                spec_c_err=spec_centroid_hz(err, fs),
                spec_c_speed=spec_centroid_hz(speed, fs),
            ))
    (HERE / "error_timescale.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))

    print(f"{'cell':<42}{'stage':<11}{'ATEp50':>8}{'ACF半宽帧':>10}{'段p50':>7}"
          f"{'段p90':>7}{'#段':>6}{'f重心误':>9}{'f重心运动':>10}")
    for r in rows:
        print(f"{r['cell'][-40:]:<42}{r['stage']:<11}{r['ate_p50']:>8.2f}"
              f"{r['acf_half_frames'] or 0:>10}{r['RunLen_p50'] or 0:>7.0f}"
              f"{r['RunLen_p90'] or 0:>7.0f}{r['n_runs']:>6}"
              f"{r['spec_c_err']:>9.2f}{r['spec_c_speed']:>10.2f}")
    fu = [r for r in rows if r["stage"] == "8_fused"]
    print()
    for lbl, key in (("ACF 半宽(帧)", "acf_half_frames"), ("段长 p50(帧)", "RunLen_p50"),
                     ("段长 p90(帧)", "RunLen_p90"), ("f重心 误差(Hz)", "spec_c_err"),
                     ("f重心 运动(Hz)", "spec_c_speed"), ("采样率(Hz)", "fs")):
        v = [r[key] for r in fu if r[key] is not None]
        if v:
            print(f"  融合级 {lbl:<14} 中位 {np.median(v):8.2f}   范围 {min(v):.2f}–{max(v):.2f}")
    print("\n⇒ 已测滤波器窗 = 3/5/7 帧（中值 3/5/7、Hampel(3)、高斯 σ2/σ4）。"
          "\n   若块宽 > 7 帧 ⇒ 「位置域滤波整族封死」判在了错尺度上。")


if __name__ == "__main__":
    main()
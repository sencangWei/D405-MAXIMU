#!/usr/bin/env python3
"""前端匹配数埋点：>10mm 的那些帧，MASt3R 的匹配是不是真的塌了？

## 为什么做这个

用户提的、此前**唯一没测过**的一条：给 MASt3R 前端加逐帧匹配/内点计数埋点，
直接看超标帧上匹配是不是塌了。它能**证实或否证**"旋转 → 特征减少 → 误差大"这条链。

动机来自两条已立的事实：
- 19/19 cell 的误差峰都伴随**激烈动作**（角速度 2.47x，`spike_attribution_20260919`）；
- 但**逐帧匹配数从来没有存在过**（`mast3r_logs/`、`mast3r.log` 里都没有）。
  `Skipped frame` 一次没打印 ⇒ `match_frac` 从没跌破 0.05，但那只是个很低的丢弃门。

## 量从哪来

`mast3r_slam/tracker.py` 里 `match_frac = valid_opt.sum() / valid_opt.numel()` **本来就在算**，
只是只用于 `min_match_frac` 丢弃门、从不落盘。本次加了 env 门控埋点
（`MAST3R_MATCH_LOG=<path>`；不设则零行为影响），逐帧记：

    frame_id, n_match(原始非对称匹配数), n_match_Q(再过 Q_conf), n_opt(再过 C_conf=最终), n_total

## 口径

- 误差 = 官方评测器同源：全局 `rigid_align` 后 `||aligned − gt||`（即 `ate_translation_max_m` 那个量）。
- 融合产物 `trajectory_fused.csv` **已落在 GT 时间轴上**（脚本先断言 t 逐位相同，不然不往下走）。
- `frame_id` ↔ 融合样本：按 `t_sec` 最近邻（`frames.csv` 比 GT 早 1.86s 起，不是同一段）。
"""
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

G = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow"
         "/20260914_validation_v10_batch/group1")
FE = G / "fusion/tight/mast3r"
PROBE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/home/robot/ego_vio_humble/reports/mast3r_g2_validation_20260919"
    "/match_probe_20260920/v10_g1_tight_09_14cfg")
FUSED = G / "fusion_current/tight/trajectory_fused.csv"
GT = G / "lighthouse_body_ground_truth.csv"
GATE_MM = 10.0
PEAK = 469


def read_csv(path):
    """返回 (表头, 行的字符串二维表)；列按需再转 float（frames.csv 有 image 字符串列）。"""
    with open(path) as f:
        hdr = f.readline().strip().split(",")
        rows = [ln.strip().split(",") for ln in f if ln.strip()]
    return hdr, rows


def colf(hdr, rows, name):
    k = hdr.index(name)
    return np.array([r[k] for r in rows], dtype=float)


def main():
    t_f, P_f, _ = E.load_trajectory(FUSED)
    t_g, P_g, Q_g = E.load_trajectory(GT)

    # ① 融合产物必须与 GT 同一时间轴，否则逐样本对齐不成立
    if not (len(t_f) == len(t_g) and np.allclose(t_f, t_g, atol=1e-9)):
        print(f"⚠ 时间轴不同（n {len(t_f)} vs {len(t_g)}）—— 本脚本口径不适用")
        return 1
    print(f"融合 n={len(t_f)} 与 GT 时间轴逐位相同 ✓")

    # ② 逐样本误差（与 ate_translation_max_m 同源）
    R, tr = E.rigid_align(P_f, P_g)
    err = np.linalg.norm(P_f @ R.T + tr - P_g, axis=1) * 1000.0

    # ③ GT 角速度 deg/s
    rot = Rotation.from_quat(Q_g)
    w = np.degrees((rot[:-1].inv() * rot[1:]).magnitude()) / np.diff(t_g)
    omega = np.concatenate([[w[0]], w])

    # ④ 帧号 ↔ 融合样本：时间戳最近邻
    fh, frows = read_csv(FE / "dataset/frames.csv")
    fid = colf(fh, frows, "input_index").astype(int)
    ft = colf(fh, frows, "t_sec")
    j = np.clip(np.searchsorted(ft, t_f), 1, len(ft) - 1)
    j = np.where(np.abs(ft[j] - t_f) < np.abs(ft[j - 1] - t_f), j, j - 1)
    dmap = np.abs(ft[j] - t_f)
    print(f"帧号映射 |Δt|: 中位 {np.median(dmap)*1e3:.2f}ms  p95 "
          f"{np.percentile(dmap,95)*1e3:.2f}ms  最大 {dmap.max()*1e3:.2f}ms  "
          f"落到不同帧 {len(set(fid[j]))}/{len(t_f)}")

    # ⑤ 读埋点
    mh, mrows = read_csv(PROBE / "matches.csv")
    col = {k: colf(mh, mrows, k) for k in mh}
    mid = col["frame_id"].astype(int)
    frac = col["n_opt"] / col["n_total"]
    mtot = col["n_total"][0]

    print(f"\n埋点 {len(mid)} 帧 / 前端 {len(ft)} 帧   匹配网格 n_total={mtot:.0f}")
    print(f"  n_match 中位 {np.median(col['n_match']):.0f}   "
          f"n_opt 中位 {np.median(col['n_opt']):.0f}   最小 {col['n_opt'].min():.0f}")
    print(f"  match_frac = n_opt/n_total:  中位 {np.median(frac):.3f}  "
          f"p5 {np.percentile(frac,5):.3f}  最小 {frac.min():.3f} "
          f"(丢弃门 0.05)")

    # ⑥ 把埋点对齐到融合样本
    pos = {f: i for i, f in enumerate(mid)}
    src = np.array([pos.get(f, -1) for f in fid[j]])
    ok = src >= 0
    print(f"  能对上埋点的融合样本: {ok.sum()}/{len(ok)}")

    fr = np.full(len(t_f), np.nan)
    fr[ok] = frac[src[ok]]
    q = np.full(len(t_f), np.nan)
    q[ok] = col["n_opt"][src[ok]]

    # ⑦ 超标帧 vs 其余
    bad = (err > GATE_MM) & ok
    good = (err <= GATE_MM) & ok
    print(f"\n{'':<26}{'样本':>7}{'match_frac中位':>16}{'p5':>9}{'最小':>9}"
          f"{'n_opt中位':>11}")
    print("-" * 80)
    for nm, m in (("超标 >10mm", bad), ("达标 ≤10mm", good)):
        print(f"{nm:<26}{m.sum():>7}{np.median(fr[m]):>16.3f}"
              f"{np.percentile(fr[m],5):>9.3f}{fr[m].min():>9.3f}"
              f"{np.median(q[m]):>11.0f}")

    # ⑧ 相关性：匹配 vs 误差、匹配 vs 角速度
    print(f"\ncorr(match_frac, err)      = {np.corrcoef(fr[ok], err[ok])[0,1]:+.3f}")
    print(f"corr(n_opt,      err)      = {np.corrcoef(q[ok], err[ok])[0,1]:+.3f}")
    print(f"corr(match_frac, omega)    = {np.corrcoef(fr[ok], omega[ok])[0,1]:+.3f}"
          "   ← 「旋转→匹配减少」这条链的直接检验")
    print(f"corr(omega,      err)      = {np.corrcoef(omega[ok], err[ok])[0,1]:+.3f}"
          "   ← 已知为正（复现对照）")

    # ⑨ 角速度分位：匹配是不是随转速掉
    print(f"\n{'角速度五分位':<16}{'ω中位':>9}{'match_frac中位':>16}{'err中位':>10}{'err最大':>10}")
    print("-" * 62)
    edge = np.percentile(omega[ok], [20, 40, 60, 80])
    lab = np.digitize(omega[ok], edge)
    for b in range(5):
        m = lab == b
        print(f"Q{b+1:<14}{np.median(omega[ok][m]):>9.1f}{np.median(fr[ok][m]):>16.3f}"
              f"{np.median(err[ok][m]):>10.2f}{err[ok][m].max():>10.2f}")

    # ⑩ 误差五分位
    print(f"\n{'误差五分位':<16}{'err中位':>9}{'match_frac中位':>16}{'ω中位':>9}{'n_opt中位':>11}")
    print("-" * 64)
    edge = np.percentile(err[ok], [20, 40, 60, 80])
    lab = np.digitize(err[ok], edge)
    for b in range(5):
        m = lab == b
        print(f"E{b+1:<14}{np.median(err[ok][m]):>9.2f}{np.median(fr[ok][m]):>16.3f}"
              f"{np.median(omega[ok][m]):>9.1f}{np.median(q[ok][m]):>11.0f}")

    # ⑪ 峰帧本身
    k = np.argmin(np.abs(np.arange(len(t_f)) - PEAK)) if PEAK < len(t_f) else None
    print(f"\n【峰帧 {PEAK}】err={err[PEAK]:.2f}mm  ω={omega[PEAK]:.1f}°/s  "
          f"match_frac={fr[PEAK]:.3f}  n_opt={q[PEAK]:.0f}")
    print("  邻域 ±6 帧:")
    for i in range(PEAK - 6, PEAK + 7):
        if 0 <= i < len(t_f):
            print(f"    帧{i:>5}  err{err[i]:7.2f}  ω{omega[i]:7.1f}  "
                  f"match_frac{fr[i]:7.3f}  n_opt{q[i]:8.0f}"
                  + ("   ← 峰" if i == PEAK else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

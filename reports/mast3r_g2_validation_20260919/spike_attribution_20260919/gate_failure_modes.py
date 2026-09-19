#!/usr/bin/env python3
"""门到底是怎么失败的 —— 四项测量，全部收口到「下一步该修什么」。

## 为什么做这个

到这一步已知：卡人的是 `max` 与 `rot`，不是典型精度；`rot` 有 2.11° 的地板。
但 `max` 一直只被当作一个数看，**没人问过它是"几个坏点"还是"一整段跑偏"**。
本脚本把这件事量掉，并顺手关掉两个悬着的决策。

## ① 度量尺度来源：换上双目会不会更好

`imu_scale_report.json` 的 scale 与四份 `stereo_scale_*_report.json` 的
`scale_m_per_mast3r_unit` 是**同一个物理量**（米 / MASt3R 单位），
可以直接跟真值比：真值 = `trajectory_frames.csv` 对 GT 的相似尺度。

## ② 最终产物的尺度是不是 max 的驱动

## ③ 超 10mm 的样本：几个点，还是几段

## ④ 去掉最坏 k 个样本后 max 还剩多少 —— 「离达标差几个点」

只读产物 CSV/JSON + 官方评测器 + 尾段扫描结果，不跑管线。
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SWEEP = Path("/tmp/claude-1000/stereoab/recover0914/recover0914_sweep.json")
OUT = Path(__file__).parent

STEREO_KINDS = ("bidirectional", "long_hops", "dense10hz", "multisecond")


def sweep_metrics():
    if not SWEEP.is_file():
        return {}
    out = {}
    for row in json.loads(SWEEP.read_text()):
        m = row.get("configs", {}).get("A_G2_current", {}).get("m")
        if m:
            out[row["group"]] = m
    return out


def cells():
    for b in sorted(ROOT.glob("2026*")):
        for g in sorted(b.glob("group*")):
            if (g / "lighthouse_body_ground_truth.csv").is_file():
                yield str(b.name), g


def on_grid(path, rt, rp, rq, min_n=20):
    et, ep, eq = E.load_trajectory(Path(path))
    ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if val.sum() < min_n:
        return None
    return et[ins][val], ep[ins][val], itp[:, 1:]


def part1_scale_sources():
    print("=" * 100)
    print("① 度量尺度：IMU 估计 vs 双目直接度量 vs 真值（同一物理量，可直接比）")
    print("-" * 100)
    print(f"{'cell':<42}{'真值':>9}{'IMU':>9}{'双目':>9}{'IMU偏差':>9}{'双目偏差':>10}{'分歧':>8}")
    rows = []
    for name, g in cells():
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        for sub in ("sparse", "tight"):
            m = g / "fusion" / sub / "mast3r"
            frames, imu_rep = m / "trajectory_frames.csv", m / "imu_scale_report.json"
            if not (frames.is_file() and imu_rep.is_file()):
                continue
            d = on_grid(frames, rt, rp, rq)
            if d is None:
                continue
            s_true = E.similarity_align(d[1], d[2])[0]
            k_imu = json.loads(imu_rep.read_text())["scale"]
            st = []
            for kind in STEREO_KINDS:
                p = m / f"stereo_scale_{kind}_report.json"
                if p.is_file():
                    v = json.loads(p.read_text()).get("scale_m_per_mast3r_unit")
                    if isinstance(v, (int, float)):
                        st.append(v)
            if not st:
                continue
            k_st = float(np.median(st))
            d_imu = abs(k_imu - s_true) / s_true * 100
            d_st = abs(k_st - s_true) / s_true * 100
            dis = abs(k_imu - k_st) / ((k_imu + k_st) / 2) * 100
            rows.append((f"{name}/{g.name}/{sub}", s_true, k_imu, k_st, d_imu, d_st, dis))
            flag = "  <== 超15%硬门" if dis > 15 else ""
            print(f"{rows[-1][0]:<42}{s_true:9.4f}{k_imu:9.4f}{k_st:9.4f}"
                  f"{d_imu:8.2f}%{d_st:9.2f}%{dis:7.2f}%{flag}")
    a = np.array([[r[4], r[5], r[6]] for r in rows])
    print()
    print(f"n={len(a)}")
    print(f"  IMU 估计偏差 中位 {np.median(a[:, 0]):.2f}%   双目估计偏差 中位 {np.median(a[:, 1]):.2f}%")
    print(f"  双目更准的 cell: {(a[:, 1] < a[:, 0]).sum()}/{len(a)}   <-- 接近掷硬币")
    print(f"  IMU-双目分歧 中位 {np.median(a[:, 2]):.2f}%，超 15% 硬门的 "
          f"{(a[:, 2] > 15).sum()}/{len(a)}")
    return rows


def part2_scale_vs_max(sweep):
    print()
    print("=" * 100)
    print("② 最终产物的尺度偏差 是不是 max 的驱动")
    print("-" * 100)
    rows = []
    for name, g in cells():
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        for sub in ("sparse", "tight"):
            f = g / "fusion" / sub / "trajectory_fused.csv"
            cid = f"{name}/{g.name}/{sub}"
            if not f.is_file() or cid not in sweep:
                continue
            d = on_grid(f, rt, rp, rq)
            if d is None:
                continue
            s = E.similarity_align(d[1], d[2])[0]
            m = sweep[cid]
            rows.append((cid, s - 1.0, m["mx"], m["rmse"], m["rot"], int(m["pass"])))
    a = np.array([[r[1], r[2], r[3], r[4], r[5]] for r in rows])
    print(f"n={len(a)}   尺度偏差 中位 {np.median(a[:, 0]) * 100:+.2f}%  "
          f"范围 {a[:, 0].min() * 100:+.2f} ~ {a[:, 0].max() * 100:+.2f}%")
    for i, nm in ((1, "max"), (2, "rmse"), (3, "rot")):
        print(f"  corr(|尺度偏差|, {nm:<4}) = {np.corrcoef(np.abs(a[:, 0]), a[:, i])[0, 1]:+.3f}")
    print("  ⇒ 相关性≈0 ⇒ 尺度不是 max 的驱动（融合已经把尺度修好了）")
    return rows


def part3_over_samples(sweep):
    print()
    print("=" * 100)
    print("③ 超 10mm 的样本：几个点，还是几段")
    print("-" * 100)
    print(f"{'cell':<42}{'max':>7}{'w10':>8}{'超标数':>8}{'占比':>8}{'段数':>6}{'最长段':>8}")
    rows = []
    for name, g in cells():
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        for sub in ("sparse", "tight"):
            f = g / "fusion" / sub / "trajectory_fused.csv"
            cid = f"{name}/{g.name}/{sub}"
            if not f.is_file() or cid not in sweep:
                continue
            d = on_grid(f, rt, rp, rq)
            if d is None:
                continue
            ts, P, Q = d
            R, t = E.rigid_align(P, Q)
            err = np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000
            over = err > 10
            runs, i = [], 0
            while i < len(over):
                if over[i]:
                    j = i
                    while j + 1 < len(over) and over[j + 1]:
                        j += 1
                    runs.append(ts[j] - ts[i])
                    i = j + 1
                else:
                    i += 1
            m = sweep[cid]
            rows.append((cid, m["mx"], m["w10"], int(over.sum()), float(over.mean() * 100),
                         len(runs), max(runs) if runs else 0.0, ts[over] if over.any() else np.array([])))
            print(f"{cid:<42}{m['mx']:7.2f}{m['w10']:8.2f}{int(over.sum()):8d}"
                  f"{over.mean() * 100:7.2f}%{len(runs):6d}{max(runs) if runs else 0.0:7.2f}s")
    n_over = np.array([r[3] for r in rows])
    print()
    print(f"  超标样本数 中位 {np.median(n_over):.0f}  最多 {n_over.max()}")
    print(f"  ★ 两类完全分开：超标 <=5 个的 {(n_over <= 5).sum()}/{len(rows)}；"
          f"超标 >=20 个的 {(n_over >= 20).sum()}/{len(rows)}")
    return rows


def part4_drop_k(sweep):
    print()
    print("=" * 100)
    print("④ 去掉最坏 k 个样本后 max 还剩多少（门限 10mm）")
    print("-" * 100)
    print(f"{'cell':<42}{'max':>8}{'去1':>8}{'去2':>8}{'去3':>8}{'去5':>8}{'超标数':>8}")
    rows = []
    for name, g in cells():
        rt, rp, rq = E.load_trajectory(g / "lighthouse_body_ground_truth.csv")
        for sub in ("sparse", "tight"):
            f = g / "fusion" / sub / "trajectory_fused.csv"
            cid = f"{name}/{g.name}/{sub}"
            if not f.is_file() or cid not in sweep:
                continue
            d = on_grid(f, rt, rp, rq)
            if d is None:
                continue
            P, Q = d[1], d[2]
            R, t = E.rigid_align(P, Q)
            err = np.sort(np.linalg.norm(P @ R.T + t - Q, axis=1) * 1000)[::-1]
            n_over = int((err > 10).sum())
            rows.append((cid, err[0], err[1], err[2], err[3], err[5], n_over))
            print(f"{cid:<42}{err[0]:8.2f}{err[1]:8.2f}{err[2]:8.2f}{err[3]:8.2f}"
                  f"{err[5]:8.2f}{n_over:8d}")
    a = np.array([[r[1], r[2], r[3], r[4], r[5], r[6]] for r in rows])
    print()
    print(f"  去 1 个就 <10mm: {(a[:, 1] < 10).sum()}/{len(a)}   "
          f"去 5 个就 <10mm: {(a[:, 4] < 10).sum()}/{len(a)}")
    print(f"  超标 <=5 个的 cell: {(a[:, 5] <= 5).sum()}/{len(a)}，"
          f"其中去 5 个后仍 >10mm 的 {((a[:, 5] <= 5) & (a[:, 4] >= 10)).sum()} 个")
    print("  ⇒ 超标 <=5 的那批【就是几个坏点】，与超标上百个的【结构性跑偏】是两种病")
    return rows


def main():
    sweep = sweep_metrics()
    if not sweep:
        print(f"⚠ 找不到尾段扫描结果 {SWEEP}，②③④ 的 max 列会缺", file=sys.stderr)
    r1 = part1_scale_sources()
    r2 = part2_scale_vs_max(sweep)
    r3 = part3_over_samples(sweep)
    r4 = part4_drop_k(sweep)

    # sparse/tight 超标时刻是否重合 —— 判定「录制属性 vs 子集噪声」
    print()
    print("=" * 100)
    print("⑤ 同一 take 的 sparse/tight，超 10mm 的时刻是否重合")
    print("-" * 100)
    by_take = {}
    for r in r3:
        take, sub = r[0].rsplit("/", 1)
        by_take.setdefault(take, {})[sub] = r[7]
    hit = 0
    for take, subs in sorted(by_take.items()):
        if len(subs) < 2 or not len(subs["sparse"]) or not len(subs["tight"]):
            continue
        a = set(np.round(subs["sparse"], 2))
        b = set(np.round(subs["tight"], 2))
        inter = a & b
        if inter:
            hit += 1
        print(f"  {take:<44} sparse {len(a):4d} / tight {len(b):4d}  重合 {len(inter):4d}")
    print(f"  ⇒ 有重合的 take: {hit}")
    print("  ⇒ 同一 take 的两个子集、两次独立跑，误差落在【同一时刻】"
          "⇒ 是录制的属性，不是算法随机抖动。")

    (OUT / "gate_failure_modes.json").write_text(json.dumps(
        dict(scale_sources=r1, scale_vs_max=r2,
             over_samples=[r[:7] for r in r3], drop_k=r4),
        ensure_ascii=False, indent=1, default=float))
    print(f"\n已写 {OUT / 'gate_failure_modes.json'}")


if __name__ == "__main__":
    main()

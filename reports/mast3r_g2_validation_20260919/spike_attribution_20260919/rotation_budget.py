#!/usr/bin/env python3
"""旋转误差预算: 门限 2.0° 到底被什么占掉了。

## 为什么要量这个

尾段配方扫描 (`recover0914_sweep.py`) 里失败几乎全卡在 `ate_translation_max`
9.8–11mm 与 `rot` 2.0–3.2° 这两条线上。把门限逐条放开后:

    完整门        达标  1/18
    放开 max      达标  5/18
    放开 rot      达标  2/18
    同时放开两者  达标 10/18

且 `v11b3/group2/sparse` 的 **max 只有 8.33、零个样本超 10mm**,
**唯一失败原因是 rot 2.08 > 2.0** —— 即 09-14 那条 holdout 的**平移已经复原**,
卡住的是**旋转**。本脚本量化旋转预算。

## 三层

1. **逐链 rot**: ①VINS / ③imu_metric / ②graph / ④fused 各自对真值的 rot RMSE。
   注意 ②③ 约 91° —— 那是**重力 bake 的坐标系约定**, 不是误差(融合会解掉);
   可比的是 ① 与 ④。
2. **常量偏移 vs 随机噪声**: 官方打分用 `rigid_align` 由**位置**定 R。
   若改用**姿态**最优的 Rc (`min ||A − Rc·B||_F`, 解 `Rc = U·diag(1,1,det(UVᵀ))·Vᵀ`,
   其中 `A·Bᵀ = U·S·Vᵀ`), 残差降幅 = 被"位置对齐"吃错的那部分常量姿态偏移。
3. **该偏移的夹角与轴**: `Rc·Rᵀ`。

只读产物 CSV + 官方评测器, 不跑管线, 不改任何东西。
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation as Rot

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
CHAINS = [("①VINS", "docker2_slam/vio_corrected_stream.csv"),
          ("③imu_metric", "fusion/{s}/mast3r/trajectory_imu_metric.csv"),
          ("②graph", "fusion/{s}/mast3r/trajectory_graph.csv"),
          ("④fused", "fusion/{s}/trajectory_fused.csv")]


def cells(fused_only=False):
    for b in sorted(ROOT.glob("2026*")):
        for g in sorted(b.glob("group*")):
            gt = g / "lighthouse_body_ground_truth.csv"
            if gt.is_file():
                yield b, g, gt


def aligned(p, rt, rp, rq):
    """返回 (P, Q, R, Rq_gt, Rq_est) —— 已按位置 rigid_align 对齐。"""
    et, ep, eq = E.load_trajectory(Path(p))
    ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if val.sum() < 10:
        return None
    P, Q, Pq = ep[ins][val], itp[:, 1:], eq[ins][val]
    R, _ = E.rigid_align(P, Q)
    return P, Q, R, Rot.from_quat(iq), Rot.from_quat(Pq)


def rot_rmse(Rq_gt, Rc, Rq_est):
    ang = np.degrees((Rq_gt.inv() * (Rot.from_matrix(Rc) * Rq_est)).magnitude())
    return float(np.sqrt((ang ** 2).mean()))


def attitude_optimal(Rq_gt, Rq_est):
    """min ||A − Rc·B||_F 的最优旋转 Rc。"""
    M = (Rq_gt.as_matrix() @ Rq_est.as_matrix().transpose(0, 2, 1)).mean(0)
    U, _, Vt = np.linalg.svd(M)
    D = np.eye(3)
    D[2, 2] = np.linalg.det(U @ Vt)
    return U @ D @ Vt


SWEEP = Path(__file__).parent / "recover0914_sweep.json"
LIM = dict(rmse=10.0, p95=10.0, mx=10.0, w10=95.0, rot=2.0)


def part0():
    """门限逐条放开 —— 看清哪道门在卡。"""
    if not SWEEP.is_file():
        print(f"(跳过 ⓪: 缺 {SWEEP.name})")
        return
    rows = json.loads(SWEEP.read_text())
    ms = [(r["group"], r["configs"]["A_G2_current"]["m"]) for r in rows
          if r.get("configs", {}).get("A_G2_current", {}).get("m")]

    def passes(m, drop=()):
        for k, v in LIM.items():
            if k in drop:
                continue
            if (m[k] < v) if k == "w10" else (m[k] > v):
                return False
        return True

    print("=" * 92)
    print("⓪ 门限逐条放开 (18 个可比 cell, 现役 G2)")
    print("=" * 92)
    for lab, drop in [("完整门(现役)", ()), ("放开 max", ("mx",)), ("放开 rot", ("rot",)),
                      ("同时放开 max+rot", ("mx", "rot"))]:
        print(f"  {lab:<20} 达标 {sum(1 for _, m in ms if passes(m, drop)):2d}/18")
    rot = np.array([m["rot"] for _, m in ms])
    mx = np.array([m["mx"] for _, m in ms])
    print(f"  ⇒ max 超 10mm 的 {int((mx > 10).sum())}/18, rot 超 2.0° 的 {int((rot > 2).sum())}/18")
    print(f"  ⇒ rmse 中位仅 {np.median([m['rmse'] for _, m in ms]):.2f}mm "
          f"⇒ 典型精度远好于门限, 卡人的是尾部/姿态")
    near = [(gid, m) for gid, m in ms if m["rmse"] < 5 and m["mx"] <= 10]
    for gid, m in near:
        print(f"     {gid:<50} rmse {m['rmse']:.2f} max {m['mx']:5.2f} "
              f"w10 {m['w10']:.1f} rot {m['rot']:.2f} → 只卡 {'/'.join(m['fail'])}")


def part1():
    print("=" * 92)
    print("① 逐链 rot RMSE (门限 2.0°)")
    print("=" * 92)
    print(f"{'cell':<48}" + "".join(f"{n:>16}" for n, _ in CHAINS))
    print("-" * 112)
    agg = {n: [] for n, _ in CHAINS}
    for b, g, gt in cells():
        rt, rp, rq = E.load_trajectory(gt)
        for sub in ("sparse", "tight"):
            if not (g / "fusion" / sub).is_dir():
                continue
            vals = []
            for n, pat in CHAINS:
                p = g / pat.format(s=sub)
                a = aligned(p, rt, rp, rq) if p.is_file() else None
                v = rot_rmse(a[3], a[2], a[4]) if a else None
                vals.append(v)
                if v is not None:
                    agg[n].append(v)
            cid = f"{b.name[9:22]}/{g.name}/{sub}"
            print(f"{cid:<48}" + "".join(
                f"{v:16.2f}" if v is not None else f"{'—':>16}" for v in vals))
    print()
    for n, _ in CHAINS:
        a = np.array(agg[n])
        if len(a):
            print(f"  {n:<14} n={len(a):2d}  中位 {np.median(a):5.2f}°  "
                  f"超2.0的 {int((a > 2.0).sum())}/{len(a)}  最大 {a.max():5.2f}°")
    print("  ⚠ ②③ 约 91° = 重力 bake 的坐标系约定, 不是误差。")


def part2():
    print()
    print("=" * 92)
    print("② 常量姿态偏移 vs 随机噪声 (④fused)")
    print("=" * 92)
    print(f"{'cell':<46}{'位置对齐':>10}{'姿态最优':>10}{'降幅':>8}")
    print("-" * 78)
    drops, r2s = [], []
    for b, g, gt in cells():
        rt, rp, rq = E.load_trajectory(gt)
        for sub in ("sparse", "tight"):
            p = g / "fusion" / sub / "trajectory_fused.csv"
            if not p.is_file():
                continue
            a = aligned(p, rt, rp, rq)
            if not a:
                continue
            _, _, R, Rq_gt, Rq_est = a
            r1 = rot_rmse(Rq_gt, R, Rq_est)
            Rc = attitude_optimal(Rq_gt, Rq_est)
            r2 = rot_rmse(Rq_gt, Rc, Rq_est)
            assert r2 <= r1 + 1e-9, (r2, r1)  # 最小二乘解不可能更差
            drops.append(r1 - r2)
            r2s.append(r2)
            print(f"{b.name[9:22] + '/' + g.name + '/' + sub:<46}"
                  f"{r1:9.2f}°{r2:9.2f}°{r1 - r2:7.2f}°")
    d, r2a = np.array(drops), np.array(r2s)
    print()
    print(f"  ⇒ n={len(d)}  降幅中位 {np.median(d):.2f}°  (最大 {d.max():.2f}°)")
    print(f"  ⇒ 姿态最优后 rot 中位 {np.median(r2a):.2f}°, 最大 {r2a.max():.2f}°")
    print(f"  ⇒ 姿态最优后超 2.0° 的 cell: {int((r2a > 2.0).sum())}/{len(r2a)}")


def part3():
    print()
    print("=" * 92)
    print("③ 常量偏移 Rc·Rᵀ 的夹角与轴 (每 take 取一个 cell)")
    print("=" * 92)
    print(f"{'take':<40}{'夹角':>8}   {'轴(x,y,z)':<24}")
    print("-" * 78)
    seen, angs = set(), []
    for b, g, gt in cells():
        rt, rp, rq = E.load_trajectory(gt)
        p = g / "fusion" / "tight" / "trajectory_fused.csv"
        if not p.is_file():
            p = g / "fusion" / "sparse" / "trajectory_fused.csv"
        if not p.is_file() or b.name in seen:
            continue
        a = aligned(p, rt, rp, rq)
        if not a:
            continue
        _, _, R, Rq_gt, Rq_est = a
        off = Rot.from_matrix(attitude_optimal(Rq_gt, Rq_est) @ R.T)
        ax = off.as_rotvec()
        n = np.linalg.norm(ax)
        a_deg = float(np.degrees(n))
        u = ax / n if n > 0 else ax
        seen.add(b.name)
        angs.append(a_deg)
        print(f"{b.name[9:]:<40}{a_deg:7.2f}°   ({u[0]:+.2f},{u[1]:+.2f},{u[2]:+.2f})")
    a = np.array(angs)
    print()
    print(f"  ⇒ {len(a)} 个 take: 中位 {np.median(a):.2f}°, 范围 {a.min():.2f}–{a.max():.2f}°")
    print(f"  ⇒ 这一项占 2.0° 门限的 {np.median(a) / 2.0 * 100:.0f}%")
    print("  ⚠ 轴向在 take 之间不一致 ⇒ **不是**一个全局标定常数, 是【每 take 的姿态偏置】。")


def part4():
    """那个每 take 的常量姿态偏移, 是上游带来的还是融合自己造的?

    对每条链都算 (r1 位置对齐 rot, r2 姿态最优 rot, 偏移夹角 |Rc·Rᵀ|)。
    r2 还顺带把 ②③ 的重力 bake 坐标系约定 (≈91°) 剥掉, 露出它们真实的姿态噪声。
    """
    print()
    print("=" * 104)
    print("④ 常量姿态偏移的来源: 逐链 r1(位置对齐) / r2(姿态最优) / 偏移夹角")
    print("=" * 104)
    hdr = f"{'cell':<44}" + "".join(f"{n:>19}" for n, _ in CHAINS)
    print(hdr)
    print(f"{'':<44}" + "".join(f"{'r1/r2/偏移':>19}" for _ in CHAINS))
    print("-" * 120)
    per_take = {}
    for b, g, gt in cells():
        rt, rp, rq = E.load_trajectory(gt)
        for sub in ("sparse", "tight"):
            if not (g / "fusion" / sub).is_dir():
                continue
            cells_txt = ""
            for n, pat in CHAINS:
                p = g / pat.format(s=sub)
                a = aligned(p, rt, rp, rq) if p.is_file() else None
                if not a:
                    cells_txt += f"{'—':>19}"
                    continue
                _, _, R, Rq_gt, Rq_est = a
                r1 = rot_rmse(Rq_gt, R, Rq_est)
                Rc = attitude_optimal(Rq_gt, Rq_est)
                r2 = rot_rmse(Rq_gt, Rc, Rq_est)
                off = float(np.degrees(np.linalg.norm(Rot.from_matrix(Rc @ R.T).as_rotvec())))
                cells_txt += f"{r1:6.1f}/{r2:5.2f}/{off:5.2f}".rjust(19)
                per_take.setdefault((b.name, sub), {})[n] = (r1, r2, off)
            print(f"{b.name[9:22] + '/' + g.name + '/' + sub:<44}{cells_txt}")

    print()
    print("=" * 104)
    print("⑤ 逐链汇总: r2 (剥掉坐标系约定后的真实姿态噪声) 与 偏移夹角")
    print("=" * 104)
    for n, _ in CHAINS:
        r2s, offs, r1s = [], [], []
        for key, d in per_take.items():
            if n in d:
                r1s.append(d[n][0])
                r2s.append(d[n][1])
                offs.append(d[n][2])
        if not r2s:
            print(f"  {n:<14} 无数据")
            continue
        print(f"  {n:<14} n={len(r2s):2d}  r1 中位 {np.median(r1s):6.2f}°  "
              f"r2 中位 {np.median(r2s):5.2f}°  偏移夹角 中位 {np.median(offs):5.2f}°  "
              f"范围 {min(offs):.2f}–{max(offs):.2f}°")

    print()
    print("=" * 104)
    print("⑥ 关键比对: ①VINS 的偏移 vs ④fused 的偏移 (同一 take 同一 cell)")
    print("=" * 104)
    print(f"{'take/sub':<32}{'VINS偏移':>10}{'fused偏移':>11}{'差值':>9}{'VINS r2':>10}{'fused r2':>10}")
    print("-" * 84)
    dv, df = [], []
    for key in sorted(per_take):
        d = per_take[key]
        if "①VINS" not in d or "④fused" not in d:
            continue
        ov, of = d["①VINS"][2], d["④fused"][2]
        dv.append(ov)
        df.append(of)
        print(f"{key[0][9:22] + '/' + key[1]:<32}{ov:9.2f}°{of:10.2f}°{ov - of:8.2f}°"
              f"{d['①VINS'][1]:9.2f}°{d['④fused'][1]:9.2f}°")
    if dv:
        r = np.corrcoef(dv, df)[0, 1]
        print()
        print(f"  ⇒ VINS 偏移中位 {np.median(dv):.2f}°, fused 偏移中位 {np.median(df):.2f}°")
        print(f"  ⇒ 两者相关 ρ = {r:+.2f}  (n={len(dv)})")
        print(f"  ⇒ VINS r2 中位 {np.median([per_take[k]['①VINS'][1] for k in per_take if '①VINS' in per_take[k]]):.2f}°")
        if np.median(dv) > 0.5 and r > 0.5:
            print("  ⇒ 判读: VINS 自己就带着同量级的偏移且与 fused 同向 ⇒ **上游带入**")
        elif np.median(dv) < 0.3:
            print("  ⇒ 判读: VINS 自己几乎没有偏移 ⇒ **融合环节自己造的**")
        else:
            print("  ⇒ 判读: 两者都有 ⇒ 部分上游带入、部分融合自造")


def chain_on_gt(p, rt, rp, rq):
    """把一条链的姿态插补到 GT 时间栅格上, 返回 (时间, 姿态)。"""
    et, ep, eq = E.load_trajectory(Path(p))
    ins, val, itp, iq = E.interpolate_ground_truth(et, rt, rp, rq, 0.1)
    if val.sum() < 10:
        return None, None
    return et[ins][val], eq[ins][val]


def part7():
    """★ ④fused 的姿态与 ①VINS 的姿态, 到底是什么关系?

    ⚠ 两条链的 val 掩码不同 (各自插补), 必须先归到**共同时间栅格**再比,
    否则长度不等会被整片跳过 (曾经因此只看到 6/20 个 cell)。
    """
    print()
    print("=" * 104)
    print("⑦ ④fused 姿态 vs ①VINS 姿态 (共同 GT 栅格)")
    print("=" * 104)
    print(f"{'cell':<46}{'常量旋转':>12}{'剥掉后残差':>13}{'公共样本':>10}")
    print("-" * 92)
    res = []
    for b, g, gt in cells():
        rt, rp, rq = E.load_trajectory(gt)
        for sub in ("sparse", "tight"):
            pv = g / "docker2_slam" / "vio_corrected_stream.csv"
            pf = g / "fusion" / sub / "trajectory_fused.csv"
            if not (pv.is_file() and pf.is_file()):
                continue
            tv, qv = chain_on_gt(pv, rt, rp, rq)
            tf, qf = chain_on_gt(pf, rt, rp, rq)
            if tv is None or tf is None:
                continue
            _, iv, if_ = np.intersect1d(np.round(tv, 4), np.round(tf, 4),
                                        return_indices=True)
            if len(iv) < 10:
                continue
            A, B = Rot.from_quat(qv[iv]), Rot.from_quat(qf[if_])
            # 要的是 B ≈ C·A (fused ≈ C·VINS) ⇒ gt 角色传 B, est 角色传 A
            C = attitude_optimal(B, A)
            ang = np.degrees((B.inv() * (Rot.from_matrix(C) * A)).magnitude())
            r = float(np.sqrt((ang ** 2).mean()))
            res.append(r)
            print(f"{b.name[9:22] + '/' + g.name + '/' + sub:<46}"
                  f"{np.degrees(np.linalg.norm(Rot.from_matrix(C).as_rotvec())):10.2f}°"
                  f"{r:12.4f}°{len(iv):10d}")
    a = np.array(res)
    if len(a):
        print()
        print(f"  ⇒ n={len(a)}  剥掉常量旋转后残差 中位 {np.median(a):.4f}°  最大 {a.max():.4f}°")
        print(f"  ⇒ 残差 ≈0 的 cell: {int((a < 0.01).sum())}/{len(a)}")
        print("  ⇒ **融合不贡献任何姿态信息** ⇒ rot 这道门 100% 由 VINS 决定。")
        print("  ⇒ 而 VINS 真实姿态噪声仅 ~1.22° (⑤), 本就在 2.0° 门内;")
        print("     把格子顶过门的是【轨迹位置与姿态互不自洽】那 ~1.6° 常量偏移。")


if __name__ == "__main__":
    part0()
    part1()
    part2()
    part3()
    part4()
    part7()

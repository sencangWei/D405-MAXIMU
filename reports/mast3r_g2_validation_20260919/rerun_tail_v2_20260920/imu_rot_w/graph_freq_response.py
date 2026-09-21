#!/usr/bin/env python3
"""§23 给 `[7/8]` 量一条**频响曲线** —— 「子空间」假说的频域形式。

## 假说（§22.3 剩下唯一说得通的那个，现在把它变成可算的）

§21 定位衰减器在 `[7/8]`（0.33×），§22 否掉了"约束密度"。
剩下的形状是：**tight/sparse 的差落在图优化的哪个子空间** —— 受约束的被子抹平、
自由的透传。图只有**一个锚**（`anchor_policy = first_node_only`，与前端 `num_fix=1` 同构），
约束全是**相对**边（双目尺度边 / IMU 预积分 / 相对运动）⇒ 自由的正是**慢变/长波**模态，
被约束住的是**快变/局部**模态。

⇒ 可算形式：**`[7/8]` 对「tight−sparse 的逐帧差」而言是一台时不变滤波器**。
把它当线性系统，量它的**频响 |H(f)|**：

    Δ6(t) = P6_tight(t) − P6_sparse(t)      （[6/8] 输入差，1800 帧）
    Δ7(t) = P7_tight(t) − P7_sparse(t)      （[7/8] 输出差）
    |H(f)|² = S7(f) / S6(f)                  （跨三轴求和的自功率谱比）

**预测**：若假说成立，`|H|` 应随 f 单调下降（低通）——
慢模态透传（|H|→1）、快模态被杀（|H|→0）。
**并且**：每格「Δ6 的低频能量占比」应预测 §21 的标量传递率（低频多 ⇒ 传递率高）。

★ 这也是「鼓包（30–50 帧宽 ⇒ 0.6–1.0 Hz）为什么动不了」的直接检验：
看 `|H|` 在那个频段是不是正好是陷波。

## 为什么零新跑

`trajectory_imu_metric.csv` 与 `trajectory_graph.csv` **都是 1800 行、同一时间基**
（同一 take 的全帧率），两臂时间戳逐位相同 ⇒ 直接相减，不用插值。

用法: graph_freq_response.py [--cells N]
"""
import argparse
import json
from pathlib import Path

import numpy as np

WF = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
HERE = Path(__file__).parent
CELLS = HERE.parent / "fusion89_sweep" / "cells.txt"


def load_traj(f: Path):
    """返回 (t, XYZ)。两臂时间戳应逐位相同；不假设，返回 t 供核对。"""
    a = np.loadtxt(f, delimiter=",", skiprows=1)
    return a[:, 0], a[:, 1:4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="fusion")
    a = ap.parse_args()

    cells = sorted({(p[0], p[1]) for p in
                    (ln.split() for ln in CELLS.read_text().splitlines() if ln.strip())})

    print("§23 `[7/8]` 频响 —— |H(f)| = sqrt(S7/S6)，Δ 是 tight−sparse 逐帧差\n")
    recs = []
    for b, g in cells:
        base = WF / b / g / a.family
        try:
            t6t, P6t = load_traj(base / "tight" / "mast3r" / "trajectory_imu_metric.csv")
            t6s, P6s = load_traj(base / "sparse" / "mast3r" / "trajectory_imu_metric.csv")
            t7t, P7t = load_traj(base / "tight" / "mast3r" / "trajectory_graph.csv")
            t7s, P7s = load_traj(base / "sparse" / "mast3r" / "trajectory_graph.csv")
        except Exception as e:
            print(f"⚠ {b[-20:]}/{g}: {e}")
            continue
        n = min(len(t6t), len(t6s), len(t7t), len(t7s))
        if n < 256:
            continue
        # 时间基必须一致，否则相减无意义
        if not (np.allclose(t6t[:n], t6s[:n], atol=1e-9)
                and np.allclose(t6t[:n], t7t[:n], atol=1e-9)):
            print(f"⚠ {b[-20:]}/{g}: 时间基不一致，跳过")
            continue
        dt = float(np.median(np.diff(t6t[:n])))
        D6 = (P6t[:n] - P6s[:n]) * 1000.0        # mm
        D7 = (P7t[:n] - P7s[:n]) * 1000.0
        # 去掉直流（全局平移规范，Sim(3) 对齐本来就会消掉）
        D6 = D6 - D6.mean(axis=0)
        D7 = D7 - D7.mean(axis=0)

        w = np.hanning(n)[:, None]               # 防泄漏
        F6 = np.fft.rfft(D6 * w, axis=0)
        F7 = np.fft.rfft(D7 * w, axis=0)
        S6 = (np.abs(F6) ** 2).sum(axis=1)
        S7 = (np.abs(F7) ** 2).sum(axis=1)
        f = np.fft.rfftfreq(n, d=dt)

        # 整体能量传递率（应≈§21 标量传递率的平方量级 —— 交叉自检）
        energy_ratio = float(np.sqrt(S7.sum() / max(S6.sum(), 1e-30)))

        # 分带（Hz）：用帧宽换算，鼓包 30–50 帧
        fps = 1.0 / dt
        bands = [(0.0, 0.2), (0.2, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 2.0),
                 (2.0, 4.0), (4.0, fps / 2 + 1)]
        hb = []
        for lo, hi in bands:
            m = (f >= lo) & (f < hi)
            if m.sum() < 1:
                hb.append(None)
                continue
            hb.append(float(np.sqrt(S7[m].sum() / max(S6[m].sum(), 1e-30))))
        # Δ6 的低频（<0.5Hz）能量占比 —— 预测它能解释 §21 的标量传递率
        mlo = f < 0.5
        low_frac = float(S6[mlo].sum() / max(S6.sum(), 1e-30))
        # 幅度口径的标量传递率（与 §21 同口径：峰值）
        amp_tr = float(np.abs(D7).max() / max(np.abs(D6).max(), 1e-9))

        # ★ 点态检验：Δ7 是不是就是 α·Δ6？若是，则 |H| 平坦（各带都≈α）。
        x6, x7 = D6.ravel(), D7.ravel()
        alpha = float((x6 @ x7) / max(x6 @ x6, 1e-30))
        pt_corr = float(np.corrcoef(x6, x7)[0, 1])
        ss_res = float(((x7 - alpha * x6) ** 2).sum())
        r2 = 1 - ss_res / max(float(((x7 - x7.mean()) ** 2).sum()), 1e-30)
        # ★ 峰在哪、峰上还剩多少（这才是 §21 的 MAX 口径）
        k6 = int(np.argmax(np.linalg.norm(D6, axis=1)))
        k7 = int(np.argmax(np.linalg.norm(D7, axis=1)))
        peak6 = float(np.linalg.norm(D6[k6]))
        peak7 = float(np.linalg.norm(D7[k7]))
        peak7_at_k6 = float(np.linalg.norm(D7[k6]))

        recs.append(dict(cell=f"{b[-20:]}/{g}", dt=dt, fps=fps, n=n,
                         energy_ratio=energy_ratio, amp_transfer=amp_tr,
                         low_frac=low_frac, bands=hb,
                         alpha=alpha, pt_corr=pt_corr, r2=r2,
                         peak6=peak6, peak7=peak7, peak7_at_k6=peak7_at_k6,
                         peak_ratio=peak7_at_k6 / max(peak6, 1e-9),
                         rms6=float(np.sqrt((D6 ** 2).mean())),
                         rms7=float(np.sqrt((D7 ** 2).mean()))))

    print(f"{'cell':<28}{'fps':>6}{'能量比':>8}{'幅度比':>8}{'Δ6低频占比':>11}"
          + "".join(f"{f'{lo}-{hi}':>8}" for lo, hi in bands))
    print("-" * 118)
    for r in recs:
        print(f"{r['cell']:<28}{r['fps']:>6.1f}{r['energy_ratio']:>8.2f}"
              f"{r['amp_transfer']:>8.2f}{r['low_frac']:>11.2f}"
              + "".join(f"{x:>9.2f}" if x is not None else f"{'—':>9}"
                        for x in r["bands"]))

    if len(recs) < 4:
        print(f"\n⚠ 样本只有 {len(recs)}，不下结论")
        return

    # ---- 判决 ----
    print(f"\n{'='*118}\n■ 判决 A：频响是不是低通（每一带的中位 |H|，n={len(recs)}）")
    for i, (lo, hi) in enumerate(bands):
        v = [r["bands"][i] for r in recs if r["bands"][i] is not None]
        if v:
            print(f"  {lo:5.1f}–{hi:5.1f} Hz   |H| 中位 {np.median(v):.2f}   "
                  f"范围 {min(v):.2f}–{max(v):.2f}")

    print(f"\n{'='*118}\n■ 判决 C：点态 —— Δ7 是不是就是 α·Δ6？（α = 最小二乘标量）")
    print(f"  {'cell':<28}{'点态corr':>9}{'R²':>7}{'α':>7}{'RMS6':>8}{'RMS7':>8}"
          f"{'RMS比':>7}{'峰值@6':>9}{'峰值@6处剩':>11}{'剩/峰':>7}")
    for r in recs:
        print(f"  {r['cell']:<28}{r['pt_corr']:>9.3f}{r['r2']:>7.3f}{r['alpha']:>7.2f}"
              f"{r['rms6']:>8.2f}{r['rms7']:>8.2f}"
              f"{r['rms7']/max(r['rms6'],1e-9):>7.2f}{r['peak6']:>9.2f}"
              f"{r['peak7_at_k6']:>11.2f}{r['peak_ratio']:>7.2f}")
    pc = np.array([r["pt_corr"] for r in recs])
    pr = np.array([r["peak_ratio"] for r in recs])
    al = np.array([r["alpha"] for r in recs])
    print(f"\n  点态 corr 中位 {np.median(pc):+.3f}   范围 {pc.min():+.3f}–{pc.max():+.3f}")
    print(f"  α 中位 {np.median(al):.2f}   范围 {al.min():.2f}–{al.max():.2f}")
    print(f"  峰值处存活比 中位 {np.median(pr):.2f}   范围 {pr.min():.2f}–{pr.max():.2f}")
    m6 = np.array([r["rms6"] for r in recs]); m7 = np.array([r["rms7"] for r in recs])
    print(f"  RMS 比 中位 {np.median(m7/m6):.2f}   ← 宽带口径；对比 MAX 口径 §21 的 0.33")
    print(f"  corr(峰值存活比, §21 MAX比) = {np.corrcoef(pr, np.array([r['amp_transfer'] for r in recs]))[0,1]:+.3f}")

    # ---- 判决 D：把图优化的「内部吸引子」从数据里解出来 ----
    # 若 Δ7 = α·Δ6（α=1−w 恒定），则模型 P7 = (1−w)·P6 + w·S，S = 与臂无关的约束解。
    # ⇒ S = (P7 − α·P6)/(1−α)。拿 tight 与 sparse 各自解出的 S **互相印证**：
    #   模型成立 ⇒ S_tight ≈ S_sparse（远小于 P7_tight − P7_sparse）。
    print(f"\n{'='*118}\n■ 判决 D：内部吸引子 S —— 图优化是不是「0.33×初值 + 0.67×与臂无关的约束解」")
    print(f"  模型 P7 = (1−α)·P6 + α·S  ⇒  Δ7 = (1−α)·Δ6（与 C 一致）")
    print(f"  {'cell':<28}{'α':>6}{'RMS(Δ7)/RMS(Δ6)':>17}{'RMS(S_t−S_s)':>14}"
          f"{'RMS(S_t−S_s)/RMS(Δ7)':>22}")
    keep = []
    for b, g in cells:
        base = WF / b / g / a.family
        try:
            _, P6t = load_traj(base / "tight" / "mast3r" / "trajectory_imu_metric.csv")
            _, P6s = load_traj(base / "sparse" / "mast3r" / "trajectory_imu_metric.csv")
            _, P7t = load_traj(base / "tight" / "mast3r" / "trajectory_graph.csv")
            _, P7s = load_traj(base / "sparse" / "mast3r" / "trajectory_graph.csv")
        except Exception:
            continue
        n = min(len(P6t), len(P6s), len(P7t), len(P7s))
        if n < 256:
            continue
        P6t, P6s, P7t, P7s = P6t[:n], P6s[:n], P7t[:n], P7s[:n]
        a6 = P6t - P6s
        a7 = P7t - P7s
        x6, x7 = a6.ravel(), a7.ravel()
        al = float((x6 @ x7) / max(x6 @ x6, 1e-30))
        if al > 0.999:
            continue
        St = (P7t - al * P6t) / (1 - al)
        Ss = (P7s - al * P6s) / (1 - al)
        rms = lambda A: float(np.sqrt((A ** 2).mean())) * 1000.0
        eS = rms(St - Ss)
        e7 = rms(a7)
        print(f"  {f'{b[-20:]}/{g}':<28}{al:>6.2f}{rms(a7)/rms(a6):>17.2f}"
              f"{eS:>14.2f}{eS/max(e7,1e-9):>22.2f}")
        keep.append((al, eS, e7))
    if keep:
        al = np.array([k[0] for k in keep]); eS = np.array([k[1] for k in keep])
        e7 = np.array([k[2] for k in keep])
        print(f"\n  α 中位 {np.median(al):.2f}  ⇒  模型说「图优化 = {(1-np.median(al))*100:.0f}% 初值"
              f" + {np.median(al)*100:.0f}% 约束解」")
        print(f"  RMS(S_t−S_s) 中位 {np.median(eS):.2f}mm  vs  RMS(Δ7) 中位 {np.median(e7):.2f}mm"
              f"  ⇒  比中位 {np.median(eS/e7):.2f}")
        print(f"  ★ 若比 <<1 ⇒ 两臂解出的**同一个** S 互相印证 ⇒ 吸引子真实存在；"
              f"若比 ≈1 ⇒ 该模型被否。")

    print(f"\n■ 判决 B：每格「Δ6 低频占比」能不能解释 §21 的标量传递率")
    lf = np.array([r["low_frac"] for r in recs])
    at = np.array([r["amp_transfer"] for r in recs])
    print(f"  corr(Δ6低频占比, 幅度传递率) = {np.corrcoef(lf, at)[0,1]:+.3f}  (n={len(recs)})")
    er = np.array([r["energy_ratio"] for r in recs])
    print(f"  corr(Δ6低频占比, 能量传递率) = {np.corrcoef(lf, er)[0,1]:+.3f}")
    print(f"  corr(幅度传递率, 能量传递率) = {np.corrcoef(at, er)[0,1]:+.3f}"
          f"   ← 两种口径自检")

    (HERE / f"graph_freq_response_{a.family}.json").write_text(json.dumps(recs, indent=1))
    print(f"\n落盘 graph_freq_response_{a.family}.json")


if __name__ == "__main__":
    main()
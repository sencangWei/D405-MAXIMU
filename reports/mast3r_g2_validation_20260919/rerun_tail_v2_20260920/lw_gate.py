#!/usr/bin/env python3
"""`lw` 到底是**在瞄准鼓包**，还是**在乱推**？—— 分两问。

## 为什么问这个，而不是再扫一遍 lw

§11.4 已知：`lw=0.1/0.2` 把全门 PASS 从 **0/18** 抬到 **1/18、2/18**，
但**逐 cell MAX 是掷硬币 9 优 / 8 差**。⇒ **再在同一批 18 组上跑一遍同样的扫描
不会有新信息**；那只会把同一个噪音再测一次。

真正没问过的是**机理**：`lw` 把 VINS 的位置注进融合轨迹。
* 若这个注入**方向对准了鼓包**（在鼓包处把轨迹往真值推）⇒ 它是有靶向的，
  那个掷硬币说明**只在 VINS 恰好可信的 cell 上生效** ⇒ 该去找一条
  **无需真值的准入判据**（这正是用户要的「可验证的通用策略」）。
* 若注入与鼓包**无关**（只是整体向 VINS 拉）⇒ 它没有靶向，掷硬币是必然的，**
  这条线可以干净收尾**。

## 两问

**A. 机理（需要真值，只为解释）**：比较 `lw=0` 与 `lw=0.2` 的逐帧位移
`Δ = P(0.2) − P(0)`。两个输出都在**同一个 `body_imu_origin` 系** ⇒ `Δ` **就是注入量本身**，
无需对齐。然后看 `Δ` 在**误差方向**上的投影：
`proj = Δ·(−e)/|e|`（`e` = `lw=0` 的逐帧误差向量）。`proj>0` ⇒ 往真值推（校正）。

**B. 可预测性（只需报告）**：`ΔMAX` / `ΔRMSE` 能不能被报告里那些
**运行时可观测**的量预测？候选：`input_disagreement_p95_mm`（两传感器分歧）、
`injected_correction_*`、`scale.ratio_mad`、`scale.relative_disagreement`。
若能 ⇒ 「何时该用 lw」是个**可验证的通用规则**；若不能 ⇒ lw 是噪音。

⚠ **多重比较警告**：n=18、候选判据 ~6 个 ⇒ 随便找一条"显著"的相关是很容易的。
所以本脚本额外做**臂间交叉检验**（在 tight 上定方向、在 sparse 上验符号；
两臂是**同一批 session 的两次独立前端跑**，不是同一份数据的重排）。

用法: lw_gate.py [--weights 0,0.2]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SCRIPTS = Path("/home/robot/ego_vio_humble/scripts")
HERE = Path(__file__).parent
PY = "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python"
VINS_CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
                   "formal_runtime_calibration/vins_config.yaml")
TAIL = ["--scale-horizon-s", "1", "--smoothing-s", "8",
        "--docker2-scale-weight", "0.25",
        "--roughness-threshold-mm", "9", "--adaptive-weight-strength", "0.45"]

PREDICTORS = ["input_disagreement_p95_mm", "injected_correction_p95_mm",
              "injected_correction_max_mm", "scale_ratio_mad",
              "scale_relative_disagreement"]


def cells():
    out = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_imu_metric.csv")):
            c = p.parents[3]
            if (c / "lighthouse_body_ground_truth.csv").exists() and \
               (c / "docker2_slam/vio_corrected_stream.csv").exists():
                out.append((c, arm))
    return out


def run(cell, traj, graph_report, lw, td, tag):
    raw, sm, rep = td / f"raw_{tag}.csv", td / f"sm_{tag}.csv", td / f"rep_{tag}.json"
    cmd = [PY, str(SCRIPTS / "fuse_docker2_mast3r_complementary.py"),
           "--mast3r", str(traj),
           "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
           "--body-t-camera-yaml", str(VINS_CONFIG), *TAIL,
           "--docker2-local-weight", str(lw),
           "--output", str(raw), "--report", str(rep)]
    if graph_report is not None:
        cmd += ["--graph-report", str(graph_report)]
    subprocess.run(cmd, check=True, capture_output=True)
    subprocess.run([PY, str(SCRIPTS / "smooth_pose_trajectory.py"),
                    "--input", str(raw), "--output", str(sm), "--method", "gaussian",
                    "--gaussian-sigma-s", "0.025"], check=True, capture_output=True)
    j = json.loads(rep.read_text())
    obs = {"input_disagreement_p95_mm": j["fusion"]["input_disagreement_p95_mm"],
           "injected_correction_p95_mm": j["fusion"]["injected_correction_p95_mm"],
           "injected_correction_max_mm": j["fusion"]["injected_correction_max_mm"],
           "scale_ratio_mad": j["scale"]["ratio_mad"],
           "scale_relative_disagreement": j["scale"]["relative_disagreement"]}
    return sm, obs


def metrics(est, tg, Pg, Qg):
    """返回 (RMSE, rot, MAX, PASS, t, P, evec, err)。evec/err 在真值系，mm。"""
    t, P, Q = E.load_trajectory(est)
    inside, valid, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    P, Q, t = P[inside][valid], Q[inside][valid], t[inside][valid]
    gt = plt_[:, 1:4]
    m = E.pose_errors(P, Q, gt, qlt_, 30)
    R, tt = E.rigid_align(P, gt)
    evec = (P @ R.T + tt - gt) * 1000.0          # 真值系，mm
    return (m["ate_translation_rmse_m"] * 1000, m["ate_rotation_rmse_deg"],
            m["ate_translation_max_m"] * 1000,
            (m["ate_translation_rmse_m"] * 1000 <= 10.0
             and m["ate_translation_p95_m"] * 1000 <= 10.0
             and m["ate_translation_max_m"] * 1000 <= 10.0
             and m["ate_translation_within_10mm_ratio"] >= 0.95
             and m["ate_rotation_rmse_deg"] <= 2.0),
            t, P, evec, np.linalg.norm(evec, axis=1), R)


def pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if len(a) < 5 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return float("nan")
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / np.sqrt((a ** 2).sum() * (b ** 2).sum()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="0,0.1,0.2")
    a = ap.parse_args()
    WS = [float(x) for x in a.weights.split(",")]
    W0 = WS[0]
    cache = HERE / "g7_cache"
    cs = cells()
    print(f"lw 机理 + 可预测性：基准 lw={W0} vs {WS[1:]}\n")
    print(f"{'cell':<38}{'arm':>7}{'RMSE':>15}{'MAX':>15}{'rot':>13}"
          f"{'proj@峰':>9}{'校正%':>7}{'PASS':>10}")
    print("-" * 114)
    allrows = {w: [] for w in WS}
    for cell, arm in cs:
        key = f"{str(cell.relative_to(ROOT)).replace('/', '_')}__{arm}__joint"
        g, gr = cache / key / "graph.csv", cache / key / "graph.json"
        if not g.exists():
            continue
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            sm0, obs0 = run(cell, g, gr, W0, td, "a")
            r0, t0, m0, p0, tt0, P0, e0, n0, R0 = metrics(sm0, tg, Pg, Qg)
            for w in WS[1:]:
                sm1, obs1 = run(cell, g, gr, w, td, f"w{w}")
                r1, t1, m1, p1, tt1, P1, e1, n1, R1 = metrics(sm1, tg, Pg, Qg)
                # ★ 两个输出同在 body_imu_origin 系 ⇒ 逐帧差就是注入量本身（无需对齐）。
                #   转到真值系只需施加基准那次的旋转 R0（旋转不改点积符号）。
                n = min(len(P0), len(P1))
                D = (P1[:n] - P0[:n]) @ R0.T * 1000.0      # mm, 真值系
                e0n, n0n = e0[:n], n0[:n]
                unit = e0n / np.maximum(n0n, 1e-9)[:, None]
                proj = (D * (-unit)).sum(axis=1)           # >0 ⇒ 往真值推
                k = int(np.argmax(n0n))                    # 基准的鼓包帧
                frac = float((proj > 0).mean())
                allrows[w].append(dict(
                    cell=str(cell.relative_to(ROOT)), arm=arm, w=w,
                    r0=r0, r1=r1, m0=m0, m1=m1, t0=t0, t1=t1, p0=p0, p1=p1,
                    proj_peak=float(proj[k]), frac=frac,
                    dmax=m1 - m0, drmse=r1 - r0, drot=t1 - t0, **obs1))
                if w == WS[1]:
                    nm = str(cell).replace(str(ROOT) + "/", "")
                    print(f"{nm[:38]:<38}{arm:>7}"
                          f"{f'{r0:6.2f}→{r1:6.2f}':>15}{f'{m0:6.1f}→{m1:6.1f}':>15}"
                          f"{f'{t0:5.2f}→{t1:5.2f}':>13}{proj[k]:>9.2f}{frac*100:>6.0f}%"
                          f"{('Y' if p0 else '-')+'→'+('Y' if p1 else '-'):>10}")
    (HERE / "lw_gate_rows.json").write_text(json.dumps(allrows, indent=1))
    print("-" * 114)
    for w in WS[1:]:
        rows = allrows[w]
        if len(rows) < 6:
            continue
        print(f"\n{'='*114}\n■ lw = {w}（基准 {W0}）  共 {len(rows)} 组")
        g = lambda k: np.array([r[k] for r in rows], float)               # noqa: E731
        dmax, drmse, drot = g("dmax"), g("drmse"), g("drot")
        prop, frac = g("proj_peak"), g("frac")
        print(f"\n【A. 机理】逐帧注入量在误差方向上的投影（>0 = 往真值推 = 校正）")
        print(f"  鼓包帧处 proj：中位 {np.median(prop):+.2f} mm（正 {int((prop>0).sum())}/{len(prop)}）")
        print(f"  全部帧里校正帧占比：中位 {np.median(frac)*100:.0f}%")
        print(f"  corr(鼓包帧 proj, ΔMAX) = {pearson(prop, dmax):+.3f}"
              f"   （强负 = ΔMAX 基本由**鼓包帧自己**决定）")
        print(f"  ΔMAX 中位 {np.median(dmax):+.2f} mm（改善 {int((dmax<0).sum())}/{len(dmax)}）"
              f"；ΔRMSE 中位 {np.median(drmse):+.2f}；Δrot 中位 {np.median(drot):+.3f}")
        print(f"\n【B. 可预测性】corr(可观测判据, ΔMAX / ΔRMSE)，分臂打印（同号≠可迁移）")
        ar = np.array([r["arm"] for r in rows])
        print(f"  {'判据':<32}{'全体':>8}{'tight':>9}{'sparse':>9}"
              f"{'全体':>9}{'tight':>9}{'sparse':>9}")
        for p in PREDICTORS:
            v = g(p)
            print(f"  {p:<32}{pearson(v,dmax):>8.3f}"
                  f"{pearson(v[ar=='tight'],dmax[ar=='tight']):>9.3f}"
                  f"{pearson(v[ar=='sparse'],dmax[ar=='sparse']):>9.3f}"
                  f"{pearson(v,drmse):>9.3f}"
                  f"{pearson(v[ar=='tight'],drmse[ar=='tight']):>9.3f}"
                  f"{pearson(v[ar=='sparse'],drmse[ar=='sparse']):>9.3f}")
        # ★ 逐点贡献：最大的 |Δ| 是哪几格？相关是不是由它们扛着的
        print(f"\n  【杠杆点检查】按 |ΔRMSE| 排序的前 5 格：")
        for r in sorted(rows, key=lambda r: -abs(r["drmse"]))[:5]:
            print(f"    {r['cell'][-28:]:<30}{r['arm']:>7}  ΔRMSE {r['drmse']:+6.2f}"
                  f"  ΔMAX {r['dmax']:+6.2f}  disagree_p95 {r['input_disagreement_p95_mm']:6.2f}")
        # 去掉这 5 格后相关还剩多少
        keep = [r for r in sorted(rows, key=lambda r: -abs(r["drmse"]))[5:]]
        kv = np.array([r["input_disagreement_p95_mm"] for r in keep])
        kd = np.array([r["drmse"] for r in keep])
        print(f"    去掉后 corr(disagreement, ΔRMSE) = {pearson(kv, kd):+.3f}（剩 {len(keep)} 格）")
        # ★ 决策形状的检验：阈值规则到底能不能净赚？
        print(f"\n  【阈值规则实战】「仅当 injected_correction_p95_mm < T 时才用 lw={w}」")
        v = g("injected_correction_p95_mm")
        print(f"    {'T(mm)':>7}{'入选':>6}{'入选内 MAX 改善':>17}"
              f"{'入选内 RMSE 改善':>18}{'弃用格 MAX 不动':>17}{'净 PASS':>9}")
        for T in (1e9, 4.0, 3.0, 2.5, 2.0, 1.5):
            sel = v < T
            nsel = int(sel.sum())
            if nsel == 0:
                continue
            dm = dmax[sel]; dr = drmse[sel]
            netpass = sum((r["p0"] and not sel[i]) or r["p1"] for i, r in enumerate(rows))
            print(f"    {T if T < 1e8 else 0:>7.1f}{nsel:>6}"
                  f"{f'{int((dm<0).sum())}/{nsel} 中位{np.median(dm):+.2f}':>17}"
                  f"{f'{int((dr<0).sum())}/{nsel} 中位{np.median(dr):+.2f}':>18}"
                  f"{len(rows)-nsel:>17}{netpass:>7}/{len(rows)}")
    print(f"\n判读：A 的强负相关 ⇒ 注入**确实**在作用且 ΔMAX 由鼓包帧定；再看 B 的**分臂**列 ——")
    print(f"      若某判据在一臂强、另一臂 ≈0 ⇒ 相关由**臂/会话身份**扛着，**不是**可迁移的规则。")
    print(f"      阈值规则那一段才是决策形状：净 PASS 必须**同时**不靠牺牲 RMSE 换到。")


if __name__ == "__main__":
    main()
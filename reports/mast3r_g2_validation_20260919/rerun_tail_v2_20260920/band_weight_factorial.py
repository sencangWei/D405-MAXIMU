#!/usr/bin/env python3
"""`[8/9]` 的注入是 `lw × 高通(VINS−MASt3R 分歧)` —— 两层衰减相乘，鼓包信号被压掉约 10×。

## 假设（本轮要检验的）

§13 量到：鼓包帧处**可用信号**（分歧在误差方向上的投影，折算到 lw=1.0）
中位 **+3.42mm**，而「只失败在 max」那批格的**缺口中位才 2.6mm** ⇒
**信号比缺口大**，lw=0.2 只交出 0.685mm（= 0.2×3.42，逐位自洽）
⇒ 上一轮「差一个数量级」的成因**不是没信息，是幅度被两层相乘的衰减压掉了**：

1. `--docker2-local-weight 0.2`（5×）
2. `[8/9]` 的高通：`high_frequency = disagreement − gaussian(disagreement, σ=smoothing_s·rate_hz)`
   —— 鼓包是 30–50 帧（3–5s）宽的**窗口尺度**特征，σ=8s 的高通只保留约 55%（约 2×）

⇒ 若假设成立，**把这两个旋钮往上推**应当把鼓包往真值推最多 3.42mm。**这是两个纯 CLI 旗标，
不改代码、且 `--smoothing-s` 从未被扫过**（§9.2 扫的是 `[9/9]` 末端输出平滑 σ=0.025–0.15s，两回事）。

## 设计：2×3 因子（不是单点扫描）

`lw ∈ {0.2(现役), 0.5, 1.0}` × `smoothing_s ∈ {8(现役), 40, 200}`，全 18 格。

* 若幅度假设成立 ⇒ MAX 应随 lw 与 smoothing_s **单调改善**。
* 若方向才是瓶颈（lw_gate 已量到鼓包帧投影**正 10/18 掷硬币**）⇒
  放大 5× 会把**错方向那 8 格**一起放大 5× ⇒ **净效果更差**。
  这两种预测在数值上是**反号**的，所以这个实验有判别力。

⚠ 口径：只报 `[8/9]`→`[9/9]` 融合链（`estimate` = fusion），真值 = `lighthouse_body_ground_truth.csv`，
门限与官方评测器一致（RMSE/p95/max ≤10mm、w10 ≥0.95、rot ≤2.0°）。

用法: band_weight_factorial.py
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
SCRIPTS = Path("/home/robot/ego_vio_humble/scripts")
HERE = Path(__file__).parent
PY = "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python"
VINS_CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
                   "formal_runtime_calibration/vins_config.yaml")
TAIL = ["--scale-horizon-s", "1", "--docker2-scale-weight", "0.25",
        "--roughness-threshold-mm", "9", "--adaptive-weight-strength", "0.45"]

LW = [0.2, 0.5, 1.0]
SM = [8.0, 40.0, 200.0]


def cells():
    out = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_imu_metric.csv")):
            c = p.parents[3]
            if (c / "lighthouse_body_ground_truth.csv").exists() and \
               (c / "docker2_slam/vio_corrected_stream.csv").exists():
                out.append((c, arm))
    return out


def run(traj, cell, graph_report, lw, sm, td, tag):
    smp, rep = td / f"s_{tag}.csv", td / f"r_{tag}.json"
    subprocess.run(
        [PY, str(SCRIPTS / "fuse_docker2_mast3r_complementary.py"),
         "--mast3r", str(traj), "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
         "--body-t-camera-yaml", str(VINS_CONFIG), *TAIL,
         "--smoothing-s", str(sm), "--docker2-local-weight", str(lw),
         "--graph-report", str(graph_report),
         "--output", str(td / f"p_{tag}.csv"), "--report", str(rep)],
        check=True, capture_output=True)
    subprocess.run([PY, str(SCRIPTS / "smooth_pose_trajectory.py"),
                    "--input", str(td / f"p_{tag}.csv"), "--output", str(smp),
                    "--method", "gaussian", "--gaussian-sigma-s", "0.025"],
                   check=True, capture_output=True)
    return smp


def metrics(est, tg, Pg, Qg):
    """(RMSE, p95, MAX, w10, rot, PASS, P, evec, err) —— 融合链，mm/deg。"""
    t, P, Q = E.load_trajectory(est)
    ins, val, plt_, qlt_ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    P, Q, t = P[ins][val], Q[ins][val], t[ins][val]
    m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
    R, tt = E.rigid_align(P, plt_[:, 1:4])
    ev = (P @ R.T + tt - plt_[:, 1:4]) * 1000.0
    v = (m["ate_translation_rmse_m"] * 1000, m["ate_translation_p95_m"] * 1000,
         m["ate_translation_max_m"] * 1000, m["ate_translation_within_10mm_ratio"],
         m["ate_rotation_rmse_deg"])
    return (*v, v[0] <= 10 and v[1] <= 10 and v[2] <= 10 and v[3] >= 0.95 and v[4] <= 2.0,
            P, ev, np.linalg.norm(ev, axis=1))


def main():
    cache = HERE / "g7_cache"
    cs = cells()
    print(f"[8/9] 幅度因子实验：lw {LW} × smoothing_s {SM}，{len(cs)} 格\n")
    rows = []
    for cell, arm in cs:
        key = f"{str(cell.relative_to(ROOT)).replace('/', '_')}__{arm}__joint"
        g = cache / key / "graph.csv"
        gr = cache / key / "graph.json"
        if not g.exists():
            continue
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            base = None
            for lw in LW:
                for sm in SM:
                    smp = run(g, cell, gr, lw, sm, td, f"{lw}_{sm}")
                    r, p95, mx, w10, rot, ok, P, ev, err = metrics(smp, tg, Pg, Qg)
                    if base is None:
                        base = (P, err, ev)  # err 1D 逐帧范数；ev 2D 逐帧向量（别拿 2D 做 argmax）
                    n = min(len(P), len(base[0]), len(base[1]))
                    # ★ 与基准同系 ⇒ 逐帧差就是注入量的增量；投影到真值方向
                    D = (P[:n] - base[0][:n]) * 1000.0
                    u = base[2][:n] / np.maximum(base[1][:n], 1e-9)[:, None]
                    k = int(np.argmax(base[1][:n]))
                    rows.append(dict(cell=str(cell.relative_to(ROOT)), arm=arm,
                                     lw=lw, sm=sm, rmse=r, p95=p95, mx=mx,
                                     w10=w10, rot=rot, pas=ok,
                                     proj_peak=float((D * (-u)).sum(axis=1)[k])))
            b = [x for x in rows if x["cell"] == str(cell.relative_to(ROOT))
                 and x["arm"] == arm and x["sm"] == SM[0] and x["lw"] == LW[0]][0]
            print(f"{str(cell.relative_to(ROOT))[-40:]:<42}{arm:>7}  "
                  f"基准 MAX {b['mx']:6.2f} R {b['rmse']:5.2f} "
                  f"{'PASS' if b['pas'] else '    '}")
    (HERE / "band_weight_rows.json").write_text(json.dumps(rows, indent=1))

    print(f"\n{'='*104}\n■ 汇总（{len(rows)//(len(LW)*len(SM))} 格）")
    print(f"  {'lw':>5}{'smooth':>8}{'MAX中位':>9}{'RMSE中位':>10}{'p95中位':>9}"
          f"{'w10中位':>9}{'rot中位':>9}{'PASS':>8}{'MAX改善':>9}")
    for lw in LW:
        for sm in SM:
            g = [x for x in rows if x["lw"] == lw and x["sm"] == sm]
            if not g:
                continue
            f = lambda k: np.median([x[k] for x in g])  # noqa: E731
            npass = sum(x["pas"] for x in g)
            print(f"  {lw:>5}{sm:>8}{f('mx'):>9.2f}{f('rmse'):>10.2f}{f('p95'):>9.2f}"
                  f"{f('w10')*100:>8.1f}%{f('rot'):>9.3f}{npass:>5}/{len(g)}"
                  f"{int(sum(x['mx'] < np.median([y['mx'] for y in g if y['lw']==LW[0] and y['sm']==SM[0]]) for x in g)):>9}")
    print(f"""
判读：
  * 幅度假设成立 ⇒ MAX 应随 lw 与 smoothing_s 单调【改善】。
  * 方向才是瓶颈 ⇒ 放大 5× 会把错方向那批一起放大 ⇒ 净效果【更差】。
  两者数值反号，本实验有判别力。proj_peak（鼓包帧注入投影）>0 = 往真值推 = 校正。""")
    # 鼓包帧投影随幅度的变化
    print(f"\n  鼓包帧注入投影 proj_peak（中位）：")
    for lw in LW:
        cells_ = []
        for sm in SM:
            g = [x["proj_peak"] for x in rows if x["lw"] == lw and x["sm"] == sm]
            cells_.append(f"s={sm:g}: {np.median(g):+6.2f}")
        print(f"    lw={lw:<5}" + "   ".join(cells_))


if __name__ == "__main__":
    main()
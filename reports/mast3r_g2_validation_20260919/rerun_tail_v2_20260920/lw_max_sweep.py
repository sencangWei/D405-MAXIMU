#!/usr/bin/env python3
"""`--docker2-local-weight` **在 MAX（当下唯一卡门的量）上**重判。

## 为什么必须重判

现行的「lw=0 对」是这么判出来的（`fusion_param_ab_20260920/local_weight_sweep.py`）：

* 判据是 **`ate_translation_rmse_deg` / `ate_rotation_rmse_deg`**，
  **`ate_translation_max_m` 一次都没记**；
* 而且当时带着 **`--use-docker2-orientation-for-lever-arm`**（该开关 09-20 已被 19/19 组
  判为有害并去掉）。

⇒ 那条结论是「lw 对 **RMSE** 的影响」在两个**已废弃的**口径下测的。
而 09-20 端到端已查明：**当下 16 次成功里 9 次只失败在 `ate_translation_max`**。

## 为什么 lw 是**最合理**的候选

`lw` 是 `[8/9]` 里**唯一把 VINS 位置注进融合轨迹的旋钮**（`lw=0` 时 `[8/9]` 基本是直通，
只剩 camera→body 与一个全局标量）。而 `bump_origin.py` 已证：**最终那个峰在 `[6/8]`
就 1:1 存在，形状 100% 来自 MASt3R 前端** ⇒ `lw=0` 下这个前端鼓包被**原样搬进**最终产物。
注入一条**来自不同传感器**的 VINS 位置，正好是最可能把那块鼓包压下去的手段
（`lw=.35` 时报告 `injected_correction_max_mm` 约 7–9mm，与鼓包同量级）。

## 便宜在哪

`[7/8]` 产物**全部复用** `g7_cache/`（`joint` 臂，现役参数）⇒ 只重跑尾段 `[8/9]+[9/9]`，
约 1s/cell。所以可以铺满全格。

★ 必看 `injected_max` 列：`fuse_docker2_mast3r_complementary.py:508` 在 **VINS 验收不过时
把 lw 静默归零** —— 那种 cell 的 lw 列是假数据，不能当证据。

用法: lw_max_sweep.py [--weights 0,0.1,0.2,0.35,0.5,0.75]
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
# [8/9] 其余参数逐字取自现役工作流 mast3r_slam_precision_workflow.sh:319-332，只切 lw
FUSE_TAIL = ["--scale-horizon-s", "1", "--smoothing-s", "8",
             "--docker2-scale-weight", "0.25",
             "--roughness-threshold-mm", "9", "--adaptive-weight-strength", "0.45"]


def cells():
    out = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_imu_metric.csv")):
            c = p.parents[3]
            if (c / "lighthouse_body_ground_truth.csv").exists() and \
               (c / "docker2_slam/vio_corrected_stream.csv").exists():
                out.append((c, arm))
    return out


def run_tail(cell, traj, graph_report, lw, td):
    raw, sm = td / "raw.csv", td / "sm.csv"
    rep = td / "rep.json"
    cmd = [PY, str(SCRIPTS / "fuse_docker2_mast3r_complementary.py"),
           "--mast3r", str(traj),
           "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
           "--body-t-camera-yaml", str(VINS_CONFIG), *FUSE_TAIL,
           "--docker2-local-weight", str(lw),
           "--output", str(raw), "--report", str(rep)]
    if graph_report is not None:
        cmd += ["--graph-report", str(graph_report)]
    subprocess.run(cmd, check=True, capture_output=True)
    subprocess.run([PY, str(SCRIPTS / "smooth_pose_trajectory.py"),
                    "--input", str(raw), "--output", str(sm), "--method", "gaussian",
                    "--gaussian-sigma-s", "0.025"], check=True, capture_output=True)
    inj = None
    try:
        inj = json.loads(rep.read_text()).get("fusion", {}).get("injected_correction_max_mm")
    except Exception:                                                  # noqa: BLE001
        pass
    return sm, inj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="0,0.1,0.2,0.35,0.5,0.75")
    a = ap.parse_args()
    WS = [float(x) for x in a.weights.split(",")]
    cache = HERE / "g7_cache"

    cs = cells()
    print(f"`--docker2-local-weight` 在 **MAX** 上重判（现役 [8/9] 配置，无姿态开关）")
    print(f"{len(cs)} 组；[7/8] 复用 g7_cache/joint（现役参数）\n")
    hdr = "".join(f"{('lw ' + str(w)):>19}" for w in WS)
    print(f"{'cell':<40}{'arm':>7}{hdr}")
    print(f"{'':<47}" + "".join(f"{'RMSE/rot/MAX':>19}" for _ in WS))
    print("-" * (47 + 19 * len(WS)))
    acc = {w: {"r": [], "t": [], "m": [], "p": []} for w in WS}
    injs = {w: [] for w in WS}
    for cell, arm in cs:
        md = cell / f"fusion/{arm}/mast3r"
        key = f"{str(cell.relative_to(ROOT)).replace('/', '_')}__{arm}__joint"
        g, gr = cache / key / "graph.csv", cache / key / "graph.json"
        if not g.exists():
            continue
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        row = []
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            for w in WS:
                try:
                    est, inj = run_tail(cell, g, gr, w, td)
                    t2, P, Q = E.load_trajectory(est)
                    _, _, plt_, qlt_ = E.interpolate_ground_truth(t2, tg, Pg, Qg, 0.1)
                    m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
                    rv, tv, mv = (m["ate_translation_rmse_m"] * 1000,
                                  m["ate_rotation_rmse_deg"],
                                  m["ate_translation_max_m"] * 1000)
                    pv = (rv <= 10.0 and m["ate_translation_p95_m"] * 1000 <= 10.0
                          and mv <= 10.0
                          and m["ate_translation_within_10mm_ratio"] >= 0.95 and tv <= 2.0)
                    injs[w].append(inj)
                except Exception:                                      # noqa: BLE001
                    rv = tv = mv = float("nan"); pv = False
                acc[w]["r"].append(rv); acc[w]["t"].append(tv)
                acc[w]["m"].append(mv); acc[w]["p"].append(pv)
                row.append((rv, tv, mv))
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name[:40]:<40}{arm:>7}"
              + "".join(f"{f'{rv:7.2f}/{tv:5.2f}/{mv:6.1f}':>19}" for rv, tv, mv in row))
    print("-" * (47 + 19 * len(WS)))
    for label, k, fmt in (("中位 RMSE (mm)", "r", "{:>19.2f}"),
                          ("中位 rot (°)", "t", "{:>19.3f}"),
                          ("中位 MAX (mm)  ← 卡门的量", "m", "{:>19.2f}")):
        print(f"{label:<40}{'':>7}" + "".join(fmt.format(np.nanmedian(acc[w][k])) for w in WS))
    print(f"{'MAX≤10mm 的组数':<40}{'':>7}"
          + "".join(f"{int(np.nansum(np.array(acc[w]['m']) <= 10.0)):>16}/{len(acc[w]['m']):<2}"
                    for w in WS))
    print(f"{'全门 PASS 的组数':<40}{'':>7}"
          + "".join(f"{int(np.sum(acc[w]['p'])):>16}/{len(acc[w]['p']):<2}" for w in WS))
    print(f"{'RMSE≤10 的组数':<40}{'':>7}"
          + "".join(f"{int(np.nansum(np.array(acc[w]['r']) <= 10.0)):>16}/{len(acc[w]['r']):<2}"
                    for w in WS))
    base = WS[0]
    for label, k in (("vs lw=0 逐 cell 更优/更差 (MAX)", "m"),
                     ("vs lw=0 逐 cell 更优/更差 (RMSE)", "r")):
        cells_s = "".join(
            f"{int(np.nansum(np.less(acc[w][k], acc[base][k]))):>11}"
            f"/{int(np.nansum(np.less(acc[base][k], acc[w][k]))):<7}" for w in WS)
        print(f"{label:<40}{'':>7}{cells_s}")
    print("\n注入修正 max（中位 mm，★ 0 或 None ⇒ 该 cell 的 lw 被静默归零，不是证据）")
    med = []
    for w in WS:
        vals = [x for x in injs[w] if x is not None]
        med.append(np.median(vals) if vals else float("nan"))
    print("{:<40}{:>7}".format("", "")
          + "".join("{:>19.3f}".format(v) for v in med))
    zero = {w: sum(1 for x in injs[w] if x is None or x == 0.0) for w in WS}
    print("{:<40}{:>7}".format("lw 被归零的 cell 数", "")
          + "".join("{:>16}/{:<2}".format(zero[w], len(injs[w])) for w in WS))


if __name__ == "__main__":
    main()
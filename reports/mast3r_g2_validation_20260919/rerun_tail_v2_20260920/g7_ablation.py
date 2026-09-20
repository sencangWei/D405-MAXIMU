#!/usr/bin/env python3
"""[7/8] 判决实验（先导版）：`--metric-scale-mode` 与「干脆跳过 [7/8]」对最终 fused ATE 的影响。

## 为什么是这个旋钮

`fuse_mast3r_stereo_imu.py:2497-2520` 三条分支：

| `--metric-scale-mode` | `selection_policy` | 用哪个尺度 |
|---|---|---|
| `joint`（现役） | `equal_weight_log_mean` | 双目与 IMU 尺度的对数等权平均 |
| `stereo` | `d405_stereo_direct_metric` | **只用双目**（= 物理基线直接给米制） |
| `imu` | `imu_adjacent_windows` | 只用 IMU |

关键：`:2770-2775` 的硬门 `imu_stereo_metric_scale_disagreement` **在 `stereo` 模式下被整个跳过**
——这正是 Codex 2026-09-11 定过、却只接进 `compare)` 而从没接进产品路径 `fusion)` 的策略。
本语料两链尺度差 3.73%（如 v10b/g1：stereo 0.32896 vs imu 0.31692），**差 1.9% 的最终尺度**；
官方评测器是**无尺度 SE(3) 对齐**，所以尺度误差会直接落进 ATE。
1.9% × 轨迹尺度 ⇒ 正好是我们要找的 5mm 量级。**这是目前唯一还没被试过的量级杠杆。**

## 做法

`[7/8]` 参数逐字取自工作流（`mast3r_slam_precision_workflow.sh:280-307`），**只切
`--metric-scale-mode`**；`[8/9]`/`[9/9]` 用现役参数；官方评测器。
额外一条 `no_g7` 臂：**整个跳过 [7/8]**，把 `trajectory_imu_metric.csv` 直接喂给 [8/9]
（不带 `--graph-report`，`lw=0` 下无副作用）⇒ 直接量出「[7/8] 图优化到底贡献了多少」。
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
PY = "/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python"
VINS_CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
                   "formal_runtime_calibration/vins_config.yaml")
IMU_CALIB = Path("/home/robot/ego_vio_humble/config/"
                 "imu_runtime_accel_calibrated_raw_gyro_20260816.yaml")
ARMS = ("joint", "stereo", "imu", "no_g7")

# ★ 把昂贵的 [7/8] 产物**留盘**（临时目录会被删）。键 = (cell名, 臂, 模式)。
#   否则每次改评估口径都要把 [7/8] 全部重跑一遍（~50min/21组）。
CACHE = Path(__file__).parent / "g7_cache"

G7_BASE = ["--stream", "infrared_left",
           "--expected-td-s", "-0.009109323",
           "--orientation-node-stride", "10", "--position-node-stride", "5",
           "--minimum-stereo-sample-hop", "1",
           "--relative-motion-sigma-m", "0.008", "--auto-visual-position-sigma",
           "--joint-max-correction-mm", "25", "--joint-correction-cap-mode", "per-node",
           "--full-rate-imu-position-refinement", "--full-rate-max-correction-mm", "20",
           "--position-mode", "keyframe-graph"]
# [8/9] 与现役工作流逐字一致
FUSE = ["--scale-horizon-s", "1", "--smoothing-s", "8",
        "--docker2-local-weight", "0", "--docker2-scale-weight", "0.25",
        "--roughness-threshold-mm", "9", "--adaptive-weight-strength", "0.45"]


def cells():
    out = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_imu_metric.csv")):
            c = p.parents[3]
            if not (c / "lighthouse_body_ground_truth.csv").exists():
                continue
            if not (c / "docker2_slam/vio_corrected_stream.csv").exists():
                continue
            out.append((c, arm))
    return out


def session_of(cell):
    prov = json.loads((cell / "lighthouse_ground_truth_provenance.json").read_text())
    return Path(prov["clock_mapping"]["d405_frames"]).parent


def run_g7(cell, md, mode, td, key):
    """跑 [7/8]。产物留盘到 CACHE/<key>/；已存在则直接复用。"""
    cdir = CACHE / key
    g, gr = cdir / "graph.csv", cdir / "graph.json"
    if g.exists() and gr.exists():
        return g, gr
    cdir.mkdir(parents=True, exist_ok=True)
    cmd = [PY, str(SCRIPTS / "fuse_mast3r_stereo_imu.py"),
           "--session", str(session_of(cell)),
           "--trajectory", str(md / "trajectory_imu_metric.csv"),
           "--stereo-report", str(md / "stereo_scale_bidirectional_report.json"),
           "--additional-stereo-report", str(md / "stereo_scale_long_hops_report.json"),
           "--additional-stereo-report", str(md / "stereo_scale_dense10hz_report.json"),
           "--additional-stereo-report", str(md / "stereo_scale_multisecond_report.json"),
           "--imu-scale-report", str(md / "imu_scale_report.json"),
           "--vins-config", str(VINS_CONFIG), "--imu-calibration", str(IMU_CALIB),
           "--keyframe-dir", str(md / "mast3r_logs/keyframes/dataset"),
           "--relative-motion-trajectory", str(cell / "docker2_slam/vio_corrected_stream.csv"),
           "--relative-motion-report", str(cell / "docker2_slam/run_acceptance.json"),
           *G7_BASE, "--metric-scale-mode", mode,
           "--output", str(g), "--report", str(gr)]
    subprocess.run(cmd, check=True, capture_output=True)
    return g, gr


def run_tail(cell, traj, graph_report, td):
    raw, sm = td / "raw.csv", td / "sm.csv"
    cmd = [PY, str(SCRIPTS / "fuse_docker2_mast3r_complementary.py"),
           "--mast3r", str(traj),
           "--docker2", str(cell / "docker2_slam/vio_corrected_stream.csv"),
           "--body-t-camera-yaml", str(VINS_CONFIG), *FUSE,
           "--output", str(raw), "--report", str(td / "rep.json")]
    if graph_report is not None:
        cmd += ["--graph-report", str(graph_report)]
    subprocess.run(cmd, check=True, capture_output=True)
    subprocess.run([PY, str(SCRIPTS / "smooth_pose_trajectory.py"),
                    "--input", str(raw), "--output", str(sm), "--method", "gaussian",
                    "--gaussian-sigma-s", "0.025"], check=True, capture_output=True)
    return sm


def main():
    cs = cells()
    print(f"[7/8] 尺度模式消融；{len(cs)} 组（固定 [8/9] 现役参数）\n")
    # ★ 当下唯一卡门的是 ate_translation_max ⇒ 只报 RMSE 会漏掉判决量。
    print(f"{'cell':<44}{'arm':>7}" + "".join(f"{a:>21}" for a in ARMS))
    print("-" * (51 + 21 * len(ARMS)))
    ate = {a: [] for a in ARMS}
    rot = {a: [] for a in ARMS}
    mx = {a: [] for a in ARMS}
    ok = {a: [] for a in ARMS}
    for cell, arm in cs:
        md = cell / f"fusion/{arm}/mast3r"
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        row, rowg, rowm = [], [], []
        for a in ARMS:
            key = f"{str(cell.relative_to(ROOT)).replace('/', '_')}__{arm}__{a}"
            with tempfile.TemporaryDirectory() as t:
                td = Path(t)
                try:
                    if a == "no_g7":
                        est = run_tail(cell, md / "trajectory_imu_metric.csv", None, td)
                    else:
                        g, gr = run_g7(cell, md, a, td, key)
                        est = run_tail(cell, g, gr, td)
                    t2, P, Q = E.load_trajectory(est)
                    _, _, plt_, qlt_ = E.interpolate_ground_truth(t2, tg, Pg, Qg, 0.1)
                    m = E.pose_errors(P, Q, plt_[:, 1:4], qlt_, 30)
                    av = m["ate_translation_rmse_m"] * 1000
                    rv = m["ate_rotation_rmse_deg"]
                    mv = m["ate_translation_max_m"] * 1000
                    # 官方评测器口径（--max-ate-*-mm 10.0 / rot 2.0 / within 0.95）
                    ov = (av <= 10.0 and m["ate_translation_p95_m"] * 1000 <= 10.0
                          and mv <= 10.0
                          and m["ate_translation_within_10mm_ratio"] >= 0.95 and rv <= 2.0)
                except Exception:                                     # noqa: BLE001
                    av, rv, mv, ov = (float("nan"),) * 3 + (False,)
            ate[a].append(av); rot[a].append(rv); mx[a].append(mv); ok[a].append(ov)
            row.append(av); rowg.append(rv); rowm.append(mv)
        best = np.nanmin(row)
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name:<44}{arm:>7}"
              + "".join(f"{v:>11.2f}{'*' if v == best else ' '}{v2:>4.2f}{v3:>5.1f}"
                        for v, v2, v3 in zip(row, rowg, rowm)))
    print("-" * (51 + 21 * len(ARMS)))
    print("（每格 = RMSE / rot / MAX，单位 mm 与 °；`*` = 该行 RMSE 最优）\n")
    for label, d, fmt in (("中位 RMSE (mm)", ate, "{:>21.2f}"),
                          ("中位 rot (°)", rot, "{:>21.3f}"),
                          ("中位 MAX (mm)", mx, "{:>21.2f}")):
        print(f"{label:<44}{'':>7}" + "".join(fmt.format(np.nanmedian(d[a])) for a in ARMS))
    print(f"{'MAX≤10mm 的组数':<44}{'':>7}"
          + "".join(f"{int(np.nansum(np.array(mx[a]) <= 10.0)):>18}/{len(mx[a]):<2}"
                    for a in ARMS))
    print(f"{'全门 PASS 的组数':<44}{'':>7}"
          + "".join(f"{int(np.sum(ok[a])):>18}/{len(ok[a]):<2}" for a in ARMS))
    j = "joint"
    for label, d, better in (("vs joint 逐 cell 更优/更差 (RMSE)", ate, np.less),
                             ("vs joint 逐 cell 更优/更差 (MAX)", mx, np.less)):
        print(f"{label:<44}{'':>7}"
              + "".join(f"{int(np.nansum(better(np.array(d[a]), np.array(d[j])))):>13}"
                        f"/{int(np.nansum(better(np.array(d[j]), np.array(d[a])))):<7}"
                        for a in ARMS))


if __name__ == "__main__":
    main()
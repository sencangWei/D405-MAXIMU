#!/usr/bin/env python3
"""瞄准 `ate_translation_max` 的 [7/8] 旋钮扫描（多组对照）。

## 为什么要打这个

`rerun_tail_v2_20260920` 的端到端结果：**16 次成功里有 9 次只失败在
`ate_translation_max_over_limit`**（RMSE 才 2.6–5.4mm、p95 多数 <10mm、w10 98–100%、
rotation 多数 <2.0°）。也就是说**当下唯一卡住验收的是几十帧宽的局部鼓包**。

`bump_trace.py` 在 v10b/g1/tight 上定位过它的来历：鼓包在 `[6/8] imu_metric`
**已经有**（窗口 max 10.62mm），`[7/8]` 把整体 RMS 从 3.58 压到 2.62，
**却把这个峰抬到 13.46mm** ⇒ 「[7/8] 总体有益、但对鼓包过度修正」。

## 候选机理与旋钮

`--relative-motion-sigma-m 0.008`（8mm）是 [7/8] 里给 **VINS 相对运动**的 sigma。
而 VINS 单链本身 24mm 级（[[report-fusion-not-vins-accuracy]]）。若这个 sigma 过紧，
图会被 VINS 的局部噪声拽着走 ⇒ 鼓包处过修正。**本扫描主变量**。
另加一条「**完全不用 VINS 相对运动**」臂（不传 `--relative-motion-trajectory`）——
它同时绕开 `validate_relative_motion_report`，对 holdout_batch2 那两条也能跑。

## 指标

这次**把 max 一起记**（上一版只记 rmse/rot，漏掉了当下唯一失败的项）。

用法: g7_knob_sweep.py [batch/group/arm ...]   （不给则跑全部有产物的 cell）
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
sys.path.insert(0, str(Path(__file__).parent))
import evaluate_slam_ground_truth as E  # noqa: E402
from vins_dir import pick_vins_dir   # noqa: E402

ROOT = Path("/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow")
SCRIPTS = Path("/home/robot/ego_vio_humble/scripts")
PY = sys.executable
VINS_CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
                   "formal_runtime_calibration/vins_config.yaml")
IMU_CALIB = Path("/home/robot/ego_vio_humble/config/"
                 "imu_runtime_accel_calibrated_raw_gyro_20260816.yaml")

# 与现役工作流逐字一致的 [7/8] 前半段
# ★ 注意 `--auto-visual-position-sigma` 不在这里：它**依赖** VINS 相对运动轨迹
#   （`fuse_mast3r_stereo_imu.py:609` 无轨迹时直接 raise）。所以按臂加，见 KNOBS。
G7_FIXED = ["--stream", "infrared_left", "--expected-td-s", "-0.009109323",
            "--orientation-node-stride", "10", "--position-node-stride", "5",
            "--minimum-stereo-sample-hop", "1",
            "--joint-max-correction-mm", "25", "--joint-correction-cap-mode", "per-node",
            "--full-rate-imu-position-refinement", "--full-rate-max-correction-mm", "20",
            "--metric-scale-mode", "joint", "--position-mode", "keyframe-graph"]
AUTO_SIGMA = ["--auto-visual-position-sigma"]

# (标签, 追加参数, 是否使用 VINS 相对运动)
KNOBS = [
    ("rel .008 现役", ["--relative-motion-sigma-m", "0.008"], True, AUTO_SIGMA),
    ("rel .02", ["--relative-motion-sigma-m", "0.02"], True, AUTO_SIGMA),
    ("rel .06", ["--relative-motion-sigma-m", "0.06"], True, AUTO_SIGMA),
    ("rel .2", ["--relative-motion-sigma-m", "0.2"], True, AUTO_SIGMA),
    # ★ 不能同时去掉 auto-sigma：那条臂必须**显式**给一个 sigma（用默认 0.020），
    #   否则 ValueError: automatic visual sigma requires an aligned relative-motion
    #   trajectory。这里同时去掉了 [8/9] 的 --docker2 ⇒ 整条 Docker2 链退出。
    ("无 VINS 相对运动", ["--visual-position-sigma-m", "0.020"], False, []),
]
FUSE = ["--scale-horizon-s", "1", "--smoothing-s", "8",
        "--docker2-local-weight", "0", "--docker2-scale-weight", "0.25",
        "--roughness-threshold-mm", "9", "--adaptive-weight-strength", "0.45"]


def all_cells():
    out = []
    for arm in ("tight", "sparse"):
        for p in sorted(ROOT.glob(f"**/fusion/{arm}/mast3r/trajectory_imu_metric.csv")):
            c = p.parents[3]
            if not (c / "lighthouse_body_ground_truth.csv").exists():
                continue
            out.append((c, arm))
    return out


def run_one(cell, arm, extra, use_vins, sigma, td):
    md = cell / f"fusion/{arm}/mast3r"
    g, gr = td / "g.csv", td / "g.json"
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
           *G7_FIXED, *sigma, *extra]
    if use_vins:
        v = pick_vins_dir(cell)
        if v is None:
            raise RuntimeError("无可用 VINS 产物")
        cmd += ["--relative-motion-trajectory", str(v[0]),
                "--relative-motion-report", str(v[1])]
    cmd += ["--output", str(g), "--report", str(gr)]
    subprocess.run(cmd, check=True, capture_output=True)
    raw, sm = td / "raw.csv", td / "sm.csv"
    fcmd = [PY, str(SCRIPTS / "fuse_docker2_mast3r_complementary.py"),
            "--mast3r", str(g), "--body-t-camera-yaml", str(VINS_CONFIG), *FUSE,
            "--graph-report", str(gr), "--output", str(raw), "--report", str(td / "f.json")]
    if use_vins:
        fcmd += ["--docker2", str(v[0]), "--docker2-report", str(v[1])]
    subprocess.run(fcmd, check=True, capture_output=True)
    subprocess.run([PY, str(SCRIPTS / "smooth_pose_trajectory.py"),
                    "--input", str(raw), "--output", str(sm), "--method", "gaussian",
                    "--gaussian-sigma-s", "0.025"], check=True, capture_output=True)
    return sm


def session_of(cell):
    p = json.loads((cell / "lighthouse_ground_truth_provenance.json").read_text())
    return Path(p["clock_mapping"]["d405_frames"]).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cells", nargs="*")
    # ★ 必须是 action="append" 而不是 nargs="*"：nargs="*" 上的 --arms 会把后面的
    #   位置参数（cell 列表）一起吞掉，且第二次 --arms 会覆盖第一次。
    ap.add_argument("--arms", action="append", default=None,
                    help="只跑这些配置臂（可给多次；默认全部）")
    a = ap.parse_args()
    knobs = [k for k in KNOBS if not a.arms or k[0] in a.arms]
    want = set(a.cells)
    cs = [(c, arm) for c, arm in all_cells()
          if not want or f"{c.relative_to(ROOT)}/{arm}" in want]
    print(f"瞄准 max 的 [7/8] 旋钮扫描；{len(cs)} 组 × {len(knobs)} 配置\n")
    head = f"{'cell':<42}{'arm':>7}" + "".join(f"{k[0]:>19}" for k in knobs)
    print(head); print("-" * len(head))
    res = {k[0]: [] for k in knobs}
    for cell, arm in cs:
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        cells_row, vals = [], []
        for label, extra, uv, sigma in knobs:
            with tempfile.TemporaryDirectory() as t:
                try:
                    est = run_one(cell, arm, extra, uv, sigma, Path(t))
                    t2, P, Q = E.load_trajectory(est)
                    ins, val, plt_, ql = E.interpolate_ground_truth(t2, tg, Pg, Qg, 0.1)
                    m = E.pose_errors(P[ins][val], Q[ins][val], plt_[:, 1:4], ql, 30)
                    # pose_errors 不返回 result/failures（那是 CLI 的 gate 函数加的），
                    # 这里按评测器同一条门限（--max-ate-*-mm 默认 10.0，rot 2.0）自己判。
                    v = (m["ate_translation_rmse_m"] * 1000, m["ate_translation_max_m"] * 1000,
                         m["ate_rotation_rmse_deg"], None)
                    v = v[:3] + (
                        v[0] <= 10.0 and m["ate_translation_p95_m"] * 1000 <= 10.0
                        and v[1] <= 10.0 and m["ate_translation_within_10mm_ratio"] >= 0.95
                        and v[2] <= 2.0,)
                except Exception as e:                                 # noqa: BLE001
                    v = (float("nan"),) * 3 + (False,)
                    if len(cells_row) == 0:
                        print(f"    [{label}] {type(e).__name__}: {str(e)[:120]}")
            res[label].append(v)
            vals.append(v)
        best = np.nanmin([v[1] for v in vals])
        name = str(cell.relative_to(ROOT))
        print(f"{name:<42}{arm:>7}"
              + "".join(f"{v[0]:>10.2f}{v[1]:>7.1f}{'*' if v[1] == best else ' '}"
                        f"{v[2]:>4.2f}{'P' if v[3] else '.'}" for v in vals))
    print("-" * len(head))
    print("（每格 = RMSE / MAX / rot / P=PASS）\n")
    for k in knobs:
        a_ = np.array([v[0] for v in res[k[0]]]); mx = np.array([v[1] for v in res[k[0]]])
        rt = np.array([v[2] for v in res[k[0]]]); ok = np.array([v[3] for v in res[k[0]]])
        print(f"{k[0]:<20} RMS中位 {np.nanmedian(a_):>6.2f}  MAX中位 {np.nanmedian(mx):>6.2f}  "
              f"MAX≤10 的组 {int(np.nansum(mx<=10)):>2}/{len(mx)}  "
              f"rot中位 {np.nanmedian(rt):>5.3f}  全门 PASS {int(ok.sum()):>2}/{len(ok)}")


if __name__ == "__main__":
    main()
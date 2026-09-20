#!/usr/bin/env python3
"""局部偏移块的逐级追踪（★ 修正版：所有段都先过同一次 camera→body 再比）。

## 为什么必须这样比

`fusion/<arm>/mast3r/trajectory_imu_metric.csv` 与 `trajectory_graph.csv` 都在
**左红外相机系**，而真值（`lighthouse_body_ground_truth.csv`）在 **body 系**。
29mm 杆臂逐帧按姿态减掉 ⇒ 直接把相机系轨迹比 body 系真值会凭空多出 ~17mm RMS
**坐标系错配假象**（[[mast3r-chain-topology]] 里已记录过同一个坑）。

⇒ 正确做法：每一段都用**同一个** `[8/9]`（`lw=0, sw=0.25` ⇒ 融合基本是直通，
只做 camera→body 与一个标量）转成 body 系，再比。这样各列差异才纯是上游贡献。

用法: bump_trace.py <cell_dir> <arm> [--lo N --hi N]
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/robot/ego_vio_humble/scripts")
import evaluate_slam_ground_truth as E  # noqa: E402

SCRIPTS = Path("/home/robot/ego_vio_humble/scripts")
PY = sys.executable
VINS_CONFIG = Path("/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/"
                   "formal_runtime_calibration/vins_config.yaml")
FUSE = ["--scale-horizon-s", "1", "--smoothing-s", "8",
        "--docker2-local-weight", "0", "--docker2-scale-weight", "0.25",
        "--roughness-threshold-mm", "9", "--adaptive-weight-strength", "0.45"]


def to_body(cell, traj, graph_report, td):
    """跑 [8/9]（+[9/9] 平滑），得到 body 系轨迹。固定同一套参数 ⇒ 各段可比。"""
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


def errs(est: Path, tg, Pg, Qg):
    t, P, Q = E.load_trajectory(est)
    inside, valid, plt_, _ = E.interpolate_ground_truth(t, tg, Pg, Qg, 0.1)
    P, t = P[inside][valid], t[inside][valid]
    gt = plt_[:, 1:4]
    R, tt = E.rigid_align(P, gt)
    return t, np.linalg.norm(P @ R.T + tt - gt, axis=1) * 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cell")
    ap.add_argument("arm", choices=("sparse", "tight"))
    ap.add_argument("--lo", type=int, default=-1)
    ap.add_argument("--hi", type=int, default=-1)
    a = ap.parse_args()
    cell = Path(a.cell).resolve()
    md = cell / f"fusion/{a.arm}/mast3r"
    tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")

    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        stages = []
        for name, traj, gr in (
            ("[6/8] imu_metric", md / "trajectory_imu_metric.csv", None),
            ("[7/8] graph", md / "trajectory_graph.csv", md / "graph_fusion_report.json"),
            ("[8/9] fused（现成）", cell / f"fusion_v2/{a.arm}/trajectory_fused.csv", None),
        ):
            sub = td / name.replace("/", "_").replace(" ", "_")
            sub.mkdir()
            if traj.name == "trajectory_fused.csv":
                est = traj
            else:
                gr = None if gr is None or not Path(gr).exists() else gr
                est = to_body(cell, traj, gr, sub)
            stages.append((name, est))

        data, times = {}, {}
        for name, p in stages:
            if not Path(p).exists():
                print(f"  （缺）{name}: {p}")
                continue
            times[name], data[name] = errs(Path(p), tg, Pg, Qg)

    ref = next(iter(data))
    tref, n = times[ref], len(data[ref])
    lo = a.lo if a.lo >= 0 else max(0, int(np.argmax(data[ref])) - 10)
    hi = a.hi if a.hi >= 0 else min(n, lo + 26)
    idx = {k: np.searchsorted(times[k], tref, side="left").clip(0, len(times[k]) - 1)
           for k in data}
    print(f"{cell}  {a.arm}   帧窗 [{lo}, {hi})   共 {n} 帧"
          f"（全部已过同一次 camera→body，可比）\n")
    print(f"{'idx':>6}" + "".join(f"{k:>26}" for k in data))
    print("-" * (6 + 26 * len(data)))
    for i in range(lo, hi):
        print(f"{i:>6}" + "".join(f"{data[k][idx[k][i]]:>26.2f}" for k in data))
    print("-" * (6 + 26 * len(data)))
    print(f"{'RMS':>6}" + "".join(f"{np.sqrt((d**2).mean()):>26.2f}" for d in data.values()))
    print(f"{'MAX':>6}" + "".join(f"{d.max():>26.2f}" for d in data.values()))
    print("\n判读：鼓包最早出现在哪一列 ⇒ 责任段。")
    if len(data) >= 2:
        ks = list(data)
        print(f"  [7/8] 对 [6/8] 的贡献：RMS {np.sqrt((data[ks[0]]**2).mean()):.2f} → "
              f"{np.sqrt((data[ks[1]]**2).mean()):.2f} mm；"
              f"该窗口 max {data[ks[0]].max():.2f} → {data[ks[1]].max():.2f} mm")


if __name__ == "__main__":
    main()
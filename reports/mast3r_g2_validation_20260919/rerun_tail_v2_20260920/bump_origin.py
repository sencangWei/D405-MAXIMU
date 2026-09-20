#!/usr/bin/env python3
"""鼓包归属：最终那个 >10mm 的峰，是 **[6/8] 就带进来的**，还是 **[7/8] 造出来的**？

## 为什么现在问这个

`bump_trace.py` 只看过 **1 个 cell 的 1 个窗口**（v10b/g1/tight idx 462-488），
结论是「[7/8] 把窗口峰 10.62→13.46」。但全量消融里 `no_g7`（整个跳过 [7/8]）
中位 MAX 是 **30.75**、`joint` 是 **15.00** ⇒ **[7/8] 全局把峰砍掉一半**。
两个观察方向相反 ⇒ 必须**全量**回答：最终峰在时刻 t* 上，
`[6/8]` 那一列**本来有多大**。

## 口径（与 bump_trace 一致，不要改）

`trajectory_imu_metric.csv` / `trajectory_graph.csv` 都在**左红外相机系**，
真值在 **body 系**；必须各段都过**同一次** [8/9] 再比，否则凭空多 ~17mm 假象。
这里三列都走**同一个** `to_body()` 代码路径，只有 `--graph-report` 有无之别。

另附 `[1/8]` 纯缩放核对：`imu_metric ≈ s × frames`（s 取自 imu_scale_report.json）。
若逐位成立 ⇒ **[6/8] 在形状上不可能造出鼓包**，鼓包的形状来自 `[1/8]` 前端。

用法: bump_origin.py [--cells-from-cache]
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


def to_body(cell, traj, graph_report, td):
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


def scale_check(md):
    """[1/8] frames 与 [6/8] imu_metric 是否逐位差一个全局标量。"""
    rep = json.loads((md / "imu_scale_report.json").read_text())
    s = float(rep["scale"])
    a = np.genfromtxt(md / "trajectory_frames.csv", delimiter=",", names=True)
    b = np.genfromtxt(md / "trajectory_imu_metric.csv", delimiter=",", names=True)
    ca = [n for n in a.dtype.names if n.startswith("p_") or n in ("x", "y", "z")]
    cb = [n for n in b.dtype.names if n.startswith("p_") or n in ("x", "y", "z")]
    A = np.column_stack([a[n] for n in ca]); B = np.column_stack([b[n] for n in cb])
    if A.shape != B.shape or A.shape[0] < 2:
        return s, None
    # 去掉公共平移后比形状：B/? 应与 A/? 相差同一标量
    r = np.linalg.norm(B - B[0], axis=1) / np.maximum(np.linalg.norm(A - A[0], axis=1), 1e-12)
    return s, (float(np.nanmin(r)), float(np.nanmax(r)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(HERE / "g7_cache"))
    a = ap.parse_args()
    cache = Path(a.cache)

    cs = cells()
    print(f"鼓包归属：{len(cs)} 组（三列同过一次 camera→body）\n")
    print(f"{'cell':<40}{'arm':>7}{'t8_peak':>9}{'t7_peak':>9}{'same?':>6}"
          f"{'max68':>8}{'max78':>8}{'e68@t7':>8}{'e78@t7':>8}{'inherit':>8}{'fr_span':>9}")
    print("-" * 112)
    rows = []
    for cell, arm in cs:
        md = cell / f"fusion/{arm}/mast3r"
        key = f"{str(cell.relative_to(ROOT)).replace('/', '_')}__{arm}__joint"
        g = cache / key / "graph.csv"
        gr = cache / key / "graph.json"
        if not g.exists():
            print(f"{str(cell).replace(str(ROOT)+'/', '')[:40]:<40}{arm:>7}   （缓存缺 {key}）")
            continue
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        try:
            _, fr_span = scale_check(md)
        except Exception:                                              # noqa: BLE001
            fr_span = None
        with tempfile.TemporaryDirectory() as t:
            td = Path(t); (td / "a").mkdir(); (td / "b").mkdir()
            t68, e68 = errs(to_body(cell, md / "trajectory_imu_metric.csv", None, td / "a"),
                            tg, Pg, Qg)
            t78, e78 = errs(to_body(cell, g, gr, td / "b"), tg, Pg, Qg)
        i68, i78 = int(np.argmax(e68)), int(np.argmax(e78))
        same = "Y" if t68[i68] == t78[i78] else "n"
        inh = e68[i78] / e78[i78] if e78[i78] > 0 else float("nan")
        span = f"{fr_span[0]:.6f}" if fr_span else "-"
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name[:40]:<40}{arm:>7}{t68[i68]:>9.1f}{t78[i78]:>9.1f}{same:>6}"
              f"{e68.max():>8.2f}{e78.max():>8.2f}{e68[i78]:>8.2f}{e78[i78]:>8.2f}"
              f"{inh:>8.2f}{span:>9s}")
        rows.append((name, arm, e68, e78, i78))
    print("-" * 112)
    inh = [e68[i78] / e78[i78] for _, _, e68, e78, i78 in rows if e78[i78] > 0]
    mx68 = [e68.max() for _, _, e68, _, _ in rows]
    mx78 = [e78.max() for _, _, _, e78, _ in rows]
    print(f"{'中位':<54}{np.median(mx68):>8.2f}{np.median(mx78):>8.2f}"
          f"{'':>8}{'':>8}{np.median(inh):>8.2f}")
    print(f"\n判读：`inherit` = [6/8] 在 [7/8] 峰时刻的误差 / [7/8] 峰高。")
    print(f"  ≈1 ⇒ 峰是 [6/8] 带进来的（[7/8] 只搬运）；<<1 ⇒ 峰是 [7/8] 造的。")
    print(f"  中位 inherit = {np.median(inh):.2f}（n={len(inh)}）")
    print(f"  [7/8] 把全局峰砍掉: 中位 {np.median(mx68):.2f} → {np.median(mx78):.2f} mm")
    print(f"  `fr_span` = ‖imu_metric−imu_metric[0]‖/‖frames−frames[0]‖ 的 min–max；"
          f"若 min==max 到 6 位 ⇒ [6/8] 是纯全局缩放，形状来自 [1/8]。")


if __name__ == "__main__":
    main()
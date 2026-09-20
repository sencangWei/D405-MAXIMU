#!/usr/bin/env python3
"""前端探针：那个 >10mm 的峰，在 **[1/8] 的两个输出里是不是同一个**？

`bump_origin.py` 已证：最终峰在 `[6/8]` 就 1:1 存在（inherit 中位 1.00），
而 `[6/8]` 恰好 = `[1/8] trajectory_frames.csv` 的**纯全局缩放**
（`B − s·A` 逐轴极差 < 1e-9）⇒ **鼓包的形状 100% 来自 MASt3R 前端，[6/8] 造不出来。**

前端有两个输出（`mast3r_logs/` 之外，`fusion/<arm>/mast3r/` 下）：

| 文件 | 是什么 |
|---|---|
| `trajectory_frames.csv` | 离线全局优化后的（有关键帧图 / 回环） |
| `trajectory_online_frames.csv` | 在线跟踪的（无全局优化） |

两者形状一致 ⇒ 峰产生自**在线跟踪**；只在 `frames` 里有 ⇒ 产生自**离线全局优化**。
这个区分决定该去动哪个旋钮，所以先测它。

用法: frontend_probe.py
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
FUSE = ["--scale-horizon-s", "1", "--smoothing-s", "8",
        "--docker2-local-weight", "0", "--docker2-scale-weight", "0.25",
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


def read(p):
    a = np.genfromtxt(p, delimiter=",", names=True)
    return (a["t_sec"], np.column_stack([a["x"], a["y"], a["z"]]),
            np.column_stack([a["qw"], a["qx"], a["qy"], a["qz"]]))


def write(p, t, P, Q):
    with open(p, "w") as f:
        f.write("t_sec,x,y,z,qw,qx,qy,qz\n")
        for i in range(len(t)):
            f.write(f"{t[i]:.9f},{P[i,0]:.9f},{P[i,1]:.9f},{P[i,2]:.9f},"
                    f"{Q[i,0]:.9f},{Q[i,1]:.9f},{Q[i,2]:.9f},{Q[i,3]:.9f}\n")


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


def main():
    cs = cells()
    print(f"前端探针：offline(`frames`) vs online(`online_frames`)；{len(cs)} 组\n")
    print(f"{'cell':<40}{'arm':>7}{'n':>7}{'shape_rms':>11}{'shape_max':>11}"
          f"{'max_off':>9}{'max_on':>9}{'ratio':>7}{'agree?':>8}")
    print("-" * 110)
    rows = []
    for cell, arm in cs:
        md = cell / f"fusion/{arm}/mast3r"
        fo, fn = md / "trajectory_frames.csv", md / "trajectory_online_frames.csv"
        if not fo.exists() or not fn.exists():
            print(f"{str(cell).replace(str(ROOT)+'/', '')[:40]:<40}{arm:>7}   （缺前端文件）")
            continue
        to, Po, Qo = read(fo); tn, Pn, Qn = read(fn)
        # 形状比对：把 online 相似变换对齐到 offline，残差只反映形状差
        n = min(len(Po), len(Pn))
        if len(Po) != len(Pn):
            ti = np.linspace(0, 1, n)
            Po2 = np.column_stack([np.interp(ti, np.linspace(0, 1, len(Po)), Po[:, k]) for k in range(3)])
            Pn2 = np.column_stack([np.interp(ti, np.linspace(0, 1, len(Pn)), Pn[:, k]) for k in range(3)])
        else:
            Po2, Pn2 = Po, Pn
        A = Pn2 - Pn2[0]; B = Po2 - Po2[0]
        s = (np.linalg.norm(B) / max(np.linalg.norm(A), 1e-12))
        # 旋转
        H = A.T @ B; U, _, Vt = np.linalg.svd(H); d = np.sign(np.linalg.det(Vt.T @ U.T))
        R = Vt.T @ np.diag([1, 1, d]) @ U.T
        res = s * (A @ R.T) - B
        shape = np.linalg.norm(res, axis=1)
        # 两条链各自过 tail 取误差峰
        # ★ [8/9] 的输入必须是米制：`trajectory_frames.csv` 是 MASt3R 单位，
        #   直接喂会 rc=1。先乘 [6/8] 的标量（逐位等价于 trajectory_imu_metric.csv）。
        scl = json.loads((md / "imu_scale_report.json").read_text())["scale"]
        with tempfile.TemporaryDirectory() as t:
            td = Path(t); (td / "o").mkdir(); (td / "n").mkdir()
            tf_off = td / "offline_metric.csv"; write(tf_off, to, Po * scl, Qo)
            tf_on = td / "online_metric.csv"; write(tf_on, tn, Pn * scl, Qn)
            tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
            _, eo = errs(to_body(cell, tf_off, None, td / "o"), tg, Pg, Qg)
            _, en = errs(to_body(cell, tf_on, None, td / "n"), tg, Pg, Qg)
        r = eo.max() / en.max() if en.max() > 0 else float("nan")
        # ★ 固定时刻比（与 bump_origin 的 inherit 同口径）：各自峰位上对方有多大。
        #   各自峰的峰高比只说明「两条链谁更差」，固定时刻比才说明「这块凸起是谁的」。
        io, inn = int(np.argmax(eo)), int(np.argmax(en))
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name[:40]:<40}{arm:>7}{n:>7}{np.sqrt((shape**2).mean()):>11.2f}"
              f"{shape.max():>11.2f}{eo.max():>9.2f}{en.max():>9.2f}{r:>7.2f}"
              f"{'Y' if r < 1.15 else 'n':>8}")
        print(f"{'':<40}{'':>7}   offline 峰处 online={en[io]:.2f}（{en[io]/eo[io]:.2f}×）  "
              f"online 峰处 offline={eo[inn]:.2f}（{eo[inn]/en[inn]:.2f}×）  "
              f"⇒ shape 差 {np.sqrt((shape**2).mean())*scl*1000:.2f} mm")
        rows.append((eo.max(), en.max(), r, en[io] / eo[io], eo[inn] / en[inn]))
    print("-" * 110)
    if rows:
        eo = np.array([r[0] for r in rows]); en = np.array([r[1] for r in rows])
        fo = np.array([r[3] for r in rows]); fn = np.array([r[4] for r in rows])
        print(f"\n中位 max_offline {np.median(eo):.2f} mm   max_online {np.median(en):.2f} mm")
        print(f"两条链峰高比 eo/en 中位 {np.median([r[2] for r in rows]):.2f}；"
              f"offline 更差 {int((eo > en*1.15).sum())}/{len(rows)}、"
              f"online 更差 {int((en > eo*1.15).sum())}/{len(rows)}、"
              f"相差 <15% {int((np.abs(eo-en) <= 0.15*np.maximum(eo,en)).sum())}/{len(rows)}")
        print(f"★ 固定时刻：offline 峰处 online/offline 中位 {np.median(fo):.2f}"
              f"（≈1 ⇒ 那块凸起 online 也有，不是离线优化造的）")
        print(f"★ 固定时刻：online 峰处 offline/online 中位 {np.median(fn):.2f}"
              f"（<1 ⇒ 离线优化确实把 online 的峰压下去了）")
        print(f"  逐 cell：offline 峰处 online 也 ≥85% 的 {int((fo >= 0.85).sum())}/{len(rows)}")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""最后的便宜探测：那个峰**落在 MASt3R 自己的关键帧图的哪里**？

## 为什么是这个

§11 已把峰钉在 `[1/8]` 前端（`[6/8]` 是纯缩放；峰在 `[6/8]` 就 1:1 存在）。
前端的形状由 **MASt3R-SLAM 自己的关键帧图**决定，它的关键帧列表落在
`mast3r_logs/dataset.txt`（77 行 = 77 个关键帧，**文件名的秒数就是其时间戳**）；
`dataset_full.txt` 是 1799 帧全量（时间基 = 相机首帧）。

**为什么这个探测值得做**：记忆里 [[motion-keyframe-hole-ab-20260920]] 已经试过
「全局降低关键帧阈值 / 补开头空洞」⇒ **整体指标全部变差**。但那是个**全局**旋钮。
若峰**系统性地**落在**长关键帧间隙**里，就该做**只在快速段加密**的靶向修法 ——
那是与已失败实验**不同**的做法，值得先花 30 分钟确认前提是否成立。
若峰落在**密集关键帧区**里，这条线索立即死掉，省下几小时。

★ 另注意 `[7/8]` 的修正节点是 **425 个**（`keyframe_nodes 78 + regular_nodes 361`，
`maximum_gap_frames = 5`）⇒ `[7/8]` 侧**不可能**有 13–47 帧宽的间隙伪影；
本题问的是**上游 MASt3R 自己**的关键帧。

用法: peak_keyframe_context.py
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


def main():
    cs = cells()
    cache = HERE / "g7_cache"
    print(f"峰 vs MASt3R 关键帧；{len(cs)} 组（误差曲线 = 缓存 [7/8] graph + 现役尾巴）\n")
    print(f"{'cell':<40}{'arm':>7}{'peak_s':>8}{'n_kf':>6}{'kf_gap_med':>11}"
          f"{'peak_gap':>10}{'to_kf':>8}{'rank':>6}{'speed':>7}{'MAX':>7}")
    print("-" * 110)
    rows = []
    for cell, arm in cs:
        md = cell / f"fusion/{arm}/mast3r"
        key = f"{str(cell.relative_to(ROOT)).replace('/', '_')}__{arm}__joint"
        g, gr = cache / key / "graph.csv", cache / key / "graph.json"
        if not g.exists():
            continue
        kf_txt = md / "mast3r_logs/dataset.txt"
        fo = md / "trajectory_frames.csv"
        if not kf_txt.exists() or not fo.exists():
            print(f"{str(cell).replace(str(ROOT)+'/', '')[:40]:<40}{arm:>7}   （缺关键帧/前端文件）")
            continue
        t0 = float(np.genfromtxt(fo, delimiter=",", max_rows=2, names=True)["t_sec"][0])
        kf = np.array([float(l.split()[0]) for l in kf_txt.read_text().splitlines() if l.strip()])
        tg, Pg, Qg = E.load_trajectory(cell / "lighthouse_body_ground_truth.csv")
        with tempfile.TemporaryDirectory() as t:
            td = Path(t)
            est = to_body(cell, g, gr, td)
            te, P, Q = E.load_trajectory(est)
            inside, valid, plt_, _ = E.interpolate_ground_truth(te, tg, Pg, Qg, 0.1)
            Pe, te2 = P[inside][valid], te[inside][valid]
            gt = plt_[:, 1:4]
            R, tt = E.rigid_align(Pe, gt)
            err = np.linalg.norm(Pe @ R.T + tt - gt, axis=1) * 1000.0
        rel = te2 - t0
        ip = int(np.argmax(err))
        tp = rel[ip]
        gaps = np.diff(kf)
        # 峰所在的那一段关键帧间隙
        j = int(np.searchsorted(kf, tp))
        gi = min(max(j - 1, 0), len(gaps) - 1)
        # 峰的间隙在全部间隙里的名次（1 = 最大）
        rank = 1 + int(np.sum(gaps > gaps[gi]))
        dkf = float(np.min(np.abs(kf - tp)))
        # 真值速度（峰附近 ±5 帧）
        sl = slice(max(0, ip - 5), min(len(gt), ip + 6))
        sp = float(np.median(np.linalg.norm(np.gradient(gt[sl], te2[sl], axis=0), axis=1)) * 1000)
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name[:40]:<40}{arm:>7}{tp:>8.1f}{len(kf):>6}{np.median(gaps):>11.2f}"
              f"{gaps[gi]:>10.2f}{dkf:>8.2f}{rank:>4}/{len(gaps):<2}{sp:>7.0f}{err.max():>7.1f}")
        rows.append((gaps[gi], float(np.median(gaps)), rank, len(gaps), err.max()))
    print("-" * 110)
    if rows:
        pg = np.array([r[0] for r in rows]); mg = np.array([r[1] for r in rows])
        rk = np.array([r[2] for r in rows]); ng = np.array([r[3] for r in rows])
        rel_gap = pg / mg
        print(f"\n峰所在间隙 / 该 cell 间隙中位：中位 **{np.median(rel_gap):.2f}×**"
              f"（>1 表示峰偏向长间隙）")
        print(f"峰间隙 > 中位的组数：**{int((pg > mg).sum())}/{len(rows)}**")
        print(f"峰间隙排进该 cell 前 25% 的组数："
              f"**{int((rk <= np.maximum(1, ng // 4)).sum())}/{len(rows)}**")
        print(f"\n判读：若峰间隙**不是**系统性偏长（中位 ≈1×、rank 均匀分布）⇒ "
              f"「长关键帧间隙」这条线索**死掉**，别再往「加密关键帧」方向投。")
        print(f"      若系统性偏长 ⇒ 靶向修法（只在快速段加密）**与已失败的全局实验不同**，值得试。")
        print(f"⚠ 但本表只有 **n=18**（每 cell 一个峰）且**混淆了速度**：关键帧按运动量放置 ⇒\n"
              f"   间隙长 ⟺ 那段跑得快，而峰本来就在快段。**必须读 `gap_error_profile.py`**\n"
              f"   （n≈1500 个间隙 + 速度分层）才算把这条线索问完。")
        print(f"⚠ `to_kf` 列（峰到最近关键帧的秒数）**不能当判据**：它同时被速度污染。")


if __name__ == "__main__":
    main()
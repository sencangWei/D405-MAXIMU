#!/usr/bin/env python3
"""把 `peak_keyframe_context.py` 的「峰落在长关键帧间隙」从 **n=18** 加到 **n≈1300**。

## 为什么必须加

`peak_keyframe_context.py` 只用了「**那个峰**」一个点/cell ⇒ n=18，且它是**混合**的
（峰间隙/中位 1.68×、12/18、9/18 进前 25% —— 高于随机但有一串明确反例，如
`v11b3/g1/tight` rank 82/137、`batch5/g2/tight` rank 80/128、`coll4/g2/sparse` rank 79/100）。

★ 更要命的是**混淆**：MASt3R 的关键帧是**按运动量**放的 ⇒ **间隙长 ⟺ 那段跑得快**，
而峰本来就出现在快段（`spike-attribution` 已证 19/19 cell 峰在激烈动作上）。
所以「峰落在长间隙」**可能只是「峰落在快段」的换一种说法**，不含新信息。

## 两步收紧

1. **天然对照（本脚本外，已做）**：同一 session 的 `tight`(77–244 kf, 中位间隙 0.19s)
   vs `sparse`(27–101 kf, 0.93s) —— 关键帧密度差 **5×**，MAX 中位 **15.9 vs 14.3**
   （**sparse 略好**），逐 session tight 胜 3 / sparse 胜 5，
   `corr(中位间隙, MAX) = −0.254` ⇒ **密度上去没换来精度**。
2. **本脚本**：不再只看峰，而是**每一个关键帧间隙**取一个点（间隙内误差最大值 +
   间隙内真值速度中位 + 间隙长度）⇒ 每 cell ~30–240 点，全语料 ~1300 点。
   然后**按速度分层**：若「间隙长 ⇒ 误差大」在**同一速度层内**消失 ⇒ 纯混淆，线索死。
   若在速度层内仍然成立 ⇒ 是独立效应，靶向修法（只在快段加密）才有前提。

用法: gap_error_profile.py
"""
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


def spearman(a, b):
    """秩相关。n<5 或常数序列返回 nan。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 5 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 0 else float("nan")


def main():
    cs = cells()
    cache = HERE / "g7_cache"
    print(f"间隙级剖析（每 cell 每个关键帧间隙一个点，n≈1300）\n")
    print(f"{'cell':<40}{'arm':>7}{'n_gap':>7}{'r(gap,err)':>12}{'r|慢':>8}"
          f"{'r|中':>8}{'r|快':>8}{'r(kf距离,err)':>15}{'r|快':>8}")
    print("-" * 113)
    raw_r, terr_r, dkf_r, dkf_terr, gaps_used = [], [], [], [], []
    for cell, arm in cs:
        md = cell / f"fusion/{arm}/mast3r"
        key = f"{str(cell.relative_to(ROOT)).replace('/', '_')}__{arm}__joint"
        g, gr = cache / key / "graph.csv", cache / key / "graph.json"
        kf_txt, fo = md / "mast3r_logs/dataset.txt", md / "trajectory_frames.csv"
        if not g.exists() or not kf_txt.exists() or not fo.exists():
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
        sp = np.linalg.norm(np.gradient(gt, te2, axis=0), axis=1) * 1000.0   # mm/s
        # 逐间隙取点
        gl, ge, gs = [], [], []
        for i in range(len(kf) - 1):
            m = (rel >= kf[i]) & (rel < kf[i + 1])
            if m.sum() < 2:
                continue
            gl.append(kf[i + 1] - kf[i])
            ge.append(err[m].max())
            gs.append(np.median(sp[m]))
        if len(gl) < 8:
            print(f"{str(cell).replace(str(ROOT)+'/', '')[:40]:<40}{arm:>7}{len(gl):>7}   （间隙太少）")
            continue
        gl, ge, gs = np.array(gl), np.array(ge), np.array(gs)
        r_raw = spearman(gl, ge)
        # 速度三分层：层内 r 说明「扣掉速度后，间隙长度还有没有解释力」
        q = np.quantile(gs, [1 / 3, 2 / 3])
        bins = [gs <= q[0], (gs > q[0]) & (gs <= q[1]), gs > q[1]]
        rb = [spearman(gl[b], ge[b]) for b in bins]
        # 另一个口径：误差 vs 到最近关键帧的距离（无需按间隙取点，用全量帧）
        dk = np.array([np.min(np.abs(kf - r)) for r in rel])
        d_raw = spearman(dk, err)
        hm = sp >= np.quantile(sp, 0.5)
        d_hot = spearman(dk[hm], err[hm])
        name = str(cell).replace(str(ROOT) + "/", "")
        print(f"{name[:40]:<40}{arm:>7}{len(gl):>7}{r_raw:>12.3f}"
              + "".join(f"{v:>8.3f}" if np.isfinite(v) else f"{'nan':>8}" for v in rb)
              + f"{d_raw:>15.3f}"
              + (f"{d_hot:>8.3f}" if np.isfinite(d_hot) else f"{'nan':>8}"))
        raw_r.append(r_raw); terr_r.extend(rb); dkf_r.append(d_raw)
        if np.isfinite(d_hot):
            dkf_terr.append(d_hot)
        gaps_used.append(len(gl))
    print("-" * 113)
    if not raw_r:
        return
    raw_r = np.array(raw_r)
    # nan 中位：只对有限值取中位
    def med(a):
        a = np.asarray(a, float); a = a[np.isfinite(a)]
        return float(np.median(a)) if len(a) else float("nan")
    # 层内 r 按「每 cell 三个层的均值」聚合，避免层大小不均
    terr_r = np.array(terr_r)
    print(f"\n全语料共用间隙 {sum(gaps_used)} 个（{len(raw_r)} 组）")
    print(f"\n① 间隙长度 vs 间隙内最大误差（Spearman，cell 级）：中位 {med(raw_r):+.3f}"
          f"；为正 {int((raw_r > 0).sum())}/{len(raw_r)}、为负 {int((raw_r < 0).sum())}/{len(raw_r)}")
    print(f"② **扣掉速度后**（速度三分层，层内 r 再取中位）：中位 {med(terr_r):+.3f}"
          f"；为正 {int(np.nansum(terr_r > 0))}/{int(np.isfinite(terr_r).sum())}")
    dkf_r = np.array(dkf_r)
    print(f"③ 到最近关键帧的距离 vs 逐帧误差：中位 {med(dkf_r):+.3f}"
          f"；为正 {int((dkf_r > 0).sum())}/{len(dkf_r)}")
    print(f"④ 同上，只取**高速半**（speed ≥ 该 cell 中位）：中位 {med(dkf_terr):+.3f}"
          f"；为正 {int((np.array(dkf_terr) > 0).sum())}/{len(dkf_terr)}")
    print(f"\n判读：②④ 若 ≈0 ⇒ 「峰落在长关键帧间隙」**完全由速度解释**，"
          f"加密关键帧这条线索**死**（与已失败的全局密度实验一致）。")
    print(f"      ②④ 若稳定为正 ⇒ 间隙长度是独立效应，靶向修法有前提，值得试。")


if __name__ == "__main__":
    main()
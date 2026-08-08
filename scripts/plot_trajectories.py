#!/usr/bin/env python3
"""两套 SLAM 轨迹对比可视化 (替代 Rerun)。

生成:
  1. slam_results/trajectory_compare.png   — 静态对比图 (俯视+侧视)
  2. slam_results/trajectory_replay.gif    — 可回放动画 (轨迹生长过程)

用法:
  python3 scripts/plot_trajectories.py
  python3 scripts/plot_trajectories.py --vins X --orb Y --out-dir Z
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    if not path.exists():
        return None, None, None
    rows = list(csv.DictReader(path.open()))
    if len(rows) < 2:
        return None, None, None
    t = np.array([float(r["t_sec"]) for r in rows])
    p = np.array([[float(r["x"]), float(r["y"]), float(r["z"])] for r in rows])
    q = np.array([[float(r["qx"]), float(r["qy"]), float(r["qz"]), float(r["qw"])] for r in rows])
    return t, p, q


def stats(name: str, t, p):
    if t is None:
        return f"{name}: 无数据"
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    vel = seg / np.maximum(np.diff(t), 1e-6)
    return (f"{name}: {len(p)}位姿 {t[-1]-t[0]:.1f}s "
            f"路径{seg.sum():.2f}m 末端{np.linalg.norm(p[-1]-p[0]):.2f}m "
            f"速度中位{np.median(vel):.2f}m/s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vins", type=Path, default=ROOT / "slam_results" / "traj_vins_stereo.csv")
    ap.add_argument("--orb", type=Path, default=ROOT / "slam_results" / "traj_orb_rgbd.csv")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "slam_results")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tv, pv, qv = load(args.vins)
    to, po, qo = load(args.orb)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("===== 轨迹统计 =====")
    print(stats("VINS Stereo-IR", tv, pv))
    print(stats("ORB  RGB-D-Inertial", to, po))

    # ---- 静态对比图 ----
    fig, axs = plt.subplots(1, 2, figsize=(14, 6))

    def plot_pair(ax, label, p, color, marker_start, marker_end):
        if p is None:
            return
        ax.plot(p[:, 0], p[:, 1], color=color, lw=1.8, label=label)
        ax.scatter(p[0, 0], p[0, 1], c="green", s=120, marker="o", edgecolors="k", zorder=5)
        ax.scatter(p[-1, 0], p[-1, 1], c="red", s=120, marker="x", zorder=5)

    # 俯视图 XY
    ax = axs[0]
    plot_pair(ax, "VINS Stereo", pv, "tab:red", "o", "x")
    plot_pair(ax, "ORB RGB-D", po, "tab:blue", "o", "x")
    ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)")
    ax.set_title("Top View (X-Y)")
    ax.grid(True, alpha=0.3); ax.legend()

    # 侧视图 XZ
    ax = axs[1]
    if pv is not None:
        ax.plot(pv[:, 0], pv[:, 2], color="tab:red", lw=1.8, label="VINS Stereo")
    if po is not None:
        ax.plot(po[:, 0], po[:, 2], color="tab:blue", lw=1.8, label="ORB RGB-D")
    ax.set_xlabel("X (m)"); ax.set_ylabel("Z (m)")
    ax.set_title("Side View (X-Z)")
    ax.grid(True, alpha=0.3); ax.legend()

    # 加注释
    fig.suptitle(
        f"VINS {len(pv) if pv is not None else 0} poses  vs  ORB {len(po) if po is not None else 0} poses",
        fontsize=13)
    plt.tight_layout()
    png = args.out_dir / "trajectory_compare.png"
    fig.savefig(png, dpi=150)
    print(f"\n静态对比图: {png}")

    # ---- 动画回放 ----
    if pv is not None or po is not None:
        try:
            import matplotlib.animation as animation
            # 统一时间轴
            t_all = [t for t in (tv, to) if t is not None]
            t_max = max(t[-1] for t in t_all)
            fps = 10
            n_frames = int(t_max * fps) + 1

            fig2, ax2 = plt.subplots(figsize=(8, 7))
            lines_v, lines_o = None, None
            pts_v, pts_o = None, None

            def init():
                nonlocal lines_v, lines_o, pts_v, pts_o
                if pv is not None:
                    lines_v, = ax2.plot([], [], color="tab:red", lw=2, label="VINS Stereo")
                    pts_v, = ax2.plot([], [], "ro", ms=5)
                if po is not None:
                    lines_o, = ax2.plot([], [], color="tab:blue", lw=2, label="ORB RGB-D")
                    pts_o, = ax2.plot([], [], "bo", ms=5)
                ax2.set_xlabel("X (m)"); ax2.set_ylabel("Y (m)")
                ax2.set_title("Trajectory Replay (top view)")
                ax2.grid(True, alpha=0.3); ax2.legend()
                all_pts = []
                if pv is not None: all_pts.append(pv[:, :2])
                if po is not None: all_pts.append(po[:, :2])
                if all_pts:
                    concat = np.vstack(all_pts)
                    xmin, ymin = concat.min(axis=0); xmax, ymax = concat.max(axis=0)
                    pad_x = max((xmax - xmin) * 0.1, 0.1); pad_y = max((ymax - ymin) * 0.1, 0.1)
                    ax2.set_xlim(xmin - pad_x, xmax + pad_x)
                    ax2.set_ylim(ymin - pad_y, ymax + pad_y)
                return [l for l in (lines_v, lines_o, pts_v, pts_o) if l is not None]

            def update(frame):
                t_now = frame / fps
                artists = []
                if pv is not None:
                    idx = min(np.searchsorted(tv, t_now), len(pv) - 1)
                    lines_v.set_data(pv[:idx + 1, 0], pv[:idx + 1, 1])
                    pts_v.set_data([pv[idx, 0]], [pv[idx, 1]])
                    artists += [lines_v, pts_v]
                if po is not None:
                    idx = min(np.searchsorted(to, t_now), len(po) - 1)
                    lines_o.set_data(po[:idx + 1, 0], po[:idx + 1, 1])
                    pts_o.set_data([po[idx, 0]], [po[idx, 1]])
                    artists += [lines_o, pts_o]
                return artists

            anim = animation.FuncAnimation(fig2, update, frames=n_frames,
                                           init_func=init, blit=False, interval=100)
            gif = args.out_dir / "trajectory_replay.gif"
            anim.save(gif, writer="pillow", fps=fps, dpi=100)
            print(f"动画回放: {gif}")
            plt.close(fig2)
        except Exception as e:
            print(f"[WARN] 动画生成失败: {e}")

    print("\n完成。静态图看整体对比, 动画看轨迹随时间生长。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""两套 SLAM 轨迹可回放对比 (Rerun)。

用法:
  python3 scripts/compare_trajectories.py \
    --vins slam_results/traj_vins_stereo.csv \
    --orb slam_results/traj_orb_rgbd.csv

打开 Rerun 窗口, 拖时间轴即可回放两条轨迹的生长过程。
"""
from __future__ import annotations

import argparse
import csv
import sys

import numpy as np


def load(path: str):
    rows = list(csv.DictReader(open(path)))
    t = np.array([float(r["t_sec"]) for r in rows])
    p = np.array([[float(r["x"]), float(r["y"]), float(r["z"])] for r in rows])
    q = np.array([[float(r["qx"]), float(r["qy"]), float(r["qz"]), float(r["qw"])] for r in rows])
    return t, p, q


def stats(name: str, t, p):
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    end_err = np.linalg.norm(p[-1] - p[0])
    return (f"{name}: 位姿{len(p)} 时长{t[-1]-t[0]:.1f}s 路径{seg.sum():.2f}m "
            f"终点误差{end_err:.3f}m")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vins", required=True)
    ap.add_argument("--orb", required=True)
    args = ap.parse_args()

    import rerun as rr
    import rerun.blueprint as rrb

    rr.init("slam_compare", spawn=True)

    rr.send_blueprint(
        rrb.Blueprint(
            rrb.Horizontal(
                rrb.Spatial3DView(
                    origin="/world",
                    contents=["/world/**"],
                    name="轨迹对比 (红=VINS双IR 蓝=ORB RGB-D)",
                ),
                rrb.TimeSeriesView(
                    origin="/stats",
                    contents=["/stats/**"],
                    name="高度/速度曲线",
                ),
                column_shares=[3.0, 1.0],
            ),
            collapse_panels=True,
        )
    )
    rr.log("world", rr.ViewCoordinates.RDF, static=True)

    # 世界坐标轴: 红=+X 绿=+Y 蓝=+Z (长 0.3m, 画在原点)
    axis_len = 0.3
    rr.log(
        "world/axes",
        rr.Arrows3D(
            origins=[[0, 0, 0]] * 3,
            vectors=[[axis_len, 0, 0], [0, axis_len, 0], [0, 0, axis_len]],
            colors=[[255, 0, 0], [0, 255, 0], [0, 0, 255]],
        ),
        static=True,
    )

    tv, pv, qv = load(args.vins)
    to, po, qo = load(args.orb)

    print(stats("VINS 双IR ", tv, pv))
    print(stats("ORB RGB-D", to, po))

    # 两条完整轨迹(淡色背景)
    rr.log("world/vins_full", rr.LineStrips3D([pv], colors=[[255, 120, 120]], radii=0.003), static=True)
    rr.log("world/orb_full", rr.LineStrips3D([po], colors=[[120, 120, 255]], radii=0.003), static=True)

    # 随时间生长的轨迹 + 当前位姿轴
    n = max(len(tv), len(to))
    t_end = max(tv[-1], to[-1])
    steps = 300
    for i in range(steps + 1):
        t_now = t_end * i / steps
        rr.set_time("time", timestamp=t_now)

        iv = min(np.searchsorted(tv, t_now), len(tv) - 1)
        io = min(np.searchsorted(to, t_now), len(to) - 1)

        rr.log("world/vins_grow", rr.LineStrips3D([pv[: iv + 1]], colors=[[255, 0, 0]], radii=0.006))
        rr.log("world/orb_grow", rr.LineStrips3D([po[: io + 1]], colors=[[0, 0, 255]], radii=0.006))

        for path, p, q in (("vins", pv, qv), ("orb", po, qo)):
            idx = iv if path == "vins" else io
            qq = q[idx]
            rr.log(
                f"world/{path}_pose",
                rr.Transform3D(
                    translation=p[idx].tolist(),
                    rotation=rr.Quaternion(xyzw=qq.tolist()),
                ),
            )
            # 位姿轴(短 0.1m): 显示当前朝向
            import numpy as _np

            def _rot(q_, v):
                q_ = q_ / _np.linalg.norm(q_)
                xyz = q_[:3]
                t_ = 2.0 * _np.cross(xyz, v)
                return v + q_[3] * t_ + _np.cross(xyz, t_)

            rr.log(
                f"world/{path}_pose_axes",
                rr.Arrows3D(
                    origins=[p[idx].tolist()] * 3,
                    vectors=[_rot(qq, _np.array([0.1, 0, 0.0])).tolist(),
                             _rot(qq, _np.array([0.0, 0.1, 0.0])).tolist(),
                             _rot(qq, _np.array([0.0, 0.0, 0.1])).tolist()],
                    colors=[[255, 0, 0], [0, 255, 0], [0, 0, 255]],
                ),
            )

        if iv > 2:
            vel = np.linalg.norm(np.diff(pv[: iv + 1], axis=0), axis=1) / np.diff(tv[: iv + 1])
            rr.log("stats/vins_vel", rr.Scalars([float(vel[-1])]))
        if io > 2:
            vel = np.linalg.norm(np.diff(po[: io + 1], axis=0), axis=1) / np.diff(to[: io + 1])
            rr.log("stats/orb_vel", rr.Scalars([float(vel[-1])]))
        rr.log("stats/vins_z", rr.Scalars([float(pv[iv, 2])]))
        rr.log("stats/orb_z", rr.Scalars([float(po[io, 2])]))

    print("Rerun 窗口已打开: 红=VINS双IR, 蓝=ORB RGB-D, 拖动时间轴回放", flush=True)
    print("Ctrl+C 退出", flush=True)
    try:
        import time as _time

        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

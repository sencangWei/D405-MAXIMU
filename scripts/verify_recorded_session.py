#!/usr/bin/env python3
"""一键"录后即验": 录制会话 → 跑一轮 VINS (FFV1 生产路径) → 出闭环报告。

用法:
    python3 scripts/verify_recorded_session.py <session_dir> [--rate 1.0] [--skip-s 1.5]
    # 返回码: 0=优 / 1=边界 / 2=坏跑或失败 (便于脚本 if ...; then 分支)

判定标准 (与 A/B 统计一致):
    优:     闭环 ≤ 3cm 且路径正常 (1.0-3.5m)
    边界:   闭环 3-25cm (提示弱可观测性, 建议复录)
    坏跑:   闭环 > 25cm 或路径 < 1.0m / > 3.5m (发散/尺度错) → 建议复录

每次调用完整跑一遍 VINS (~70s), 互不干扰, 可直接在录制现场使用。
"""
import argparse
import csv
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCRIPT = "scripts/replay_mp4_to_ros2.py"   # 生产 FFV1 路径 (与 raw db3 统计等同)
SKIP_S = 1.5          # 跳过开头 (等 IMU 初始对准)
RATE = 1.0            # 实时率
WATCH_S = 100         # 最长等待回放完成
DRAIN_S = 4           # 回放结束后等 VINS 消化

GOOD_CM = 3.0
BAD_CM = 25.0
PATH_MIN, PATH_MAX = 1.0, 3.5


def classify(closure_cm, path_m):
    if path_m < PATH_MIN or path_m > PATH_MAX:
        return "坏跑(路径异常)", 2
    if closure_cm > BAD_CM:
        return "坏跑", 2
    if closure_cm > GOOD_CM:
        return "边界", 1
    return "优", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", help="录制会话目录 (含 mp4/ir_left.mkv 等)")
    ap.add_argument("--rate", type=float, default=RATE)
    ap.add_argument("--skip-s", type=float, default=SKIP_S)
    ap.add_argument("--raw-dir", default=None,
                    help="预解码裸帧目录 (跳过 ffmpeg 子进程, 隔离验证用)")
    ap.add_argument("--db3", action="store_true",
                    help="改用原始 db3 回放 (gold standard 对照, 需会话含 .db3)")
    args = ap.parse_args()

    sess = Path(args.session)
    if not (sess / "mp4" / "ir_left.mkv").exists():
        sys.exit(f"[verify] 会话缺 mp4/ir_left.mkv: {sess}")

    # 清理上一轮残留
    subprocess.run(
        "pkill -f 'replay_mp4_to_ros2.py|replay_db3_to_ros2.py|replay_db3_hevc|vins_fusion_ros2_node'; "
        "pkill -f ffmpeg", shell=True)
    time.sleep(1)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
        out_csv = tf.name

    env = dict(os.environ)
    env["REPLAY_SCRIPT"] = "scripts/replay_db3_to_ros2.py" if args.db3 else REPLAY_SCRIPT
    env["VINS_OUT"] = out_csv
    if args.raw_dir:
        env["REPLAY_RAW_DIR"] = args.raw_dir

    print(f"[verify] 会话: {sess}")
    print(f"[verify] 路径: {'db3 原始' if args.db3 else 'FFV1 mkv 生产'}"
          f"{' + 预解码' if args.raw_dir else ''}  rate={args.rate}x")
    t0 = time.time()
    # 经 bash 先 source ROS 环境再跑 harness (rclpy 依赖 ROS python 路径)
    cmd = (
        "source /opt/ros/humble/setup.bash; "
        "source /home/robot/ros2_ws/install/setup.bash; "
        f"python3 scripts/_test_vins_dynamic.py {shlex.quote(str(sess))} "
        f"{shlex.quote(str(args.skip_s))} 1.0 0 0"   # skip, rate, imu-shift, imu-align
    )
    proc = subprocess.run(
        ["bash", "-c", cmd], cwd=str(ROOT), env=env, capture_output=True, text=True)
    elapsed = time.time() - t0

    rows = []
    try:
        with open(out_csv) as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        pass
    os.unlink(out_csv)

    # 透传 harness 的输出 (轨迹/警告) 供现场查看
    for line in (proc.stdout or "").splitlines():
        if line.startswith(("轨迹", "VINS 警告", "轨迹太少")):
            print("  " + line)

    if len(rows) <= 3:
        print("\n[verify] 结果: 失败 — VINS 几乎没出轨迹")
        print(f"[verify] 耗时 {elapsed:.0f}s")
        return 2

    p = np.array([[float(r["x"]), float(r["y"]), float(r["z"])] for r in rows])
    closure_cm = float(np.linalg.norm(p[-1] - p[0]) * 100)
    path_m = float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1)))
    ts = np.array([float(r["t_sec"]) for r in rows])
    v = np.linalg.norm(np.diff(p, axis=0), axis=1) / np.maximum(np.diff(ts), 1e-6)
    medspeed = float(np.median(v))

    verdict, code = classify(closure_cm, path_m)
    print("\n" + "=" * 46)
    print(f"  闭环误差   {closure_cm:6.2f} cm")
    print(f"  轨迹路径   {path_m:6.2f} m")
    print(f"  轨迹点数   {len(rows)}")
    print(f"  中位速度   {medspeed:.3f} m/s")
    print(f"  判定       {verdict}")
    print("=" * 46)
    print(f"[verify] 耗时 {elapsed:.0f}s  (一跑一验, 建议现场录完即验)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

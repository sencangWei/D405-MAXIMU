#!/usr/bin/env bash
# 工作流 B 遗留项的前置：**回放确定性**检验。
#
# 计划原话是「3 组 take 上确认默认（不带 VINS_RATE）产物与改动前逐字节相同」。
# 但那个验证的**前提**是回放可复现 —— 若同一条 take 跑两遍都不逐字节相同，
# 「与改动前逐字节相同」就无从谈起（而且既往所有 A/B 的可比性都悬在这上面）。
# 所以先测前提：同 take、同速率、同参数，**跑两遍**，比 `vio_raw.csv` /
# `vio_corrected_stream.csv`；再与格内 `docker2_slam/` 那份存证比。
#
# ★ 产物写格**外**目录：写进 <cell>/docker2_slam_*/ 会被 vins_dir.py 的
#   sorted("docker2_slam_*") glob 选中而静默顶替正式那份。
set -o pipefail
ROOT=/home/robot/ego_vio_humble
D="$(cd -- "$(dirname -- "$0")" && pwd)"
SESSION="$1"
CELLNAME="${2:?用 <batch>/<group> 形式的格名，用于定位格内存证}"
RATE="${3:-0.5}"
GT_CELL="$ROOT/reports/lighthouse_umi_workflow/20260914_validation_v10_${CELLNAME}"
VINS_CONFIG=/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml

# ★ 不在这里开 -u：/opt/ros/humble/setup.bash 引用未绑定变量，非交互 shell 下 -u 会当场退出。
source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
set -u

echo "session=$SESSION  格内存证=$GT_CELL  rate=$RATE"
for rep in 1 2; do
    OUT="$D/repeat${rep}"
    mkdir -p "$OUT"
    started=$SECONDS
    python3 "$ROOT/scripts/test_vins_auto_loop.py" "$SESSION" \
        --config "$VINS_CONFIG" \
        --imu-shift-ms 0 \
        --rate "$RATE" \
        --expect-loop any \
        --out-dir "$OUT" > "$OUT/replay_driver.log" 2>&1
    echo "[repeat$rep] rc=$? 用时 $((SECONDS-started))s"
done

echo
echo "=== 两遍之间 ==="
for f in vio_raw.csv vio_corrected_stream.csv run_acceptance.json; do
    a="$D/repeat1/$f"; b="$D/repeat2/$f"
    if [ -f "$a" ] && [ -f "$b" ]; then
        cmp -s "$a" "$b" && echo "  $f  逐字节相同 ✅  ($(md5sum "$a" | cut -c1-12))" \
                         || echo "  $f  **不同** ❌  ($(md5sum "$a"|cut -c1-12) vs $(md5sum "$b"|cut -c1-12))"
    else
        echo "  $f  缺产物"
    fi
done

echo
echo "=== 与格内存证 docker2_slam/ 比 ==="
for f in vio_raw.csv vio_corrected_stream.csv; do
    a="$D/repeat1/$f"; b="$GT_CELL/docker2_slam/$f"
    if [ -f "$a" ] && [ -f "$b" ]; then
        cmp -s "$a" "$b" && echo "  $f  与格内逐字节相同 ✅" \
                         || echo "  $f  **与格内不同** ❌  ($(md5sum "$a"|cut -c1-12) vs $(md5sum "$b"|cut -c1-12))"
    else
        echo "  $f  缺一侧"
    fi
done

python3 - "$D/repeat1" "$D/repeat2" "$GT_CELL/docker2_slam" <<'PY'
import json, sys
from pathlib import Path
def row(p):
    f = Path(p) / "run_acceptance.json"
    if not f.exists(): return "  <无 run_acceptance.json>"
    j = json.loads(f.read_text())
    r, c = j["raw_trajectory_diagnostics"], j["corrected_trajectory_diagnostics"]
    return (f"  result={j['result']:<4} cov={j['pose_coverage']:.4f} n={j['raw_odometry_samples']} "
            f"raw_max={r['max_step_m']*1000:7.2f}mm corr_max={c['max_step_m']*1000:7.2f}mm "
            f"failures={j['failures']}")
for lbl, p in (("repeat1", sys.argv[1]), ("repeat2", sys.argv[2]), ("格内存证", sys.argv[3])):
    print(f"{lbl:<10}{row(p)}")
PY
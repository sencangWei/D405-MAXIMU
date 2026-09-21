#!/usr/bin/env bash
# 工作流 B 第 4 项前提：回放速率单调性实验（g1 上 1.0× -> 0.5× -> 0.25×）。
#
# 判据：0.25× 是否**优于** 0.5×？不优则「0.25× 升级阶梯」作废，只保留 rc 判别。
#
# ★ 产物写进本目录（格**外**），不写 <cell>/docker2_slam_rate0p25/ —— 后者会被
#   reports/.../vins_dir.py 的 sorted("docker2_slam_*") glob 选中，
#   一旦 PASS 就会静默顶替 0.5× 那份，改变该格的语义。实验不该有这种副作用。
set -o pipefail
RATE="${1:-0.25}"
ROOT=/home/robot/ego_vio_humble
D="$(cd -- "$(dirname -- "$0")" && pwd)"
OUT="$D/$(basename "$(dirname "$2")")_rate0p${RATE/./}"
SESSION="$2"
VINS_CONFIG=/home/robot/umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml

# ★ 不在这里开 -u：/opt/ros/humble/setup.bash 会引用未绑定变量，
#   非交互 shell 下 -u 会让整个脚本当场退出（工作流也是先 set -eo pipefail、
#   source 之后才 set -u）。
source /opt/ros/humble/setup.bash
source /home/robot/ros2_ws/install/setup.bash
set -u

mkdir -p "$OUT"
echo "session=$SESSION  rate=$RATE  out=$OUT"
started=$SECONDS
python3 "$ROOT/scripts/test_vins_auto_loop.py" "$SESSION" \
    --config "$VINS_CONFIG" \
    --imu-shift-ms 0 \
    --rate "$RATE" \
    --expect-loop any \
    --out-dir "$OUT" 2>&1 | tee "$OUT/replay_driver.log" | tail -30
rc=${PIPESTATUS[0]}
echo "rc=$rc  用时 $((SECONDS-started))s"

python3 - "$OUT" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]) / "run_acceptance.json"
if not p.exists():
    print("无 run_acceptance.json"); raise SystemExit
j = json.loads(p.read_text())
raw, cor = j["raw_trajectory_diagnostics"], j["corrected_trajectory_diagnostics"]
print(f"result={j['result']}  failures={j['failures']}")
print(f"  rate={j['replay_rate']}  coverage={j['pose_coverage']:.4f}  raw_samples={j['raw_odometry_samples']}")
print(f"  raw  max_step={raw['max_step_m']*1000:.2f}mm endpoint={raw['endpoint_delta_m']*1000:.2f}mm")
print(f"  corr max_step={cor['max_step_m']*1000:.2f}mm endpoint={cor['endpoint_delta_m']*1000:.2f}mm")
print(f"  修正是否起作用: {'否(==raw)' if abs(cor['max_step_m']-raw['max_step_m'])<1e-9 else '是'}")
PY
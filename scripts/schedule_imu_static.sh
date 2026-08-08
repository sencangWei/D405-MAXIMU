#!/usr/bin/env bash
# 定时静置采集 IMU (Allan 分析用)
#
# 用法: ./scripts/schedule_imu_static.sh <HH:MM> [时长小时]
#   例: 今晚 2:00 开始录 4 小时
#       ./scripts/schedule_imu_static.sh 02:00 4
#
# 内部用 systemd-run 一次性定时器 (用户级, 不需要 root)
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

TIME="${1:?用法: $0 <HH:MM> [时长小时]}"
HOURS="${2:-4}"
DURATION_SEC=$((HOURS * 3600))

# 验证时间格式
if ! [[ "$TIME" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
    echo "[ERROR] 时间格式应为 HH:MM (如 02:00)"
    exit 1
fi
HOUR="${BASH_REMATCH[1]}"
MIN="${BASH_REMATCH[2]}"

# 转成 systemd 日历格式 (明天这个时间)
CALENDAR="*-*-* $HOUR:$MIN:00"

LOG_DIR="${ROOT}/recordings/imu_static_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

# 生成将要执行的采集命令
CMD="python3 -u ${ROOT}/scripts/record_imu_static.py --duration ${DURATION_SEC} --out ${LOG_DIR} >> ${LOG_DIR}/capture.log 2>&1"

echo "============================================"
echo " 定时静置 IMU 采集计划"
echo "============================================"
echo " 启动时间:  明天 $TIME"
echo " 录制时长:  $HOURS 小时 ($DURATION_SEC s)"
echo " 数据目录:  $LOG_DIR"
echo " systemd:   $CALENDAR"
echo "============================================"
echo ""
echo "[确认] 按 Enter 确认定时 (Ctrl+C 取消)"
read -r _

# 创建一次性 systemd 定时器 (用户级, transient)
systemd-run --user --on-calendar="$CALENDAR" --unit="imu-static-$RANDOM" \
    bash -c "$CMD"

echo ""
echo "[OK] 定时器已创建!"
echo "  明天 $TIME 将自动开始录制 $HOURS 小时"
echo "  数据输出到: $LOG_DIR"
echo ""
echo "查看定时器: systemctl --user list-timers | grep imu-static"
echo "取消定时器: systemctl --user list-timers | grep imu-static (记下名字再 stop)"
echo ""
echo "早上起来分析噪声:"
echo "  python3 scripts/allan_variance_imu.py --bin $LOG_DIR/imu.bin --out ~/allan_result.png"

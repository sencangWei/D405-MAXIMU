#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"

minimum_duration_s=21600

echo "将IMU刚性放稳、释放线缆拉力，并保持空调温度/风速不变。"
read -r -p "确认采集期间不会碰到设备后按回车；采集将在明早06:00自动停止……"

now_epoch="$(date +%s)"
today_0600_epoch="$(date -d "$(date +%F) 06:00:00" +%s)"
if (( now_epoch < today_0600_epoch )); then
    target_epoch="$today_0600_epoch"
else
    target_epoch="$(date -d 'tomorrow 06:00:00' +%s)"
fi

duration_s=$((target_epoch - now_epoch))
if (( duration_s < minimum_duration_s )); then
    echo "BLOCKED：距离06:00只剩${duration_s}秒，不足正式6小时 Allan 窗口。" >&2
    exit 2
fi

target_text="$(date -d "@$target_epoch" '+%F %T %Z')"
echo "正式采集时长：${duration_s}秒；采样预计在 ${target_text} 结束。"

# imu_bench 内部还有一次开始提示；预先送入一个回车，避免重复等待。
printf '\n' | systemd-inhibit \
    --what=sleep \
    --why="IMU Allan capture until 06:00" \
    python3 -m product_calibration.imu_bench allan \
        "$@" \
        --duration "$duration_s" \
        --minimum-duration "$minimum_duration_s"

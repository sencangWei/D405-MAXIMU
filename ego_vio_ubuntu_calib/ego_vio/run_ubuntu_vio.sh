#!/bin/bash
# Ubuntu 本地运行 ego_vio + OpenVINS + Rerun 可视化
# 依赖: ROS2 Jazzy, OpenVINS, ego_vio 已配置

# 0) 清理
sudo fuser -k /dev/ttyACM* 2>/dev/null || true
pkill -9 -f "run_realtime|run_subscribe|rerun" 2>/dev/null || true
sleep 1

# 1) 环境
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash 2>/dev/null || true

echo "[run_ubuntu_vio] 启动全链路..."

# 2) OpenVINS 先启 (订阅者先就位, 避免积压)
echo "[run_ubuntu_vio] OpenVINS..."
ros2 launch ov_msckf subscribe.launch.py \
    config:=ego_d405 \
    max_cameras:=1 \
    use_stereo:=false &
PID_OV=$!
sleep 3

# 3) 采集 + 可视化 (Runtime 内置 Rerun)
echo "[run_ubuntu_vio] 采集+可视化..."
python3 -u ~/ego_vio/scripts/run_realtime.py \
    --config ~/ego_vio/config/devices_ubuntu.yaml \
    --backend openvins_ros2 &
PID_CAPTURE=$!

function cleanup() {
    echo "[run_ubuntu_vio] 停止..."
    kill $PID_CAPTURE $PID_OV 2>/dev/null || true
    wait $PID_CAPTURE $PID_OV 2>/dev/null || true
    pkill -f run_subscribe 2>/dev/null || true
}
trap cleanup SIGINT SIGTERM

wait

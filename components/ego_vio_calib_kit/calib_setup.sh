#!/bin/bash
# ============================================
# ego_vio Ubuntu 标定环境一键安装
# 适用: Ubuntu 24.04 (不限于 ROS2 Jazzy)
# ============================================

set -e

echo "=== ego_vio 标定环境安装 ==="

# 1. 系统依赖
echo "[1/4] 系统包..."
sudo apt update
sudo apt install -y python3-pip python3-venv python3-full \
    libopencv-dev python3-opencv \
    libusb-1.0-0-dev udev usbutils

# 2. RealSense SDK
echo "[2/4] RealSense SDK..."
if ! python3 -c "import pyrealsense2" 2>/dev/null; then
    # 方法A: pip (推荐)
    pip3 install --user pyrealsense2 --break-system-packages 2>/dev/null || \
    # 方法B: apt fallback
    sudo apt install -y ros-jazzy-librealsense2 2>/dev/null || \
    echo "!! RealSense 安装失败, 请手动安装: pip3 install pyrealsense2"
fi

# 3. Python 依赖
echo "[3/4] Python 包..."
pip3 install --user --break-system-packages \
    pyserial \
    "numpy>=1.24,<2" \
    "opencv-python>=4.8" \
    "pyyaml>=6.0" \
    "rosbags>=0.9" \
    "scipy>=1.10" \
    aprilgrid

# 4. 串口权限
echo "[4/4] 串口权限..."
sudo usermod -a -G dialout $USER 2>/dev/null || true

echo ""
echo "=== 安装完成 ==="
echo "重新登录使 dialout 组生效, 或临时: sudo chmod 666 /dev/ttyACM*"
echo ""
echo "标定采集:"
echo "  python3 scripts/collect_calib_data.py --config config/devices_ubuntu.yaml --mode camera --phase-secs 8"
echo "  python3 scripts/collect_calib_data.py --config config/devices_ubuntu.yaml --mode imucam --phase-secs 10"
echo ""
echo "转 bag:"
echo "  python3 scripts/convert_to_kalibr_bag.py --input recordings/<session> --output calib.bag"
echo ""
echo "Kalibr 标定 (需要 ROS1 Noetic 或 Docker):"
echo "  docker run -v \$(pwd):/data kalibr:latest kalibr_calibrate_camera ..."

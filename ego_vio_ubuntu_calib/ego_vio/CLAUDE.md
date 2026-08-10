# ego_vio — 实时双目 VIO 系统

## 项目概述
基于 RealSense D405 + 军工级 IMU (KT-EX9-2) 的实时 Visual-Inertial Odometry 系统。
后端使用 OpenVINS (MSCKF)，ROS2 Jazzy 作为中间件，Rerun 做可视化。

## 硬件
- 相机: Intel RealSense D405 (序列号 260322273737)
- IMU: KT-EX9-2 (400Hz, UART CH340, PPS 同步待接入)
- 主机: i3 mini PC (Ubuntu 24.04)
- 标定: Kalibr (camchain-imucam-datacalib.yaml, imu-datacalib.yaml)

## 架构 (Ubuntu 原生模式)

```
RealSense D405 ──→ ego_vio capture ──→ /cam0/image_raw (ROS2)
KT-EX9-2 IMU ────→ ego_vio capture ──→ /imu0 (ROS2)
                                         ↓
                                   OpenVINS (ov_msckf)
                                         ↓
                              /ov_msckf/odomimu (ROS2)
                                         ↓
                              rerun_vio_viewer.py (Rerun 可视化)
```

## 关键文件

| 文件 | 用途 |
|------|------|
| `ego_vio/runtime.py` | 主运行时，串起采集→VIO→可视化 |
| `ego_vio/vio/openvins_ros2_bridge.py` | 发布 ROS2 图像+IMU 话题 |
| `ego_vio/camera/realsense_capture.py` | D405 采集，支持 global_time |
| `ego_vio/imu/imu_reader.py` | KT-EX9-2 串口解析 (EB 90 22 帧头) |
| `config/devices_ubuntu.yaml` | Ubuntu 设备配置 |
| `config/ov_left_hand.yaml` | Kalibr 标定参数 |
| `run_ubuntu_vio.sh` | 一键启动脚本 |
| `scripts/rerun_vio_viewer.py` | 本地 Rerun 可视化 (图像+轨迹) |

## 启动方式

```bash
# 清理残留
sudo fuser -k /dev/ttyACM* 2>/dev/null
pkill -9 -f run_realtime

# 一键启动
~/ego_vio/run_ubuntu_vio.sh
```

## OpenVINS 配置

- 路径: `~/ros2_ws/src/open_vins/config/ego_d405/`
- 启动: `ros2 launch ov_msckf subscribe.launch.py config:=ego_d405 max_cameras:=1 use_stereo:=false`

## 已修复的问题
- Ceres 2.2 API: LocalParameterization → Manifold (State_JPLQuatLocal.h/.cpp)
- ROS2 Jazzy 头文件: .h → .hpp (image_transport, tf2_geometry_msgs, cv_bridge)
- IMU 端口: 使用固定路径 `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00`
- cam_latency_ms: Ubuntu global_time 下设为 0

## 当前状态 (2026-07-31)
- ✅ ROS2 Jazzy + OpenVINS 编译通过
- ✅ ego_vio 采集正常 (IMU 400Hz, CAM 30fps)
- ✅ OpenVINS 订阅话题正常
- ❌ 相机 USB 错误: VIDIOC_S_FMT errno=5，需重新插拔
- ⏳ 漂移验证 (需相机修复后测试)
- ⏳ GPS PPS 同步 (硬件待接入)
- ⏳ Windows 双系统安装 (启动盘已制作)

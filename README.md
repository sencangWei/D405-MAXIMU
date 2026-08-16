# D405-MAXIMU

D405 相机 + KT-EX9-2 军工 IMU 的完整数据采集与标定工程。

## 1. 项目目标

为小电脑 + D405 深度相机 + 外置 IMU 提供一套工程化的采集、标定、打包流程，输出可直接给后端视觉惯性 SLAM（如 VINS-Fusion、OpenVINS、ORB-SLAM3）使用的数据与配置文件。

## 2. 硬件组成

| 设备 | 型号 | 用途 |
|---|---|---|
| 深度相机 | Intel RealSense D405 | RGB / Depth / 双目 IR |
| IMU | KT-EX9-2J-2-F1 | 400Hz 陀螺 + 加速度 |
| 主机 | Ubuntu 22.04 / x86 小电脑 | 采集 + 后处理 |

## 3. 目录说明

```
D405-MAXIMU/
├── ego_vio/              # 核心 Python 包
│   ├── camera/           # RealSense 采集封装
│   ├── imu/              # KT-EX9 UART 读取 + 时间戳拟合
│   ├── recorder/         # 图像/IMU 落盘
│   ├── runtime.py        # 采集运行时
│   └── timing.py         # counter 时间戳去抖
├── scripts/              # 脚本入口
│   ├── capture_d405_720p_rgbd_imu.py    # 三路采集脚本
│   ├── capture_d405_720p_all_streams.py # 四路采集脚本
│   ├── collect_calib_data.py            # 标定数据采集
│   ├── convert_to_kalibr_bag.py         # 转 Kalibr bag
│   ├── inspect_imu.py                   # IMU 实时检查
│   └── rerun_vio_viewer.py              # Rerun 可视化
├── config/               # 标定与运行配置
│   ├── d405_factory_720p.yaml           # 相机出厂内参
│   ├── camimu_720p_leftir_kalibr.yaml   # 相机-IMU 外参 + 时间偏移
│   ├── imu_kalibr.yaml                  # IMU 噪声参数
│   ├── aprilgrid_6x6_35mm.yaml          # 标定板
│   └── devices_ubuntu.yaml              # 设备配置
├── tools/                # C++ 诊断/采集工具
├── tests/                # 单元测试
├── capture_d405_720p_rgbd_imu.sh        # 推荐：三路采集入口
├── capture_d405_720p_all_streams.sh     # 备用：四路采集入口
├── requirements.txt
└── README.md
```

## 4. 环境安装

```bash
# Python 依赖
pip install -r requirements.txt

# 系统依赖
sudo apt install zstd libusb-1.0-0-dev

# 相机 SDK：使用系统 pyrealsense2 2.58.2（RSUSB 后端）
# IMU 串口：/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00
```

## 5. 数据采集

### 5.1 RGB-D采集（RGB + Depth + 左 IR）

适合 RGB-D 后处理实验。是否合格必须以每次采集生成的
`acceptance.json`为准，不能沿用历史平均丢帧率。

```bash
./capture_d405_720p_rgbd_imu.sh --duration 60 --no-preview
```

### 5.2 当前VINS产品主采集（RGB + 左 IR + 右 IR）

当前产品回归使用D405双IR 30fps＋外置IMU 400Hz，RSUSB后端、正式窗口零丢帧验收：

```bash
./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 60
```

### 5.3 四路实验采集（RGB + 左 IR + 右 IR + Depth）

D405四路720p并发可能触发内部调度和吞吐问题。只用于Depth/平面因子实验，
每条数据仍须独立通过`acceptance.json`，不得假定固定丢帧率。

```bash
./capture_d405_720p_all_streams.sh --duration 60 --no-preview
```

### 5.3 数据输出结构

```
recordings/d405_720p_all_YYYYMMDD_HHMMSS/
├── d405_720p_all_streams.db3   # rosbag2 sqlite，含图像
├── d405_frames.csv             # 相机时间戳、帧号、时间域
└── external_imu/
    ├── imu.bin                 # IMU 原始二进制
    └── imu_ts.csv              # IMU 时间戳与 counter
```

`recordings/` 已加入 `.gitignore`，数据不进入仓库。

## 6. 相机-IMU 标定

### 6.1 采集标定数据

使用 AprilGrid 6×6 标定板，手持相机-IMU 做充分激励：

```bash
python3 scripts/collect_calib_data.py \
  --config config/devices_ubuntu.yaml \
  --mode imucam \
  --phase-secs 10 \
  --strict \
  --preview
```

### 6.2 转换为 Kalibr bag

```bash
python3 scripts/convert_to_kalibr_bag.py \
  --input recordings/calib_YYYYMMDD_HHMMSS \
  --output calib.bag
```

### 6.3 运行 Kalibr

```bash
kalibr_calibrate_imu_camera \
  --bag calib.bag \
  --cam config/d405_factory_720p.yaml \
  --imu config/imu_kalibr.yaml \
  --target config/aprilgrid_6x6_35mm.yaml
```

### 6.4 当前标定结果

当前权威时间外参来自2026-08-08重新标定：

- 重投影残差：**0.42 px**
- 相机-IMU 时间偏移：**td = -11.7 ms**
- 加速度随机游走：0.039 m/s²
- 陀螺随机游走：0.0045 rad/s

VINS配置使用`estimate_td: 0`并固定`td: -0.0117`。旧的`-7.36ms`结果已废弃，
禁止与在线时间偏移估计同时使用，否则会形成双重补偿。

## 7. 数据质量验证

以下旧会话只用于展示报告字段，不代表当前产品采集门槛。当前必须逐条读取
`acceptance.json`，要求双IR 30fps和IMU 400Hz正式窗口零丢帧、零回退：

| 流 | 帧数 | 帧率 | 丢帧 |
|---|---|---|---|
| color | 1791 | 30.0fps | 1.4% |
| infrared_left | 1791 | 30.0fps | 0.5% |
| depth | 1791 | 30.0fps | 1.2% |

- IMU：24169 样本，60.4s，**400.0Hz**
- 相机-IMU 对齐：**100% 相机帧有前后 IMU**
- 相机帧距最近 IMU：**中位 0.71ms，最大 48.95ms**
- 时间戳域：**global_time**，无回退

## 8. 快速验证脚本

```bash
python3 -c "
import csv, statistics
rows = list(csv.DictReader(open('recordings/d405_720p_all_20260804_215229/d405_frames.csv')))
print('frames:', len(rows))
for st in ['color', 'infrared_left', 'depth']:
    v = [float(r[f'{st}_device_ms']) for r in rows]
    dts = [(b-a) for a,b in zip(v,v[1:])]
    exp = statistics.median(dts)
    gaps = sum(1 for d in dts if d > exp*1.5)
    print(f'{st}: {len(v)} frames, drop {gaps}/{len(dts)} ({gaps/len(dts)*100:.1f}%)')
"
```

## 9. 后端 SLAM 使用

把 `d405_720p_all_*.tar.zst` 解压后得到 `.db3` rosbag，配合三个标定配置：

```bash
tar --use-compress-program='zstd -d' -xf d405_720p_all_YYYYMMDD_HHMMSS.tar.zst
```

在 ROS 里发布 `/cam0/image_raw`（左 IR）、`/cam1/image_raw`（Depth 或 RGB）、`/imu0`，即可运行 VINS-Fusion / OpenVINS。

## 10. 设计要点

- D405 **不支持硬件同步**外部触发（D4 V4 板无 sync 引脚），相机-IMU 同步靠时间戳软对齐 + Kalibr 时间偏移。
- D405 四路 720p 内部调度是丢帧瓶颈；三路 720p 可显著降低丢帧。
- IMU 使用 counter 拟合去抖，无 PPS 时也能保持 400Hz 稳定。
- KT-EX9-2 供电 **3.3V ± 0.3V**，UART 电平 3.3V TTL。

## 11. License

内部项目，私有仓库。

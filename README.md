# D405-MAXIMU

D405 相机 + KT-EX9-2 IMU 数据采集与标定工程。

## 功能

- 三路 720p 采集：`RGB + Depth + 左 IR` + 外置 IMU 400Hz
- 四路 720p 采集：`RGB + 左 IR + 右 IR + Depth` + IMU 400Hz
- IMU 标定数据采集 + Kalibr bag 转换
- 出厂内参、双目外参、相机-IMU 外参 + 时间偏移配置

## 采集入口

```bash
# 推荐：三路 720p，丢帧 ~1%，适合后端 RGB-D 视觉惯性 SLAM
./capture_d405_720p_rgbd_imu.sh --duration 60 --no-preview

# 备用：四路 720p，丢帧 ~15%，数据最全但负载高
./capture_d405_720p_all_streams.sh --duration 60 --no-preview
```

采集数据会保存到 `recordings/`（已加入 `.gitignore`，不推仓库）。

## 标定配置

`config/` 目录包含：

| 文件 | 说明 |
|---|---|
| `d405_factory_720p.yaml` | D405 出厂内参 + 左右 IR 双目外参 |
| `camimu_720p_leftir_kalibr.yaml` | Kalibr 标定的相机-IMU 外参 + 时间偏移 |
| `imu_kalibr.yaml` | IMU 噪声参数 |
| `aprilgrid_6x6_35mm.yaml` | AprilGrid 标定板 |
| `devices_ubuntu.yaml` | 运行时设备配置 |

## 数据结构

采集会话目录示例：

```
recordings/d405_720p_all_YYYYMMDD_HHMMSS/
├── d405_720p_all_streams.db3   # rosbag2 sqlite，含 RGB/Depth/IR 图像
├── d405_frames.csv             # 相机时间戳、帧号、时间域
└── external_imu/
    ├── imu.bin                 # IMU 原始样本（ts, counter, gx, gy, gz, ax, ay, az, temp）
    └── imu_ts.csv              # IMU 时间戳
```

## 快速验证数据

```bash
python3 -c "
import csv, statistics
rows = list(csv.DictReader(open('recordings/.../d405_frames.csv')))
print('frames:', len(rows))
for st in ['color', 'infrared_left', 'depth']:
    v = [float(r[f'{st}_device_ms']) for r in rows]
    dts = [(b-a) for a,b in zip(v,v[1:])]
    exp = statistics.median(dts)
    gaps = sum(1 for d in dts if d > exp*1.5)
    print(f'{st}: {len(v)} frames, drop {gaps}/{len(dts)}')
"
```

## 依赖

```bash
pip install -r requirements.txt
```

需要系统 pyrealsense2、pyserial、OpenCV、ROS2（VIO 路径）。

## 目录说明

```
ego_vio/        # Python 包（camera/imu/recorder/runtime/timing）
scripts/        # 采集、标定、诊断、转换脚本
config/         # 标定与运行配置
tools/          # C++ 诊断与采集工具
tests/          # 单元测试
```

## 设计要点

- D405 四路 720p 内部调度限制，建议后端 SLAM 用三路采集
- IMU 时间戳用主机接收时刻 + counter 拟合去抖
- 相机时间戳用 `global_time`，与 IMU 软对齐
- 相机-IMU 时间偏移已通过 Kalibr 标定为 -7.36ms

## License

内部项目，私有仓库。

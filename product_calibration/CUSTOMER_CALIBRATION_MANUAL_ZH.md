# 产品标定操作手册（六步独立命令，工程验证版）

适用产品：已经完成最终机械装配的 D405 双 IR＋KT-EX9 IMU 产品。

这不是“一条命令连续采完所有数据”。客户先创建产品档案，然后严格按 1～6 顺序，
每次只运行当前步骤的一条命令。每条命令内部完成：

```text
设备与前置结果检查 → 屏幕动作提示 → 本步骤采集 → 自动求解
                    → 自动质量判定 → 保存原始数据/参数/报告
```

显示 `PASS` 后退出，客户才执行下一条命令。`FAIL` 重做当前步，`BLOCKED` 按屏幕提示
处理设备或前置步骤。系统不提供 `run-all`。

现场首次操作可先打开 `TOMORROW_CALIBRATION_CHECKLIST_ZH.md`，它按实际时间顺序列出
上电、建档、第 1 步和过夜 Allan 采集。

> 下面六个 `calibrate_*.sh` 已连接实际采集和求解后端，不是占位命令。当前没有完整
> 硬件，状态仍是“工程验证版”：离线回归已执行，真实 D405＋IMU＋STM32 的逐步 HIL
> 尚未执行，因此暂不能作为客户发布版签发。

## 0. 创建本机产品档案（只做一次）

```bash
cd /home/robot/ego_vio_calib_kit
./calibrate_init.sh 产品编号
```

脚本自动读取并绑定 D405 序列号及固定的 `/dev/serial/by-id/` IMU 端口；识别不唯一即
BLOCKED。STM32 固件哈希和机械版本字段会在硬件 HIL 阶段补入，未补齐前不得发布。
所有输出进入 `calibration_sessions/产品编号/`，重做某一步时创建新的 attempt，不覆盖
上一份原始数据。

工程机已经把 Kalibr 隔离在 ROS 1 Noetic 容器中，主机继续使用 ROS 2 Humble；
`kalibr_calibrate_cameras`、`kalibr_calibrate_imu_camera` 和
`kalibr_camera_validator` 是本机命令。AprilGrid 检测库安装在主机 Python 环境中。
明天继续使用已经在历史标定中跑通的实体 `6×6` AprilGrid：单 tag
`35.2 mm`，净间距 `10.56 mm` (`tagSpacing=0.3`)；不需要重新生成或打印。

## 1. IMU 静态 bias

```bash
./calibrate_01_imu_static.sh 产品编号
```

把整机按实际工作姿态刚性放稳，线缆不受力，按回车后保持不动。命令自动预热 2 分钟、
正式采集 8 分钟，随后计算三轴陀螺 bias、加速度均值/模长、温度、频率、抖动、丢帧和
复位。输出 `imu_static_bias/report.yaml`。本步不求 IMU 比例矩阵。

## 2. IMU Allan 长时间噪声参数

```bash
./calibrate_02_imu_noise.sh 产品编号
```

整机继续保持固定、恒温、不断电。命令自动采集 15～24 小时，完成后自动计算加速度计/
陀螺仪 noise density、random walk、bias stability，生成 Kalibr 单位参数、Allan 曲线和
`imu_allan/report.yaml`。中途移动、断电、计数器复位、正式窗口丢帧或时长不足直接 FAIL，
不会仅保存一张图就算完成。

## 3. IMU 任意多姿态内参

```bash
./calibrate_03_imu_intrinsic.sh 产品编号
```

按屏幕逐个改变整套刚体姿态，不要求支架具有标准 ±X/±Y/±Z 六面。每个姿态放稳后按
回车，脚本自动检查静止并采 30～60 秒；固定采 20 个拟合姿态＋10 个事先锁定的验证
姿态。采完立即做椭球拟合，输出加速度 bias、scale/非正交矩阵和留出误差到
`imu_multipose/report.yaml`。验证姿态不会回流参与拟合。

这一步校正的是加速度计。陀螺完整比例/非正交参数需要计量转台，不能用手转“约 90°”
当真值；第 5 步的标准 Kalibr 命令只求相机—IMU 外参和时间偏移，也不冒充陀螺内参。

## 4. D405 双 IR 相机内参和双目外参

```bash
./calibrate_04_d405_stereo.sh 产品编号
```

整机固定，客户只移动 AprilGrid。屏幕依次提示九宫格、近/中/远距离和不同倾角；左右
IR 必须同步采集 `1280×720@30`。命令先采一份求解集，再重新独立采一份
留出集；留出图像不会进入 Kalibr 优化。求解左右内参与 `T_cam1_cam0`，检查
Kalibr 重投影 RMS，并与历史工厂基线
`18.079 mm` A/B；实机模式改用当前连接 D405 的 `1280×720@30 Y8` factory profile
外参作为正式基线。输出 `d405_stereo/report.yaml`。

求解后命令用固定内外参整流留出图，匹配相同 AprilGrid ID/角点并计算
`|v_left-v_right|`。留出集必须有至少 40 个有效同步视角、左右都覆盖九宫格，
且纵向极线错位 P95 `≤1.0 px`。任一超限即第 4 步 FAIL。

客户流程不写 D405 NVRAM，只生成产品侧候选配置。

## 5. 相机—IMU 联合外参和时间偏移

```bash
./calibrate_05_camera_imu.sh 产品编号
```

固定 AprilGrid，移动已经锁紧的 D405＋IMU 整套刚体。屏幕引导 XYZ 平移和 roll、pitch、
yaw，同时控制清晰度和标定板覆盖。命令连续组织两个相互独立的采集 attempt；每个
attempt 都自动生成双 IR＋IMU Kalibr bag并求 `T_cam0_imu`、`T_cam1_imu` 和两路 `td`。

两次结果自动互比，再与 2026-08-08 两份已跑通金样 A/B。重复性、重投影或 IMU 残差
任一超限即 FAIL。输出 `camera_imu/report.yaml`；旧 `td=-11.7 ms` 只作对照，不自动
复制到新装配。

## 6. 世界 Z 标定矩阵

```bash
./calibrate_06_world_z.sh 产品编号
```

脚本分次提示录制已知水平平面运动和真实升降运动。每一条录制结束后运行冻结 SLAM 链
并导出轨迹。至少 3 条平面轨迹用于拟合/leave-one-out，至少 2 条真实升降轨迹只作负例
验证。当前实时入口仍加载冻结历史配置；把第 1～5 步候选配置注入冻结 VINS 后再采的
接线尚未完成，所以目前只允许用离线参数对已经使用候选配置生成的轨迹做正式求解，
不能把默认实时入口的结果签成新产品 Z 标定。

命令只允许求一个全局刚体旋转 `R_world_z_from_vins_world`，输出
`world_z/report.yaml` 和候选矩阵。平面留出轨迹 Z P5–P95 必须 `<10 mm` 且不劣于
原始结果；真实升降高度保留必须 `≥80%`。禁止逐条压平、固定角度补偿或人工改终点。

## 完成和返工规则

六步都 PASS 后，系统生成一份候选标定包，但仍不覆盖历史冻结 Humble 配置。交付人员
还要用历史数据和新采数据做端到端 SLAM A/B，确认旧 `<1 cm` 回环基线没有退化后，
才把候选包签名为产品配置。

## 工程复算参数（客户正常操作不使用）

```bash
./calibrate_01_imu_static.sh 产品编号 --input-capture <采集目录>
./calibrate_02_imu_noise.sh 产品编号 --input-capture <采集目录>
./calibrate_03_imu_intrinsic.sh 产品编号 --input-csv <30姿态均值.csv>
./calibrate_04_d405_stereo.sh 产品编号 --input-camchain <camchain.yaml> --input-results <results-cam.txt> --input-validation <独立双IR留出录制目录>
./calibrate_05_camera_imu.sh 产品编号 --run1 <run1.yaml> --results1 <run1.txt> --run2 <run2.yaml> --results2 <run2.txt>
./calibrate_06_world_z.sh 产品编号 \
  --planar p1=<p1.csv> --planar p2=<p2.csv> --planar p3=<p3.csv> \
  --elevation e1=<e1.csv> --elevation e2=<e2.csv>
```

仅对内置 2026-08-08 历史金样做无留出图回归时，可在第 4 步追加
`--legacy-reference-only`。命令会校验 camchain/results 的 SHA-256；只有与内置金样
完全一致才允许工程回归 PASS，但 `release_eligible` 始终为 `false`，不能用于新产品签发。

- IMU 更换：从第 1 步重做。
- IMU采样率、固件滤波或时间戳链改变：至少重做第 1、2、5、6 步。
- D405内部模组或工作 profile 改变：从第 4 步重做。
- D405 与 IMU相对安装改变：重做第 5、6 步。
- 只改变产品在机器人上的整体倾斜角，而相机与 IMU 相对位置未变：外参仍有效，但
  世界坐标定义改变时重做第 6 步。

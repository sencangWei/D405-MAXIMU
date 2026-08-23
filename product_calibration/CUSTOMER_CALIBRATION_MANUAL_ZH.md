# 产品标定操作手册（五个必需阶段，分步执行）

适用产品：已经完成最终机械装配的 D405 双 IR＋KT-EX9 IMU 产品。

这不是“一条命令连续采完所有数据”。客户先创建产品档案，然后按 1→2→4→5→6 顺序，
每次只运行当前步骤的一条命令。每条命令内部完成：

```text
设备与前置结果检查 → 屏幕动作提示 → 本步骤采集 → 自动求解
                    → 自动质量判定 → 保存原始数据/参数/报告
```

显示 `PASS` 后退出，客户才执行下一条命令。`FAIL` 重做当前步，`BLOCKED` 按屏幕提示
处理设备或前置步骤。系统不提供 `run-all`。

现场首次操作可先打开 `TOMORROW_CALIBRATION_CHECKLIST_ZH.md`，它按实际时间顺序列出
上电、建档、第 1 步和过夜 Allan 采集。

下面五个必需 `calibrate_*.sh` 都连接实际采集、求解和报告后端，不是占位命令。每台产品
仍必须用自己的硬件完成各阶段采集；不能复制另一台产品的 PASS 报告。

## 0. 创建本机产品档案（只做一次）

新电脑只在首次部署时执行：

```bash
cd /home/robot/ego_vio_calib_kit
./calib_setup.sh
# 注销并重新登录后
./calibrate_preflight.sh
```

`calib_setup.sh` 只支持 Ubuntu 22.04，并安装永久 `dialout/docker` 权限和固定 Kalibr
容器入口；禁止用 `chmod 666` 临时放开设备。预检只读，不会启动采集或改参数。
交付介质必须预装 `/home/robot/ego_vio_humble`、`/home/robot/D405-MAXIMU` 和固定镜像
`ego-vio-kalibr:1f602274-minimal`。目录不同可分别设置
`EGO_VIO_CAPTURE_RUNTIME`、`EGO_VIO_RSUSB_RUNTIME`、`EGO_VIO_VINS_RUNTIME`。

```bash
cd /home/robot/ego_vio_calib_kit
./calibrate_init.sh 产品编号
```

脚本自动读取并绑定 D405 序列号及固定的 `/dev/serial/by-id/` IMU 端口；识别不唯一即
BLOCKED。STM32 固件哈希和机械版本字段会在硬件 HIL 阶段补入，未补齐前不得发布。
所有输出进入 `calibration_sessions/产品编号/`，重做某一步时创建新的 attempt，不覆盖
上一份原始数据。

本手册对应 `workflow format_version: 3`。旧版 session 冻结了当时的 workflow，必须
作为历史证据原样保留，不能改 `_frozen_inputs/` 或把旧 PASS 直接续接到新版流程。
升级工具后如状态显示 `ACTIVE_WORKFLOW_DIFFERS_FROM_SESSION`，请由交付负责人换一个
新的 session 根目录重新建档，例如：

```bash
export EGO_VIO_CALIBRATION_SESSIONS=/home/robot/ego_vio_calib_kit/calibration_sessions_v3
./calibrate_init.sh 产品编号
```

之后第 1、2、4、5、6 步和 `calibrate_status.sh` 必须保持同一个环境变量。旧目录只读
归档，不移动、不覆盖；这属于有意的 fail-closed 版本隔离。

中断后或交接给下一位操作员时，先查看状态：

```bash
./calibrate_status.sh 产品编号
```

它只读，不会采集或改报告。需要把 session 放到其他磁盘时，所有命令统一设置
`EGO_VIO_CALIBRATION_SESSIONS=/绝对路径`，不要只搬其中一个阶段。

工程机把 Kalibr 隔离在 ROS 1 Noetic 容器中，主机继续使用 ROS 2 Humble；
`kalibr_calibrate_imu_camera` 和售后诊断用的 `kalibr_calibrate_cameras`、
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

整机继续保持固定、恒温、不断电。命令默认采集 10 小时，最低接受 6 小时，完成后自动计算加速度计/
陀螺仪 noise density、random walk、bias stability，生成 Kalibr 单位参数、Allan 曲线和
`imu_allan/report.yaml`。中途移动、施工振动、断电、计数器复位、正式窗口丢帧或时长不足直接 FAIL，
不会仅保存一张图就算完成。入口会自动使用 `systemd-inhibit` 阻止系统睡眠；不要关闭
终端、断电或手动挂起电脑。

## 3. IMU 任意多姿态内参（可选研发诊断，客户跳过）

```bash
./calibrate_03_imu_intrinsic.sh 产品编号
```

按屏幕逐个改变整套刚体姿态，不要求支架具有标准 ±X/±Y/±Z 六面。每个姿态放稳后按
回车，脚本自动检查静止并采 30～60 秒；固定采 20 个拟合姿态＋10 个事先锁定的验证
姿态。采完立即做椭球拟合，输出加速度 bias、scale/非正交矩阵和留出误差到
`imu_multipose/report.yaml`。验证姿态不会回流参与拟合。

本步骤不属于产品签发前置，标准客户流程不要运行。2026-08-23 使用同一份水平回放做
盲 A/B 后，这份矩阵虽在一条数据上略减小 Z 波动，却使平面短边缩小约 13.2%，而且
无法跨数据稳定改善 Z。因此正式相机—IMU Kalibr 使用原始 IMU，`product-live` 的
`imu.calibration` 保持为空，由 VINS 在线估计 bias。脚本仅保留给研发排查传感器异常。

这一步校正的是加速度计。陀螺完整比例/非正交参数需要计量转台，不能用手转“约 90°”
当真值；第 5 步的标准 Kalibr 命令只求相机—IMU 外参和时间偏移，也不冒充陀螺内参。

## 4. D405 出厂双 IR 参数锁定和极线验收

```bash
./calibrate_04_d405_factory.sh 产品编号
```

这一阶段不重新拟合相机内参。脚本从当前连接的 D405 `1280×720@30 Y8` profile
读取 Intel 出厂双 IR 内参和 IR1→IR2 外参，绑定序列号与固件，生成后续 Kalibr
相机—IMU标定使用的固定 `camchain`。当前产品运行选择这一套 factory rectified
参数，是因为同数据 A/B 中它的 SLAM 结果优于 2026-08-21 自由拟合内参。

参数导出后，整机固定，客户只移动 AprilGrid。左右 IR 必须同时看到标定板，预览按
左上、上中、右上、左中、正中、右中、左下、下中、右下依次引导九宫格验证。

独立留出集使用专门九宫格流程：预览绘制 3×3 网格并用黄色框标出当前目标格，
绿色圆点表示标定板中心已安全进入格子内部，红点表示靠近分界线。程序按左上、上中、
右上、左中、正中、右中、左下、下中、右下逐格进行，实时显示当前位置和尚缺格；
每格至少取得 15 个稳定检测帧且完成计时后才进入下一格，不能跳过。

每次打开相机后，采集器先等待至少 60 组连续、左右配对的双 IR 帧（约 2 秒）；
启动期跳帧只写入预热证据并重新累计，预热完成后的下一帧才进入正式数据集。
第 4 步独立验证集的正式窗口必须设备帧号连续、左右配对且丢帧为 0，否则 FAIL。

命令使用刚导出的 factory 内外参整流验证图，匹配相同 AprilGrid ID/角点并计算
`|v_left-v_right|`。留出集必须有至少 40 个有效同步视角、左右都覆盖九宫格，
且纵向极线错位 P95 `≤1.0 px`。任一超限即第 4 步 FAIL。

客户流程不写 D405 NVRAM，也不安装自由拟合相机内参。超限表示设备、profile、图像
质量或双目几何需要售后诊断；不能靠放宽门槛或覆盖 factory 参数掩盖问题。

## 5. 相机—IMU 联合外参和时间偏移

```bash
./calibrate_05_camera_imu.sh 产品编号
```

固定 AprilGrid，移动已经锁紧的 D405＋IMU 整套刚体。屏幕引导 XYZ 平移和 roll、pitch、
yaw，同时控制清晰度和标定板覆盖。命令连续组织两个相互独立的采集 attempt；每个
attempt 都用原始 IMU 自动生成双 IR＋IMU Kalibr bag并求 `T_cam0_imu`、
`T_cam1_imu` 和两路 `td`。
两份 attempt 同样执行 60 组连续双 IR 预热，并要求正式相机窗口零丢帧。

两次结果自动互比，再与 2026-08-08 两份已跑通金样 A/B。重复性、重投影或 IMU 残差
任一超限即 FAIL。输出 `camera_imu/report.yaml`；旧 `td=-11.7 ms` 只作对照，不自动
复制到新装配。

正式第 5 步只接受第 2 步报告绑定且 SHA-256 未变化的 `imu_kalibr.yaml`，以及第 4 步
正式 live factory 留出验收绑定的 camchain。两份联合采集都必须同时通过相机正式窗口
和 IMU 频率、计数器、CRC、队列、时间戳健康门；报告保存实际 Kalibr bag、原始 IMU
和健康证据哈希。只给求解后的 camchain/results 做离线复算会返回 `BLOCKED`，其数值
结果可供研发比较，但不能推进第 6 步。

开发台架若STM32尚未到货、正式前置阶段还未全部签发，可用
`./camera_bench_05_camera_imu.sh 产品编号` 采集当前USB转TTL链的两份独立候选。该入口
不推进正式阶段：机械安装不变时空间外参可保留复核，但TTL链得到的 `td` 在STM32时间链
启用后必定需要重新A/B，不能直接签发。详见 `product_calibration/CAMERA_IMU_TTL_NOW_ZH.md`。

## 6. 世界 Z 标定矩阵

```bash
./calibrate_06_world_z.sh 产品编号
```

脚本先在当前 attempt 内生成隔离候选运行目录：相机内参/双目外参来自本机 D405 factory
导出，第 5 步两次联合标定共识外参和 `td` 分别只应用一次，IMU 加速度矩阵不加载；其余
VINS噪声和自适应回环算法沿用已验证 `product-live` 基线。该目录不会覆盖默认配置。

随后脚本分次提示录制已知水平平面运动和真实升降运动。每条都以无窗口
`product-live` 候选运行并导出原始/回环轨迹。至少 3 条平面轨迹用于
拟合/leave-one-out，至少 2 条真实升降轨迹只作负例验证。

命令只允许求一个全局刚体旋转 `R_world_z_from_vins_world`，输出
`world_z/report.yaml` 和候选矩阵。平面留出轨迹 Z P5–P95 必须 `<10 mm` 且不劣于
原始结果；真实升降高度保留必须 `≥80%`。禁止逐条压平、固定角度补偿或人工改终点。

## 完成和返工规则

五个必需阶段都 PASS 后，系统保留完整候选配置、哈希和报告，但仍不覆盖已经签发的运行配置。交付人员
还要用历史数据和新采数据做端到端 SLAM A/B，确认旧 `<1 cm` 回环基线没有退化后，
才把候选包签名为产品配置。

## 工程复算参数（客户正常操作不使用）

```bash
./calibrate_01_imu_static.sh 产品编号 --input-capture <采集目录>
./calibrate_02_imu_noise.sh 产品编号 --input-capture <采集目录>
# 可选研发诊断，不进入客户签发链：
./calibrate_03_imu_intrinsic.sh 产品编号 --input-csv <30姿态均值.csv>
./calibrate_04_d405_factory.sh 产品编号 \
  --input-factory-calibration <d405_factory_calibration.yaml> \
  --input-validation <独立双IR九宫格录制目录>
./calibrate_05_camera_imu.sh 产品编号 --run1 <run1.yaml> --results1 <run1.txt> --run2 <run2.yaml> --results2 <run2.txt>
./calibrate_06_world_z.sh 产品编号 \
  --planar p1=<p1.csv> --planar p2=<p2.csv> --planar p3=<p3.csv> \
  --elevation e1=<e1.csv> --elevation e2=<e2.csv>
```

上面的第 5 步离线命令只产生研发数值复算，因无法验证当时两份原始采集的相机/IMU
健康、设备身份和输入哈希，产品状态固定为 `BLOCKED`。

售后/研发若要对相机做自由拟合诊断，使用 `camera_bench_04_d405_stereo.sh`。该入口与
客户正式第 4 步隔离，结果不会自动进入运行配置。仅对内置 2026-08-08 历史金样做
无留出图回归时，旧兼容入口仍支持 `--legacy-reference-only`；其
`release_eligible` 始终为 `false`。

- IMU 更换：重做第 1、2、5、6 步；只有研发怀疑比例/非正交异常时才做可选第 3 步。
- IMU采样率、固件滤波或时间戳链改变：至少重做第 1、2、5、6 步。
- 更换 D405 或工作 profile 改变：从第 4 步重新导出新设备自己的 factory 参数；
  不复制旧相机参数，也不常规自由拟合。
- D405 与 IMU相对安装改变：重做第 5、6 步。
- 只改变产品在机器人上的整体倾斜角，而相机与 IMU 相对位置未变：外参仍有效，但
  世界坐标定义改变时重做第 6 步。

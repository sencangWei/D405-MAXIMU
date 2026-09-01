# 第二套正式 UMI D405 产品 V1.0.2

正式版本：`UMI_DEVICE2_D405_PRODUCT_V1_0_2_20260901`
正式镜像：`umi-ego-vio:device2-c48df736-d405-product-v1.0.2-20260901`

本目录是第二套设备的版本化正式源码。相机 `260322279785`、STM32/IMU/编码器
`c48df736...`、2026-09-01 新壳体夹爪曲线和本次装配的相机—IMU标定已经绑定。

该工程使用容器一 `umi-ego-vio:product-v1-20260824` 作为不可变父层，保留其
D405 VINS、回环、实时可视化和30 Hz后处理行为，只叠加：

- 设备集合 `UMI_DEVICE_02_C48DF736`；
- STM32/编码器串口 `c48df736...`；
- 夹爪标定 `UMI_MANUAL_GRIPPER_C48DF736_20260901_SHELL2_V2`；
- 手动夹爪只使用一条“编码器角度→无载软垫间距”曲线；开合方向仅作诊断，不影响距离；
- 当前 D405 的持久绑定；
- 当前装配重新签发的相机—IMU外参和时间偏移。

D435i 临时运行链不在此镜像中。D405 内参采用该相机自己的 Intel factory
rectified 参数并完成独立验收。正式时间偏移为
`td=-0.009109323459933042 s`，world-Z 选择严格恒等矩阵；禁止复制第一套的
`-0.009312 s` 或旧 TTL 的 `-0.0117 s`。

## 命令

```bash
cd /home/robot/releases/umi_device2_d405_product_1.0.2-20260901
./umi-device2-d405.sh build
./umi-device2-d405.sh software-check
./umi-device2-d405.sh hardware-check
./umi-device2-d405.sh status
```

正式日常命令：

```bash
./umi-device2-d405.sh realtime
UMI_CAPTURE_PREVIEW=1 ./umi-device2-d405.sh capture 60
./umi-device2-d405.sh postprocess <recordings中的会话目录名>
```

## 售后重新标定（正常使用不执行）

所有标定报告写入第二套独立目录
`~/umi_ego_vio_data_device2_c48df736/calibration_sessions/`，不会修改容器一或
D435i 临时档案。

```bash
./umi-device2-d405.sh calibrate-init
./umi-device2-d405.sh calibrate-static
./umi-device2-d405.sh calibrate-noise
./umi-device2-d405.sh calibrate-d405
./umi-device2-d405.sh calibrate-camera-imu
./umi-device2-d405.sh calibrate-world-z
./umi-device2-d405.sh calibrate-world-z-resume
./umi-device2-d405.sh calibrate-world-z-retry-elevation2
```

`calibrate-static` 为前2分钟预热、后8分钟正式静态窗口；`calibrate-noise`
默认绑定已签发的同型号IMU产品族参数，不重新采6至10小时 Allan。D405阶段固定整机、
只移动 AprilGrid 完成九宫格；联合阶段固定 AprilGrid、移动整个相机和IMU刚体，自动完成
两份独立采集。第5步由启动器自动分成三段：正式Humble产品容器采集并验证两轮原始
数据、已验证的`ego-vio-kalibr:1f602274-minimal`隔离镜像离线求解、正式产品容器复核
SHA-256/残差/两轮重复性后签发。不会把ROS Noetic写入产品镜像，也不会把Docker
socket挂进产品容器。

实体标定板固定为 `6×6` AprilGrid、tag边长 `35.2 mm`、间距 `10.56 mm`
（`tagSpacing=0.3`）。对应YAML已作为带SHA-256校验的只读资产打包进第二套派生镜像。
九宫格检测器固定为历史验证过的 `aprilgrid==0.5.0` wheel，并以`--no-deps`
安装，禁止升级容器一的NumPy/OpenCV。
标定预览沿用容器一采集程序，只增加已验证的字体回退：优先Noto CJK，
镜像缺少该字体时使用DejaVu，全部字体都缺失时仍保留画面。此补丁不包含
D435i投射器参数，软件预检会对此fail-closed。
像素格式保持容器一合同：正式产品录制为RGB `YUYV`＋左右IR `Y8`写入DB3；
第4步只使用左右IR `Y8`，并临时以质量90 JPEG保存供AprilGrid和极线验收。
标定JPEG与400Hz IMU分别写入独立队列，禁止图像编码反压串口读取。

## 已保留的工程 A/B 入口

这些命令只用于回溯签发证据或重新标定，不是客户日常入口。

```bash
./umi-device2-d405.sh candidate-realtime
./umi-device2-d405.sh candidate-realtime-record 60
UMI_CAPTURE_PREVIEW=1 ./umi-device2-d405.sh candidate-capture 60
./umi-device2-d405.sh candidate-postprocess <候选会话名> baseline
./umi-device2-d405.sh candidate-postprocess <候选会话名> candidate
```

`candidate-realtime` 仅在当次容器进程中使用最新 world-Z PASS 候选，
不会写入或替换 `active_runtime_calibration`。启动后保持设备静止 5 秒，
Rerun 显示 RGB、实时轨迹、编码器角度和夹爪开合距离；`Ctrl-C` 停止。
每次候选实时运行的候选/report/config 哈希证据写入
`candidate_ab/logs/realtime_*/candidate_realtime_binding.yaml`。

`candidate-realtime-record 60` 使用单一 D405/STM32 采集进程，把同一份
双 IR 与 IMU 发布给实时 VINS，同时完整写入 RGB YUYV＋双 IR DB3、原始
`imu.bin`、夹爪角度/开合距离和相机对齐表。Rerun 保持实时显示，结束后打印
可直接交给 `candidate-postprocess` 的会话名。该模式禁止另开 `candidate-capture`。

当前主机没有可用 GPU 时，Rerun 会使用软件渲染。候选入口只降低可视化进程
的调度优先级，VINS/回环二进制、15 Hz 后端和标定均不变；前 1.2 秒的静止
ZUPT 收敛段仅不画到操作员界面，原始/校正轨迹 CSV 仍完整保留。

工程候选会话固定写入 `candidate_ab/recordings`。采集结束会计算原始 DB3 SHA-256，绑定
world-Z正式报告、候选manifest、设备身份和配置哈希；两次后处理前都会重新核对同一
DB3，结果写入 `candidate_ab/slam_results` 并生成各自的溯源manifest。若期间产生了
新的world-Z attempt，后处理仍读取采集时绑定的原候选，不会静默切换。

已有 Docker2 V1 正式运行标定的本机升级到 V1.0.2 时，**不要**再次执行
`install-bundled-runtime-calibration`。本版的 `vins_config.yaml`、`left.yaml`、
`right.yaml`、`device_config.yaml` 与 V1 字节一致；夹爪 V2 单曲线随镜像加载，不从旧
runtime manifest 取值。升级前用以下命令确认四个冻结文件未被污染：

```bash
ACTIVE=/home/robot/umi_ego_vio_data_device2_c48df736/active_runtime_calibration
test "$(sha256sum "$ACTIVE/vins_config.yaml" | awk '{print $1}')" = 3f47e90f838aff2e4770eecccc5bebe29b29fd07833576ab8568cf6bd693db36
test "$(sha256sum "$ACTIVE/left.yaml" | awk '{print $1}')" = 52941d0724ecac8a59c3daeb494ecc5bbd94b7d063983f9b2944346d53f27b21
test "$(sha256sum "$ACTIVE/right.yaml" | awk '{print $1}')" = 52941d0724ecac8a59c3daeb494ecc5bbd94b7d063983f9b2944346d53f27b21
test "$(sha256sum "$ACTIVE/device_config.yaml" | awk '{print $1}')" = 2bd4311e229df57722cd956853551131415a3fdd4ae920a14853136d71146973
./umi-device2-d405.sh status
```

四条 `test` 均返回 0 才能沿用。任何一条失败都停止升级，禁止覆盖，按
`ROLLBACK_ZH.md` 保留现场。

只有全新电脑、且 `active_runtime_calibration` 尚不存在时，才执行首次安装：

```bash
test ! -e /home/robot/umi_ego_vio_data_device2_c48df736/active_runtime_calibration
./umi-device2-d405.sh install-bundled-runtime-calibration
UMI_CAPTURE_PREVIEW=1 ./umi-device2-d405.sh capture 60
./umi-device2-d405.sh realtime
./umi-device2-d405.sh postprocess <会话目录名>
```

激活槽已存在时安装命令会按设计拒绝覆盖。回滚依据见 `ROLLBACK_ZH.md`。

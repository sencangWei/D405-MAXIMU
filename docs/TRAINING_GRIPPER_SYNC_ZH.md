# 训练采集：相机／IMU／轨迹／夹爪时间同步合同

## 结论

磁编码器不参与 VINS 或 SLAM 优化，但它是训练数据的必需状态流。正式 STM32
`stm32_combined_v1` 采集会同时生成：

- `d405_720p_rgb_stereo_ir.db3`：RGB＋双 IR 原始图像；
- `d405_frames.csv`：相机帧在主机单调时钟域的时间；
- `external_imu/imu.bin`：保持原有 40 字节格式，供 VINS／SLAM 使用；
- `external_imu/gripper_encoder.csv`：约 400 Hz 编码器原始和换算状态；
- `gripper_camera_alignment.csv`：每个相机帧对应的最近有效夹爪状态；
- `acceptance.json`：四条流及同步质量的机器可读 PASS／FAIL。

## 时间戳定义

STM32 在同一个 63 字节联合包中提供 `imu_first_byte_rx_us` 和
`encoder_read_us`，两者来自同一个 MCU 定时器。采集器仅用一次冻结的
MCU→主机单调时钟映射，同时得到：

```text
imu_ts_mono     = imu_device_time     + mcu_to_host_offset
encoder_ts_mono = encoder_device_time + mcu_to_host_offset
```

不允许用每个 USB 包的到达时刻代替采样时刻。编码器在 IMU 帧确认后读取，所以是
硬件绑定的“最近邻采样”，不是同一触发沿；当前实机差值为 65–67 µs。

相机使用 D405 `global_time` 映射到同一主机单调时钟域。由于编码器与 IMU 共用
时间域，相机帧关联夹爪时必须复用当前相机—IMU 联合标定：

```text
encoder_query_ts = camera_ts_mono + td
td = -0.009312 s
```

然后选择离 `encoder_query_ts` 最近的有效编码器样本。400 Hz 下理论最近邻上限约
1.25 ms，产品验收门为 2.0 ms。

## 训练字段

训练优先使用：

- `raw_count` / `angle_deg`：磁编码器原始状态；
- `direction`：`opening`、`closing` 或刚开始时的 `unknown`；
- `closure_ratio`：0 为完全张开，1 为完全闭合；
- `encoder_ts_mono`：夹爪真实采样时间；
- `camera_encoder_delta_ms`：选中样本与校正后相机时刻的有符号差。

`estimated_no_load_gap_mm` 是软垫未夹物体时两夹爪内侧间距估计；夹住器械后软垫会
压缩，因此它不能冒充物体直径。加载状态仍可可靠使用原始角度和 `closure_ratio`。

## 轨迹关联

实时／后处理轨迹应保留其传感器时间戳。若轨迹以相机帧为更新单位，直接用
`camera_ts_mono` 或相机帧号连接 `gripper_camera_alignment.csv`；不要再次按墙钟时间
猜测。若生成更高频轨迹，则对 `gripper_encoder.csv.encoder_ts_mono` 做最近邻或线性
插值，并保留实际时间差作为训练元数据。

## 正式采集

```bash
cd /home/robot/ego_vio_humble

./capture_d405_720p_rgb_stereo_ir_rsusb.sh \
  --serial 260322273737 \
  --imu-port /dev/serial/by-id/usb-Silicon_Labs_CP2102N_USB_to_UART_Bridge_Controller_f6a5f836b505f011ae3b8c1272aab386-if00-port0 \
  --duration 60 \
  --capture-mode rgb_stereo_ir \
  --output-root /home/robot/ego_vio_humble/recordings
```

只有 `acceptance.json.result == PASS` 且
`acceptance.json.gripper_encoder.result == PASS` 的 STM32 会话才能进入训练集。

## 当前验收门

- 编码器行数必须与正式 IMU 行数完全一致；
- `encoder_valid` 必须全部为 1；
- 编码器时间戳不得倒退；
- IMU→编码器 MCU 内部差值必须为 0–250 µs；
- 每个相机帧必须成功关联，最大最近邻误差不超过 2.0 ms；
- IMU、相机原有零丢帧／频率／CRC 门继续生效。

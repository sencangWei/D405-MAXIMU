# 当前仅接 IMU 的台架采集

这两条命令用于 D405、最终 STM32 和最终支架尚未到齐时的 IMU 工程评估。输出始终标记
为 `bench_provisional`、`release_eligible: false`，不会推进正式产品标定状态。

## 首次串口权限

当前 USB 转串口设备有稳定的 `/dev/serial/by-id/` 名称，但 `robot` 用户需要加入
`dialout` 组：

```bash
sudo usermod -aG dialout robot
```

执行后注销并重新登录，再用下面命令确认串口可读写：

```bash
test -r /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00 \
  && test -w /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B7E005674-if00 \
  && echo PASS
```

禁止用 `chmod 666 /dev/ttyACM0` 代替永久权限配置。

## 1. 10 分钟静态

IMU 刚性放在稳定桌面上，避免线缆拉力、风扇和桌面振动；桌面不必绝对水平：

```bash
cd /home/robot/ego_vio_calib_kit
./imu_bench_01_static.sh
```

命令预热 2 分钟、正式统计 8 分钟，输出陀螺 bias、加速度均值/模长、温度、频率、
丢帧、CRC 和静态波动。打开串口后先用 1 秒同步窗口丢弃可能的半帧，再开始正式 10 分钟；
同步窗口不会掩盖正式窗口内的 CRC、缺口或丢弃字节。单一姿态不求完整加速度计内参。

## 2. 先做 6 小时 Allan

静态命令结束后仍保持 IMU 不动，确认电脑不会休眠、不会断电：

```bash
cd /home/robot/ego_vio_calib_kit
systemd-inhibit --what=sleep --why="6h IMU Allan bench capture" \
  ./imu_bench_02_allan_6h.sh
```

6 小时达到当前最低时长门槛。施工振动期间不要采，宁可选择连续安静的夜间窗口。
Allan 正式窗口同样在 1 秒串口同步完成后开始。

Allan 噪声不依赖 IMU 朝向，也不依赖 D405 或支架倾角。这份原始数据会保存 SHA-256；
最终 STM32 到齐后，先复核其供电、量程、滤波、400 Hz 采样、时间戳、CRC 和丢帧，并做
短时 A/B。若这些条件一致且 A/B 无材料性变化，可把当前 6 小时数据绑定进最终产品档案，
不必机械地重采 6 小时；只有供电/滤波/采样链改变或 A/B 不一致时才重采。

固定装配后仍应重做 10 分钟静态 bias，因为 bias 与温度和上电状态有关；这不等于重做
Allan。D405 只在相机和相机—IMU联合标定阶段需要。

如以后有连续安静的 10 小时窗口，也可运行可选命令 `./imu_bench_02_allan_10h.sh`。

所有结果写入 `imu_bench_results/时间戳_步骤/`，包含原始 `imu.bin`、原始串口包、
采集健康统计、SHA-256、`report.yaml`，Allan 另含 `allan.png`。

## 3. 30 姿态加速度计内参（仅研发诊断）

这不是客户签发必做项。仅当研发怀疑加速度比例、非正交或交叉轴异常时使用：

```bash
cd /home/robot/ego_vio_calib_kit
./imu_bench_03_intrinsic.sh
```

移动整个固定支架，不要单独扭动 IMU。按屏幕提示采前 20 个拟合姿态和后 10 个独立
验证姿态，每个姿态静止 30 秒。姿态无需精确对准 ±X/±Y/±Z，但要覆盖正放、倒放、
各侧边和斜角，不能集中在同一平面或同一半球。每个姿态若检测到移动、CRC、计数器
缺口、丢弃字节或频率异常，只重采该姿态，已经通过的姿态不会丢失。
同一姿态连续 3 次未通过时会生成聚合 `capture_stop.json` 并停止，避免无限重试和占满磁盘；
采集中按 Ctrl-C 会立即记录为 `BLOCKED` 并停止。

报告只作诊断证据，不会写入正式 Kalibr 输入或 `product-live`。D405 到货、支架整体
倾角以及仅更换串口传输为 STM32 联合包不影响报告本身，但也不会把它升级成运行参数。

### 只补采失败的姿态覆盖

如果 30 姿态报告只有 `fit_octant_coverage` 未通过，不要重新采 30 组，也不要把后 10 组
独立验证数据改成拟合数据。使用原尝试目录继续：

```bash
cd /home/robot/ego_vio_calib_kit
./imu_bench_03_intrinsic.sh --resume-attempt \
  /home/robot/ego_vio_calib_kit/imu_bench_results/原时间_imu_multipose_bench
```

恢复命令会校验原 CSV 的 SHA-256，排除静止、传输或采集时长不合格的原拟合姿态，保留
有效拟合姿态和原来的 10 个独立验证姿态。随后只采缺失象限的明显斜角；离任一坐标面
至少应有 `0.15 g` 余量。方向重复、靠近坐标面、发生移动或传输异常的候选只保留为失败
证据，不会加入拟合。达到至少 20 个有效拟合姿态和 7 个象限后自动重新求解和验证。
补采最多允许 12 个候选；仍未达到覆盖门槛时会落盘 `FAIL report.yaml` 后停止。

不确定支架方向时，先摆好后运行 2 秒方向预览；预览不会加入拟合，也不会消耗正式候选：

```bash
./imu_bench_03_orientation_preview.sh --source-attempt \
  /home/robot/ego_vio_calib_kit/imu_bench_results/原时间_imu_multipose_bench
```

屏幕会显示校正后的 `ax/ay/az`、象限、最小离轴余量和 `正式补采可用=True/False`。
只有方向属于缺失象限、余量至少 `0.15 g`、静止与传输均合格时才显示可用。显示 True 后
保持支架不动，再运行 `--resume-attempt` 正式采集 30 秒。

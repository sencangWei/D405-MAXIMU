# 客户产品标定操作手册（终端向导版）

适用产品：D405 双 IR＋KT-EX9 IMU＋STM32F070/CP2102N＋AS5047P 编码器。

## 先记住三条

1. 看到 `PASS` 才能进入下一项；`FAIL` 按报告重做；`BLOCKED` 表示设备或前置证据不全。
2. 标定工具不会自动修改生产配置。候选值通过最终 A/B 后才由交付人员启用。
3. 标定完成后不要再拆相机、IMU、磁钢或夹爪传动；拆了就按失效表重做相应阶段。

## 第一次创建产品档案

```bash
cd /home/robot/ego_vio_calib_kit
python3 product_calibration_wizard.py init \
  --product-id 客户产品编号 \
  --output calibration_sessions/客户产品编号
```

创建失败时不要手工建目录覆盖，先处理屏幕显示的问题。

## 每次开始前查看状态

```bash
python3 product_calibration_wizard.py status \
  --session calibration_sessions/客户产品编号
```

状态含义：

- `READY`：可以做这一项。
- `BLOCKED`：前一项未通过或设备证据缺失。
- `PASS`：证据存在且 SHA-256 没有变化。
- `FAIL`：测量超限、证据丢失或文件被修改。

## 查看当前步骤

例如相机—IMU标定：

```bash
python3 product_calibration_wizard.py guide camera_imu
```

可用阶段按顺序为：

1. `identity`：锁紧整机并登记身份。
2. `d405_stereo`：D405 双 IR 参数导出和独立核验。
3. `imu_static_bias`：工作温度静态偏置。
4. `imu_multipose`：30 个任意姿态 IMU 椭球内参。
5. `imu_allan`：15–24 小时恒温静止噪声。
6. `camera_imu`：两份独立动态数据求外参和时间偏移。
7. `encoder_transport`：STM32 C2 联合包耐久验收。
8. `encoder_distance`：编码器角度到夹爪距离。
9. `world_z`：平面正例和真实升降负例。
10. `final_acceptance`：10 秒、60 秒、90 分钟整机验收。

## 登记结果

分析脚本必须生成含 `result: PASS`、`FAIL` 或 `BLOCKED` 的 YAML/JSON。例如：

```bash
python3 product_calibration_wizard.py record \
  --session calibration_sessions/客户产品编号 \
  --stage identity \
  --result PASS \
  --artifact /绝对路径/identity/report.yaml
```

向导会记录证据 SHA-256。以后文件被改过，原来的 PASS 会自动失效。
传入的报告会复制到 session 规定位置，因此原分析目录可以归档或移动；不要手工修改
session 里的 `_frozen_inputs/`、`session.yaml` 或阶段报告。

## IMU 任意姿态数据和求解

采集器最终应输出以下 CSV 列，单位是 `m/s²`：

```text
pose_id,split,ax,ay,az
P01,fit,0.12,-0.08,9.74
...
V01,validation,-4.30,8.61,1.84
```

同一姿态可以有多行原始样本，求解器会先按 `pose_id` 求均值。至少 20 个不同
`fit` 姿态和 10 个事先锁定的 `validation` 姿态；不能看完验证误差再把姿态改成拟合集。

```bash
python3 fit_imu_multipose_ellipsoid.py \
  --input calibration_sessions/客户产品编号/raw/imu_multipose.csv \
  --output /tmp/imu_multipose_report.yaml

python3 product_calibration_wizard.py record \
  --session calibration_sessions/客户产品编号 \
  --stage imu_multipose --result PASS \
  --artifact /tmp/imu_multipose_report.yaml
```

`FAIL` 时仍按 `--result FAIL` 登记，不要编辑 YAML 把结果改成 PASS。

## 相机—IMU两份结果与旧金样 A/B

两次采集和 Kalibr 求解完全独立，输出 `run1-camchain-imucam.yaml` 和
`run2-camchain-imucam.yaml` 后运行：

```bash
python3 compare_camera_imu_calibration.py \
  --run1 run1-camchain-imucam.yaml \
  --run2 run2-camchain-imucam.yaml \
  --output /tmp/camera_imu_report.yaml
```

工具会从仓库内 `GOLDEN_BASELINE_20260808.yaml` 读取旧的两份外参和时间偏移，报告
“新两次之间的重复性”和“每次新结果相对对应旧金样的差异”。这里 PASS 只证明新两次
重复性，不代表可以直接启用；还要人工检查 Kalibr 重投影/IMU 残差并完成最终 SLAM A/B。

## 相机和 IMU 到底怎么拿

- 纯 D405 双目标定：整机固定，移动标定板。
- 相机—IMU联合标定：标定板固定，移动相机＋IMU整套刚体。
- 相机倾斜安装没有问题，不需要把相机拆下来摆正，也不要求 IMU 轴对准桌面。
- IMU 多姿态标定移动的是整套支架，不直接掰 IMU 和线缆。

## 与以前跑通版本的关系

向导绑定了 2026-08-08 黄金基线。新标定报告必须显示与历史双目基线、两次 Kalibr
重复性、`td=-11.7 ms` 和冻结 SLAM 的 A/B。旧硬件数据继续使用旧黄金配置，新装配
使用新候选；不得用新文件覆盖旧黄金文件再比较。

## 什么情况下要重做

- D405 内部模组或工作分辨率/FPS改变：重做 `d405_stereo` 及其后续。
- D405 与 IMU相对位置改变：重做 `camera_imu`、`world_z`、最终验收。
- IMU更换：重做所有 IMU 项、`camera_imu`、`world_z`、最终验收。
- STM32固件、USB桥或时间戳算法改变：重做 `encoder_transport`，并复核 `camera_imu`时间。
- 磁钢、编码器或夹爪传动改变：重做 `encoder_distance`。

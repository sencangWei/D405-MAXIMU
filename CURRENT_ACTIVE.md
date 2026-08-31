# CURRENT_ACTIVE

当前唯一推荐链路：

- 采集：`./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 60`
- 实时：`./run_vins_realtime.sh stable`
- 历史 `<1 cm` 冻结链复现：`./run_vins_realtime.sh frozen`
- 同采集实时准确＋可复放冻结后端：`./run_vins_realtime.sh frozen-record --duration 60`
- 相机：D405 双IR 1280×720@30（彩色只记录，不与IR组成伪双目）
- IMU：KT-EX9-2 400Hz
- VINS：`/home/robot/ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml`
- 时间：`estimate_td=0, td=-0.0117`，VINS回放shift=0
- IMU运行时标定：`config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml`

时间戳契约（2026-08-31 起）：D405 使用 `global_time` 传感器事件时间；STM32
IMU 使用 MCU 事件计时器映射到同一主机单调时钟；机械臂 `actual_deg` 使用
SocketCAN 内核反馈帧接收事件时间。评估器对事件时间样本不再叠加旧的固定
16.58 ms 偏移；旧机械臂 JSONL 没有反馈事件字段时才走明确标注的兼容分支。
机械臂协议本身没有电机生成时间戳，因此 CAN 内核接收时间是可取得的最接近
反馈事件时间，并在报告中标明，不伪装成电机内部采样时刻。

所有当前/证据/废弃分类见 `JAZZY_HANDOFF_20260816/CURRENT_RUNTIME_PROFILE.md`。旧RGB+IR伪双目、7.36ms、FAIL手工gyro和固定世界Z候选均不得加载。

`stable` 是当前工作区实时链；它不等于历史 `a3a38b8` direct-BRIEF 冻结回环链。
需要复现历史四组 `<1 cm` 三维闭环报告时，必须显式使用 `frozen`，并检查启动日志中
冻结回环 SHA256。

GitHub 单仓库恢复时，配套标定工具和修改后的 VINS 源码快照位于 `components/`。

## 2026-08-31 鲁棒联合估计与大回环门禁

已加入 `scripts/calibrate_robot_umi_joint.py`：将多段平移、旋转、升降运行放到同一
鲁棒 hand-eye 方程中联合搜索共享设备时间偏移，并估计 `T_body_gripper_end`（含
TCP 杠杆臂）。该工具只输出 `PASS_CANDIDATE`/`DIAGNOSTIC_CANDIDATE`，不会自动覆盖
Docker2 的正式标定或 VINS 配置，也不使用首尾重合答案。

回环节点对修正量 `>= 10 mm` 启用 8 帧连续确认、双 IR 右目几何和更严格 PnP 内点/重投影
门禁；低质量/模糊帧继续进入 VIO，只降低视觉因子权重，失败时仅拒绝回环边。

本轮三段联合候选与分段诊断见 `reports/robust_multirun_20260831.md`。主机源码构建已
验证新门禁；Docker2 已签发镜像内的 `.product_live_build` 仍是旧回环二进制，正式镜像
未被静默替换，需单独制作候选镜像后才能在 Docker2 产品入口启用该门禁。

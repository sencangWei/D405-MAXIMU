# CURRENT_ACTIVE / CALIBRATION

当前 STM32 `product-live` 标定合同是：Intel factory 双 IR 整流内参、最终装配
相机—IMU 外参、`estimate_td=0`、`td=-0.009312s`、回放 shift=0。实际运行配置以
`/home/robot/ego_vio_humble/config/product_live_stm32/` 为准。

2026-08-08 的 `td=-0.0117s` 只属于历史冻结 TTL 链。它用于复现历史 `<1 cm`
结果，不能覆盖当前 STM32 产品链。

D405 客户标定不再自由拟合双 IR 内参。正式流程从连接设备导出 Intel factory
1280×720@30 参数，并做设备身份、基线、零丢帧和独立极线 P95 验收。研发需要时仍可
运行 Kalibr 自由拟合作为诊断对照，但结果不会自动进入产品配置。

2026-08-23 已用同一份水平回放完成 30 姿态加速度矩阵盲 A/B：改善不能跨数据稳定，
且候选使水平轨迹短边缩小约 13.2%。正式链因此保持原始 IMU 输入、设备
`calibration: ""` 和 VINS 在线 bias；30 姿态工具只作研发诊断，不再是客户前置。

本仓库以下目录只作证据，不得作为当前运行标定：

- `imu_manual_calibration/intrinsic_20260803_000908/calibration_candidate.yaml`：FAIL手工gyro候选；
- `world_z_calibration/runtime_*`：失败世界Z实验；
- `product_slam_candidate_20260814/isolated_builds/`：冻结/隔离构建证据；
- 旧7.36ms配置：只作08-04历史档案。

运行代码与当前配置以 `/home/robot/ego_vio_humble/CURRENT_ACTIVE.md` 为准。
完整产品标定决策见 `memory/PRODUCT_CALIBRATION_POLICY_20260823.md`。

客户可复用入口已经固定为 `calibrate_init.sh` 和五个必需脚本
`calibrate_01`、`02`、`04`、`05`、`06`；首次部署使用 `calib_setup.sh` 与
`calibrate_preflight.sh`。第6步只生成隔离
候选，不覆盖上述当前运行配置。

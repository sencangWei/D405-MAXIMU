# CURRENT_ACTIVE / CALIBRATION

当前标定权威值是2026-08-08 Kalibr：VINS `td=-0.0117s`、`estimate_td=0`、回放shift=0。原始bag与报告在旧机`/tmp/calib_run`，迁移包中位于`calibration/calib_run_20260808/`。

本仓库以下目录只作证据，不得作为当前运行标定：

- `imu_manual_calibration/intrinsic_20260803_000908/calibration_candidate.yaml`：FAIL手工gyro候选；
- `world_z_calibration/runtime_*`：失败世界Z实验；
- `product_slam_candidate_20260814/isolated_builds/`：冻结/隔离构建证据；
- 旧7.36ms配置：只作08-04历史档案。

运行代码与当前配置以 `/home/robot/ego_vio_humble/CURRENT_ACTIVE.md` 为准。

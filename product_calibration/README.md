# 产品标定工具

这里保留“历史已跑通金样”和“新产品候选”两条独立路径。任何脚本产生的 `PASS`
只代表本阶段门槛通过，不会直接写入冻结 SLAM 配置。

## 当前可离线执行

- `product_calibration_wizard.py`：创建、查看和登记可恢复标定会话；会话内冻结
  workflow、黄金基线和每阶段报告，并校验 SHA-256。
- `compare_camera_imu_calibration.py`：比较两份独立 Kalibr camchain，并自动与
  2026-08-08 金样 A/B。
- `fit_imu_multipose_ellipsoid.py`：对任意姿态加速度计数据拟合椭球，仅以留出姿态验收。
- `fit_multisession_world_z.py`：已有的多平面 leave-one-out 和真实升降保护工具；其
  PASS 仍禁止直接启用，须经过端到端 SLAM 验收。

## 硬件到齐后复用的已有采集链

- Kalibr 数据采集和转换位于 `ego_vio_ubuntu_calib/ego_vio/scripts/`。
- `manual_imu_calibration_capture.py` 的串口读取与静态采集可复用，但原六面提示和
  `solve_manual_imu_calibration.py` 的 ±X/±Y/±Z 假设不能用于倾斜整机；后续应接入
  任意姿态 CSV 输出。
- STM32 63 字节联合包未在本阶段假装通过；正式 reader、C2 固件和实机 HIL 到齐后
  才能完成 `encoder_transport`。

详细方法见 `CALIBRATION_METHODS.md`，客户步骤见 `CUSTOMER_CALIBRATION_MANUAL_ZH.md`。

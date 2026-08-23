# 产品标定工具

这里保留“历史已跑通金样”和“新产品候选”两条独立路径。任何脚本产生的 `PASS`
只代表本阶段门槛通过，不会直接写入冻结 SLAM 配置。

## 内部基础设施（不是客户主入口）

- `product_calibration_wizard.py`：创建、查看和登记可恢复标定会话；会话内冻结
  workflow、黄金基线和每阶段报告，并校验 SHA-256。
- `compare_camera_imu_calibration.py`：比较两份独立 Kalibr camchain，并自动与
  2026-08-08 金样 A/B。
- `fit_imu_multipose_ellipsoid.py`：可选研发诊断；对任意姿态加速度计数据拟合椭球，
  结果不进入正式 Kalibr 或 product-live。
- `fit_multisession_world_z.py`：已有的多平面 leave-one-out 和真实升降保护工具；其
  PASS 仍禁止直接启用，须经过端到端 SLAM 验收。

客户最终入口严格是 `STAGE_COMMAND_CONTRACT.yaml` 中五条必需独立命令。保留编号 3
的多姿态工具仅属研发诊断。每条客户命令执行
当前模块的采集＋自动求解＋自动判定，不提供一次跑完的 `run-all`。
合同 v2 将 v1 的 `customer_steps` 重命名为 `customer_release_steps`，并新增
`optional_engineering_diagnostics`；外部读取器必须按 `format_version` 选择字段。

正式第 4 步是 `../calibrate_04_d405_factory.sh`：读取并锁定连接设备的 Intel
factory rectified 双 IR 参数，再做九宫格极线验收；不重新拟合客户相机内参。
`../camera_bench_04_d405_stereo.sh` 只用于售后/研发自由拟合诊断。

## 后端适配约束

- Kalibr 数据采集和转换位于 `ego_vio_ubuntu_calib/ego_vio/scripts/`。
- `manual_imu_calibration_capture.py` 的串口读取与静态采集可复用，但原六面提示和
  `solve_manual_imu_calibration.py` 的 ±X/±Y/±Z 假设不能用于倾斜整机；后续应接入
  任意姿态 CSV 输出。
- 正式第5、6步要求 STM32 63字节联合包；reader 已支持该协议，但每台新板仍须先完成
  独立的固件/CRC/时间戳/队列 HIL，不能复制另一块板的验收结论。
- 第6步会从本产品必需前置报告生成隔离 `product-live` 候选并采集世界Z正负例；候选
  PASS 不会自动覆盖默认配置，仍须端到端SLAM A/B后签发。
- 第5步的正式 live 路径同时门禁相机和IMU采集健康，并校验第2步 IMU YAML、第4步
  camchain 的 SHA-256；只有求解输出的离线复算固定为非发布 `BLOCKED`。
- workflow 升版不迁改旧 session。出现 `ACTIVE_WORKFLOW_DIFFERS_FROM_SESSION` 时，
  使用新的 session 根目录重新建档，旧目录只读归档。

详细方法见 `CALIBRATION_METHODS.md`，客户步骤见 `CUSTOMER_CALIBRATION_MANUAL_ZH.md`。

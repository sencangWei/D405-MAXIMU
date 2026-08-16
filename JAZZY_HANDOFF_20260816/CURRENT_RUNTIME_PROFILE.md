# 当前有效运行配置（唯一推荐入口）

## CURRENT_ACTIVE

| 项目 | 当前值/入口 |
| --- | --- |
| 采集 | `./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 60` |
| 相机 | D405 彩色YUYV + 左IR + 右IR，1280×720@30 |
| IMU | KT-EX9-2，400Hz，921600 baud |
| 采集后端 | librealsense 2.58.2，RSUSB，DB3先写内存盘 |
| 实时VINS | `./run_vins_realtime.sh stable` |
| VINS配置 | `ros2_ws/src/vins_fusion_ros2/config/d405_stereo_imu/d405_stereo_imu_config.yaml` |
| 时间 | VINS `estimate_td=0, td=-0.0117`，回放shift=0 |
| IMU运行时标定 | `config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml` |
| 图像模式 | 真双目只能左IR+右IR；RGB+IR禁止当双目 |

`run_vins_realtime.sh` 当前会从有效双IR配置派生本轮临时配置；不要手工改为 `d405_rgb_ir_imu_config.yaml`。

## REGRESSION_EVIDENCE

以下内容必须保留，但不能作为当前运行入口：

- `recordings/SESSION_CLASSIFICATION.tsv` 中标记为 `REGRESSION_EVIDENCE` 的会话；
- `reports/` 下历史 A/B、自动回环、Depth/Z、失败门禁和轨迹图；
- `桌面/ego_vio_calib_kit/product_slam_candidate_20260814/` 的隔离构建与证据；
- `/tmp/calib_run` 迁移后的 `calibration/calib_run_20260808/` 权威原始 bag；
- FFV1/inline/UVC失败对照、230503动态场景负样本、151212真实升降安全负样本。

证据可以离线回放验证结论，不能把其中的实验配置覆盖到当前 runtime。

## LEGACY_DO_NOT_RUN

- `config/camimu_720p_leftir_kalibr.yaml`：08-04旧标定，`7.36ms`只作档案。
- `ros2_ws/src/vins_fusion_ros2/config/d405_rgb_ir_imu/d405_rgb_ir_imu_config.yaml`：RGB+左IR伪双目、旧7.36ms/在线td实验，只作历史复现。
- `桌面/ego_vio_calib_kit/imu_manual_calibration/intrinsic_20260803_000908/calibration_candidate.yaml`：明确FAIL，手工gyro matrix A/B恶化，运行时必须拒绝。
- `桌面/ego_vio_calib_kit/world_z_calibration/runtime_*`：失败的世界Z实验运行目录，不是有效标定。
- `recordings_legacy_quarantine_20260816/`：91个已从活跃`recordings/`移出的旧或可重录会话，新机不复制。

旧脚本的逐项状态见 `scripts/ENTRYPOINT_STATUS.md`。`run_vins_realtime.sh level-candidate` 已改为明确报错退出，避免失败的固定Z候选再次污染实时轨迹。

## 当前尚未完成

- 自动回环完整产品门禁仍 `NOT_READY`；不能宣称所有闭环 <1cm。
- 世界Z动态误差没有产品级解决；固定调平和当前Depth因子都没有通过正样本泛化门。
- Jazzy/Python3.12适配尚未实机验证；必须 clean build 和HIL复验。

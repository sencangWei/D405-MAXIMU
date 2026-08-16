# 脚本入口状态

## CURRENT_ACTIVE

- 根目录 `capture_d405_720p_rgb_stereo_ir_rsusb.sh`：当前正式采集入口。
- `scripts/capture_d405_720p_rgb_stereo_ir.py`：上述入口的实现。
- 根目录 `run_vins_realtime.sh stable`：当前实时VINS入口。
- `scripts/replay_db3_to_ros2.py`：当前原始DB3离线回放。
- `scripts/test_vins_auto_loop.py`：自动回环候选验收工具，不等于客户发布完成。
- `scripts/verify_recorded_session.py`：录后单条验收。
- `scripts/build_librealsense_rsusb.sh`：RSUSB构建入口；Jazzy需先完成Python ABI自动发现适配。

## REGRESSION_EVIDENCE

- `bag_to_ffv1.py`、`replay_mp4_to_ros2.py`：FFV1无损统计复验；不作为当前默认采集。
- `_test_vins_dynamic.py`、`_test_orb_dynamic.py`：历史A/B工具。
- Depth/世界Z、declared-loop、product-release脚本：候选算法与证据门工具，只能按各自清单运行。

## LEGACY_DO_NOT_RUN

- `capture_d405_mp4_inline.py`：inline管线曾周期性特征塌缩与尺度膨胀。
- `capture_d405_nvenc.py`、`bag_to_mp4_nvenc.py`：有损NVENC只可观赏，不能作SLAM母版。
- `capture_d405_720p_all_streams.py`及四流旧入口：D405四路720p吞吐实验，不是当前三路生产入口。
- `capture_d405_720p_rgbd_imu.py`、旧callback/native smoke：早期采集实验，已被RSUSB三路正式链路替代。
- `run_vins_realtime.sh level-candidate`：已硬禁用；固定世界Z候选跨会话不泛化。

旧脚本保留用于解释历史数据和Git差异，不要从文件名猜测“看起来更新”就运行。

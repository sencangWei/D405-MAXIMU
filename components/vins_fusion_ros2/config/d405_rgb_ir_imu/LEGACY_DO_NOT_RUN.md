# LEGACY_DO_NOT_RUN

本目录的 `d405_rgb_ir_imu_config.yaml` 是 2026-08-04 的 RGB+左IR伪双目实验配置，保留仅为复现实验历史：

- RGB与左IR基线约0.01mm，不构成可用双目；
- 文件保留旧 `7.36ms + estimate_td=1` 实验设置；
- 当前权威配置是相邻 `d405_stereo_imu/d405_stereo_imu_config.yaml`；
- 当前VINS时间合同是 `estimate_td=0, td=-0.0117` 且回放shift=0。

任何生产、实时或Jazzy迁移运行都不得加载本目录。

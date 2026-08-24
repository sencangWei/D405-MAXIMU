# 正式配置状态（2026-08-24）

- 实时设备：`devices_product_live_stm32.yaml`
- VINS：`product_live_stm32/vins_config.yaml`
- 左／右 IR：D405 出厂双 IR 内参
- 当前固定装配：`estimate_td=0`、`td=-0.009312 s`
- IMU协议：`stm32_combined_v1`，运行时不加载被否决的加速度候选
- 夹爪：`gripper/umi_manual_gripper_20260824.yaml`，不参与 VINS 优化

正式入口不会搜索其他配置。历史 `td=-0.0117 s`、Jazzy、ORB、冻结链和 Z 候选只在
Git 历史标签中保留，不得复制回本目录。

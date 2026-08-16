# CURRENT_ACTIVE / VINS-Fusion

当前产品候选配置：

```text
config/d405_stereo_imu/d405_stereo_imu_config.yaml
```

合同：D405左/右IR真双目30fps、外置IMU400Hz、`estimate_td=0`、`td=-0.0117`、回放shift=0。

`config/d405_rgb_ir_imu/` 是旧RGB+左IR伪双目与7.36ms实验，已标记 `LEGACY_DO_NOT_RUN`。新机不得把它当默认配置。

自动回环当前仍是候选：部分闭环毫米级，但完整历史正闭环回归仅5/12轮稳定通过，客户发布状态不是完成。

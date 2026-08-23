# Humble 老版与当前新版说明（2026-08-23）

本仓库用独立 Git 引用保存两条链，禁止把两者混成一个“最新版”。

## 老版：历史 `<1 cm` 冻结复现链

- 分支：`release/humble-known-good-20260816`
- 提交：`76d6623a8d189d8d861ec77d1534e26fb02f323b`
- 标签：`humble-known-good-20260816`
- 用途：复现历史 a3 direct-BRIEF + v7 冻结回环结果和作为回退基线。
- 固定条件：`estimate_td=0`、`td=-0.0117`、回放额外偏移 `0 ms`。

老版不接收 STM32 63 字节协议、新装配 `td`、产品实时保护或后续自适应回环改动。

## 新版：STM32 product-live RC1

- 分支：`release/humble-stm32-product-live-rc1-20260823`
- 标签：`humble-stm32-product-live-rc1-20260823`
- 系统：Ubuntu 22.04 + ROS 2 Humble。
- 传感器：D405 双 IR 1280×720@30，KT-EX9-2 400 Hz，经 STM32/CP2102N 固定 63 字节协议输入。
- 相机参数：D405 出厂双 IR 内参/外参；不加载本轮被否决的运行时加速度修正。
- 相机—IMU时间：当前装配共识 `td=-0.009312 s`、`estimate_td=0`。
- 回环：当前自适应产品候选，Rerun 显示 `/odometry_rect`，同时保存原始 `/odometry`。
- 保护：动态近景导致位姿单步超过 0.05 m 时锁存失败并冻结最后可信轨迹。

源码被明确分成两套组件：

- `components/vins_fusion_ros2`：实时 VINS 前端/后端与失效保护；
- `components/vins_fusion_ros2_product_loop`：已验收的自适应回环变体。

在新检出目录构建并运行：

```bash
cd /home/robot/ego_vio_humble
./build_product_live.sh
./run_vins_realtime.sh product-live
```

`build_product_live.sh` 在 `.product_live_build/` 生成两套隔离工作区和本机二进制哈希清单。
发布分支不提交 `build/install/log`、原始 DB3、图片或标定会话。

## 固件与标定工具

- STM32 Mode-B 固件源码、PC协议工具和已烧录的 `firmware.bin` 位于
  `firmware/stm32f070_imu_encoder/`。
- 独立标定工具使用分支 `release/calibration-product-workflow-v3-20260823`，不复制
  大体积采集数据到本仓库。

## 当前限制

- 世界 Z 仍是已知限制。禁止固定角度压平、人工终点修正，也不启用已被盲 A/B 否决的
  `product-live-z-candidate`。
- 当前新版是 RC：D405+STM32 传输已完成 3 小时零丢帧压力验收；磁编码器“角度到夹距”
  的产品标定尚待完成，因此不能把编码器距离链标为最终签发。
- 从源码重新构建会产生不同绝对路径和 ELF 哈希，必须再做短时 HIL 后才可替换已验收二进制。

## 回退

```bash
git switch release/humble-known-good-20260816
git rev-parse HEAD
# 必须为 76d6623a8d189d8d861ec77d1534e26fb02f323b
```

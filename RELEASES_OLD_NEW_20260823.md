# 历史版与正式产品版 Git 映射

这些版本必须独立检出，禁止在一个工作目录中混用 build/install/config。

| 类型 | Git 引用 | 固定提交／状态 | 用途 |
|---|---|---|---|
| 历史 Humble | `release/humble-known-good-20260816`、`humble-known-good-20260816` | `76d6623a8d189d8d861ec77d1534e26fb02f323b` | 只用于复现 a3 direct-BRIEF + v7 历史报告 |
| 历史 Jazzy | `handoff/jazzy-20260816` | `9020dea60815c77b7e6eac7cf9afd0ef3261f6d0` | 只用于迁移研究，不是客户产品 |
| STM32 RC1 | `release/humble-stm32-product-live-rc1-20260823`、`humble-stm32-product-live-rc1-20260823` | 已封存开发候选 | 只用于回溯 2026-08-23 HIL |
| 正式产品 | `release/humble-stm32-product-v1-20260824`、`humble-stm32-product-v1.1-20260824` | 以远端标签解引用提交为准 | 客户部署和小电脑迁移唯一来源；v1.1 已移除历史运行文件 |
| 标定工具 | `release/calibration-product-v4-20260824`、`calibration-product-v4-20260824` | 独立历史分支 | 产品标定、夹爪 App 接口和不可替代证据 |

正式产品分支包含：

- `components/vins_fusion_ros2`：当前实时 VINS、确定性 15 Hz 后端、静止 ZUPT、
  位姿失效保护；
- `components/vins_fusion_ros2_product_loop`：当前自适应回环；
- `firmware/stm32f070_imu_encoder`：已烧录 Mode-B 固件源码、固件和哈希；
- `config/product_live_stm32`：唯一 VINS 标定配置；
- `config/gripper/umi_manual_gripper_20260824.yaml`：夹爪空载状态换算；
- `build_product_live.sh`、`run_vins_realtime.sh`、`run_slam_postprocess.sh`：客户入口。

正式分支不提交 raw DB3、标定采集、build/install/log 或本机依赖。新电脑必须从源码
执行 `scripts/build_librealsense_rsusb.sh` 和 `build_product_live.sh`，然后做短时 HIL；
不得复制其他机器的 ROS 工作区。

历史回退只能在另一个目录执行：

```bash
git clone --branch humble-known-good-20260816 \
  https://github.com/sencangWei/D405-MAXIMU.git /独立路径/ego_vio_historical
```

禁止在 `/home/robot/ego_vio_humble` 内切换历史标签。

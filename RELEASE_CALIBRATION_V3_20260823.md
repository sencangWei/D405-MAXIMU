# 产品标定工具 v3 发布说明（2026-08-23）

GitHub 分支/标签：

- 分支：`release/calibration-product-workflow-v3-20260823`
- 标签：`calibration-product-workflow-v3-20260823`

这是独立标定工具源码分支，不是历史 Humble SLAM 分支。历史可回退运行链位于同一
GitHub 仓库的 `release/humble-known-good-20260816`；当前 STM32 SLAM RC 位于
`release/humble-stm32-product-live-rc1-20260823`。

## 客户必需流程

每个阶段独立采集、自动求解、自动验收，不提供一条命令全部跑完：

```bash
./calibrate_init.sh PRODUCT_ID
./calibrate_01_imu_static.sh PRODUCT_ID
./calibrate_02_imu_noise.sh PRODUCT_ID
./calibrate_04_d405_factory.sh PRODUCT_ID
./calibrate_05_camera_imu.sh PRODUCT_ID
./calibrate_06_world_z.sh PRODUCT_ID
```

- 第 4 步锁定当前 D405 的 Intel factory 双 IR 参数并做九宫格、零丢帧、极线 P95
  验收；不重新自由拟合客户相机内参。
- 第 5 步用最终刚性装配做两次独立相机—IMU Kalibr，门禁采集健康和输入 SHA-256。
- 第 6 步只生成隔离 Z 候选，不能覆盖已签发运行配置；仍需端到端 SLAM A/B。
- 编号 3 的 30 姿态椭球拟合仅为研发诊断，不属于客户签发前置，也不自动加载到 VINS。

首次部署与中断恢复：

```bash
./calib_setup.sh
# 注销并重新登录
./calibrate_preflight.sh
./calibrate_status.sh PRODUCT_ID
```

完整操作见 `product_calibration/CUSTOMER_CALIBRATION_MANUAL_ZH.md`，方法与门限见
`product_calibration/CALIBRATION_METHODS.md`，当前不可混用的历史/现行参数见
`memory/PRODUCT_CALIBRATION_POLICY_20260823.md`。

## 数据边界

Git 只保存代码、命令合同、手册和测试。以下内容故意不上传：

- `calibration_sessions*`；
- `imu_bench_results`、`camera_bench_results`、`camera_imu_bench_results`；
- 原始 DB3、图片、bag、长稳数据、生成报告和旧 SLAM 轨迹。

这些数据必须由交付介质或客户档案单独保存，不能依赖 Git LFS 隐式下载。

## 当前未签发项

STM32 63 字节 IMU链已经完成实板与 3 小时传输测试，但磁编码器的磁场、零点、方向、
回绕、名义半径 `25.15 mm` 和角度到夹距模型仍待实机标定。定义和明日流程见
`memory/ENCODER_DISTANCE_HANDOFF_20260823.md`。

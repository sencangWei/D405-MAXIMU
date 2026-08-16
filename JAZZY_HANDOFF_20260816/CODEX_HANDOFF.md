# 给新电脑 Codex 的完整交接

## 任务与工作区

硬件是 Intel RealSense D405 + KT-EX9-2 外置 IMU（400 Hz）。目标是近距离手持 VIO/SLAM，优先保证真实轨迹精度、采集完整性与可诊断性。

新机是 ROS 2 Jazzy。迁移包保留的是旧机 Ubuntu 22.04 / ROS 2 Humble 的完整证据，不能直接执行旧 `install/` 或 Python 3.10 二进制。

新机接手顺序：

1. 完整阅读本文件、`README_先看这里.md`、`JAZZY_FIRST_BOOT.md`。
2. 阅读两个项目根目录的 `AGENTS.md`。
3. 阅读 `memory/claude_project_memory/MEMORY.md` 和同目录全部 14 个内容 `.md`。
4. 阅读主工程 `.planning/product_slam_auto_loop/{task_plan,findings,progress}.md` 和 `.planning/jazzy_handoff_20260816/`。
5. 查看 `metadata/git/`，特别是三个仓库的 `status.txt`、dirty patch、untracked 列表与 HEAD；不要只看远端 main。
6. 按 `JAZZY_FIRST_BOOT.md` 创建全新的 Jazzy 工作区并从 `ros2_ws_humble_snapshot/src/` 重建。

## 绝对不能改错的事实

1. **VINS 时间偏移**：`estimate_td: 0`、`td: -0.0117`，回放 `--imu-shift-ms 0`。
2. **ORB 时间偏移**：回放 `--imu-shift-ms 11.7`。
3. 旧 `7.36ms` 只保留为 08-04 失败历史，禁止运行使用；双重补偿曾导致约 846m 发散。
4. 回放中的约 91.42° IMU 重力旋转与 VINS/ORB 外参中的约 90° bake 是成对的，不能只删一边。
5. 真双目是左 IR + 右 IR，基线约 10mm。RGB 与左 IR 基线约 0.01mm，不能当双目。
6. 生产目标是三路相机 30fps + 外部 IMU 400Hz，禁止用固定 15fps 换精度。
7. 原始 DB3 是当前生产母版。FFV1 已经通过 36 轮统计洗清“编码损坏”嫌疑，但当前不作为默认生产管线。
8. 跑 VINS 前必须清理残留节点并使用独立 ROS 域；多个 `/odometry` 发布者曾造成 65m 假路径和 1e18m 数值。

## 当前确认有效的采集链路

入口：

```bash
cd <Jazzy上的ego_vio_humble>
./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 60
```

旧机已证明 RSUSB librealsense 2.58.2 后端能实现：

- 彩色 YUYV + 左 IR + 右 IR，1280×720@30；
- 外置 IMU 400Hz；
- 正式窗口相机三路零跳号、零重复、零时间戳回退；
- IMU 正式窗口零坏帧、零 resync、零 counter drop；
- DB3 先写 `/dev/shm`，结束后搬到录制目录。

每条录制必须读取自身 `acceptance.json`，不能拿历史平均值替代单条验收。主数据在 `projects/ego_vio_humble/recordings/`，不要删失败会话；失败会话是回归与负样本证据。

## 标定权威来源

- 权威原始材料：`calibration/calib_run_20260808/`。
- `calib_imucam-camchain-imucam.yaml`：08-08 Kalibr 结果，时间偏移 `-0.0117s`，相机-IMU旋转差约 1.41°。
- 单相机/双IR/IMU标定与分析在 `projects/ego_vio_calib_kit/`。
- 用户明确保留的 IMU `-Z` 静态面只有 14.69s，但 5879 样本、静态波动约 0.00136g，允许作为加速度计标定输入。

### 最新未提交的实时 IMU 修复

旧实时链路错误加载了：

```text
projects/ego_vio_calib_kit/imu_manual_calibration/intrinsic_20260803_000908/calibration_candidate.yaml
```

该文件自己声明 `acceptance.status: FAIL`、`runtime_applied: false`。A/B 证据还表明手工 90° 陀螺矩阵会把闭环从约 3.60cm 恶化到约 17.84cm。

主工程当前未提交改动已经：

- 在 `ego_vio/imu/calibration.py` 拒绝显式 FAIL 或 `runtime_applied:false` 的标定；
- 新增 `config/imu_runtime_accel_calibrated_raw_gyro_20260816.yaml`：保留六面加速度计校正，陀螺矩阵使用单位阵、bias 为 0，由 VINS 在线估计陀螺偏置；
- `config/devices_vins_fusion_live.yaml` 改用该运行时配置；
- 新增针对测试，旧机定向测试 7 项通过。

用户对修正后的实时轨迹反馈“挺不错”。这些改动尚未被主仓库 HEAD 包含，必须从完整工作树或 dirty patch 带到 Jazzy，不能只 clone GitHub。

## VINS / 自动回环实际状态

主路径是 VINS-Fusion 双 IR + 外部 IMU；经验稳定配置：

- `td=-0.0117`、`estimate_td=0`
- `max_solver_time/iterations` 对应迭代 8
- `keyframe_parallax=10`
- `max_cnt=400`
- `min_dist=20`

本地 `vins_fusion_ros2` 已移植官方 loop_fusion，并做了双 IR 几何验真、原子关键帧消息、连续多帧一致性、4DoF 位姿图和失效门控。曾在部分声明真闭环上达到 4.53mm 与 6.71mm，但完整冻结回归的真实结论是：

- 历史声明真闭环稳定通过 **5/12 轮**；
- `114728`、`121306` 存在稳定漏回环；
- `151212` 真实升降负样本三轮 0 误回环，Z跨度保持；
- 当前完整客户发布门是 `NOT_READY`，不能承诺“所有轨迹自动回环 <1cm”；
- `max_loop_candidates=24` 和 tracked-BRIEF 对应匹配器是召回改进方向，但必须继续做正负回归，不能放宽 3cm 全局修正安全门来换召回。

证据与完整演进在：

```text
projects/ego_vio_humble/.planning/product_slam_auto_loop/
projects/ego_vio_humble/reports/
projects/ego_vio_calib_kit/product_slam_candidate_20260814/
```

## 世界 Z / 垂直误差实际状态

没有完成。不要把平面轨迹强制压成 Z=0，也不要用用户提供终点修正产品轨迹。

已证伪：

- 单一固定世界旋转不能跨会话泛化：平面会话倾角约 1.276° 与 2.527°；
- 固定调平候选没有降低验证会话 Z span（约 26.38mm → 26.50mm）；
- 纯 IMU 水平平移标定受手碰与轻微旋转串扰，不能单独证明世界 Z；
- 当前 Depth 多平面前端在库存数据里只看到了墙面/不合格平面，因果因子安全保持 `DISABLED`，没有伪改轨迹，但也没有精度收益。

正确后续方向：

1. 录制能持续看到同一真实水平面的 RGB/双IR/Depth/IMU 正样本，同时包含水平移动、真实升降和返回；
2. 用真值只做运行后评分，不能输入 SLAM；
3. 验证严格因果、只沿重力方向、有界、失去平面支持能释放的 Depth/平面软因子；
4. 分别报告水平平移 Z P95、真实升降幅值误差、回环误差、ATE/RPE 和失败率。

对应证据：

```text
projects/ego_vio_humble/reports/world_z_level_ab_20260816/
projects/ego_vio_humble/reports/depth_plane_001100_gravity_locked_20260816/
projects/ego_vio_humble/config/imu_level_20260816.yaml
projects/ego_vio_calib_kit/world_z_calibration/
```

## ORB/OpenVINS 状态

- `ros2_ws_humble_snapshot/src/open_vins` 存在并带 `UPSTREAM_COMMIT`，但当前产品主路径不是它。
- `ros2_ws_humble_snapshot/src/ego_orbslam3_ros2` 包装层存在。
- 记忆里曾使用的 `/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3` core 当前旧机上已经不存在，因此没有被迁移。需要时从原 Git 来源重新获取并核对历史补丁。
- ORB RGB-D-Inertial 的历史负结果：直接使用更小 Allan 噪声或增加 BA 迭代反而显著恶化，已经回退；不要重复。

## 新 Codex 的近期优先级

1. 完成 Jazzy clean build 和 RSUSB/Python 3.12 适配；先通过静态/软件测试，再接硬件。
2. 复现三路30fps + IMU400Hz 10秒、60秒和90分钟验收，确认新主机无丢帧和温漂/计数复位。
3. 在不读取人工路径标签的条件下重跑稳定基线会话，核对轨迹数量、哈希、回环接受、Z跨度与失效状态。
4. 把最新实时 IMU 标定门禁改动纳入可回滚提交，避免 FAIL gyro matrix 再次进入运行时。
5. 继续 tracked-BRIEF/候选召回正负回归；完整隐藏动作矩阵达标前保持 `NOT_READY`。
6. 获取真实水平 Depth 正样本后再继续 Z 因子；没有正样本时保持禁用。

## 交付措辞

可以说：采集链路在旧机上通过零丢帧验收；部分回环候选达到毫米级闭合；实时 IMU 错标定根因已修正并有定向测试。

不能说：全部 SLAM 已产品完成、任意回环都 <1cm、世界 Z 已解决、Jazzy 已验证、ORB core 已完整迁移。

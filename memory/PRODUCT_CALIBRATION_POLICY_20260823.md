# 产品标定决策（2026-08-23）

## 当前正式选择

当前 `product-live` 使用混合标定，而不是把所有参数都换成一次自由拟合结果：

1. D405 双 IR 相机内参固定使用当前设备经 librealsense 导出的 Intel 出厂整流参数。
   当前 D405 `260322273737` 的 1280×720@30 参数为
   `fx=fy=647.519775`、`cx=638.534302`、`cy=369.768250`、畸变为零。
2. D405 IR1→IR2 外参从当前设备 factory profile 导出并做基线、刚体和独立极线验收。
3. 相机—IMU 空间外参及时间偏移必须对最终刚性装配进行两份独立联合标定。
   当前 STM32 装配共识为 `td=-0.009312 s`。
4. 历史冻结链仍保持 `td=-0.0117 s`；它只属于旧 TTL/旧装配历史复现，不得覆盖
   当前 STM32 `product-live`。
5. 当前及后续标准 `product-live` 不加载 30 姿态加速度矩阵；VINS 初始化在线估计
   bias。2026-08-23 同回放盲 A/B 否决了该候选：Z 改善不稳定且水平短边缩小约
   13.2%。30 姿态工具只保留为研发诊断，不再进入客户签发依赖、第5步 Kalibr 输入
   或第6步隔离候选。
   完整证据为
   `/home/robot/ego_vio_humble/.planning/pre_encoder_slam_acceptance_20260823/`
   `Z_CANDIDATE_092447_ACCN_0P1_AB_ZH.md`；正式配置复核时必须看到
   `imu.calibration: ""`。
6. 当前 VINS IMU 噪声沿用历史长时实测并经 SLAM 验证的运行值。新 Allan 结果是
   产品证据，不得未经 SLAM A/B 直接替换运行权重。

## 为什么不重新拟合 D405 内参

2026-08-22 对同一录制数据做过最终二进制 A/B：

- Intel factory 双 IR 内参：闭环 `7.959 mm`，低特征连续门 PASS；
- 2026-08-21 Kalibr 自由拟合内参：实时 `13.983 mm`、严格后处理
  `10.749 mm`，且连续低特征门 FAIL。

因此产品流程不再把“重新求相机内参”作为客户标定步骤。正式第 4 步改为：

```text
读取当前 D405 factory profile
→ 绑定序列号/固件/profile
→ 生成固定 camchain
→ 九宫格双 IR 零丢帧采集
→ 独立极线 P95 验收
```

原 `camera_bench_04_d405_stereo.sh` 只保留为售后/研发诊断工具。其自由拟合结果不会
自动进入产品运行配置，也不能覆盖 Intel factory 参数。

## 什么时候重做

- 换 D405：重新执行第 4、5、6 步；第 4 步仍是导出新相机自己的 factory 参数，
  不是用旧相机参数，也不是常规自由拟合。
- D405 与 IMU 相对安装改变：重做第 5、6 步。
- 换 IMU：重做第 1、2、5、6 步；仅在研发怀疑比例或交叉轴异常时做可选第3步。
- IMU 量程、滤波、采样率、STM32 时间戳链或 USB 桥改变：重做受影响的 Allan、
  时间偏移和端到端 A/B。
- 只改变整机相对机器人/地面的整体安装角、而相机与 IMU 刚性关系不变：相机内参
  和相机—IMU外参不失效；若世界坐标定义改变，只重做第 6 步。

## 客户入口

客户只运行五条必需独立命令，不使用 `run-all`：

```bash
./calibrate_01_imu_static.sh PRODUCT_ID
./calibrate_02_imu_noise.sh PRODUCT_ID
./calibrate_04_d405_factory.sh PRODUCT_ID
./calibrate_05_camera_imu.sh PRODUCT_ID
./calibrate_06_world_z.sh PRODUCT_ID
```

编号3的 `calibrate_03_imu_intrinsic.sh` 只供研发诊断。每个必需阶段必须独立完成
采集、自动求解、PASS/FAIL/BLOCKED 报告和下一步提示；不得跳过
前置阶段，也不得自动覆盖已签发运行配置。

## 已实现的可复用运行方式

- 新机先执行 `./calib_setup.sh`，注销重登后执行只读 `./calibrate_preflight.sh`。
- 中断恢复或人员交接执行 `./calibrate_status.sh PRODUCT_ID`；它只读 session 状态。
- 客户第4步只导出当前 D405 factory 参数并做独立极线验收，永远不自由拟合或写
  D405 NVRAM。
- 第5步输出两次独立 Kalibr 结果的刚体共识候选；旧 TTL `-11.7 ms` 不参与赋值。
- 第5步只有 live 两次采集同时通过相机和 IMU 健康门，并且第2步 IMU YAML、第4步
  camchain 的绑定 SHA-256 一致时才 `release_eligible: true`；无原始健康证据的离线
  数值复算固定 `BLOCKED`，不得喂给第6步。
- 第6步从 identity、第4、5步 PASS 报告生成 attempt 内隔离运行配置，通过
  `product-live` 的显式配置覆盖接口采 3 条平面和2条升降轨迹。默认
  `/home/robot/ego_vio_humble/config/product_live_stm32/` 不会被写入。
- 第6步候选中的 Kalibr IMU frame 到既有 VINS body frame 映射固定保存在
  `product_calibration/runtime_candidate.py`，其来源是 2026-08-22 四组 STM32
  联合标定共识与已验收 product-live 外参的可复算关系；每台产品的 `T_cam_imu`
  仍来自自己的第5步。

workflow v3 不静默迁移旧 session。旧 session 冻结输入保持原样并只读归档；若当前
workflow 哈希不同，必须换新的 session 根目录重新建档，不能修改 `_frozen_inputs`
或复制旧 PASS 绕过门禁。

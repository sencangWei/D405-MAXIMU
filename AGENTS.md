# ego_vio_calib_kit — D405 + KT-EX9-2 标定工具包

> 本文件为 Codex 交接导航。当前长期决策保存在仓库 `memory/`，换机后仍可读取。

## 这是什么
D405 相机 + KT-EX9-2 IMU(400Hz)的标定工具包: IMU 内参/零偏、IMU-相机外参与时间偏移(Kalibr)、采集与分析脚本。

## 关键事实(勿重复踩)
- **时间偏移按硬件链隔离**：历史冻结 TTL 链固定 `td=-0.0117s`；当前 STM32
  `product-live` 使用最终装配两轮联合标定共识 `td=-0.009312s`。两者都
  `estimate_td:0`，禁止跨链复制或重复补偿。
- **陈旧 7.36ms(08-04 标定)已废弃**: 用它 + 在线估计 = 双重补偿 → 发散 846m。看到任何脚本/配置里还有 7.36 就是过时值。
- **D405 内参产品规则**：双 IR 1280×720@30 固定采用连接设备的 Intel factory
  rectified 内参，不做客户自由拟合。2026-08-22 A/B 为 factory `7.959mm PASS`，
  新 Kalibr 内参 `13.983mm` 且低特征门 FAIL。第4步只导出/锁定 factory 参数并
  做零丢帧、基线与独立极线 P95 验收。
- D405 硬件关键事实: RGB↔左IR 基线 ≈0.01mm(伪双目退化)，双IR factory基线
  ≈18.079mm，Depth 单位 0.0001m。见 humble 的 AGENTS.md。

## SLAM 主工作区(接任务去这里)
VINS/ORB 的采集、回放、验证、精度工程全部在 `/home/robot/ego_vio_humble/`(有完整 AGENTS.md,含命令、铁律、已知 bug)。本仓库只做标定分析,SLAM 任务不要在这边做。

## 完整记忆位置(接手必读)
先读仓库内 `memory/README.md` 和它指向的当前决策文件。旧
`/home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory/` 在系统重装后
可能不存在，不能再作为唯一记忆来源。

# ego_vio_calib_kit — D405 + KT-EX9-2 标定工具包

> 本文件为 Codex 交接导航(2026-08-10)。深度调查记录在 Claude 记忆目录,需要细节时让 Codex 直接读。

## 这是什么
D405 相机 + KT-EX9-2 IMU(400Hz)的标定工具包: IMU 内参/零偏、IMU-相机外参与时间偏移(Kalibr)、采集与分析脚本。

## 关键事实(勿重复踩)
- **08-08 Kalibr 结果是时间偏移权威值**: `td = -0.0117s`(T_cam_imu 旋转 1.41°)。它被 bake 进 VINS 配置(`estimate_td:0` + `td=-0.0117`)和 ORB 回放(`--imu-shift-ms 11.7`)。
- **陈旧 7.36ms(08-04 标定)已废弃**: 用它 + 在线估计 = 双重补偿 → 发散 846m。看到任何脚本/配置里还有 7.36 就是过时值。
- D405 硬件关键事实: RGB↔左IR 基线 ≈0.01mm(伪双目退化),双IR 基线 ≈10mm,Depth 单位 0.0001m。见 humble 的 AGENTS.md。

## SLAM 主工作区(接任务去这里)
VINS/ORB 的采集、回放、验证、精度工程全部在 `/home/robot/ego_vio_humble/`(有完整 AGENTS.md,含命令、铁律、已知 bug)。本仓库只做标定分析,SLAM 任务不要在这边做。

## 完整记忆位置(接手必读)
`/home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory/*.md`(14 个内容文件 + MEMORY.md 索引,含每条结论的完整调查过程与数据)。**接手任何任务前,先 Read 这些文件获取完整背景,再动手**。

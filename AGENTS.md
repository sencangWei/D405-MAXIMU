# ego_vio_calib_kit — D405 + KT-EX9-2 标定工具包

> 本文件为 Codex 交接导航(2026-08-10)。深度调查记录在 Claude 记忆目录,需要细节时让 Codex 直接读。

## 这是什么
D405 相机 + KT-EX9-2 IMU(400Hz)的标定工具包: IMU 内参/零偏、IMU-相机外参与时间偏移(Kalibr)、采集与分析脚本。

## 关键事实(勿重复踩)
- **Docker2 数据必须使用 Docker2 正式配置**: `estimate_td: 0`、`td = -0.009109323s`，配置源为 `umi_docker2_product_1.0.0-20260829/docker2_release/formal_runtime_calibration/vins_config.yaml`，回放必须 `--imu-shift-ms 0`，不得再套用 `-0.0117s`。
- `td = -0.0117s` 是非 Docker2 的旧通用标定记录；只允许用于明确指定该标定版本的数据，不能用于 Docker2 采集或报告。
- **陈旧 7.36ms(08-04 标定)已废弃**: 用它 + 在线估计 = 双重补偿 → 发散 846m。看到任何脚本/配置里还有 7.36 就是过时值。
- D405 硬件关键事实: RGB↔左IR 平移约0.017mm(几乎共光心，不可当双目),本机双IR出厂基线 18.083254mm,Depth 单位 0.0001m。见 humble 的 AGENTS.md。

## SLAM 主工作区(接任务去这里)
VINS/ORB 的采集、回放、验证、精度工程全部在 `/home/robot/ego_vio_humble/`(有完整 AGENTS.md,含命令、铁律、已知 bug)。本仓库只做标定分析,SLAM 任务不要在这边做。

## ★ 2026-09-22 交接包(MASt3R 深度融合精度工程,先读这个)
`/home/robot/ego_vio_humble/reports/codex_handoff_20260922/HANDOFF.md`
—— 09 月整条 MASt3R 融合精度工程的完整交接:用户逐字意图、现役产线拓扑、**唯一卡点**、
**已封死的 14 族杠杆清单(别重跑)**、「别引用错的」陷阱、**会静默零产物的操作坑**、下一步方向。
同目录 `memory/` = 45 个记忆文件快照;`transcript_main/` = 用户原话逐字 + 助手回合 + 命令日志。

**三句话小结**:①**「09-14 有好结果可复原」前提已被盘上证据否掉**(09-14 生产 0/10 PASS,
比现役更差),别再往 09-14 复原;②现役产线**已跑通**,只卡精度门 `ate_translation_max`,
缺口 = 约 27 帧缓变位置偏移块;③**14 族杠杆已逐族实测封死**,误差与 MASt3R 上游相关(+0.425)、
与 VINS 无关(−0.006)、与误差同频带 ⇒ 位置域后处理原理上无解、22 臂共模 ⇒ 改单臂权重必然无效。

**证据主体**:`/home/robot/ego_vio_humble/reports/mast3r_g2_validation_20260919/rerun_tail_v2_20260920/README.md`
(3027 行,按时间追加;⚠ 早期章节结论可能已被后期推翻,**以每节末尾的「★ 更正」为准**)。

## 完整记忆位置(接手必读)
`/home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory/*.md`
(**45 个** .md = `MEMORY.md` 索引 + 44 个内容文件,含每条结论的完整调查过程与数据)。
**接手任何任务前,先 Read `MEMORY.md`(索引),再按需下钻**,别只读本文件。

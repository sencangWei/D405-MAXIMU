# ego_vio_humble — D405 双IR + 外置 IMU SLAM 项目

> 本文件由 Claude Code 会话记忆(2026-08-10)蒸馏而来,供 Codex 无缝接手。
> 深度细节见文末"完整记忆位置",需要时可让 Codex 直接读那些 .md。

## 项目一句话
RealSense D405(左IR+右IR 真双目 1280×720@30, 原始 db3)+ KT-EX9-2 IMU(400Hz 串口二进制)的 VIO/SLAM 精度工程。主路径 VINS-Fusion fork(ROS2 Humble),副路径 ORB-SLAM3 RGB-D-Inertial fork。用户唯一要求:**精度最高**。

## 环境与命令(必守)
- 工作目录:`/home/robot/ego_vio_humble`(session 一律传完整路径,裸名报"没有 db3"→轨迹0)
- 跑测试必须 `bash -c 'source /opt/ros/humble/setup.bash && source /home/robot/ros2_ws/install/setup.bash && python3 ...'`(只 source 前者 → Package not found;直接 python3 → rclpy 缺失)
- **测试前必 pkill 清场**:`pkill -9 -f vins_fusion_ros2_node; pkill -9 -f replay`(残留节点同时订阅 /imu0、/odometry 会污染 → 数值爆炸误判回归)
- VINS/回放日志默认 `/tmp/vins_t.log`、`/tmp/replay_t.log`;可用 `VINS_LOG`、`REPLAY_LOG` 指定每轮独立路径。
- VINS 测试: `REPLAY_SCRIPT=scripts/replay_db3_to_ros2.py VINS_OUT=/tmp/x.csv python3 scripts/_test_vins_dynamic.py recordings/<session> 1.5 1.0 0 0`(参数=skip-s, rate, imu-shift-ms, imu-align-s;默认已修,零参也正确)
- 录后即验: `python3 scripts/verify_recorded_session.py recordings/<session>`(原始 db3 生产母版,单轮~70s;判定 优≤3cm/边界≤25cm/坏跑,返回码 0/1/2)。FFV1 暂停生产使用,仅在磁盘受限时用 `--ffv1` 继续验证。
- ORB 测试: `python3 scripts/_test_orb_dynamic.py recordings/<session> rgbd`
- **git 身份**: 本仓库已有本地 `user.name=JO-ara-dev`、`user.email=1482268287@qq.com`;不要依赖全局配置。

## 硬件关键事实
- **双IR 基线 ≈10mm** → 视差 ≈0px @1-2m,深度约束弱但 stereo 可用。**RGB↔左IR 基线 ≈0.01mm**(伪双目完全退化,禁止把 RGB+IR 当 stereo)。
- Depth 单位 = 0.0001m;ORB RGB-D `RGBD.DepthMapFactor: 10000` 正确。
- 左IR 内参(720p PinHole): fx=fy=647.52, cx=638.534, cy=369.768, 畸变 0。
- IMU 二进制 40B `<dI7f`: ts(double,**单位是秒**), counter(uint), gx,gy,gz,ax,ay,az,temp。400Hz。

## 两套 SLAM 路径状态
1. **VINS 双IR**(主): 稳定基座已打 tag。
   - `d405-mono-rgb-stable-20260809`(commit e7824ac): 6 文件修复集(RGB mono 配置基座),推荐 mono 配置 `config/d405_rgb_ir_imu/d405_rgb_mono_config.yaml`(num_of_cam=1, cam0=RGB);`d405_rgb_ir_imu_config.yaml` 是双IR 配置。
   - `vins-dual-ir-stable-20260810`(commit e239352): 双IR 经验最优点配置,**别再调参**(26+ 变体全中性/更差,闭环可达 ~1.4cm)。最优值: `td=-0.0117`(estimate_td:0)、`iter 8`、`parallax 10`、`max_cnt 400`、`min_dist 20`。坏值: td=0 炸(122.8cm)、estimate_td:1 不生效、max_cnt 250 炸/600 更差、min_dist 15 不稳(1/3 跑 51.5cm)。
   - 纯 origin 8/8 全败,必须用 fork。**改配置后须 rebuild + pkill 清场再复验**(记忆 vins-fork-state)。
2. **ORB RGB-D-Inertial**: tag `orb-rgbd-inertial-stable-20260809`(commit d431b3a,fork 在 `/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3`)。已跑通,世界帧重锚定后 z_mean 跨次极差 0.0152m。小回路(<1m)VINS 双IR 明显占优。
   - **负结果(勿重蹈)**: Allan 实测噪声(更小)→ IMU 约束强 361 倍盖过深度约束 → z 垂直漂 3.48m,已 revert;BA 迭代数加倍(z_mean 极差 8× 恶化)→ 已 revert。**基线即最优,别再压噪声/迭代数**。RGB-D 模式深度是尺度真值,视觉主导正确。

## 铁律(破坏必炸/发散,勿动)
1. **~90° 重力 bake 成对**: 回放 `replay_db3_to_ros2.py` / `replay_mp4_to_ros2.py` 的 `publish_imu` **硬编码** IMU 旋转 R_rep≈91.42°(重力 y→z)。VINS/ORB 配置的 `body_T_cam0`/`T_b_c1` bake 了等量 ~90° 与之自洽。**必须成对保留,缺一即崩/发散**。"57°"是误记。
2. **时间偏移单次补偿**: VINS = 回放 `--imu-shift-ms 0` + 配置 `estimate_td:0` + `td=-0.0117`(08-08 Kalibr)。ORB = 回放 `--imu-shift-ms 11.7`。**双重补偿必发散**(陈旧 7.36 + 在线估计 = 846m 爆炸;ORB 7.36 慢回路 = 318m)。ORB shift 扫描实测: 7.36→318m / 10.0→45cm / **11.7→最佳3.5cm** / 13.0→22.5cm,`_test_orb_dynamic.py` 默认已修 `ORB_SHIFT_MS=11.7`。
3. **关键录制必须旧 db3 管线**: `capture_d405_720p_rgb_stereo_ir.py` 生成的原始 db3 是生产母版。`bag_to_ffv1.py` 仅保留为未来磁盘受限时的无损候选。inline mkv 管线(`capture_d405_mp4_inline.py`)会周期性特征塌缩 → 尺度膨胀/闭环差(见下)。有损 HEVC 不可用于 SLAM。
4. **系统必须保持 30fps**:用户要求录制、回放、视觉前端、VINS 后端和轨迹输出都以 30fps 为目标,禁止用固定15fps换精度。现有 `inputImageCount%2==0 || featureBuffer.empty()` 是历史自适应降载逻辑,尚未满足确定性30fps;后续必须在完整30fps下解决初始化稳定性并验收实际处理率。
5. **图像 QoS**: IMU 深度 2000、图像 100(防处理延迟丢帧);回放图像 RELIABLE 而非 BEST_EFFORT。
6. 回放改代码后必须重跑 A/B 验证(字节无损 ≠ 链路正确)。

## 已知 bug 与修复(勿重复踩)
- **replay_mp4 3 大 bug(已修)**: ① 生成器闭包晚绑定按引用捕获 key → 双流全标 ir_right;② rclpy `msg.data=` setter O(N) ~32ms/帧 → 拖慢回放,须预分配 array + `msg.data[:] = array.array("B",buf)` 原地写;③ `--imu-align-s 0` 误触发 auto 对齐 +357ms → 尺度爆炸,已改为仅字符串 "auto" 触发。
- **初始化守卫(已实施)**: 鲁棒 solveGyroscopeBias(Huber IRLS, δ≈1.4°, 3 迭代) + 检查**总 bg**(非增量) + 拒绝时回滚 + 阈值 0.01 rad/s。日志 `[DIAG-BG]`<0.01=干净,`[INIT-GUARD] count=N`=重试次数。230503 类手占满画面的动态场景根本性不可解,守卫会诚实报失败(0 点),需要 dynamic-VIO 才行。
- **选验证/压测会话**: 必须用静止/微动会话(如 111538、06_192749 有 60s 静止段),**不要用手部动态会话**——手占满画面的动态场景在初始化+滑窗两阶段都根本性失败,任何参数都救不回来。
- **进程卫生**: `ros2 run` 的 wrapper 被杀后 child 节点存活成僵尸 → 必须 `os.killpg` 或 `pkill -9 -f vins_fusion_ros2_node`。出现 1e18m 级数值先查残留进程,别怀疑算法回归。
- **Umeyama 转置 bug(分析脚本)**: 正确实现 `H=Pcc.T@Qcc; R=Vt.T@U.T`。对齐一律用 `/tmp/umeyama_correct.py`。此 bug 曾误判 VINS 深度反转(实际 ATE~1.5cm 正常)。
- **odom TOCTOU 数据竞争(未修)**: `/odometry` 发布 check()/get() 非原子,可能混入异常帧(1e35m)。轨迹统计需过滤 >1m 帧。

## 最近结论(2026-08-11)
- **2026-08-11 DDS 回放修复**:`25243c1` 在发布前等待 cam0/cam1/imu 全部匹配,111538 跨会话从 2/3 初始化成功改善到 3/3,每轮输入严格相同(2396图像/15995 IMU)。固定15fps实验 `059f243` 虽降低Z残差,但违反用户30fps要求,已由 `6083f2f` 完整撤销,禁止作为交付方案。
- **采集管线 A/B**(84×52 矩形,各 3 轮): 旧 db3→FFV1 闭环 **2.1/2.4/3.2cm** 稳定;新 inline mkv **14.4/33.7/73.8cm** + 尺度膨胀 1.2-2×。隔离实验(旧会话走 db3 原始路径=2.1cm)证明差异锁定在**采集方法**,与编码/回放路径无关。已排除: IMU 数据、时间戳、帧对齐、丢帧、skip 不对称、亮度/纹理、处理节奏。机制 = inline 会话特征周期性塌到 0。
- 生产数据率: FFV1 双IR ≈11.35MB/s → 128GB ≈3.1h。
- 交货缓解: 更大回路(边长 1.5-2m)/双份录制/录后即验(verify_recorded_session.py)。
- **FFV1 本身已被大样本洗清(勿再怀疑格式)**: 36 轮 A/B(raw/FFV1/ffv1raw 各 12)Fisher 双尾 p=1.000 无显著差异,优率 83/83/75%。坏跑(~8-12%)是 53×43cm 慢回路固有 run-to-run 方差(raw 自己 1/12 发散),**不是 FFV1 编码问题**。唯一有实锤的管线差异是 2026-08-10 A/B 的**采集方法**(旧 db3 管线 vs 新 inline mkv)。

## 完整记忆位置(接手必读)
Claude 会话记忆(`/home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory/*.md`,共 13 个内容文件)含上述每条的完整调查过程、数据表、验证命令。**接手任何任务前,先 Read 这些文件获取完整背景,再动手**(本文件是浓缩版,细节以原始记忆为准)。文件清单:
`capture-pipeline-ab-result.md` `d405-hardware-facts.md` `dual-ir-divergence-rootcause.md` `orb-replay-time-offset.md` `orb-rgbd-inertial-status.md` `recording-format-ffv1-lossless.md` `vins-230503-rootcause.md` `vins-alignment-bug.md` `vins-config-optimal.md` `vins-fork-state.md` `vins-init-guard.md` `vins-process-hygiene.md` `vins-replay-args.md`

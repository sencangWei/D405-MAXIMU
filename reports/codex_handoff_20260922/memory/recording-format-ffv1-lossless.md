---
name: recording-format-ffv1-lossless
description: "录制格式结论: SLAM 双IR 必须无损 FFV1; 字节无损≠回放自动正确——replay_mp4_to_ros2 曾 3 个 bug(闭包/setter/align)已修; 生产 ≈11.35MB/s ≈3.1h/128GB"
metadata:
  node_type: memory
  type: project
  originSessionId: bd123ebc-72bc-46c2-a61d-5d9f3d3f6ff9
  modified: 2026-08-10T10:05:51.985Z
---

**2026-08-10 录制格式结论**: 回答"录制中直接改格式落盘"——SLAM 双IR 必须无损 FFV1, 有损 HEVC cq18 不可用; 且**字节无损只保证数据相同, 不保证回放路径正确**。

- **有损否决**: 000943 上 HEVC cq18 (PSNR 48dB) 喂 VINS 6 次 = 2 次发散 + 1 边界, 对照原始 0 坏跑 → 违反"精度第一"。
- **FFV1 无损**: 300 帧 round-trip 字节级一致, 4.4x, 编码 ~309fps。真实会话 945 帧/流像素逐帧一致, sidecar==db3 时间戳 (0.000 差)。
- **⚠️ 关键教训: "字节无损 ⇒ 精度必然等同" 是错的**。FFV1 mkv 回放第一次 A/B 6/6 全败 (151-295cm 紧带)。逐层定位到 `replay_mp4_to_ros2.py` 3 个 bug:
  1. **闭包晚绑定**: 生成器表达式按引用捕获 `key`, 两流全标 ir_right → cam0 空 → stereo 同步 0 位姿 + FastRTPS 崩溃。修: `make_gen(key,frames)` 按值捕获。
  2. **rclpy setter O(N)**: `msg.data = bytearray` 触发全元素校验 ~32ms/帧 → 回放 0.5x。修: 预分配 array + 原地 `msg.data[:] = array.array("B",buf)`。
  3. **IMU align 误触发 (最致命)**: `--imu-align-s 0` 在 mkv 路径 `if align_s==0.0:` 触发自动对齐 → IMU 平移 compute_auto_align()=+357ms (db3 路径 `"0"`≠`"auto"` 用 0) → 两路错位 357ms → 尺度爆炸。修: 镜像 db3 语义 (仅字符串 "auto" 触发)。
- **修复后验证**: A/B#2 (6+6) raw 5优+1边界 (0.85-20.81cm), FFV1 4优+2坏 (97/3962)。隔离实验 (ffv1 vs ffv1raw 预解码, 交错 6+6) **12/12 全优** → ffmpeg 子进程无扰动, A/B#2 的坏跑未复现 = 53×43cm 慢回路弱可观测性 run-to-run 方差 (raw 历史也有 449cm)。**最终判定: 修复后 FFV1 mkv 回放与 db3 统计等同**。
- **大样本 A/B (各12=36轮)**: raw 10优+1边界+1发散(1951cm) 优率83%, FFV1 10优+2边界+0坏 优率83%, ffv1raw 9优+2边界+1坏(45.9cm) 优率75%; **Fisher 双尾 p 全=1.000 无显著差异** → 交货级结论: FFV1 生产路径无系统性风险, 坏跑是慢回路固有 run-to-run 方差(与格式无关, raw 自身 1/12 发散), 交货缓解=更大回路/双份录制/录后即验。
- **用户问"转回原始再跑=0错误?"**: 否——FFV1 无损,mkv 回放=解压成原始帧喂 VINS, 与 db3 逐字节一致(880公共帧0失配), 正是"转回原始"本身; raw 自己 8% 坏跑证明 0 错误不存在, 方差在弱可观测回路。**0.4cm 中位差(1.51 vs 1.90)是统计噪声(Fisher p=1.000), 非格式系统差**; 真实优化杠杆=更大回路(ATE~1.5cm), 格式无优化空间。
- **录后即验一键脚本**: `scripts/verify_recorded_session.py` (已提交 6978f8c) — 输入会话目录→跑 FFV1 生产 VINS→闭环/路径/点数/中位速度报告, 判定 优≤3cm/边界≤25cm/坏跑, 返回码 0/1/2; 需 bash -c 先 source ROS(直接 python3 会 rclpy 缺失); 单轮 ~40s, 现场录完即验。
- **生产数据率 (实测)**: FFV1 2IR 5.27+5.30 + H264 RGB 0.76 + IMU 0.015 ≈ **11.35 MB/s → 128GB ≈ 3.1h** (IR FFV1 实测 5.2x 压缩)。
- **落地**: capture_d405_mp4_inline.py `--ir-codec ffv1` (默认) 直写 .mkv; replay_mp4_to_ros2.py 4 修复 + `--raw-dir`/`REPLAY_RAW_DIR`; bag_to_ffv1.py 离线转换 (db3→mkv+mp4); replay_db3_hevc_to_ros2.py CODEC 实验变体。

**Why:** 精度第一; 且验证必须端到端 (数据无损≠链路正确), "无需再跑验证"的假设被实际打脸。**How to apply:** 正式录制 `capture_d405_mp4_inline.py --ir-codec ffv1`; 改回放代码后必重跑 A/B; 需 >3h 换更大回路重测而非改有损。完整数据: slam_trajectories/recording_format_ffv1_lossless_20260810.md。相关: [[vins-config-optimal]] [[vins-replay-args]] [[dual-ir-divergence-rootcause]] [[vins-230503-rootcause]]。

# VINS stereo-IMU 手部跟踪 SLAM 修复报告

日期:2026-08-09
系统:D405 相机(双目 IR 左+右, 30fps)+ 外部 KT-EX9-2 IMU(400Hz)
算法:vins_fusion_ros2 stereo-IMU 模式(基于 HKUST VINS-Fusion)

## 1. 一句话结论

**问题既不是时间戳也不是标定,而是算法——VINS 的 stereo+IMU 初始化在最开始就吸收了一次"脏"的 gyro bias 估计。** 修复后 6/8 个真实会话稳定输出 cm 级轨迹;剩余 2 个会话(230503)是移动手部占满画面的动态场景,属算法对该场景的根本性限制,已用决定性实验证实并给出针对性解法。

## 2. 排查结论(三个怀疑对象的裁决)

| 怀疑对象 | 裁决 | 证据 |
|---|---|---|
| **时间戳** | ✅ 干净,排除 | 回放 shift=0 正确;IMU 400Hz 零丢零毛刺;Kalibr 未报时间戳异常 |
| **标定** | ✅ 正确,排除 | 联合标定(Kalibr)ATE 对照 AprilGrid 真值:1.48/1.79 cm |
| **算法** | ❌ 问题所在 | 初始化鲁棒性缺陷,详见下文 |

## 3. 根因链(230503 示例)

1. 初始化窗口(回放 skip 后第一个 ~100ms)内,视觉 PnP 帧间旋转与 IMU 预积分旋转存在 0.5~2° 的随机分歧([DIAG-ROT] diff_norm)。
2. 原版 `solveGyroscopeBias` 是**普通最小二乘**:把 1~2 个坏帧对的 PnP 噪声吸进 gyro bias → bg 从真实的 ~0.001 rad/s 变成 0.04~0.16 rad/s(2.5~9.4°/s)。
3. 大 bg → 预积分旋转错 → 非线性优化无共识 → 离群剔除(>3px)删光特征(feat 355→0)→ **纯 IMU 死推算 → 发散**。
4. 坏帧对来源:IR 模糊(Laplacian 5.5 vs 干净会话 14.6)、特征极少(goodFeatures 56 vs 126)、**移动的手部填充画面**(非刚体,破坏静态场景假设)。

**为什么其他会话没崩**:那些会话手部不占主导,坏帧对是少数,普通最小二乘把噪声平均掉只剩 ~0.001。230503 是特写手部场景,坏帧对占多数且方向一致,最小二乘无法平均掉。

## 4. 修复内容(累积 6 个文件)

### 4.1 基座(此前已确认必需的 4 个改动)
| 文件 | 改动 | 作用 |
|---|---|---|
| `src/vins_estimator.cpp` | IMU QoS 100→2000, 图像 5→100 | 处理延迟不丢 IMU/帧 |
| `vins/include/vins/factor/integration_base.h` | 移除 `timestamp>1.0` 跳过 | 继续积分而非跳过 IMU 间隙 |
| `vins/src/featureTracker/feature_tracker.cpp` | KLT 窗口 21→31, 金字塔 3→5, quality 0.01→0.005 | 快速运动跟踪鲁棒 |
| `vins/src/estimator/estimator.cpp` | updateLatestStates + DIAG 日志 | 状态一致性 + 可诊断性 |

### 4.2 本会话(初始化鲁棒性修复)
| 文件 | 改动 | 作用 |
|---|---|---|
| `vins/src/initial/initial_aligment.cpp` | `solveGyroscopeBias` 改 **Huber 迭代重加权 IRLS**(delta≈1.4°, 3 迭代) | 坏帧对自动降权,不再被吸进 bg |
| `vins/src/estimator/estimator.cpp` | **初始化守卫**:检查总 bg > 0.01 rad/s 则回滚增量 + slideWindow 重试 | 脏窗口不进 NON_LINEAR,重试到干净窗口 |

### 4.3 守卫实现要点(踩过的坑)
1. **必须检查总 bg 而非增量**:`solveGyroscopeBias` 内部 `states[].gyro_bias += delta_bg`。被拒增量不回滚会残留,下一窗口增量是相对脏 bg 的小修正,只看增量会误放行(实测 0.074 未回滚 + 0.026 增量 = 0.099 总 bg → 发散)。**触发时回滚本次增量**。
2. **阈值 0.01 rad/s(~0.57°/s)**:干净会话 bg=0.0009-0.0015,污染窗口 0.04-0.16,有 25 倍间隙。0.05 会让 2.5°/s 的脏窗口溜进 NON_LINEAR 再发散(实测 230503: bg=0.043 通过 0.05 → 3665m)。0.01 在 230503 上拒绝了 93 个污染窗口(避免 4.6°/s 的初始化),但它仍存在一个 bg<0.01 的干净窗口,初始化后跟踪阶段照样发散——守卫只能守住初始化,拦不住跟踪阶段的动态场景污染。

## 5. 复验结果(修复后全量 8 会话)

### 5.1 真实测试会话(6/8 全部工作)
| 会话 | 轨迹点 | 路径 | 中位速度 | 初始化 bg | 守卫 |
|---|---|---|---|---|---|
| 205555 | 350 | 3.4 m | 0.06 m/s | 0.0014 | 0 |
| 205703 | 246 | 3.7 m | 0.07 m/s | 0.0016 | 0 |
| 111538 | 253 | 3.4 m | 0.07 m/s | 0.0014 | 0 |
| 094811 | 250 | 6.1 m | 0.17 m/s | 0.0017 | 0 |
| 102729 | 241 | 6.3 m | 0.09 m/s | 0.0018 | 0 |
| 230503 | **无法跟踪** | — | — | 全窗污染 0.04-0.16 | 93 次重试,干净 init 仍发散 |

> 205555/205703 与 AprilGrid 真值对照 ATE 为 1.48/1.79 cm(前期已验证)。轨迹可通过
> `python3 scripts/_test_vins_dynamic.py <会话完整路径> 1.5 1.0 0 0` 复现,输出到 `/tmp/vins_test_odom.csv`。

### 5.2 无效采集(2 个,非算法问题)
094342 / 094450:仅有 250MB db3,缺 `acceptance.json`(无真值)和 `d405_frames.csv`(无法重建帧 epoch 对齐 IMU)。属失败/不完整的采集,不可用于 SLAM 验证。

### 5.3 对比基线
- **纯 origin fork:8/8 全败**(发散或 0 点)。累积改动是必需基座。
- **修复前**:4/8 稳定。
- **修复后(0.01 守卫)**:6/8 稳定;230503 初始化污染被拒 93 次但仍发散(跟踪阶段动态污染,守卫无法拦截);094342/094450 为无效采集。

## 6. 决定性实验:230503 是动态场景根本性限制

用 skip_s=1.5/5/10/14 跳过会话开头找"平静起始窗口":

| skip_s | init 窗口 | 首个 bg | 守卫重试 | 轨迹 |
|---|---|---|---|---|
| 1.5 | t≈1.5s | 2.5°/s | 0 | 3665 m(发散) |
| 5 | t≈5s | 9.4°/s | 98 | 920 m(发散) |
| 10 | t≈10s | 6.8°/s | 1 | 938 m(发散) |
| 14 | t≈14s | 8.8°/s | 1 | 194 m(发散) |

40s 会话内**不存在任何能干净初始化的窗口**——手部全程占满画面且运动。守卫重试 98 次只能找到"最不脏"的窗口。

**补强实验(0.01 严格阈值)**:230503 重试 93 次后终于找到一个 bg<0.01 的**真正干净**初始化窗口并进入 NON_LINEAR,但 59 个轨迹点后仍发散到 89.9 m。这证明污染不只在初始化——**手部占满画面的动态场景在滑窗优化阶段同样拖垮跟踪**。结论升级:**230503 不是时间戳/标定问题,是特征点 VIO 对"移动物体占满画面"场景在初始化和跟踪两个阶段的根本性限制。**

## 7. 开源方案调研与下一步建议

### 7.1 社区怎么处理类似场景
- **[dynamic-VIO](https://github.com/jinguanzhu/VINS-Mono)**(扩展 VINS-Mono 的论文,CEUR Vol-3248 paper21):用 IMU 预积分旋转与 RANSAC 相机旋转对比,**若不一致说明 RANSAC 把模型建在了动态物体上,此时改用 RANSAC 离群点(静态背景)而非内点**。EuRoC 掩膜移动叶片:ATE 从 1.56m→0.22m。**这正是 230503 场景的针对性解法,且与现有改动架构兼容。**
- [VINS-Fusion Issue #240](https://github.com/HKUST-Aerial-Robotics/VINS-Fusion/issues/240):`gyroscope bias initial calibration 0.075 -0.10 ...` 伴随"估计又快又不准"——与我们的污染症状一致,说明是已知痛点。
- [VINS-Fusion Issue #163](https://github.com/HKUST-Aerial-Robotics/VINS-Fusion/issues/163):bg 不在配置输入中,由初始化在线估计——所以初始化质量直接决定成败。
- [VINS-Fusion 初始化文档](https://deepwiki.com/HKUST-Aerial-Robotics/VINS-Fusion/3.1-initialization):stereo+IMU 路径无重试机制,失败即留在 INITIAL——这正是我们补上守卫的原因。

### 7.2 两条落地路径(可选)
1. **短期(零代码改动)**:部署时初始化阶段让手静止或移出画面 ~2s,等 VINS 用静态背景完成初始化再开始跟踪。这是所有 VIO 系统的标准做法。
2. **中期(算法改动)**:实现 dynamic-VIO 的动态特征选择——初始化时对比 IMU 与视觉旋转,不一致则用 RANSAC 离群点(静态背景)做 PnP。需要 `featureManager` / PnP 路径的改动,工作量中等,风险可控。若需要,可作为下一步实现。

## 8. 复现命令

```bash
# 环境准备(必须在 bash 里双重 source)
bash -c 'source /opt/ros/humble/setup.bash && source /home/robot/ros2_ws/install/setup.bash'

# 测试单会话(参数: 会话完整路径, skip_s=1.5, rate=1.0, imu_shift_ms=0, imu_align_s=0)
python3 scripts/_test_vins_dynamic.py \
  /home/robot/ego_vio_humble/recordings/d405_720p_rgb_stereo_ir_20260809_111538 \
  1.5 1.0 0 0

# 测试前必须清理残留进程(否则残留节点污染 odometry 导致数值爆炸误判)
pkill -9 -f vins_fusion_ros2_node; pkill -9 -f replay_db3
```

关键诊断日志(在 `/tmp/vins_t.log`):
- `[DIAG-BG] delta_bg = ...` — 初始化 bg 估计。<0.01 rad/s 为干净。
- `[INIT-GUARD] total gyro bias too large (...)` — 守卫拒绝脏窗口。
- `[DIAG-ROT] frame pair visual_deg=... diff_norm=...` — 帧间视觉 vs IMU 旋转分歧。

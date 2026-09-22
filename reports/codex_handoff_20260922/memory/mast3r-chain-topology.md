---
name: mast3r-chain-topology
description: "★ MASt3R 生产链是【一条链】不是四条平行链: frames→[6/8]×标量→imu_metric→[7/8]图优化→graph→[8/9]融合→fused; [6/8] 恒等于纯缩放(23/23 cell 残差0.0000mm); rigid_align 残差【依赖绝对尺度】, 别把它当形状误差; ★★09-20 更正: 「产出不是度量轨迹(偏大5.6%)」是**坐标系错配假象**(相机系 csv 比 body 系真值, 而 camera_to_body 非刚性、杆臂29mm逐帧减 ⇒ 半径缩6.1%); 过 camera_to_body 后 graph 倍率=1.001, **本来就是度量的**"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-20T07:22:15.737Z
---

## 拓扑（读 `scripts/mast3r_slam_precision_workflow.sh` 确认）

```
[6/8] align_mast3r_scale_with_imu.py        --trajectory trajectory_frames.csv
                                            --output     trajectory_imu_metric.csv
[7/8] fuse_mast3r_stereo_imu.py             --trajectory trajectory_imu_metric.csv
                                            --output     trajectory_graph.csv
[8/9] fuse_docker2_mast3r_complementary.py  --mast3r     trajectory_graph.csv
                                            --output     trajectory_fused_unsmoothed.csv
[9/9] smooth_pose_trajectory.py             --input fused_unsmoothed --output trajectory_fused.csv
```

⇒ **`imu_metric` 是 `graph` 的输入，`graph` 是融合的输入。** 四个 `trajectory_*.csv`
**不是四条平行链**（我曾当兄弟处理，据此得出"②→③ 恶化"——那是**把管线读反了**）。

> 注意：`mast3r_fusion_original_v1_workflow.sh` 是**另一条数据流**（融合直接吃
> `stereo_bidir_imu_orientation`），与现役不同。别拿它推现役。

## 两条实测铁律

1. **`[6/8]` 的全部作用 = 乘一个标量。** `imu_metric == frames × k`，
   **23/23 个 cell 逐点残差 < 0.01mm（全局最差 0.0000mm）**。它**不做任何形状工作**。
   `imu_scale_report.json` 的 `scale` 字段就是那个 k（0.30–0.69）。
2. **`rigid_align` 不估尺度 ⇒ 其残差强烈依赖输入的绝对尺度。**
   原始帧轨迹比真值大 2.8 倍，那个看似"形状误差 810mm"的数**绝大部分是"太大"贡献的**。
   量形状必须**先按相似变换把尺度归到 1.0 再 `rigid_align`**。
   （09-14 b3/g2/tight 尺度归一后：raw 28.94 / imu_metric 28.94 / graph 21.19 / fused 11.02，单位 mm max。）

## ★★ 09-20 更正：`[6/8]` 的产物**本来就是度量的**（旧说法是坐标系错配）

~~对真值做相似对齐，23 个 cell：中位 0.9466，范围 0.9045–1.0782，20/23 偏大。~~
**这条作废。** 成因：把**相机系**的 `mast3r/trajectory_*.csv` 直接比 **body 系**的
`lighthouse_body_ground_truth.csv`。两者之间差 `camera_to_body`，而该变换**不是刚性的**：

```python
body_positions = positions - body_rotations.apply(body_t_camera[:3, 3])
```

杆臂只有 **29 mm**，却是**逐帧按姿态**减掉的 ⇒ 轨迹半径 **6.1% 收缩**
（172.29 → 161.78 mm，`similarity_align` 残差 12.2 mm 的**非刚性形变**）。

**验算**：`0.9401 / 0.9383 = 1.0019` ≈ fused 实测 `1.0016`
⇒ **过完 `camera_to_body`，图优化轨迹的倍率就是 1.001**（1.001/1.023/0.989/1.012/1.004…）。

⚠ `camera_to_body` 几何上**是对的**（`T_world_body = T_world_cam0 · T_body_cam0⁻¹`，逐项对过），
所以这不是 bug 而是**评测口径错配**。**凡拿 `mast3r/trajectory_*.csv` 比 body 系真值的
地方，都必须先过这一步**（含 `rigid_align` 的 ATE：v1 阶段预算因此把 graph 的 ATE
从 2.6mm 虚报成 17.0mm）。

**同一条路径的副产品**：`write_trajectory(…, mast3r_rotations)` 里的 `mast3r_rotations`
已被 `camera_to_body_with_body_orientation_prior` **重绑成对齐后的 VINS 姿态**，
视觉姿态被整个丢弃 ⇒ 这才是「fused 姿态 ≡ VINS 姿态 × 常量旋转」的**代码级原因**。

⇒ 与 [[codex-gate-blindspot-history]] 里 Codex 定的策略「尺度由双红外负责」相关；
且本语料上 docker2 与 MASt3R 的尺度只差 **0.44%** ⇒ MASt3R 学到的度量尺度在本台架上是准的。
**这是现状记录，不是改法建议**——改 `--metric-scale-mode` 是通用策略变更，须用户裁决。

## 顺带否掉的假设

`[6/8]` 的 `--td-s -0.009109323` 是**硬编码常量**。扫 ±40ms 结果只在 52.90–55.07mm，
**−9.11ms 已是最优**，任何 td 都救不回 ③ 超过 ②；用 −9.11 重跑**逐位复现**盘上 ③ ⇒ 台架可信。

## 证据

`reports/mast3r_g2_validation_20260919/spike_attribution_20260919/chain_topology.py`
（+ README.md）可复现以上全部数字；只读产物 CSV + 官方评测器，不跑管线。

相关: [[spike-attribution-20260919]]、[[fusion-scale-gate-umeyama-check]]、[[report-fusion-not-vins-accuracy]]

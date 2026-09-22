---
name: mast3r-frontend-regression-20260918
description: "【已破案】MASt3R 前端静止伪造运动/门不过 = 启动器 mast3r_slam_precision_workflow.sh 的 --calib 循环门控(拿 config[use_calib] 当传参前提,而该值正由 --calib 反向赋值→恒假);三臂实验证明加 --calib 即逐字节复原 09-16 好运行;修复已落地"
metadata:
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-18T18:21:40.166Z
---

## 根因(2026-09-19 破案,三臂实验证实)

**`mast3r_slam_precision_workflow.sh` 的 `--calib` 传参门控是个循环条件。**

```bash
# 坏(原第 102 行):config_uses_calibration() 内部 load_config 后读 config["use_calib"]
if config_uses_calibration "$CONFIG"; then
    mast3r_args+=(--calib "$output/dataset/calibration.yaml")
fi
```
而 `main.py:349-360` 里 **`--calib` 正是把 `config["use_calib"]` 置 True 的唯一途径**。
所有配置文件的 `use_calib` 都写着 `False` ⇒ 门恒假 ⇒ `--calib` 永不传入 ⇒ **前端静默丢掉 D405 相机模型**。

`use_calib=False` 的连锁后果(全部实测):`K=None` → `get_points_poses` 不调 `constrain_points_to_ray`
→ 点图停在 MASt3R `X_canon` 而非真实射线 → 后端走 `solve_GN_rays()` 而非 `solve_GN_calib()`
→ `track()` 走 `opt_pose_ray_dist_sim3` 而非 `opt_pose_calib_sim3` → **深度方向无约束,静止场景也造运动**。

**⚠ `main.py:322` 的 `print(config)` 在 `--calib` 处理(349)之前**,所以日志里 `'use_calib': False`
**不能**证明 `--calib` 没传。这是之前一直误判的根源。

### 三臂实验(字节级同一数据集,同一 git 树 `26c0273c`)

| 臂 | 条件 | `dataset_full.txt` sha | frame1 位移 | 前300帧 |
|---|---|---|---|---|
| 好运行(09-16) | (旧脚本,自动带 --calib) | `4372b8178590b748` | 0.0610 mm | 0.0303 mm |
| A 对照 | 无 `--calib` | `4129f024894c3a61` | 3.6032 mm | 31.5 mm |
| B | `NVIDIA_TF32_OVERRIDE=0` | `d7d9b9011520d417` | 3.6411 mm | 31.66 mm |
| **C** | **`--calib`** | **`4372b8178590b748`** ✅ | **0.0610 mm** | **0.0303 mm** |

C 对 `dataset_full` / `dataset_online`(`05ef7e4386614be0`)/ `dataset.txt`(`0200bb39e46e7a83`)
**三个文件全部逐位相同于 09-16 好运行** ⇒ **回归就是这一个标志位,没有第二个。**

### 修复(已落地,备份在 `backups/rollback_20260919_calib_fix/`)

删掉 `config_uses_calibration()`,改成:
```bash
if [[ -f "$output/dataset/calibration.yaml" ]]; then
    mast3r_args+=(--calib "$output/dataset/calibration.yaml")
fi
```
**四个启动器全部经由这一个脚本拉前端**(`mast3r_fusion_legacy_v10_workflow.sh` /
`mast3r_fusion_original_v1_workflow.sh` / `mast3r_stereo_imu_workflow.sh` / `mast3r_slam_adaptive_precision_workflow.sh`),
所以单点修复覆盖全链路;`--calib` 在整个 home 里只此一处。

### 断代(时间线终于自洽)

磁盘上四份脚本副本:`gate=0` 只有修复后的现役版;`gate=2` 的有 09-16 18:24 备份、09-18 快照、
今天的回滚备份。**⇒ 门是在 09-16 12:12(好运行)到 09-16 18:24 之间被加进脚本的。**
这个脚本**不被 git 跟踪**,所以加门不产生任何哈希变化 —— 这正是"代码逐字节没变、行为却变了"之谜。
先前记忆里"09-15 17:26 是回归分界"是 git 暂存区假象(见下),**与真实断点无关,别再引用**。

## 前半程的排除记录仍然有效(避免重走)

- **不是内核**:138/136 同样坏。
- **不是数据**:1799 张左目 + 1799 `stereo_right` 全量 md5 逐字节相同;`frames.csv`/`imu_rotation_priors.csv`/`calibration.yaml` 全同。
- **不是权重**:checkpoint `e28f91b488554653…` 是官方预训练(mtime 09-07),与 09-14 manifest 逐位相同。用户"自训练模型不行"的怀疑不成立。
- **不是 TF32**:B 臂实测(比 A 只差 0.038 mm)。`main.py:299` 确实有 fork 加的 `allow_tf32=True`。
- **不是编译产物**:所有 `.so` mtime 09-07(`mast3r_slam_backends…so` 09-07 17:09),09-15 窗口内无重建。
- **不是 CUDA 驱动**:595.84,`dpkg.log` 9 月零条。
- **不是代码**:A/B(现役 vs legacy-v10,7 个文件不同、`tracker.py` 差 347 行)输出**逐字节相同** ——
  因为那套 stereo 点图机制要求 `use_calib`(`solve_metric_pose` 首行就 `and use_calib`),在 `use_calib=False` 下**完全空转**。
  **这个"空转"本身就是根因的指纹。**
- **不是 git 哈希**:`toolchain_dirty_diff_sha256` 比的是**工作区 vs 暂存区**,`git add/reset` 就能改它而文件不动。别再拿它当回归分界。

## 关键诊断量(以后先看这两个)

1. **frame1 位移**:好 ≈ 0.06 mm,坏 ≈ 3.6 mm。前 300 帧:好 0.03 mm,坏 31.5 mm。**一眼可辨。**
2. **`[2/8]` 的 `relative_p90_p10`**:好 0.164,坏 0.816(门槛 0.5)。report 里还有
   `direction_cosine`(MASt3R 位移方向 vs 双目米制方向夹角):好中位 2.96°,坏 15.70°(p90 78.59°)。

## 仍然成立的两个附带事实

- `dataloader.py` 里 `RGBFiles.timestamps = np.arange(N)/30.0` —— 前端用**合成的 30 Hz 时间戳**,
  不是 `frames.csv` 里的真实 ~29.98 Hz。C 臂证明它与本回归无关,但仍是待办。
- 09-14 那批产物**本身也没过精密门**(group1 rmse 4.88/p95 7.09/max 13.60,FAIL),
  所以"复原好运行" ≠ "达标"。**别把逐字节复原当成验收通过。**

## 运维教训(踩过的坑)

- **`ego_vio_humble` 下不要跑递归 grep。** 它含巨大的 `reports/` 树,一条 `grep -rn` 会啃满这块
  13–34 MB/s 的机械盘(`/dev/sda5`,2.8T 已用 93%),把正在读 6.3GB db3 的 prepare 任务**饿成 D 状态假死**。
  `2>/dev/null | grep -v /reports/` 是**事后过滤**,救不了扫描本身。要限定目录。
- 后台包装器会在**真正的工作进程还活着**时就报 `[exited with code 0]`,别拿它判断作业结束,要查 PID。

## 端到端复现验证(2026-09-19,修复后跑完整自适应流水线)

用 09-14 v11 holdout batch3 group1(`d405_720p_rgb_stereo_ir_20260914_202142`)跑
`mast3r_slam_adaptive_precision_workflow.sh`,与基准 `20260914_validation_v11_holdout_batch3/group1/` 逐文件比对:

**逐字节一致**(证明修复后前端完全复原):`mast3r_logs/dataset{,_full,_online}.txt`、
`trajectory_imu_metric.csv`(`ae7a9d0716ea3a8d`)、`trajectory_graph.csv`、
四个 `trajectory_stereo_*.csv`、`trajectory_all_frames_interpolated.csv`、
`trajectory_online_*`,`dataset/` 下 3603 个文件(仅 2 个 JSON 是路径串差异)。

**`[2/8]` 门 PASS 且数值逐位相同**:`scale=0.36185216402845194`、
`relative_p90_p10=0.21205572284676902`。⇒ 修复后与基准同属"带内参"那条正确链路
(今天的 launcher 条件 `-f calibration.yaml` 成立、确实传了 `--calib`,产物又与基准逐字节相同)。

**选型行为也复现**:tight 候选同样死在 `ValueError: stereo report scales disagree by more than 5%`
(基准 `selection_report.json` 里 `tight_candidate_exit_status: 1` + 同一句 failure 文案),
`[3/4]` 同样回退 sparse。⇒ 不是新回归。

**尾段不要拿 09-14 基准对比**:见 [[mast3r-fusion-param-generations]]。

**最终精度仍 FAIL,唯一失败项 `ate_translation_max`**(本次 13.061 mm / 基准 13.605 mm,门 10 mm);
其余四项全过(RMSE 4.756 / P95 6.773 / 10mm 内 99.713% / 姿态 1.981°)。该 max 就是
[[lighthouse-tracker-branch-switch]] 那条 freeze-then-catchup 真值伪影(帧 1080-1084),
**不是算法缺陷**;9 个无真值去刺变体全试过,救不回来(它平滑、不尖、不孤立)。
内部质量门 `input_quality_report.json` = PASS。

相关:[[mast3r-frontend-0915-edit-window]](前半程排除记录)、[[mast3r-fusion-param-generations]]、
[[d405-848x480-ab-result]]、[[lighthouse-tracker-branch-switch]]、[[capture-pipeline-ab-result]]

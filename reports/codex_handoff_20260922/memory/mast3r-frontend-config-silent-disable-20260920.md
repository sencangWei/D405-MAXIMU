---
name: mast3r-frontend-config-silent-disable-20260920
description: "★09-14 前端复现失败的根因=【传给前端的配置被换掉】: tight 候选靠 adaptive 脚本传 motion_kf_tight.yaml(0.15/5.0°), 该脚本已成死代码; 现役默认 offline.yaml 没有这两个键 ⇒ tracker.py:81-99 cfg.get(...,0.0) 兜成 0.0 而守卫是 'limit>0.0' ⇒ 运动关键帧【静默关闭】。判据: Motion keyframe 打印次数(基线73/sparse 0/现役配置0), 基线首个触发帧457==重跑分叉首帧; 判决实验=用 motion_kf_tight.yaml 重跑 ⇒ dataset_full.txt 与09-14【逐字节相同】(sha256 036067f6...) 且那次开着埋点 ⇒ 同时证明①可复原②与1484dd0的+1386行代码改动无关③埋点零行为影响"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-19T19:34:53.426Z
---

## 症状

按现役默认调用重跑 `20260914_validation_v10_batch/group1/fusion/tight` 的前端，
`dataset_full.txt` 与 09-14 基线 **前 457 帧逐位相同，帧 457 起分叉**，一路差到尾。

## 根因：一个键缺失 ⇒ 一整个机制静默消失

`mast3r_slam/tracker.py:81-99`：

```python
translation_limit  = float(cfg.get("motion_keyframe_translation", 0.0))
rotation_limit_deg = float(cfg.get("motion_keyframe_rotation_deg", 0.0))
triggered = (translation_limit > 0.0 and translation >= translation_limit) or \
            (rotation_limit_deg > 0.0 and rotation_deg >= rotation_limit_deg) or aged_motion
```

**缺键 → 默认 0.0 → `> 0.0` 守卫恒假 → 永远不触发。不报错、不警告。**

| 运行 | 配置 | `Motion keyframe` 次数 | 首个触发帧 |
|---|---|---|---|
| tight 基线（09-14） | `..._motion_kf_tight.yaml`（0.15 / 5.0°） | **73** | **457** |
| sparse 基线（09-14） | `..._offline.yaml` | 0 | — |
| 现役默认配置跑 tight 数据集 | `..._offline.yaml` | 0 | — |

配置来源：`scripts/mast3r_slam_adaptive_precision_workflow.sh`
（`[1/4]` sparse→`offline.yaml`，`[2/4]` tight→`motion_kf_tight.yaml`）。
**该脚本现已无人调用（死代码）**，现役 `mast3r_slam_precision_workflow.sh` 默认
`config/mast3r_slam_d405_offline.yaml` —— 那是 **sparse** 的配置。
`Motion keyframe` 打印在 `tracker.py:529`；**基线首个触发帧 457 == 分叉首帧**。

## 判决实验（一个实验钉死三件事）

用 `motion_kf_tight.yaml` 重跑同一 dataset：

```
sha256(dataset_full.txt) = 036067f60dfc121889258fd2d8bc47c61aff6708c1159a3e01a09b250c6d208a
                         == 09-14 基线（逐字节相同）
Motion keyframe 73 / 73
```

1. **09-14 前端可完整复原** —— 只差一个 `MAST3R_SLAM_CONFIG` 传参；
2. **分叉 100% 由配置造成**，与 09-19 的 `1484dd0`（tracker.py +420 行、共 +1386 行）**无关**
   —— 基线由 `e6f4e3d`+脏工作区产出，本次由 `1484dd0`+埋点产出，输出逐字节相同；
3. **埋点零行为影响**（那次埋点是开着的）。

## 怎么查（可复用的方法）

**`toolchain_commit` 不同的两次跑，先反查"它当时到底读了哪份配置"** ——
拿基线日志第 2 行（`main.py:322` 打印的 config）逐个 `load_config` 比对即可反查出文件名。
⚠ `main.py:322` 的打印**早于** `--calib` 赋值（:350），所以那份打印里 `use_calib` 恒为 False，
**不是**丢标定的证据。
⚠ `config.py: set_global_config()` 只做 `config.update(cfg)`（浅更新）——
顶层键会跨次残留，但 `tracking` 是顶层键、每次整体替换，所以**只比 `tracking` 是干净的**；
比全量会被前一次加载的 key 污染。

## 风险 / 待办

- 现役生产路径若重跑前端，tight 候选**不再有运动关键帧** ⇒ 与 09-14 不可复现。
- 历史 `fusion_current/*` 评测全部建立在 09-14 前端产物上（尾段重跑复用
  `fusion/<subset>/mast3r/`，**不重跑前端**）⇒ 评测结论有效，但**当前默认调用复现不出它们**。
- 与 [[mast3r-frontend-regression-20260918]] 的 `--calib` 循环门控是**同一类**静默陷阱：
  一个 `cfg.get` 缺省值 / 一个循环条件，就让整个机制无声失效。
- 证据与细节：MASt3R-SLAM fork `sencang-fork/d405-fork-20260919` 的 `D405_NOTES.md`；
  报告 `reports/mast3r_g2_validation_20260919/spike_attribution_20260919/README.md` 五。

相关: [[mast3r-frontend-regression-20260918]]、[[frontend-match-collapse-falsified-20260920]]、
[[motion-keyframe-hole-ab-20260920]]、[[mast3r-chain-topology]]

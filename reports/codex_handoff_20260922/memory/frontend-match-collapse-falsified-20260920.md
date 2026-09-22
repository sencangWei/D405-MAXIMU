---
name: frontend-match-collapse-falsified-20260920
description: "★「旋转→特征减少→误差大」这条链【被否】(2026-09-20): 前端逐帧匹配埋点首次落盘(MAST3R_MATCH_LOG, tracker.py:267 的 match_frac 本来就在算、只用于丢弃门从不落盘)。corr(match_frac,ω)=-0.024≈0; 超标帧 match_frac 中位0.581 > 达标帧 p5 0.438, 全链最小0.307反而落在【达标】帧; 角速度五分位 match_frac 几乎平(Q5 ω=25.3°/s 比 Q2-Q4 还高); 峰帧邻域误差已回落(13.74→0.85)而 match_frac 仍单调下跌(0.724→0.466) ⇒ 两者【解耦】, 匹配下降是转离关键帧的【结果】不是位置误差的【原因】。丢弃门0.05, 余量6倍, Skipped frame 全程0次"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-19T19:35:01.161Z
---

## 被否的是什么

用户提的假说：**旋转快 → 特征匹配减少 → 估计误差变大**。
此前唯一没测过的一条，因为**逐帧匹配数从来没有存在过**（`mast3r_logs/`、`mast3r.log` 里都没有；
`Skipped frame` 一次没打印 ⇒ `match_frac` 从没跌破 0.05，但那只是个很低的丢弃门）。

## 怎么测的

`mast3r_slam/tracker.py:267` 的 `match_frac = valid_opt.sum() / valid_opt.numel()`
**本来就在算，只用于 `min_match_frac` 丢弃门、从不落盘**。加了 env 门控埋点
`MAST3R_MATCH_LOG=<path>`，逐帧记 `frame_id, n_match, n_match_Q, n_opt, n_total`
（匹配网格 384² = 147456）。**不设则零行为影响** —— 已用与 09-14 基线逐字节对照证明
（见 [[mast3r-frontend-config-silent-disable-20260920]]）。

靶 = `v10/g1/tight`（A 类，2 个超标样本），1798 帧；帧号↔融合样本按时间戳映射，
|Δt| 中位 0.00ms，1743/1743 全中。

## 结果：全部指向"否"

| 检验 | 结果 | 含义 |
|---|---|---|
| `corr(match_frac, ω)` | **−0.024** | 「转得快→匹配变少」**不存在** |
| `corr(match_frac, err)` | −0.113 | 极弱 |
| 超标帧(2) match_frac | 中位 **0.581**、最小 0.535 | — |
| 达标帧(1741) match_frac | 中位 0.626、**p5 0.438**、最小 0.307 | 超标帧**比很多达标帧还高** |
| 丢弃门 `min_match_frac` | 0.05 | 全链最小 0.307，**6 倍余量** |

- **角速度五分位**：match_frac = 0.762 / 0.620 / 0.612 / 0.604 / **0.679** —— 几乎平，
  **Q5（ω=25.3°/s）反而比 Q2–Q4 高**。同期 `err 最大` 确实随 ω 上升（4.11→13.74），
  复现了「动作激烈」这条已知相关（[[spike-attribution-20260919]]），
  但它**不经过匹配数**这条中介。
- **峰帧邻域**（帧 469）：误差 `6.66→13.74→0.85` 已回落，
  而 match_frac 仍 `0.724→0.466` **单调下跌** ⇒ **两者解耦**。
  匹配下降是"相机转离了上个关键帧"的**结果**，不是位置误差的**原因**
  （帧 457 正是该 take 的**首个运动关键帧**）。

## 边界（别过度推广）

**只否掉「特征塌缩」这一条假说。** 它不动平移侧之外的东西，
也**不解释 B 类、不解释 rot 门** —— 后者见 [[rotation-is-the-binding-gate]]。
「动作激烈」这条相关性仍然成立，只是**机制不是匹配数**。

## 证据

`reports/mast3r_g2_validation_20260919/spike_attribution_20260919/`
的 `frontend_match_probe.py` / `.txt`，原始埋点
`reports/.../match_probe_20260920/v10_g1_tight_09_14cfg/matches.csv`（1798 帧）。
已推 `sencang` 分支 `dl-fusion/mast3r-d405-深度学习融合`（commit `cc9face3`，blob SHA 已核）。
埋点本身在 MASt3R-SLAM fork `sencang-fork/d405-fork-20260919`（commit `fa1690a`）的 `D405_NOTES.md`。

相关: [[spike-attribution-20260919]]、[[gate-failure-taxonomy-20260919]]、
[[rotation-is-the-binding-gate]]、[[mast3r-frontend-config-silent-disable-20260920]]

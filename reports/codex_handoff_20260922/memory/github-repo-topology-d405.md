---
name: github-repo-topology-d405
description: "★ GitHub 备份拓扑(2026-09-19 查清): sencangWei/D405-MAXIMU 是【多血统桶仓】—— dl-fusion/* / calib-kit/* / handoff/* / docker-* / release/* 各分支【互无共同祖先】; calib_kit 的老家是 calib-kit/*(共同祖先 097ef05), 不是 dl-fusion; origin(JO-ara-dev)已失效; MASt3R-SLAM 是另一回事"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-19T07:52:24.017Z
---

## 三个本地目录 ↔ 远端的分属关系(别再猜)

| 本地目录 | 血统 | 远端归属 |
|---|---|---|
| `/home/robot/ego_vio_humble` | 产品 SLAM 主干 | `D405-MAXIMU` 的 **`dl-fusion/mast3r-d405-深度学习融合`** |
| `/home/robot/桌面/ego_vio_calib_kit` | **独立血统**(根提交 `4a9dca8` 2026-08-10 «calib_kit 首次初始化») | `D405-MAXIMU` 的 **`calib-kit/*`** |
| `/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM` | 上游 rmurai0610 | **`sencangWei/MASt3R-SLAM`**(fork, 09-18 18:39 建) 的 `d405-fork-20260919` |

## ★ D405-MAXIMU 是「多血统桶仓」, 不是一条主线

`gh api repos/sencangWei/D405-MAXIMU/branches` 上 `dl-fusion/*`、`calib-kit/*`、
`handoff/*`、`docker-1`、`docker-2`、`release/*`、`backup/*` 并存, 但
**它们之间没有共同祖先** —— 这是正常的, 不是仓库坏了。

⇒ **不要试图"合并进去"**: calib_kit 与 `dl-fusion/...` 无共同祖先,
`git merge` 只会造无关历史。要放内容只能**新开一条分支**。

⇒ **同名分支不是同一分支**: 远端 `handoff/jazzy-20260816`(`9020dea6`, 97 提交)
与 calib_kit 的同名本地分支**毫无关系**。**强推会抹掉那 97 个提交 —— 绝不能做。**

## calib_kit 真正的老家(按共同祖先判)

`calib-kit/product-calibration-20260819`(`5bf4c1f`) 与
`calib-kit-product-calibration-20260819`(`57da2ea`) 与本地 calib_kit 的
**共同祖先都是 `097ef05`**(«backup: freeze calibration tools for Jazzy handoff», 08-16 17:33)。
⇒ 本地那 2 个提交(`0d70ec2` AGENTS.md 时间偏移 / `e07dcd1` world_z 标定产物)
是**从未推出去的**, 而远端那两条分支各有 3/1 个提交本地从未取回 ⇒ **双向分叉**。

2026-09-19 处置: 本地 2 个提交推到**新分支 `calib-kit/jazzy-handoff-20260819`**
(非强推, 不动任何既有分支), 已按还原演练验证(`6ad7b50a…` tree 双边一致, 文件逐位相同)。
**只推 git 已跟踪的 159 文件 / 55 MB**(标定工具+原始 npz+结果+docs+tests);
未跟踪的 579 MB `product_slam_candidate_20260814/` 与 214 MB tarball **不推**。

## origin 已死

`/home/robot/ego_vio_humble` 的 `origin` = `JO-ara-dev/D405-MAXIMU.git`
→ **`Repository not found.` 永久不可用**。唯一可写远端 = `sencang`
→ `https://github.com/sencangWei/D405-MAXIMU.git`。
git 已配 `credential.https://github.com.helper = gh auth git-credential`, 推送无需另配。

相关: [[always-commit-and-push-algorithm-progress]]

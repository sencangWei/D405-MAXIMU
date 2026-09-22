---
name: always-commit-and-push-algorithm-progress
description: "用户铁律(2026-09-19)：重要进度/算法改动必须当天完整提交并推到可写远端备份；汇报'备份完成'前必须先查清代码住在哪、那个仓库有没有可写远端"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-18T20:03:40.882Z
---

用户原话：「以后这种重要进度算法必须完整提交以防之后改坏回不来」「为了找回算法我们跑了两天了，给我备份好」
「我不说你又忘了」。

**怎么应用(下次必须做，不用等用户说)：**

1. **任何算法本体的实质改动或抢修成果，当天就提交到分支并推到用户能写的远端。** 不要攒着。
2. **汇报"备份完成"之前，先逐个组件查清三件事**，缺一不可：
   - 代码**实际住在哪个目录**（算法本体常常不在主仓库里！）；
   - 那个目录**是不是 git 仓库**，`origin` **指向谁**；
   - 有没有**分支 / tag / stash** 兜住改动。
   **工作区里有改动但没分支没 tag 没 stash = 没有备份。**
3. **第三方克隆（`origin` 指向别人的仓库）是重灾区**：改动推不上去，必须先
   `gh repo fork` 到用户账号，或在自有仓库里放 `git format-patch` 补丁 + 新文件。
   只写"已提交本地分支"不算完成。
4. **验证要真做**：从远端把产物拉回来，在干净的上游树上演练还原，比对树哈希或内容哈希，
   而不是只看 `git push` 的输出。

**Why:** 2026-09-19，MASt3R-SLAM 工具链（`/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM`，
算法本体）的 fork 改动 —— 13 个文件 +1395/−34，含 388 行的 `stereo_depth.py` ——
**连续多天只存在于工作区**，因为那个克隆的 `origin` 指向上游 `rmurai0610/MASt3R-SLAM`，
我们的改动无处可推。一次 `git checkout .` 或重装工具链就会永久消失。
而我此前汇报过"备份完成"，实际只备份了调度层（`scripts/` + `config/`），
**算法本体一行都没有** —— 用户为此跑了两天才找回算法，这是最不能接受的那种"假完成"。

**How to apply:** 把它当成收尾清单的一步，和"跑测试"同级。汇报备份时要说清
**备份了什么、在哪个远端、怎么验证的**，而不是只说"已备份"。

### 两个当场可用的自查手法（2026-09-19 又栽了一次后补）

1. **看 push 输出的区间 `A..B`** —— 那才是远端这次真正移动的范围。
   如果区间起点是你以为"早就推过"的某个提交，说明中间那些**根本没上去**。
   2026-09-19 我就这么栽了一次：状态里汇报 `87d16b9`、`d19be6d` 已推送，
   实际 `git reflog show sencang/<branch>` 显示远端 tip 是
   `2b842db → 2f8b773 → 40b1410 → d19dde5`，那两个提交**只存在于本地**。
   `git reflog show <remote>/<branch>` 是事后复核"到底推过几次、推到哪"最快的证据。
2. **汇报前跑一次真还原**：`git clone --depth 1 --single-branch --branch <b> <url> /tmp/x`
   再 `sha256sum` 比对关键产物。本地与克隆回来的哈希一致才算数。

## 全组件审计结果(2026-09-19 实测, 复查用)

| 组件 | 位置 | git? | origin | 状态 |
|---|---|---|---|---|
| SLAM 主工作区 | `ego_vio_humble` | ✅ | `JO-ara-dev/D405-MAXIMU`(不可写) + **`sencang`=sencangWei(可写)** | ✅ 洁净, 推到 `sencang` 的 `dl-fusion/mast3r-d405-深度学习融合` |
| MASt3R-SLAM 本体 | `ego_pipeline/work/toolchains/MASt3R-SLAM` | ✅ | 上游 `rmurai0610`(不可写) + **`sencang-fork`(可写)** | ✅ D405 fork 提交 `1484dd0` 已在 `sencang-fork/d405-fork-20260919`;工作区只剩 `.cuda/`+`thirdparty/lietorch/` 两个**构建产物**未跟踪(非算法) |
| 标定工具包 | `/home/robot/桌面/ego_vio_calib_kit` | ✅ | **⚠ 空 —— 根本没有远端** | ❌ **本地分支 `handoff/jazzy-20260816` 之外无处可去**;需用户决定建远端 |
| 流水线启动器 | `ego_vio_humble/scripts/mast3r_slam_precision_workflow.sh` | ✅ | 同主工作区 | ✅ **现已跟踪**(旧记忆说"不被跟踪"已过期), `--calib` 循环门控修复在第 88–93 行且已提交 |

**判别远端可写性**: `git remote -v` 里指向用户自己账号(`sencangWei` / `JO-ara-dev` 看情况)
的才是可推的;指向上游作者(`rmurai0610`)的推不动。

相关:[[mast3r-frontend-regression-20260918]]、[[mast3r-fusion-param-generations]]

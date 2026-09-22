# Codex 接手包 —— D405 双IR/IMU × MASt3R 深度融合精度工程

> 生成 2026-09-22。上一手 = Claude Code 会话 `616ba18d`（2026-09-16 → 09-22，144 个用户回合）。
> **本文件是入口。先读本文件，再按需下钻到 `memory/` 与 `transcript_main/`。**
> 读者 = 冷启动接手的工程 agent。以下每条都标了「已证 / 未证」，没标的就是我还没验的。

---

## 0. 三十秒版本

用户的长期指令是**「把 MASt3R 深度前端与 Docker2 VINS 的融合算法真正跑通、精度做到 10mm 以内」**。

- **09-14「有好结果可复原」这个前提已被盘上证据否掉**：09-14 生产 **0/10 PASS**，
  `ate_translation_max` 中位 **15.41mm**，比现役（13.10–14.29mm）**更差**。**别再往 09-14 复原。**
- 现役产线**已跑通**（不再零产物 —— `[9/9]` 质量门 09-21 降级为诊断、尺度门 09-22 降级为可选诊断），
  **卡住的只有精度门这一项**。22 格语料上 16 跑成功，其中 **9/16 只失败在 `ate_translation_max`**。
- **14 族精度杠杆已逐族被实测封死**（§5 清单），且都被同一堵墙挡住：
  误差缺口是**约 27 帧（0.9s）的缓变位置偏移块**，而所有已试的杠杆
  **只能移动或放大它、不能消除它**。
- **下一步只剩三个方向**（§8）：直取那个块的成因 / 换评价口径 / 新增窗口尺度绝对约束。

---

## 1. 用户要什么（逐字，权威）

**常设指令**（transcript `user_turns.md` 第 353 条，逐字）：

> 我们14号跑出来的轨迹都很好就往14复原走先把算法真正跑通找回来，然后我们中间出现的难点要同步推到github仓库

**实验纪律**（多轮重申，逐字）：

> 先跑1但不要只跑一组数据，要多跑几组对照

**其他已拍板**：用户明确同意「③④ 尺度不一致降级为诊断 + ② 0.5× 自动重试」、
「前端先扫 `matching` 调参面」；明确**不做**「① 逃生舱放宽」、「③' 其余 11 条 failures 也降级」、
「`--metric-scale-mode` 改 `stereo`」。

⚠ **`transcript_main/user_turns.md` 是意图的权威记录**（144 条逐字）。凡与任何摘要冲突，以它为准。
⚠ **注意用户的措辞习惯**：用户说「14号99%都在10mm」时指的是**当时的观感**；
盘上复核是 0/10 PASS。**用户的记忆与盘上证据不一致时，以盘上证据为准，但要如实告诉用户。**

---

## 2. 代码住在哪 / 交付面

| 项 | 值 |
|---|---|
| **实际工作仓库** | `/home/robot/ego_vio_humble` |
| 分支 | `dl-fusion/mast3r-d405-深度学习融合` |
| **唯一可写远端** | `sencang` = `https://github.com/sencangWei/D405-MAXIMU.git` |
| 当前 HEAD | `2698e583`（09-22） |
| 已死远端 | `origin` = JO-ara-dev/D405-MAXIMU.git —— **永久失效，别往那推** |
| 前端工具链 | `/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/`（**不被本仓库 git 跟踪**；是 fork，`sencang-fork` 可写） |
| 前端 venv | `/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/.venv/bin/python` |
| 语料 | `/home/robot/ego_vio_humble/reports/lighthouse_umi_workflow/<batch>/<group>/` |

⚠ **易混**：会话启动时的 gitStatus 属于 `/home/robot/桌面/ego_vio_calib_kit`（分支 `handoff/jazzy-20260816`），
那是**另一个目录**，**不是**算法工作目录。两处都有 `AGENTS.md`，内容不同。
⚠ `D405-MAXIMU` 是**多血统桶仓**：`dl-fusion/*` / `calib-kit/*` / `handoff/*` **互无共同祖先**，
这是正常现象，**别想 merge**。远端 `handoff/jazzy-20260816`（97 提交）与本地同名分支**毫无关系**，
**强推会毁掉它**。

**备份铁律**：改动当天提交 + 推送。验证靠
`gh api repos/sencangWei/D405-MAXIMU/git/trees/<branch>?recursive=1`
取远端 content-addressed blob SHA，与本地 `git rev-parse HEAD:<path>` **逐位比**，
**绝不读 `git push` 输出当证据**。

---

## 3. 现役产线长什么样

**一条链，不是四条**：

```
frames → [1/8] 前端(MASt3R-SLAM) → [2/8..5/8] 双红外尺度 → [6/8] IMU 米制尺度
       → [7/8] 图联合优化 → [8/9] VINS 姿态互补融合 → [9/9] 质量门+平滑 → evaluate
```

| 段 | 事实（已证） |
|---|---|
| `[1/8]` | **误差形状 100% 来自这里**（`[6/8]` 恒等于纯缩放，23/23 格残差 0.0000mm） |
| `[6/8]` | 恒等于**纯全局缩放**，不改形状 |
| `[7/8]` | = **÷0.33 收缩器** |
| `[8/9]` | 融合**根本不贡献姿态**：fused 姿态 ≡ VINS×常量旋转（20/20 格残差 0.0000°）⇒ **rot 门 100% 由 VINS 定** |
| 结构更正 | 产线**不是** ray+distance 优化器。`main.py:350-353` 的 `if args.calib: config["use_calib"]=True` 反向覆盖 yaml ⇒ 活跃的是 `opt_pose_calib_sim3`（pixel+log-depth）。**`sigma_ray`/`sigma_dist` 与产线无关，别拿它们推理** |

**入口**：`scripts/mast3r_slam_precision_workflow.sh`（子命令 `fusion)` = 产品路径、
`compare)` = 实验路径，两者**尺度模式不同**，别混）。

---

## 4. 唯一的卡点（已证）

- 卡门的是 **`ate_translation_max`**，不是 RMSE、不是 rot、不是覆盖率。
- 缺口 = **约 27 帧（≈0.9s）的缓变位置偏移块**。
  - 与 **MASt3R 上游**相关（`corr(fused, MASt3R graph)=+0.425`）
  - 与 **VINS 无关**（`corr(fused 全帧误差, VINS)=−0.006 ≈ 0`）
  - 是**局部偏移块，不是累积漂移**
- ⇒ **目标 = 把 fused ATE 压到 ≲4mm**（ATE≥10mm 时 rot 门 0/8 过；<5mm 时 3/4 过。
  这道 rot 门**本质是伪装成旋转的位置门**）。
- ⚠ **信号与误差同频带** ⇒ **位置域后处理在原理上无解**（不是没试对）。
- ⚠ **在 22 臂上这是共模** ⇒ **改单臂权重类必然无效**（已被 §14/§19/§24.7/§29 **四次独立证实**）。

---

## 5. 已封死的杠杆清单（★ 别重跑，会浪费几小时到几天）

以下每一族都是**实测**封死，多数还配了**机理上界**。全部证据在
`reports/mast3r_g2_validation_20260919/rerun_tail_v2_20260920/`。

| # | 族 | 位置 | 判决要点 |
|---|---|---|---|
| 1 | **`[8/9]` 参数族** | §19 | **1008 跑**（56 点 × 18 格），**PASS 全来自同一个 session**、没打开任何新格 |
| 2 | `[8/9]` 后处理加权（幅度/频带/自适应） | §14 | 3×3 因子 `lw{0.2,0.5,1.0} × smoothing_s{8,40,200}` **两轴全单调变差**，现役 `0.2/8` 是整格最优。**10× 衰减是安全裕度，不是 bug** |
| 3 | `[7/8]` 运行时旋钮 | §14/§16 | 两个修正上限旋钮**空转**（joint 25 只裁 3/18；full-rate 20 **18/18 一次没生效**）⇒ 不是调错，是**没参与** |
| 4 | **前端布点/关键帧密度族（全局+靶向）** | §12/§17 | 先否全局降阈值，再否靶向「只在快段加密」。★**天然对照**：tight vs sparse 是同 session 真前端两跑、**密度差 5×**，
`corr(间隙,MAX)=−0.254`（**负**）、扣速度后 r²≈3%、高速半里 −0.031 ⇒ **无独立解释力** |
| 5 | **C-as-weight** | §24.7 | 软权重不崩 match_frac 但**也没用**（恶化 0.2–0.3mm） |
| 6 | **位置域鲁棒滤波整族** | §25.2 | Hampel/中值/高斯**逐位无效**。★**机理**：滤波器长度（3–7 帧）**比误差相关长度（27 帧）短 4–9 倍** ⇒ **滤错对象** |
| 7 | **`huber`** | §29 | 3 剂量 × 2 格，`|Δ|@峰` 中位 1.13/0.51/0.87mm，判据（≥3mm）全不过。⚠**与 C-as-weight 不同：方向单调**（k 越小越好，2/2 格同时改善 MAX 与 RMSE）⇒ 是「方向对但**量级差一个数量级**」的真信号 |
| 8 | `imu_rotation_constraint_weight` | §17 | **上界 + 实测双否** |
| 9 | `C_conf` | §20.3 | **被设成结构性空转**的旋钮 |
| 10 | 「约束密度」 | §21.3 | 被否 |
| 11 | 「子空间/低通」 | §22 | `\|H\|` 0.28–0.36 与全场一样 ⇒ **无陷波** |
| 12 | **尺度模式消融** | §(描述) | **21 组 × 4 模式 全门 PASS 0/21** ⇒ 尺度模式**不是卡门的杠杆**；`joint` 是唯一 RMSE+rot 中位都最优的 |
| 13 | 「跳过 `[7/8]` 救 max」 | §(描述) | **已撤回**：全量消融里 no_g7 **全面最差**（RMSE 0优/17差），两三条 cell 不足以立线索 |
| 14 | **前端 `matching` 调参面** | §36.3 | **C2 6 跑**：`24Δ = 7改善17变差`；卡门量 max **仅 1 改善 5 变差、无一格翻门**。★**非零效应**：唯一真改善 `v11b3/g2` **−0.702mm**（链已证逐位可复现 ⇒ 非噪声），但该格卡 VINS 定的 rot 门 ⇒ **投入产出不成立** |

⚠ **源文件里的族编号有前后不一致**（`§21.3` 与 `§24.7` 都自称「第 12 死族」）。
**以本节的功能清单为准，别去对编号。**

**负结果三连**（同一批实验里一并否掉，别再试）：
`--relative-motion-sigma-m` 惰性（25× 只动 0.01mm）、**加重平滑更糟**、
±0.5s 最优时移**压掉 0%**。

---

## 6. 「别引用错的」（★ 这些坑我踩过，会得出反向结论）

1. **`--calib` 反向覆盖 `use_calib`**：`main.py:350-353` 的 `if args.calib: config["use_calib"]=True`
   而 `run_frontend.sh` **一直传 `--calib`** ⇒ yaml 里的 `use_calib: False` 是**死键**。
   曾经误以为产线跑 ray+distance 优化器，整段结论作废。
2. **「不报 VINS 单链精度」**：引用任何 `precision.*` 前**先读 `estimate` 字段**确认是哪条链。
   新建目录里的单链报告也叫 `precision.md`，是个陷阱。
3. **「引用 fused/ATE 数字必须注明族」**：`fusion/` = 09-14 产线（旧）；
   `fusion_current/` = 09-20 现役；`fusion_v2/` = 现役世代；harness = 第三族。
4. **凡拿 `max` 做族间比较，必须先剔坏真值组**：12 个门禁组里 **4 组真值坏**，分 2 类 ——
   **分支 REJECT**（`lighthouse_tracker_branch_switch`：tracker 中途换位姿分支，
   8ms 跳 5–20mm 不回位，把整条 ATE 抬到 ~10mm）与 **窗截断**
   （tracker 窗短于相机窗 ⇒ GT 铺不满 ⇒ `timestamp_overlap_ratio_below_limit` **与精度无关地 FAIL**）。
5. **「前端位移 ≥8–26mm 判据」已被否且方向相反**：满足判据的臂只换 +0.168mm；
   唯一改善者位移仅 6.64mm。**别再用位移量筛前端臂。**
6. **别用 `toolchain_dirty_diff_sha256` 当回归分界**（它比的是工作区 vs 暂存区）。
7. **单位坑**：`frames.csv` 是 **MASt3R 单位**不是米；
   `rigid_align` 残差**依赖绝对尺度**，别当形状误差。
8. **`local_opt.window_size` 是死键**（`global_opt.py:31` 赋值后无消费点）⇒ **前端没有滑窗**，
   每次解整张累积图，唯一锚是 CUDA 硬编码 `num_fix=1` ⇒
   **「相对因子对整窗平移失明」在这里同样成立**。唯一局部直写通道
   `SharedKeyframes.update_T_WCs`（`frame.py:345-347`，**现无调用者**）。

---

## 7. 操作坑（每条都让整条**静默零产物**，我踩过）

| # | 坑 | 后果 | 修法 |
|---|---|---|---|
| 1 | `\| sed "s/^/[$cell] /"` 而 `$cell` 含 `/` | `/` 提前终止 s 命令 ⇒ sed 报错退出 ⇒ 上游写 stdout 吃 **SIGPIPE 整条死掉** | **每格各写一份日志文件**，不用 sed 前缀 |
| 2 | `> "$OUT/../fusion.log"` 而 `$OUT` 尚不存在 | bash **不预归一化**路径 ⇒ `..` 直接 ENOENT，全链 **0s 挂掉** | 先 `mkdir -p "$OUT"` |
| 3 | `[9/9]` 质量门 rc=3 + `set -e` | 曾**整条中止、零产物** | **09-21 已降级为诊断**（只容忍 rc=3），产线不再零产物 |
| 4 | `imu_stereo_metric_scale_disagreement` 硬 FAIL | 同上 | **09-22 已降级**：`--scale-disagreement-policy {fail,diagnose}`，**默认 fail 契约不变**，`fusion)` 传 `diagnose`。⚠ **`--max-scale-disagreement-ratio` 只喂 `consistent`、不影响选中的尺度** ⇒ 可合成触发翻门而轨迹一字节不变（验证全靠这个性质） |
| 5 | **VINS 回放不是确定性的** | 同 take/同配置/同速率两遍：`raw_max` **10.37 vs 19.35mm**、产物文件全不同 | 实时回放 = 宿主时序改变输入流。**⇒ 凡 VINS 侧跨跑比较（尤其 ≤1mm）都带此噪声底，先重复跑再下结论。**★ 对照：**MASt3R 融合链是确定的**（整条重跑轨迹逐字节相同） |
| 6 | `pgrep -f '字符串'` / `pkill -f '字符串'` | 命中**自身命令行** | 用 `ps -o pid=,ppid=,etime=,stat=,args= -p <PID>` |
| 7 | `ps -eo ... -p <PID>` | `-p` 被 `-e` 覆盖 ⇒ 列出全部进程 | 同上 |
| 8 | 外层 shell 是 **zsh** | `${arm:16s}` 被当子串展开（`bad math expression`）；`${PIPESTATUS[0]}` 需 bash；`grep --include=*.py` 需引号 | 用 bash 写脚本，或避开这些写法 |
| 9 | `set -u` + `source ROS setup.bash` | 非交互 shell 下**致命退出** | 先 `set -o pipefail`，source 后再 `set -u` |
| 10 | 被杀的前端跑留孤儿进程占 GPU（曾 21.8GB） | PPID 已死被 reparent 到 `systemd --user` | 按 PID `kill -TERM` |
| 11 | 成立 `done` 的环境：`source /opt/ros/humble/setup.bash && source /home/robot/ros2_ws/install/setup.bash` | 只 source 前者 ⇒ Package not found；直接 python3 ⇒ rclpy 缺失 | 见下 §9 命令 |
| 12 | **跑测试前必 pkill 清场** | 残留节点同时订阅 `/imu0`、`/odometry` ⇒ 污染 ⇒ **数值爆炸，误判回归** | `pkill -9 -f vins_fusion_ros2_node; pkill -9 -f replay` |
| 13 | 前端 `frame.py:241` `buffer=512` | 关键帧容量硬顶；`dist_thresh` 收到 3cm 时**每帧都新建关键帧** ⇒ 撞顶崩（`IndexError: index 512 ... size 512`） | **工具链在该参数域无容量保护**。正常跑关键帧数 43–47，离 512 极远 |
| 14 | **别 `git add reports/`** | 磁盘 237GB / `.git` 4.0GB | 只加**特定证据路径** |

---

## 8. 下一步（三个方向，都需要用户定）

**A. 直取那个「27 帧缓变位置偏移块」的成因**（唯一能真正过门的路）
- 已知：与 MASt3R 上游相关（+0.425）、与 VINS 无关（−0.006）、同频带、22 臂共模。
- **未走过的路**：`sigma_depth` 是唯一能让 depth 参与的旋钮（`sigma_pixel=1.0` / `sigma_depth=1e+1`）；
  前端 `[1/8]` 的**拓扑/边权/迭代/门限全在 Python**（`global_opt.py` / `main.py:run_backend` / `tracker.py`）
  ⇒ **改前端不用重建 CUDA**（位姿图优化不在 `.so` 里）。
- ⚠ **前端杠杆需 8–26mm**（加权类传递率只有 **0.10–0.15×**，布点类 **0.33×**）。

**B. 换评价口径**
- 用户曾提出「尖峰丢掉用前后两帧插补」（`user_turns.md` #394），但当时的结论是
  **「连续尖峰不行」**——⚠ **该结论基于「A 类=尖峰」的旧判断，而 A 类后来被证不是尖峰**
  （中位 73 帧）。**这条值得重新评估。**

**C. 新增窗口尺度绝对约束**
- §14 结案的落点原话：「只剩上游 `[1/8]` 或**新增窗口尺度绝对约束**」。

**明确别做**：改进 `[8/9]` 权重类（四次证实无效）、位置域滤波（原理无解）、
往 09-14 复原（前提被否）、改 `--metric-scale-mode`（用户明令单轨迹不改，
任何改动必须是**所有数据组**上验证过的通用策略且**由用户决定**）。

---

## 9. 常用命令

```bash
# 环境（顺序不能反）
bash -c 'source /opt/ros/humble/setup.bash && source /home/robot/ros2_ws/install/setup.bash && python3 ...'

# 清场（跑测试前必做）
pkill -9 -f vins_fusion_ros2_node; pkill -9 -f replay

# 全链重跑一格（C2 姿势，只换前端配置）
MAST3R_SLAM_CONFIG=<cfg> bash scripts/mast3r_slam_precision_workflow.sh \
    fusion <session> <vins_traj> <vins_report> <out_dir>

# 逐帧误差剖面（复现值与官方 precision.json 逐位吻合）
python3 reports/mast3r_frontend_matching_sweep_20260922/fused_error_profile.py \
    <对照 fused csv> <本臂 fused csv> <gt csv>

# 备份验证（逐位 blob SHA，绝不读 push 输出）
gh api repos/sencangWei/D405-MAXIMU/git/trees/<branch>?recursive=1   # 比 sha
```

---

## 10. 本包内容

```
codex_handoff_20260922/
├── HANDOFF.md                    ← 本文件（入口）
├── extract_transcript.py         ← 重新生成 transcript 的脚本
├── memory/                       ← 45 个记忆文件（完整知识库，384KB）
│   ├── MEMORY.md                 ← 索引（先读这个，再按需下钻）
│   └── *.md                      ← 每条一个事实
└── transcript_main/
    ├── user_turns.md             ← ★ 144 条用户原话（逐字，意图权威记录，18KB）
    ├── assistant_text.md         ← 3562 个助手文字回合（结论与判断，1.5MB）
    └── commands.log.gz           ← 5805 条 Bash 命令（复现用，1.0MB 压缩）
```

**建议接手顺序**：
1. 本文件
2. `memory/MEMORY.md`（索引，17KB —— 每行一个要点，按需下钻）
3. `reports/mast3r_g2_validation_20260919/rerun_tail_v2_20260920/README.md`
   （**3027 行**，全部实验的原始台账，§1–§36 按时间。**这是真正的证据主体**）
4. `transcript_main/user_turns.md`（确认用户到底要什么）
5. 需要细节时 grep `assistant_text.md` / `commands.log.gz`（`zcat … | grep`）

⚠ **读 `README.md` 时注意**：它是**按时间追加**的，**早期章节的结论可能已被后期推翻**
（例如 §24.6 整段作废、§25.3 机理归属被 §28 改、《03 类不是尖峰》）。
**每节末尾的「★/★★ 更正」才是当前有效结论。**
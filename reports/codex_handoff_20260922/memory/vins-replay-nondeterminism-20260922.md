---
name: vins-replay-nondeterminism-20260922
description: "★★ VINS 回放**不是确定性的**: 同 take/同配置/同速率连跑两遍, raw_max 10.37 vs 19.35mm(差87%)、三份产物文件全不同 ⇒ 任何「与改动前逐字节相同」的 VINS 侧验证**原理上做不到**; 对照: MASt3R 融合链**是**确定的(整条重跑轨迹逐字节相同) ⇒ 凡 VINS 侧跨跑比较的旧结论都带这个噪声底, 尤其 <=1mm 的差异"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-21T22:40:38.354Z
---

## 实测（2026-09-22）

`holdout_b2/g1`（session `d405_720p_rgb_stereo_ir_20260914_175024`），`--rate 0.5`，
其余参数逐字相同，**连跑两遍**：

| | result | coverage | raw 样本 | **raw max** | **corr max** |
|---|---|---|---|---|---|
| repeat1 | PASS | 0.9909 | 1742 | **10.37mm** | 12.09mm |
| repeat2 | PASS | 0.9915 | 1742 | **19.35mm** | 10.96mm |

`vio_raw.csv` / `vio_corrected_stream.csv` / `run_acceptance.json` **三份全不同**。
⇒ **raw_max 两遍差 87%。**

**机理**：回放是**实时**的，宿主时序（丢帧、IMU 调度）会改变实际喂进去的流 —— 不是纯函数。

**对照（同一天测）**：MASt3R 融合链**是确定的** —— 同臂（`match_wide`，`b5/g4`）
整条 `mast3r_slam_precision_workflow.sh fusion` 重跑一遍：
`trajectory_fused.csv`、`trajectory_fused_unsmoothed.csv`、
`mast3r/trajectory_frames.csv`、`mast3r/trajectory_graph.csv` **全部逐字节相同**，
`precision.json` / `graph_fusion_report.json` 归一化内嵌输出路径后**逐键相同**。
（`[1/8]` 前端在 C1 单跑与 C2 全链两次独立运行之间也逐字节相同。）
⇒ **`[1/8]`→`[9/9]`+eval 全链确定；只有 VINS 回放注入的那条输入流不定。**

★ **这次比较之所以干净**：两臂**复用同一份** `docker2_slam/` 的 VINS 轨迹
（`run_c2.sh` 只换 `MAST3R_SLAM_CONFIG`）⇒ 前端对照不含回放噪声。**这是做前端 A/B 的正确姿势。**

## 四条后果

1. **「默认产物与改动前逐字节相同」这类 VINS 侧验证，原理上做不到** —— 不是漏做。
   要证「默认路径未变」，改用**可证的 argv 恒等**（`VINS_RATE` 未设时
   `--rate "${VINS_RATE:-0.5}"` 逐字展开为 `--rate 0.5`；新增代码全在 `python3` 调用之后）。
2. **单跑对照会被削弱**：0.5× 的 `corr_max` 历次观测 = **10.38 / 10.96 / 12.09mm**
   （散度 ~1.7mm）；raw_max = **10.37 / 14.73 / 19.35mm**（散度 ~9mm）。
   ⇒ 速率阶梯里「0.25× 的 14.37 更差」**未确立**；成立的只是「**无证据**显示 0.25× 更好」。
   **阶梯照样不做**（结论不变，但理由要写准：缺证据，不是有反证）。
3. **凡 VINS 侧跨跑比较的旧结论都带这个噪声底**，尤其 **≤1mm 量级**的差异、
   以及「某次跑 FAIL 是因为 X」这类**单跑归因**。与 [[survey-22-arms-before-concluding]]
   的普查纪律同向：**先重复跑再下结论**。
4. ★ **顺带查实**：`holdout_b2/g1` 的 `docker2_slam/` 里存的是 **1.0× 的 FAIL 跑**
   （`replay_rate: 1.0`、`failures=[runtime watchdog is SLAM_FAILED:
   corrected_trajectory_jump, pose coverage 0.9590<0.98]`、1685 样本），
   **不是** 0.5× 的 PASS 跑 —— 而 `vins_dir.py::pick_vins_dir` **要求 PASS**
   ⇒ 该格的台架选目录会跳过它。

## 复现

`reports/replay_determinism_20260922/run_determinism.sh`（两遍重复 + 与格内存证比）。
★ 产物写格**外**目录：写进 `<cell>/docker2_slam_*/` 会被 `vins_dir.py` 的
`sorted("docker2_slam_*")` glob 选中而静默顶替正式那份。

相关: [[mast3r-rerun-tail-v2-20260920]]（§36.2/§36.3/§36.4）、
[[report-fusion-not-vins-accuracy]]、[[vins-process-hygiene]]
---
name: survey-22-arms-before-concluding
description: "本仓铁律——任何\"某格上看到 X\"的结论，落笔前先在 22 臂 fusion/ 族上普查（零新跑，几十秒）"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-21T07:03:59.452Z
---

在 MASt3R 融合这条链上，**从单格往外推的结论本会话 3/3 次被 22 臂普查否掉**（2026-09-21）：

1. 「`v10/g1` 上没有几十帧宽的鼓包，只有一根两帧尖峰」❌
   → 普查：超标帧数**中位 73**（最大 357），连续段中位 **11 帧**；那格是 **3/22** 的特例。
2. 「那根融合尖峰是 VINS 的错」❌
   → 普查：corr(fused 误差, VINS 误差) 中位 **−0.006**；corr(fused, MASt3R graph) **+0.425**。
3. 「中值/Hampel 滤波逐位无效 ⇒ 旋钮没接线」❌
   → 普查：接线正常，是**滤错了对象**（污染在航向不在位置）。

**Why:** `fusion/` 族 22 臂全部已在盘，普查一次几十秒；而单格结论的错误代价是整轮
实验方向跑偏（本会话为此白跑了 C-as-weight 的整条尾链，且选的还是 3/22 的尖峰特例格）。

**How to apply:** 写进 README / 记忆 / 对用户汇报之前，先在
`reports/mast3r_g2_validation_20260919/rerun_tail_v2_20260920/fusion89_sweep/cells.txt`
列的 22 臂上把同一量算一遍（`fusion/<arm>/{trajectory_fused.csv, mast3r/trajectory_graph.csv}`
+ `docker2_slam/` 的 VINS 链，真值 `lighthouse_body_ground_truth.csv`）。
只有普查支持才落笔；普查否掉就把 n=1 的结论**原样记下并标为已否**，不要静默删掉
（`§25.4` 就是这么处理的）。

相关：[[mast3r-rerun-tail-v2-20260920]]、[[gate-failure-taxonomy-20260919]]、
[[spike-attribution-20260919]]
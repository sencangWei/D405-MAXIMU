---
name: report-fusion-not-vins-accuracy
description: "用户只关心融合链精度,汇报精度必须评融合产物,不许拿 VINS 单链的数字充当"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-19T03:35:28.250Z
---

**汇报精度时只能评融合链产物 `fusion/trajectory_fused.csv`,绝不能用 VINS 单链
(`slam/vio_corrected_stream.csv` 或 `docker2_slam/vio_corrected_stream.csv`)的数字。**

**Why:** 项目的价值在融合那一段(把 VINS 与 MASt3R 两条链图优化融在一起)。VINS 单链
精度既不证明也不证伪融合有用。2026-09-19 我把三个 720p 报告的 FAIL 数字当"新录制精度"
汇报,实际那三个 `precision.json` 的 `estimate` 指向的都是 `slam/vio_corrected_stream.csv`
—— 纯 VINS,用户明确指出这不是他要的。

**How to apply:**
- 拿到任何 `precision.md/json`,先读 `estimate` 字段确认评的是哪条链再引用。
- 命名陷阱:老目录里单链报告叫 `docker2_precision.md`、融合报告叫 `fusion_precision.md`,
  一眼能分;**新目录里单链报告就叫 `precision.md`**,看着像总报告,其实只是单链。
- 只有 `fusion/trajectory_fused.csv` 或 `fusion_precision.*` 才算数。
- 参考 [[mast3r-fusion-param-generations]]、[[slam-tail-error-elimination-20260919]]。

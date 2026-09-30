# 交叉对照发现（待完成）

- 09-27 的 9/10 是 SIFT free-PnP LM+raw gyro 重估接受边，再加 full-seam metric-window graph 的研究结果；当前 `fusion-guarded` 用原始 PnP，不是同一实现。旧版两段研究脚本把十组样本写死，新六组不能直接原样调用。
- 09-30 新六组原始相机/IMU/Tracker 数据验收均通过；产品内部仅 take2 过最终 10 mm 门，take1/3 在四尺度报告合并时被 5% 门拒绝，take6 质量门拒绝，take4/5 有轨迹但超过 10 mm。详细证据见 `../six_take_validation_20260930/findings.md`。
- 当前任务先固定旧十组完整产品入口，之后对新六组移植旧 SIFT 观测候选；后者**不包含** 09-28 的窗口约束，不可与历史 9/10 直接等价。
- 旧十组冻结前端/原始 PnP 报告重跑当前图和融合后端：8/10 PASS，仅 `fresh2`/`fresh4` 最大 ATE 16.15/16.18 mm FAIL。首组 `fresh1` 额外完整重跑当前产品，前端 `trajectory_frames.csv` 与旧缓存 SHA256 同为 `f191e68a...`，最终 `trajectory_fused.csv` 与冻结输入重跑结果 SHA256 同为 `4d0ef071...`；因此剩余九组用冻结输入做交叉后端实验，有一组完整链等价性对照，而**非声称十组都完整前端重跑**。
- 09-27 SIFT-LM+raw gyro 观测候选固定用于六组新样本：`take1`/`take3` 仍在 `merge_stereo_reports` 5% 跨尺度门拒绝；`take2` PASS 最大 5.82 mm；`take4`/`take5`/`take6` 最大 23.38/12.49/68.70 mm FAIL，`take6` 内部质量仍 REJECT。相比当前产品的 7.02/23.47/12.70/68.72 mm，该观测改动无法解释新数据整体退化。09-28 full-seam 窗口候选仍在运行中。

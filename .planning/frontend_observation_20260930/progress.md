# 进度

- 2026-09-30：阅读 HANDOFF、现役配置、MASt3R `main.py`/`tracker.py`、新组日志及原图。确定先做固定前景 mask 的失败/通过对照，不直接推广。
- 首次隔离运行把 `capture` 外层目录误作 D405 DB3 会话；`select_db3` 明确报 `no non-empty db3`，未进入前端。已从 `capture_manifest.json.d405_session` 核实真实录制目录，换新输出目录重试；不覆盖这个错误产物。
- take4 单变量遮挡候选跑完融合与官方冻结评分：原版 RMSE/P95/MAX=7.075/17.677/23.472 mm，候选=6.036/14.381/21.973 mm；内部质量 PASS 但官方 FAIL（含姿态 2.353°）。效果不足以推广；已启动通过组 take2 的同开关 A/B。
- take2 同开关运行前端 1199 帧、四路双目各自 PASS，但 `merge_stereo_reports` 5% 尺度一致门失败；原版该组官方 PASS。拒绝 mask 候选，正式 `MAST3R_MASK_FIXED_SELF` 继续默认关闭。
- 已在产品融合入口增加视觉前端完整覆盖检查，防止 take1/3 的 240/392 帧失跟尾段耗尽后续管线再误报尺度问题；新三单测和全桥接测试 39/39 PASS，旧产物 CLI 实证 take1/3 拒绝、take2 通过。

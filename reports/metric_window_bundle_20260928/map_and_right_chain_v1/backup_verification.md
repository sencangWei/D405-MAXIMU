# 远端恢复验证

2026-09-28。代码与精确选定证据提交 a83043801fe3a66c4a6dc3e363be6cbc3d76ab9f，
普通推送至 sencang（https://github.com/sencangWei/D405-MAXIMU.git）
分支 codex/feature-loss-diagnostic-20260928。

实际来源为 /home/robot/ego_vio_humble；备份工作树为
/home/robot/ego_vio_feature_loss_backup_20260928。未向不可写 origin 推送，
未强推，未添加整个 reports/，没有拷贝模型或原始录制。

在独立目录 /home/robot/ego-feature-loss-restore-J9l9eE 仅从远端 fetch、
检出该提交；相对 985db16a 的全部 738 个变更文件与现场逐字节一致，
含 726 份新诊断 NPZ、两项 census、比较与报告、源码和测试。
恢复后 83 项相关测试通过（0.52s）。较早直接诊断的源码哈希也已
验证对应远端备份 985db16a，不冒充当前新增链式函数的版本。

这是诊断能力与证据备份，不是生产精度修复。当前 9/10 PASS，
fresh4 最大 13.801442 mm 未改善；无正在运行的 SLAM/GPU 重放。

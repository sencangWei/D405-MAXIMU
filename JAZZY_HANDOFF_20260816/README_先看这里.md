# ROS 2 Jazzy 迁移包：先看这里

这不是一个重新压缩的大归档，而是一套**直接把原始项目与数据流式复制到移动固态**的交接工具。这样不会在旧电脑仅剩约 101 GiB 的系统盘上再制造一份约 320 GiB 的重复数据，也不会改变或删除源文件。

## 旧电脑上怎么复制

1. 把移动固态格式化为 `ext4` 并挂载。2026-08-16 实测有效迁移源约185GiB；复制脚本额外要求20GiB余量，因此可用空间必须不少于205GiB。
2. 确认此时没有正在进行的 D405 采集。
3. 在旧电脑执行：

```bash
cd /home/robot/ego_vio_humble/JAZZY_HANDOFF_20260816
./copy_to_ssd.sh /media/robot/你的移动固态挂载名
```

脚本会创建：

```text
<移动固态>/ego_vio_jazzy_handoff_20260816/
```

如果移动固态根目录属于root且不可写，但根目录下已有由当前用户拥有的普通`RECOVERY/`目录，脚本会安全地改用：

```text
<移动固态>/RECOVERY/ego_vio_jazzy_handoff_20260816/
```

复制结束后会生成整包 `SHA256SUMS`。默认会校验全部文件；约185GiB有效迁移源的复制与哈希都可能需要较长时间，但脚本可安全重跑并续传，不会执行 `--delete`。

## 复制完成后怎么验

```bash
cd /media/robot/你的移动固态挂载名/ego_vio_jazzy_handoff_20260816
./handoff/verify_bundle.sh .
```

必须看到最终的 `VERIFICATION PASS`。若中途断电或拔盘，重新执行旧电脑上的 `copy_to_ssd.sh` 即可续传，然后再验。

## 包内最重要的入口

- `handoff/CODEX_HANDOFF.md`：给新电脑 Codex 的完整当前状态、有效结论、失败候选和下一步。
- `handoff/JAZZY_FIRST_BOOT.md`：Jazzy 不能直接复用 Humble 二进制，如何干净重建。
- `handoff/WORLD_Z_POSTPROCESS_HANDOFF.md`：尚未完成的世界 Z 后处理，包含保留数据、已证伪方案、量化基线和新机验收门。
- `handoff/ASSET_MANIFEST.md`：带走的目录、用途、遗漏风险。
- `projects/ego_vio_humble/`：SLAM 主工程、保留的39组`recordings/`（18组当前数据 + 21组关键回归证据）、报告、测试和`.git`。
- `projects/ego_vio_calib_kit/`：标定工程、IMU 标定、产品候选证据和 `.git`。
- `ros2_ws_humble_snapshot/`：旧 ROS 工作区全快照；新机只从 `src/` 重建。
- `calibration/calib_run_20260808/`：三份原始 Kalibr bag 与报告，不能丢。
- `memory/claude_project_memory/`：14 个长期调查记忆文件和索引。
- `metadata/`：旧机系统、Git 状态、dirty patch、bundle、源/目标文件统计。

GitHub同一私有仓库保留两条恢复线：`handoff/jazzy-20260816`用于Jazzy移植，`release/humble-known-good-20260816`用于必要时重装Humble回退。

数据和配置的三类状态见 `handoff/CURRENT_RUNTIME_PROFILE.md`：`CURRENT_ACTIVE`、`REGRESSION_EVIDENCE`、`LEGACY_DO_NOT_RUN`。新机只从第一类启动；第二类只回放；第三类不复制或禁止运行。

## 重要边界

- 当前采集主链路已经证明可实现 D405 双 IR `1280x720@30` + 外置 IMU `400Hz` 正式窗口零丢帧。
- 当前自动回环是候选工程，不是完整客户发布：历史声明真闭环只稳定通过 `5/12` 轮，完整隐藏真值动作矩阵仍缺。
- 世界 Z 动态误差还没有产品级解决，必须在新机继续；固定旋转调平没有跨会话泛化，Depth 因子缺真实水平面正样本，因此都不能假装已完成。完整接手边界见 `WORLD_Z_POSTPROCESS_HANDOFF.md`。
- 权威时间偏移只有 `td=-0.0117s`。旧 `7.36ms` 是失败历史，禁止恢复。
- 旧机 Humble 的 `build/`、`install/`、Python 3.10 `pyrealsense2` 只能作取证；Jazzy 必须重新编译。
- 另有91组旧/可重录会话位于旧机隔离区，不进入迁移包；其中本轮新增隔离10组、54.992GiB。完整清单见`RERECORDABLE_DATA_REMOVED.md`，在整包SHA校验前均可恢复。

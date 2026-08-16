# 迁移资产清单

复制脚本会在实际执行时将每一项的文件数与字节数写入 `metadata/SOURCE_TREE_SUMMARY.tsv`，并对最终包生成逐文件 SHA-256。下表是逻辑清单；动态统计与 `SHA256SUMS` 才是最终验收依据。

| 包内路径 | 旧机源路径 | 内容 | 必须性 |
| --- | --- | --- | --- |
| `projects/ego_vio_humble/` | `/home/robot/ego_vio_humble/` | 主工程、39组当前/关键回归录制数据、报告、测试、`.deps`、Git历史与全部未提交文件；当前目录约171GiB | 必须 |
| `projects/ego_vio_calib_kit/` | `/home/robot/桌面/ego_vio_calib_kit/` | 标定工具、IMU/世界Z实验、产品候选证据、轨迹图与 Git 历史 | 必须 |
| `ros2_ws_humble_snapshot/` | `/home/robot/ros2_ws/` | `vins_fusion_ros2`、`open_vins`、`ego_orbslam3_ros2` 和 Humble 构建取证 | 必须 |
| `calibration/calib_run_20260808/` | `/tmp/calib_run/` | `calib_imucam*.bag`、`calib_intrinsics.bag`、Kalibr PDF/YAML/TXT；约 10 GiB | 必须 |
| `diagnostics/realtime_runs/` | `/tmp/ego_vio_vins_live_*` | 最近实时 VINS 运行日志 | 推荐 |
| `diagnostics/orb_node_backup/` | `/tmp/orb_node_backup/` | 现存 ORB 节点小备份 | 推荐 |
| `memory/claude_project_memory/` | `/home/robot/.claude/projects/-home-robot----ego-vio-calib-kit/memory/` | `MEMORY.md` + 14 个调查记忆（含本轮Jazzy交接） | 必须 |
| `codex/user_skills/` | `/home/robot/.codex/skills/` | 本机自定义 Codex skills；不含 token、会话或全局配置 | 推荐 |
| `codex/RTK.md` | `/home/robot/.codex/RTK.md` | 本机机器人开发规则（若存在） | 推荐 |
| `legacy_and_protocols/` | 桌面/文档中的协议与旧归档 | KT-EX9-2 新旧协议、英文 Datasheet、旧迁移包 | 必须 |
| `metadata/` | 复制时生成 | 主机/包版本、Git bundle、dirty patch、清单、文件统计 | 必须 |
| `handoff/` | 本目录 | 给人和新 Codex 的交接说明与验证脚本 | 必须 |

GitHub `D405-MAXIMU` 的 `handoff/jazzy-20260816` 分支还内置
`components/ego_vio_calib_kit/` 和 `components/vins_fusion_ros2/` 两份无嵌套 `.git`的
源码快照，便于只拉一个仓库时开始 Jazzy 移植。完整历史仍以移动盘内的
原始仓库和 Git bundle 为准。

## 当前审计数量

- 初始审计 `/home/robot/ego_vio_humble/recordings`：130 个一级会话目录、120,989 个普通文件、308,050,589,221 字节。
- 精细分类后：18个`CURRENT_ACTIVE`（104.620GiB）、21个`REGRESSION_EVIDENCE`（62.946GiB）、91个`LEGACY_DO_NOT_RUN`（116.336GiB）。最后一类移到项目外隔离区，不进入新机迁移包。
- `/tmp/calib_run`：三份原始 bag 合计约 10.6 GB，最大文件为 `calib_imucam.bag`（4,085,822,909 字节）。
- 2026-08-16 容量预检：有效迁移源约185GiB，含20GiB余量需205GiB；当前移动固态可用222GiB，预检通过。

## 明确未包含

- `~/.codex/config.toml`、登录凭据、token、全局 Codex/Claude 会话数据库：它们可能含敏感凭据，且不是项目运行输入。
- `/media/robot/ego-recordings/lvzhi`：审计时判定为另一项目的数据，默认不混进本项目迁移包。
- `/home/robot/ego_vio_recordings_legacy_quarantine_20260816`：91个已被替代或可在新机重录的会话，共116.336GiB。保留在旧机作短期可恢复隔离，整包SSD校验通过后才可最终删除。
- `/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3`：记忆里曾引用，但当前旧机上已不存在。包内只有 `ros2_ws/src/ego_orbslam3_ros2` 包装层和 `/tmp/orb_node_backup`。新 Codex 必须从原 Git 来源恢复 ORB-SLAM3 core，不能声称已经带走。

## Git 双保险

完整项目目录已经包含 `.git` 和未跟踪文件。复制脚本还会在 `metadata/git/` 额外生成：

- 当前分支、HEAD、远端、标签、分支和完整 `git status`；
- tracked dirty 的 binary patch、staged binary patch；
- untracked 文件列表；
- `git bundle --all`，用于恢复所有已提交引用。

未跟踪文件不能进入 Git bundle，因此完整目录与最终 SHA-256 仍是主备份。

# 迁移审计发现

## 主机与容量

- 旧机：Ubuntu 22.04.5 / x86_64 / ROS 2 Humble 工作区；根盘 2.8TiB，已用97%，只剩约101GiB。
- 已挂载 `/media/robot/ego-recordings` 是 238.5GiB 的 ext4 盘，不一定是用户准备的新移动固态；复制脚本不得自行选盘。
- 已确认主资产：`/home/robot/ego_vio_humble` 288GiB、`/home/robot/桌面/ego_vio_calib_kit` 1.2GiB、`/home/robot/ros2_ws` 2.8GiB、长期记忆68KiB、本地技能2.4MiB。
- 已知主资产至少约292GiB；根盘空间不足以先制作一份完整本地副本，因此必须直接流式复制到移动固态。

## 迁移原则

- Jazzy 不能直接复用 Humble 的 `build/ install/ log/` 或 Python 3.10 二进制扩展；这些只作旧机取证，不作为新机运行产物。
- 完整源码树与 `.git` 必须复制，另生成 git bundle/dirty patch 作为双保险。
- DB3 等原始数据不重新压缩，避免额外空间和无意义耗时。

## 数据与源码分布

- `ego_vio_humble/recordings` 单独占288GiB，是迁移主体；最大单会话约11GiB，包含早期标定、全流、双IR、失败对照、产品候选和盲测数据，不能只挑“看起来有用”的会话。
- `ego_vio_humble` 其余源码/报告仅约百余MiB；`.deps` 18MiB含RSUSB本机二进制，只作证据，Jazzy需重编。
- `ego_vio_calib_kit` 1.2GiB，其中产品候选证据约579MiB、Git历史109MiB、IMU标定/轨迹/世界Z失败实验均需保留。
- ROS工作区源码为 `vins_fusion_ros2`、`open_vins`、`ego_orbslam3_ros2`；整个工作区2.8GiB，其中源码547MiB、Humble build/install/log约2.2GiB。
- 相关根路径下确认3个主Git仓库：`ego_vio_humble`、`ego_vio_calib_kit`、`ros2_ws/src/vins_fusion_ros2`。产品候选目录内还有4个隔离VINS仓库，随标定包整体复制即可。
- 记忆里提到的 `/home/robot/ego_pipeline/work/toolchains/ORB_SLAM3` 当前不存在，不能在交接文档中假装已打包；必须记录为缺失/待在Git远端恢复。

精细审计后将81个被替代旧会话（61.344GiB）原子移到项目外隔离区；有效迁移包只保留18个当前RSUSB PASS和31个仍被报告/回归需要的证据会话，主工程当前约226GiB。上面的288GiB是隔离前基线，用于核对没有凭空丢数据。

## 关键临时目录与协议

- `/tmp/calib_run` 是必须资产而非可丢缓存：三份原始Kalibr bag约10GiB，并包含08-08权威`td=-0.0117s`的YAML/PDF/TXT。
- 需带走桌面两份KT-EX9-2新旧协议、英文Datasheet、手工协议文本和三份旧迁移归档；不复制微信目录里的重复副本。
- `/tmp/ego_vio_vins_live_*`和`/tmp/orb_node_backup`体积小，作为实时故障诊断一起带走。

## 当前运行状态边界

- 最新实时问题根因为FAIL手工陀螺矩阵被错误加载；主项目工作树已增加运行时门禁并切到“六面加速度校正+原始陀螺”，定向6项测试通过，但尚未提交，必须靠完整工作树/dirty patch迁移。
- 自动回环历史冻结回归只通过5/12正闭环轮，完整客户发布仍NOT_READY。
- 固定世界Z旋转不跨会话泛化，Depth因子库存缺水平面正样本；当前Z不是完成状态。
- Jazzy/Python3.12不能加载当前硬编码的`pyrealsense2.cpython-310...so`；迁移后第一项代码适配是按实际`EXT_SUFFIX`发现并重建RSUSB 2.58.2。

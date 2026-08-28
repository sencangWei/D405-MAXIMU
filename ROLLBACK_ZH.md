# 第二套 UMI 产品 V1 回滚

正式发布不会删除候选代码、候选镜像或原始标定数据。

回滚目标：

- 候选工程：`/home/robot/umi_docker_device2_d405_formal_20260828`
- 候选镜像 ID：`sha256:8aead3e15158e9ac42780f24dd45c846f28558fe4457cc3f846da4b6e41fb5fa`
- 候选标定：world-Z `attempt_002/candidate_runtime`

触发条件：正式镜像完整性、设备身份、ROS图、传感器频率、时间戳、轨迹、夹爪显示、
录制或后处理任一正式验收失败。

回滚是一次显式生产变更。先停止当前命令，再由操作者确认后使用候选工程的
`candidate-realtime`/`candidate-capture`；不要覆盖或删除正式/候选标定槽。

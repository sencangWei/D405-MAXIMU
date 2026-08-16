# ROS 2 Jazzy 新机首次恢复清单

## 已验证与未验证边界

- 已在旧机 Ubuntu 22.04 / ROS 2 Humble 验证：主工程Python测试、标定工具测试、修改版VINS构建与C++测试、迁移脚本小样本校验。
- 已做源码级Jazzy适配：当前采集/实时入口不再写死Humble路径或CPython3.10扩展名。
- **尚未在目标Jazzy电脑验证**：Jazzy clean build、D405/IMU设备权限、30fps/400Hz HIL、长时间稳定性。到新机完成本文件第5～7节前，状态只能是`READY`，不能称为`ACCEPTED`。
- 世界 Z 后处理仍未完成；新机在复现基线后必须按 `WORLD_Z_POSTPROCESS_HANDOFF.md` 接续，禁止启用旧固定调平候选或事后强制压平。

## 同一私有仓库的两条版本线

- `handoff/jazzy-20260816`：Jazzy迁移候选；采集和实时入口已自动识别ROS发行版、工作区及Python ABI。
- `release/humble-known-good-20260816`：冻结的Ubuntu22.04 + ROS2 Humble已验证基线；若Jazzy移植无法按期通过，可重装Humble后从该分支恢复。

两条分支都在`https://github.com/sencangWei/D405-MAXIMU`，不是两个仓库。录制数据和完整未跟踪证据只在移动盘交接包中。

## 1. 先验证迁移介质

在移动固态包根目录执行（本次旧机实际路径位于移动盘`RECOVERY/`下）：

```bash
./handoff/verify_bundle.sh .
```

只有 `VERIFICATION PASS` 后才开始移植。保留移动固态作为只读母备份；开发副本放到新机内部盘。

## 2. 不复用旧二进制

以下内容只作取证，不能直接运行：

- `ros2_ws_humble_snapshot/build/`
- `ros2_ws_humble_snapshot/install/`
- `ros2_ws_humble_snapshot/log/`
- `projects/ego_vio_humble/.deps/` 内的 CPython 3.10 扩展
- 所有通过 `/opt/ros/humble` 构建的可执行文件

Jazzy 通常是 Ubuntu 24.04 + Python 3.12，ABI 与 Humble/Python 3.10 不兼容。

## 3. 把项目恢复到新机内部盘

推荐使用移动盘里的完整工作树，它包含Git未跟踪报告和录制数据：

```bash
mkdir -p ~/D405-MAXIMU ~/ego_vio_calib_kit
rsync -aH --info=progress2 \
  <迁移包>/projects/ego_vio_humble/ ~/D405-MAXIMU/
rsync -aH --info=progress2 \
  <迁移包>/projects/ego_vio_calib_kit/ ~/ego_vio_calib_kit/
```

若暂时只恢复代码，可从唯一私有仓库拉取：

```bash
git clone -b handoff/jazzy-20260816 \
  https://github.com/sencangWei/D405-MAXIMU.git ~/D405-MAXIMU
```

录制数据随后从迁移包的`projects/ego_vio_humble/recordings/`复制到项目同名目录。

## 4. 建立干净 Jazzy 工作区

如果直接从 GitHub 的 `D405-MAXIMU` 交接分支恢复，主工程已在
`components/ego_vio_calib_kit/` 和 `components/vins_fusion_ros2/` 内携带配套源码快照。
它们可复制到新建工作区的 `src/`；不要复制 Humble 的 `build/`、`install/`或
`log/`。移动盘交接包仍保留原始三个 Git 仓库与 bundle，用于恢复完整历史。

建议：

```bash
mkdir -p ~/ego_vio_jazzy_ws/src
cp -a ~/D405-MAXIMU/components/vins_fusion_ros2 \
  ~/ego_vio_jazzy_ws/src/vins_fusion_ros2
cd ~/ego_vio_jazzy_ws
source /opt/ros/jazzy/setup.bash
rosdep update
rosdep install --from-paths src --ignore-src -r -y --rosdistro jazzy
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
colcon test --packages-select vins_fusion_ros2
colcon test-result --verbose
```

若 Jazzy 编译报 ROS 头文件 `.h`/`.hpp` 差异，按报错逐处最小修复并加测试；不要复制旧 `install/` 掩盖问题。

## 5. 重建 librealsense RSUSB

旧机有效链路是librealsense `2.58.2` + `FORCE_RSUSB_BACKEND=ON`。交接分支已改成通过`sysconfig`自动发现当前Python ABI；Jazzy上必须重新构建，不能复用迁移包内CPython3.10文件。

```bash
sudo apt update
sudo apt install -y build-essential cmake ninja-build git pkg-config \
  libusb-1.0-0-dev python3-dev python3-pip python3-venv ros-dev-tools

cd ~/D405-MAXIMU
./scripts/build_librealsense_rsusb.sh
PYTHONPATH="$PWD/.deps/librealsense-rsusb-2.58.2/python" \
  python3 -c 'import pyrealsense2 as rs; print(rs.__file__)'
```

不要同时加载系统 pyrealsense2 和本地 RSUSB pyrealsense2。启动日志必须记录实际模块路径、版本、相机固件与 USB 类型。

## 6. 软件验证顺序

```bash
cd ~/D405-MAXIMU
python3 -m pip install --user -r requirements.txt
python3 -m pytest -q tests

export EGO_VIO_ROS_DISTRO=jazzy
export EGO_VIO_ROS_WS="$HOME/ego_vio_jazzy_ws"
source /opt/ros/jazzy/setup.bash
source "$EGO_VIO_ROS_WS/install/setup.bash"
```

1. 在两个 Python 项目分别运行针对性测试，再运行正式 `pytest tests`。
2. 对 `vins_fusion_ros2` 做 Release build、C++ 单测和无硬件节点配置烟测。
3. 确认 `td=-0.0117`、`estimate_td=0`、回放 shift=0，扫描并人工审计任何 `7.36` 运行引用。
4. 用迁移包内稳定 DB3 离线回放，不接相机也能验证 VINS、回环与报告链。
5. 最后才接 D405/IMU 做 10秒 → 60秒 → 90分钟 HIL。

## 7. HIL 接受门

先接D405和IMU但不要移动设备，按风险递增执行：

```bash
cd ~/D405-MAXIMU
./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 10
./capture_d405_720p_rgb_stereo_ir_rsusb.sh --duration 60
./run_vins_realtime.sh smoke
```

逐条检查新会话`acceptance.json`。10秒和60秒都通过后，再由操作员监督做90分钟长稳测试；不要在无人看守时做会碰撞或移动硬件的动作。

- D405 左IR/右IR/彩色均为 1280×720@30；帧号零跳变、零重复、时间戳严格递增。
- IMU 399–401Hz；正式窗口坏帧、resync、counter drop、串口重连均为0。
- 每条会话 `acceptance.json` 为 PASS；不可只看终端“启动成功”。
- 相机/IMU timestamp domain 与 `-11.7ms` 单次补偿符合当前合同。
- 先静止/微动初始化，再做水平、真实升降、原地旋转与快速运动；快速运动曝光不超过当前 8ms 门限。
- 测试时清理残留节点并使用独立 `ROS_DOMAIN_ID`。

## 8. Git 恢复原则

移动盘完整工作树是含数据/报告的主来源；GitHub私有仓库的`handoff/jazzy-20260816`分支是代码恢复源。若工作树损坏：

1. 用 `metadata/git/*.bundle` 恢复全部已提交 refs；
2. 应用 tracked/staged binary patch；
3. 依据 `untracked_files.txt` 从迁移包完整树恢复未跟踪文件；
4. 对照 `status.txt` 和 SHA-256。

任何新提交前先打可回滚标签/分支，且不要一次提交 288 GiB 的 `recordings/`。

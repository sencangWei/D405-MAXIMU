# ROS 2 Jazzy 新机首次恢复清单

## 1. 先验证迁移介质

在移动固态包根目录执行：

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

## 3. 建立干净 Jazzy 工作区

建议：

```bash
mkdir -p ~/ego_vio_jazzy_ws/src
cp -a <迁移包>/ros2_ws_humble_snapshot/src/. ~/ego_vio_jazzy_ws/src/
cd ~/ego_vio_jazzy_ws
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

若 Jazzy 编译报 ROS 头文件 `.h`/`.hpp` 差异，按报错逐处最小修复并加测试；不要复制旧 `install/` 掩盖问题。

## 4. 重建 librealsense RSUSB

旧机有效链路是 librealsense `2.58.2` + `FORCE_RSUSB_BACKEND=ON`。当前脚本和包装器仍有 `pyrealsense2.cpython-310-x86_64-linux-gnu.so` 的硬编码；Jazzy/Python 3.12 必须先改为自动发现当前 ABI 文件，例如通过：

```bash
python3 - <<'PY'
import sysconfig
print(sysconfig.get_config_var('EXT_SUFFIX'))
PY
```

重点审计：

```text
projects/ego_vio_humble/scripts/build_librealsense_rsusb.sh
projects/ego_vio_humble/capture_d405_720p_rgb_stereo_ir_rsusb.sh
projects/ego_vio_humble/capture_d405_720p_depth_stereo_ir_rsusb.sh
projects/ego_vio_humble/run_vins_realtime.sh
```

不要同时加载系统 pyrealsense2 和本地 RSUSB pyrealsense2。启动日志必须记录实际模块路径、版本、相机固件与 USB 类型。

## 5. 软件验证顺序

1. 在两个 Python 项目分别运行针对性测试，再运行正式 `pytest tests`。
2. 对 `vins_fusion_ros2` 做 Release build、C++ 单测和无硬件节点配置烟测。
3. 确认 `td=-0.0117`、`estimate_td=0`、回放 shift=0，扫描并人工审计任何 `7.36` 运行引用。
4. 用迁移包内稳定 DB3 离线回放，不接相机也能验证 VINS、回环与报告链。
5. 最后才接 D405/IMU 做 10秒 → 60秒 → 90分钟 HIL。

## 6. HIL 接受门

- D405 左IR/右IR/彩色均为 1280×720@30；帧号零跳变、零重复、时间戳严格递增。
- IMU 399–401Hz；正式窗口坏帧、resync、counter drop、串口重连均为0。
- 每条会话 `acceptance.json` 为 PASS；不可只看终端“启动成功”。
- 相机/IMU timestamp domain 与 `-11.7ms` 单次补偿符合当前合同。
- 先静止/微动初始化，再做水平、真实升降、原地旋转与快速运动；快速运动曝光不超过当前 8ms 门限。
- 测试时清理残留节点并使用独立 `ROS_DOMAIN_ID`。

## 7. Git 恢复原则

完整工作树是主来源；GitHub 不是完整来源，因为三个仓库都有大量未提交/未跟踪证据。若工作树损坏：

1. 用 `metadata/git/*.bundle` 恢复全部已提交 refs；
2. 应用 tracked/staged binary patch；
3. 依据 `untracked_files.txt` 从迁移包完整树恢复未跟踪文件；
4. 对照 `status.txt` 和 SHA-256。

任何新提交前先打可回滚标签/分支，且不要一次提交 288 GiB 的 `recordings/`。

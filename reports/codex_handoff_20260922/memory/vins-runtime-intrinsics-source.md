---
name: vins-runtime-intrinsics-source
description: VINS 内参来自 active_runtime_calibration yaml(非 db3 camera_info);preflight 只查存在性→分辨率不匹配是静默错;installer 拒绝覆盖且无注销入口
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-17T14:33:45.344Z
---

VINS 的内参**不**从 db3 的 `camera_info` 读，而是读
`$DATA_ROOT/active_runtime_calibration/{vins_config,left,right,device_config}.yaml`
（由 `device2_d405_control.py` 的 `install-runtime-calibration` 子命令装入）。
MASt3R-SLAM 侧相反——它从 db3 的 `camera_info` / `Stereo_Baseline` 读，
而那正是 librealsense 按实际协商 profile 写的，故采集几何一变它自动跟着变。

**为什么重要**：`lighthouse_umi_preflight.py` 对相机 yaml **只查存在性、不查分辨率**，
所以"用 848×480 录制却装 720p 标定"会**静默用错内参**（fx 差 1.5 倍），preflight 不报。

**How to apply**：
- 任何采集几何变更（分辨率/帧率）都要同时换 VINS 标定集，否则静默错。
- `active_runtime_calibration` 的 manifest 必须含 `camera_imu_td_s` ——
  `cmd_install_runtime()`/`active_runtime()` 直接索引它，缺了就是
  "四个标定文件哈希全对但根本装不上"。只对文件哈希不足以判定可安装性。
- 装之前先跑 `device2_d405_control.py` 的校验（或见
  `docker2_release/.backup/20260917_848x480_runtime_calibration/test_install_848.py`
  的隔离实测法：把 DATA/ACTIVE_* 指到临时目录，调官方函数）。
- **两套标定互斥**：`cmd_install_runtime` 在 `ACTIVE_MANIFEST.exists()` 时直接
  `Blocked`（"拒绝静默覆盖"），且**没有注销子命令**。切换几何必须人为先移除
  `$DATA_ROOT/active_runtime_calibration`（破坏性）。回滚 = 装回
  `formal_runtime_calibration_installed/`（部署中那份 720p 集，已从镜像取出纳入版本管理）。

**848×480@90 已核实（2026-09-17）**：容器内脚本按 `args.config.parent` 解析
`left.yaml`/`right.yaml`（`test_vins_auto_loop.py:533`），所以 **通过
`UMI_PRECISION_VINS_CONFIG` 覆盖 `--config` 就能整套换掉内参**，派生集自包含。
`lighthouse_umi_precision_workflow.sh:6` 现已可覆盖（默认值逐位不变）。
另一条独立轴：`camera_info` 只有 `body_T_cam0` 外参被用于打分，
而外参在各 config 血脉间逐位相同 ⇒ **打分环节无混淆**，混淆只在 VINS 回放（内参+环路口）。

被中断的 848 take 残片（只读 sqlite 探查）独立佐证了派生正确性：
`Infrared_{1,2}/camera_info` 记录 `848x480; fx=fy=430.099; ppx=422.966; ppy=236.388;
coeffs 全 0`，与派生集逐位吻合；三路各 2674 帧 / 29.75 s = **89.9 fps**，
即设备确实跑满了 848x480@90（"撑不住"的担忧已排除）。
另：IR 848 值 ×(1280/848) = 649.2 = 部署版 `left.yaml` 的 `fx=649.206665039062`
⇒ **`cam0/cam1` 对应 IR 流**，派生方向无误。

相关：[[jazzy-handoff-20260816]]、[[capture-pipeline-ab-result]]、[[capture-stop-hazard]]

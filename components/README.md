# Vendored source components for Jazzy handoff

This directory makes the `D405-MAXIMU` handoff branch self-contained at the
source level. It intentionally contains no nested `.git` directories, ROS
build/install/log outputs, recordings, credentials, or host-specific binary
extensions.

## ego_vio_calib_kit

- Source repository: `/home/robot/桌面/ego_vio_calib_kit`
- Exported local branch: `handoff/jazzy-20260816`
- Exported commit: `097ef053b4b891849543ea552c128391a6e9bb25`
- Purpose: D405/KT-EX9-2 calibration tools and retained calibration inputs.

## vins_fusion_ros2

- Source repository: `/home/robot/ros2_ws/src/vins_fusion_ros2`
- Exported local branch: `handoff/jazzy-20260816`
- Exported commit: `8198dab5952e4458fb930a23c79ef7add0168dc6`
- Purpose: the modified ROS 2 VINS-Fusion source used by this project.

On ROS 2 Jazzy, copy these two directories into a clean workspace `src/` and
rebuild. Do not copy the Humble `build/`, `install/`, or `log/` directories.
The mobile-SSD handoff additionally preserves the original repositories and
their Git bundles for full history recovery.

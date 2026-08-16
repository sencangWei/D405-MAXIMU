============================================
ego_vio Ubuntu 标定工具包
2026-08-01
============================================

包含：
  ego_vio_ubuntu_calib.tar.gz  — 项目代码(采集+转bag脚本)
  calib_setup.sh               — 一键安装依赖

用法：
  1. 解压: tar xzf ego_vio_ubuntu_calib.tar.gz
  2. 安装: bash calib_setup.sh
  3. 修改 config/devices_ubuntu.yaml (IMU端口、相机序列号)
  4. 采集标定数据 (见下方)
  5. 拷回 bag 到 ros@192.168.113.224 跑 Kalibr

=== 采集命令 ===

相机内参 (手持标定板多角度拍):
  python3 scripts/collect_calib_data.py --config config/devices_ubuntu.yaml --mode camera --phase-secs 8

相机-IMU外参 (标定板贴墙, 晃相机+IMU):
  python3 scripts/collect_calib_data.py --config config/devices_ubuntu.yaml --mode imucam --phase-secs 10

=== 转 bag ===
  python3 scripts/convert_to_kalibr_bag.py --input recordings/<session> --output calib.bag

=== 传输 ===
  scp calib.bag ros@192.168.113.224:~/calib_data/

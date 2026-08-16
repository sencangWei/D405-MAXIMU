# LEGACY_DO_NOT_RUN

本目录的 `calibration_candidate.yaml` 明确记录：

- `acceptance.status: FAIL`
- `runtime_applied: false`
- 手工90°陀螺矩阵A/B使闭环约从3.60cm恶化到17.84cm

它只作失败实验与原始采样证据，禁止加载到实时采集或VINS。当前主工程运行时加载器会主动拒绝该文件。

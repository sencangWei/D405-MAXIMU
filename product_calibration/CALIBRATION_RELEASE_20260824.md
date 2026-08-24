# 产品标定工具 v4.1（2026-08-24）

Git 引用：

- 分支：`release/calibration-product-v4-20260824`
- 标签：`calibration-product-v4.1-20260824`
- 远端：`sencangWei/D405-MAXIMU`

本仓库是正式产品的独立标定和证据仓库，不是 VINS 运行工作区。正式运行配置仍由
`/home/robot/ego_vio_humble/config/product_live_stm32/` 唯一提供；本仓库脚本产生的
候选参数必须经过分阶段门禁和端到端 A/B，不能直接覆盖产品配置。

v4.1 继承 v4 的全部标定合同，并清理了不参与正式工作流的历史
Jazzy/OpenVINS 副本；采集、转 bag 和 RSUSB 依赖现在唯一来自正式 Humble
产品运行时。

v4 新增并冻结：

- UMI 手动夹爪闭合／张开双曲线空载状态模型；
- 66.90 mm 完全张开间距、双边闭合距离、单边行程和 closure ratio 的明确定义；
- App v1 JSON Schema、串口所有权、健康状态和 JSONL 记录接口；
- 3 个盲测点：最大绝对误差 1.195 mm、平均绝对误差 0.829 mm；
- `loaded_object_size_valid=false`：软垫受力时不冒充器械尺寸。

必须永久保留但不推送普通 Git 历史的大体积证据包括：`calibration_sessions*/`、
`camera_bench_results/`、`camera_imu_bench_results/`、`imu_bench_results/` 和 `reports/`。
这些目录由本机备份/交付介质保留；GitHub 只保存代码、手册、摘要证据和哈希。

# 世界 Z 开源方案复核（2026-08-23）

## 当前结论

保持现有正式链，不启用 30 姿态加速度矩阵，也不启用本轮 Depth 平面软修正。
现有 yaw-only 全率投影已经消除了回环 pitch/roll 把 XY 旋入 Z 的实现问题；剩余 Z
误差来自原始 VIO 的弱可观测漂移，以及 VINS-Fusion 4DoF 位姿图对三维平移误差的分配。

VINS-Fusion 官方代码明确把 IMU 模式称为 `x, y, z, yaw` 四自由度位姿图，而不是
`x, y, yaw + 固定 z`：

- https://github.com/HKUST-Aerial-Robotics/VINS-Fusion/blob/master/loop_fusion/src/pose_graph.cpp

因此单靠原生回环不能知道一段运动是否应保持恒高。maplab/ROVIOLI 也明确记录了地面
车辆平面运动缺少 Z 方向激励时可能出现漂移或尺度退化：

- https://github.com/ethz-asl/maplab/wiki/ROVIOLI-Introduction

## 可用方案与本产品适配性

### 1. 静止更新（ZUPT）：可作为低风险实验，但不能单独解决运动中 Z

OpenVINS 提供开源 ZUPT，在系统确实静止时用 IMU 和视觉视差门控更新状态：

- https://docs.openvins.com/update-zerovelocity.html
- https://github.com/rpng/open_vins/blob/master/ov_msckf/src/update/UpdaterZeroVelocity.cpp

它适合本产品起点/终点明确静止的场景，可帮助速度和 bias 收敛；但官方说明这种合成
观测本质上更接近“零加速度/恒速”，必须再做速度或视差门，不能在匀速平移时误触发。
它不能提供运动中的绝对高度，也不能替代相机—IMU时间/空间标定。

### 2. 平面/结构因子：理论正确，但当前 D405 视场 A/B 已拒绝

Kimera-VIO 开源了 point-plane/parallel-plane 等结构规则后端，说明把平面作为有噪声
几何观测加入因子图是成熟方向：

- https://github.com/MIT-SPARK/Kimera-VIO

本项目已经实现了更小范围的等价安全原型：D405 Depth 下半图 RANSAC、重力法向门、
创新门、因果有界且只改世界 Z。新水平组和真实升降组盲 A/B 的整体结果都是
`NO_NET_IMPROVEMENT`：水平平面 P95 `8.61→9.30 mm`，升降末端残差
`39.351→39.736 mm`。原因是升降前没有持续看到同一地面，升降后首次看到地面时无法
判断当前高度是正确基准还是已有漂移。证据见：

- `.planning/depth_plane_height_factor_20260823/DEPTH_PLANE_Z_AB_REPORT_ZH.md`

若以后相机视场能在升降前、升降中和回落后持续看到同一平面，或实现带身份关联的
plane landmark，才值得重新验收；不能只把“当前看到一个水平面”当成旧地面。

### 3. 条件距离/高度观测：可靠性最高，但需要持续可见的物理参考

PX4 的开源 EKF 使用量程、创新门、最大倾角、最大速度和信号有效时长来条件融合
range finder 高度，这种设计比固定 `Z=0` 更符合本产品有真实升降的需求：

- https://github.com/PX4/PX4-ECL/blob/master/EKF/common.h
- https://github.com/PX4/PX4-ECL/blob/master/EKF/ekf.h

对本产品而言，这意味着 D405 Depth 必须持续对着同一地面/顶面，或增加专用测距传感器；
如果参考平面离开视野，绝对高度会重新变成不可观测，不能靠软件猜测。

### 4. 全程平面运动约束：不采用

固定高度、固定 roll/pitch 或按轨迹拟合平面，能让水平样本好看，但会抹掉真实
25–26 cm 升降，违反产品安全门。除非上层系统明确、可信地给出“当前必为平面模式”，
否则不能启用。

## 推荐顺序

1. 正式链维持当前原始 IMU、factory 相机参数、新装配外参/`td`、yaw-only 回环投影。
2. Z 与三维闭环分开报告：水平 Z P5–P95、真实升降保留率、回落残差分别验收。
3. 若继续投入，第一候选是独立分支的严格静止 ZUPT，只在起点/终点静止段启用并做
   水平、升降和动态物体负例 A/B。
4. 要承诺运动中的绝对高度，优先改变 Depth 视场或增加专用测距，而不是继续扫描 IMU
   矩阵或把轨迹压平。

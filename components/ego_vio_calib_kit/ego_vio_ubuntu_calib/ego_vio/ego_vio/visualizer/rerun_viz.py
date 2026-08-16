"""Rerun 实时可视化: 双手位姿轨迹 + 姿态坐标系 + 相机视锥 + 图像。

内存管理: Rerun 只用于实时展示 —— 图像只保留最新一帧(覆盖),
轨迹点全程保留(点很小, 内存大头是图像)。

限频: log_pose 由 IMU 回调以 400Hz 触发, 内部对坐标轴(30Hz)、
轨迹抽点(20Hz)、轨迹重绘(15Hz)、历史姿态轴(2Hz)、图像(15Hz)、
视锥(10Hz)分别限流。

布局(Blueprint 显式指定, 保证 3D 视图一定出现):
  左: 3D 视图 world/** —— 三平面坐标网格 + 历史姿态轴 + 相机视锥 + 全程轨迹
  右: 每单元一个 2D 图像视图 + stats 文本

实体:
  world/grid                         三平面参考网格(动态自适应, 紧包轨迹)
  world/axis_lines                   三坐标轴粗线(以轨迹起点为原点)
  world/axis_labels                  坐标轴刻度标签(±米)
  world/{unit}/axes                  当前姿态 RGB 坐标轴(30Hz)
  world/{unit}/pose_axes_history     历史姿态轴(自适应密度, 2Hz 重绘)
  world/{unit}/trajectory            全程轨迹线(20Hz 抽点, 15Hz 重绘)
  world/{unit}/frustum               相机视锥(10Hz)
  world/{unit}/image                 最新彩色帧(15Hz)
  stats                              帧率/丢帧/姿态统计文本

自适应显示:
  - 以第一个轨迹点为坐标原点(0,0,0)
  - 六个边界随轨迹运动方向独立扩展, 轨迹碰到哪一侧就只顶开哪一侧
  - 历史姿态轴按轨迹弧长等距采样, 始终只显示约 N 个
"""

from __future__ import annotations
from collections import deque
from typing import Optional

import math

import numpy as np


# ---------- 限频工具 ----------

def _rate_due(ts: float, last_ts: float, hz: float) -> tuple:
    """按理想时间相位限频, 避免输入频率不整除时刷新率持续偏低。"""
    period = 1.0 / hz
    elapsed = ts - last_ts
    if elapsed < 0.0:
        return True, ts
    if elapsed + 1e-9 < period:
        return False, last_ts
    periods = max(1, math.floor((elapsed + 1e-9) / period))
    return True, last_ts + periods * period


# ---------- 四元数/旋转工具 ----------

def _quat_rotate(pts: np.ndarray, q: np.ndarray) -> np.ndarray:
    """用四元数 q(xyzw) 旋转点集 pts (N,3)。"""
    x, y, z, w = q
    pts = np.asarray(pts, dtype=float)
    uv = np.cross(np.array([x, y, z]), pts)
    uuv = np.cross(np.array([x, y, z]), uv)
    return pts + 2.0 * (w * uv + uuv)


def _quat_conj(q: np.ndarray) -> np.ndarray:
    return np.array([-q[0], -q[1], -q[2], q[3]])


# ---------- 场景辅助 ----------

def _axis_ticks(start: float, stop: float, step: float) -> list:
    """生成从 start 到 stop 的刻度值列表, 对齐到 step 整数倍, 跳过 0。"""
    if stop < start:
        return []
    s = math.ceil(start / step) * step
    e = math.floor(stop / step) * step
    ticks = []
    v = s
    while v <= e + 1e-9:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _nice_step(max_len: float) -> float:
    """根据最大边长选一个好看的刻度步长。"""
    if max_len <= 0.5:
        return 0.05
    if max_len <= 1.5:
        return 0.1
    if max_len <= 5.0:
        return 0.25
    if max_len <= 15.0:
        return 0.5
    if max_len <= 40.0:
        return 1.0
    if max_len <= 100.0:
        return 2.0
    return 5.0


def _format_m(v: float) -> str:
    """把米值格式化成简洁字符串。"""
    v = round(v, 6)
    if abs(v) < 1e-9:
        v = 0.0
    if abs(v) < 0.01:
        return f"{v:.2f}"
    if abs(v) < 1.0:
        return f"{v:.2f}"
    return f"{v:.1f}"


def _bbox_scene(bbox_min: np.ndarray, bbox_max: np.ndarray,
                n_lines: int = 16) -> tuple:
    """生成 MATLAB 风格的三平面填充网格、坐标轴、刻度标签。

    返回:
      planes:      (vertex_positions, triangle_indices) 三个填充平面
      grid_lines:  [[[x1,y1,z1],[x2,y2,z2]], ...] 三平面网格线
      axis_lines:  [[[x1,y1,z1],[x2,y2,z2]], ...] 三条坐标轴线段
      labels:      [(text, position, color), ...] 轴刻度标签
      step:        刻度步长
    """
    bbox_min = np.asarray(bbox_min, dtype=float)
    bbox_max = np.asarray(bbox_max, dtype=float)
    p0 = bbox_min
    p1 = bbox_max

    max_len = float((p1 - p0).max())
    step = _nice_step(max_len)

    grid_lines = []
    labels = []
    label_color = [140, 140, 140]  # 淡灰色, 不抢眼

    # 三个坐标轴(粗线): 沿着包围盒边缘, 像 MATLAB 那样
    axis_lines = [
        # X 轴: 底面前边缘 (y=p0[1], z=p0[2])
        [[p0[0], p0[1], p0[2]], [p1[0], p0[1], p0[2]]],
        # Y 轴: 底面右边缘 (x=p1[0], z=p0[2])
        [[p1[0], p0[1], p0[2]], [p1[0], p1[1], p0[2]]],
        # Z 轴: 左前垂直边 (x=p0[0], y=p0[1])
        [[p0[0], p0[1], p0[2]], [p0[0], p0[1], p1[2]]],
    ]
    # 轴名称放在各轴中部外侧, 避免遮挡端点数字。
    labels.append((
        "X (m)",
        [(p0[0] + p1[0]) / 2.0, p0[1] - step, p0[2]],
        [255, 80, 80],
    ))
    labels.append((
        "Y (m)",
        [p1[0] + step, (p0[1] + p1[1]) / 2.0, p0[2]],
        [80, 255, 80],
    ))
    labels.append((
        "Z (m)",
        [p0[0] - step, p0[1], (p0[2] + p1[2]) / 2.0],
        [80, 150, 255],
    ))

    xt = _axis_ticks(p0[0], p1[0], step)
    yt = _axis_ticks(p0[1], p1[1], step)
    zt = _axis_ticks(p0[2], p1[2], step)

    def label_ticks(ticks: list, start: float, stop: float) -> list:
        """只显示规整的稀疏刻度, 不显示持续变化的任意端点小数。"""
        selected = ticks[::2]
        if ticks and selected[-1] != ticks[-1]:
            selected.append(ticks[-1])
        return selected

    # ---------- 填充平面顶点 (两个三角形拼一个矩形) ----------
    # XY 平面 z=p0[2]
    v_xy = [
        [p0[0], p0[1], p0[2]],
        [p1[0], p0[1], p0[2]],
        [p1[0], p1[1], p0[2]],
        [p0[0], p1[1], p0[2]],
    ]
    # XZ 平面 y=p1[1]
    v_xz = [
        [p0[0], p1[1], p0[2]],
        [p1[0], p1[1], p0[2]],
        [p1[0], p1[1], p1[2]],
        [p0[0], p1[1], p1[2]],
    ]
    # YZ 平面 x=p0[0]
    v_yz = [
        [p0[0], p0[1], p0[2]],
        [p0[0], p1[1], p0[2]],
        [p0[0], p1[1], p1[2]],
        [p0[0], p0[1], p1[2]],
    ]
    vertex_positions = v_xy + v_xz + v_yz
    triangle_indices = [
        [0, 1, 2], [0, 2, 3],       # XY
        [4, 5, 6], [4, 6, 7],       # XZ
        [8, 9, 10], [8, 10, 11],    # YZ
    ]

    # ---------- XY 平面 (z = p0[2], 底面) ----------
    z = p0[2]
    for x in xt:
        grid_lines.append([[x, p0[1], z], [x, p1[1], z]])
    for y in yt:
        grid_lines.append([[p0[0], y, z], [p1[0], y, z]])
    # X 轴刻度: 沿底面前边缘, 放在外侧 (y 负方向)
    for x in label_ticks(xt, p0[0], p1[0]):
        labels.append((_format_m(x), [x, p0[1] - step * 0.4, p0[2]], label_color))
    # Y 轴刻度: 沿底面右边缘, 放在外侧 (x 正方向)
    for y in label_ticks(yt, p0[1], p1[1]):
        labels.append((_format_m(y), [p1[0] + step * 0.4, y, p0[2]], label_color))

    # ---------- XZ 平面 (y = p1[1], 右侧面) ----------
    y = p1[1]
    for x in xt:
        grid_lines.append([[x, y, p0[2]], [x, y, p1[2]]])
    for z in zt:
        grid_lines.append([[p0[0], y, z], [p1[0], y, z]])
    # Z 轴刻度: 沿左前垂直边, 放在外侧 (y 负方向)
    for z in label_ticks(zt, p0[2], p1[2]):
        labels.append((_format_m(z), [p0[0] - step * 0.4, p0[1], z], label_color))

    # ---------- YZ 平面 (x = p0[0], 左侧面) ----------
    x = p0[0]
    for y in yt:
        grid_lines.append([[x, y, p0[2]], [x, y, p1[2]]])
    for z in zt:
        grid_lines.append([[x, p0[1], z], [x, p1[1], z]])

    return vertex_positions, triangle_indices, grid_lines, axis_lines, labels, step


def _resample_by_arclength(trajectory: list, n_samples: int = 60) -> list:
    """按轨迹弧长等距采样, 返回索引列表。

    trajectory: [(ts, t, q), ...], t 为位置(3,), q 为四元数 xyzw
    """
    m = len(trajectory)
    if m <= n_samples:
        return list(range(m))
    pts = np.array([p[1] for p in trajectory])
    seg_len = np.linalg.norm(pts[1:] - pts[:-1], axis=1)
    dists = np.concatenate(([0.0], np.cumsum(seg_len)))
    total = dists[-1]
    if total < 1e-6:
        return list(range(m))
    sample_dists = np.linspace(0.0, total, n_samples)
    idx = np.searchsorted(dists, sample_dists)
    return list(np.clip(idx, 0, m - 1))


def _camera_frustum(fx: float = 600.0, fy: float = 600.0,
                    cx: float = 320.0, cy: float = 240.0,
                    width: int = 640, height: int = 480,
                    scale: float = 0.08) -> np.ndarray:
    """生成相机视锥线框顶点 (M,3), 每两个点一条线段。

    相机坐标系: z 向前, x 向右, y 向下。
    顶点按相机中心 → 近平面四角连线。
    """
    corners = np.array([
        [0, 0, 1.0],
        [width, 0, 1.0],
        [width, height, 1.0],
        [0, height, 1.0],
    ], dtype=float)
    dirs = np.zeros_like(corners)
    dirs[:, 0] = (corners[:, 0] - cx) / fx
    dirs[:, 1] = (corners[:, 1] - cy) / fy
    dirs[:, 2] = 1.0
    plane = dirs * scale

    apex = np.array([0.0, 0.0, 0.0])
    lines = []
    for p in plane:
        lines.append([apex, p])
    for i in range(4):
        lines.append([plane[i], plane[(i + 1) % 4]])
    return np.array(lines)


class RerunVisualizer:
    AXIS_LEN_MIN = 0.02       # 姿态轴最小长度(米), 手部小范围
    AXIS_LEN_RATIO = 0.06     # 姿态轴长度 = bbox_size * ratio
    ORIGIN_AXIS_RATIO = 0.15  # 坐标轴长度 = bbox_size * ratio
    FRUSTUM_SCALE = 0.03      # 视锥长度(米), 手部近距离

    AXES_HZ = 20.0            # 姿态轴重绘限频
    TRAJ_HZ = 8.0             # 轨迹抽点限频 (手部慢, 低频更干净)
    TRAJ_DRAW_HZ = 8.0        # 轨迹线重绘限频
    HISTORY_DRAW_HZ = 1.0     # 历史姿态轴重绘限频
    FRUSTUM_HZ = 5.0          # 视锥重绘限频
    IMAGE_HZ = 15.0           # 图像限频
    CAMERA_HZ = 8.0           # 自动取景更新频率
    SCENE_HZ = 8.0            # 紧边界网格更新频率
    CAMERA_DISTANCE_MIN = 0.12
    CAMERA_DISTANCE_RATIO = 1.4
    CAMERA_SMOOTHING = 0.25

    def __init__(
        self,
        unit_names: list,
        app_id: str = "ego_vio",
        purge_sec: Optional[float] = None,
        max_points: int = 36000,
        spawn: bool = True,
        connect_addr: Optional[str] = None,
    ):
        try:
            import rerun as rr
        except ImportError:
            raise RuntimeError("需要 rerun-sdk: pip install rerun-sdk")
        self.rr = rr
        self.unit_names = unit_names
        self.purge_sec = purge_sec

        if connect_addr:
            rr.init(app_id, connect=connect_addr)
        else:
            rr.init(app_id, spawn=spawn)

        try:
            import rerun.blueprint as rrb
        except ImportError as exc:
            raise RuntimeError("当前 rerun-sdk 缺少 Blueprint API") from exc
        required_eye_fields = ("position", "look_target", "eye_up", "spin_speed")
        if not all(hasattr(rrb.EyeControls3D, name) for name in required_eye_fields):
            raise RuntimeError(
                "自动取景需要 rerun-sdk>=0.28，请升级后重新运行"
            )
        self._rrb = rrb

        # 轨迹元素为 (ts, t, q); 位置已相对起点原点
        self._trajectory = {n: deque(maxlen=max_points) for n in unit_names}
        self._origin = {n: None for n in unit_names}  # 轨迹起点作为坐标原点
        self._last_image_ts = {n: 0.0 for n in unit_names}
        self._last_axes_ts = {n: 0.0 for n in unit_names}
        self._last_traj_pt = {n: 0.0 for n in unit_names}
        self._last_traj_draw = {n: 0.0 for n in unit_names}
        self._last_history_draw = {n: 0.0 for n in unit_names}
        self._last_frustum_ts = {n: 0.0 for n in unit_names}
        self._last_camera_ts = 0.0
        self._last_scene_ts = 0.0
        self._scene_step = 0.1
        self._scene_bbox_min = None
        self._scene_bbox_max = None
        self._axis_len = self.AXIS_LEN_MIN
        self._data_bbox_min = np.zeros(3)
        self._data_bbox_max = np.zeros(3)
        self._camera_center = np.zeros(3)
        self._camera_distance = self.CAMERA_DISTANCE_MIN

        # 初始场景
        self._update_scene_aux()
        self._send_blueprint(
            self._camera_center,
            self._camera_distance,
            make_default=True,
        )

    def log_pose(self, unit: str, pose, cam_fx: float = 600.0, cam_fy: float = 600.0,
                 cam_cx: float = 320.0, cam_cy: float = 240.0,
                 img_width: int = 640, img_height: int = 480):
        """更新某单元的姿态坐标轴、历史姿态轴、轨迹线和相机视锥(内部限频)。

        pose: vio.Pose (ts, t, q, valid)。q 为 xyzw。
        """
        rr = self.rr
        if not pose or not getattr(pose, "valid", True):
            return

        t_world = np.asarray(pose.t).reshape(3)
        q = np.asarray(pose.q).reshape(4)   # xyzw, body→world

        # 以第一个有效位姿为坐标原点
        if self._origin[unit] is None:
            self._origin[unit] = t_world.copy()
        origin = self._origin[unit]
        t = t_world - origin

        # 六个方向独立累计边界, 轨迹碰到哪一侧就只把哪一侧顶出去。
        self._data_bbox_min = np.minimum(self._data_bbox_min, t)
        self._data_bbox_max = np.maximum(self._data_bbox_max, t)

        # 姿态轴出现前先把旋转后的三个端点纳入场景边界, 避免轴以
        # 30Hz 更新而网格以 15Hz 更新时短暂穿出网格。
        axes_due, axes_ts = _rate_due(
            pose.ts, self._last_axes_ts[unit], self.AXES_HZ
        )
        axes_world = None
        visual_bbox_min = self._data_bbox_min.copy()
        visual_bbox_max = self._data_bbox_max.copy()
        if axes_due:
            axes_local = np.eye(3) * self._axis_len
            axes_world = _quat_rotate(axes_local, q)
            axis_endpoints = t + axes_world
            visual_bbox_min = np.minimum(
                visual_bbox_min, axis_endpoints.min(axis=0)
            )
            visual_bbox_max = np.maximum(
                visual_bbox_max, axis_endpoints.max(axis=0)
            )

        scene_due, scene_ts = _rate_due(
            pose.ts, self._last_scene_ts, self.SCENE_HZ
        )
        if (scene_due or axes_due) and self._bbox_expanded(
            visual_bbox_min, visual_bbox_max
        ):
            self._last_scene_ts = scene_ts
            self._update_scene_aux_from_bbox(
                visual_bbox_min,
                visual_bbox_max,
            )

        # 自动取景: 始终从三平面的开口象限观察, 平滑跟随轨迹中心和尺度。
        camera_due, camera_ts = _rate_due(
            pose.ts, self._last_camera_ts, self.CAMERA_HZ
        )
        if camera_due:
            self._last_camera_ts = camera_ts
            self._update_camera_view()

        # 姿态坐标轴(限频)
        if axes_due:
            self._last_axes_ts[unit] = axes_ts
            rr.log(
                f"world/{unit}/axes",
                rr.Arrows3D(
                    vectors=axes_world.tolist(),
                    origins=[t.tolist()] * 3,
                    colors=[[255, 60, 60], [60, 255, 60], [60, 120, 255]],
                ),
            )

        # 相机视锥: 在相机坐标系生成, 转到世界系(限频)
        frustum_due, frustum_ts = _rate_due(
            pose.ts, self._last_frustum_ts[unit], self.FRUSTUM_HZ
        )
        if frustum_due:
            self._last_frustum_ts[unit] = frustum_ts
            frustum_local = _camera_frustum(cam_fx, cam_fy, cam_cx, cam_cy,
                                            img_width, img_height,
                                            scale=self.FRUSTUM_SCALE)
            frustum_world = _quat_rotate(frustum_local.reshape(-1, 3), q).reshape(-1, 2, 3) + t
            rr.log(
                f"world/{unit}/frustum",
                rr.LineStrips3D(frustum_world, colors=[self._unit_color(unit, dim=0.6)]),
            )

        # 轨迹抽点(限频); 保存相对原点的位置和姿态
        traj_due, traj_ts = _rate_due(
            pose.ts, self._last_traj_pt[unit], self.TRAJ_HZ
        )
        if traj_due:
            self._last_traj_pt[unit] = traj_ts
            self._trajectory[unit].append((pose.ts, t.tolist(), q.tolist()))
            if self.purge_sec is not None:
                self._purge_old(unit, pose.ts)

        # 轨迹线高频重绘, 让轨迹增长连续。
        draw_due, draw_ts = _rate_due(
            pose.ts, self._last_traj_draw[unit], self.TRAJ_DRAW_HZ
        )
        if draw_due:
            self._last_traj_draw[unit] = draw_ts
            self._log_trajectory(unit)

        # 历史姿态轴数据量较大, 独立低频重绘。
        history_due, history_ts = _rate_due(
            pose.ts, self._last_history_draw[unit], self.HISTORY_DRAW_HZ
        )
        if history_due:
            self._last_history_draw[unit] = history_ts
            self._log_pose_axes_history(unit)

    def log_image(self, unit: str, color, ts: float, max_hz: float = 15.0):
        """更新最新一帧彩色图像(覆盖, 不累积历史, 限频)。"""
        rr = self.rr
        image_due, image_ts = _rate_due(ts, self._last_image_ts[unit], max_hz)
        if not image_due:
            return
        self._last_image_ts[unit] = image_ts
        rr.log(f"world/{unit}/image", rr.Image(color))

    def log_stats(self, stats: dict, ts: float):
        """记录统计文本(覆盖, 不累积历史)。"""
        rr = self.rr
        lines = []
        for name, s in stats.items():
            if isinstance(s, dict):
                parts = [f"{k}={v}" for k, v in s.items()]
                lines.append(f"[{name}] " + " ".join(parts))
            else:
                lines.append(f"{name}: {s}")
        rr.log("stats", rr.TextLog("\n".join(lines)))

    @staticmethod
    def _camera_position(center: np.ndarray, distance: float) -> np.ndarray:
        """把相机放在三平面开口象限(+X, -Y, +Z)。"""
        direction = np.array([0.62, -0.72, 0.62], dtype=float)
        direction /= np.linalg.norm(direction)
        return np.asarray(center, dtype=float) + direction * distance

    def _send_blueprint(
        self,
        center: np.ndarray,
        distance: float,
        make_default: bool = False,
    ):
        """发送包含确定相机位姿的完整布局。"""
        rrb = self._rrb
        position = self._camera_position(center, distance)
        img_views = [
            rrb.Spatial2DView(origin=f"world/{name}/image", name=name)
            for name in self.unit_names
        ]
        right = rrb.Vertical(
            *(img_views + [rrb.TextLogView(origin="stats", name="stats")])
        )
        blueprint = rrb.Blueprint(
            rrb.Horizontal(
                rrb.Spatial3DView(
                    origin="world",
                    name="3D Pose / Trajectory",
                    line_grid=False,
                    background=[40, 40, 40],
                    eye_controls=rrb.EyeControls3D(
                        kind=rrb.Eye3DKind.FirstPerson,
                        position=position.tolist(),
                        look_target=np.asarray(center, dtype=float).tolist(),
                        eye_up=[0.0, 0.0, 1.0],
                        spin_speed=0.0,
                        speed=max(distance, 1.0),
                    ),
                ),
                right,
                column_shares=[0.7, 0.3],
            ),
            collapse_panels=True,
        )
        self.rr.send_blueprint(
            blueprint,
            make_active=True,
            make_default=make_default,
        )

    def _update_camera_view(self):
        """向当前轨迹包围盒的理想观察位姿平滑靠近。"""
        desired_center = (
            self._scene_bbox_min + self._scene_bbox_max
        ) / 2.0
        bbox_diagonal = float(np.linalg.norm(
            self._scene_bbox_max - self._scene_bbox_min
        ))
        desired_distance = max(
            self.CAMERA_DISTANCE_MIN,
            bbox_diagonal * self.CAMERA_DISTANCE_RATIO,
        )
        alpha = self.CAMERA_SMOOTHING
        self._camera_center += (desired_center - self._camera_center) * alpha
        self._camera_distance += (
            desired_distance - self._camera_distance
        ) * alpha
        self._send_blueprint(self._camera_center, self._camera_distance)

    def _bbox_expanded(self, bbox_min: np.ndarray, bbox_max: np.ndarray) -> bool:
        """判断包围盒是否越过当前场景缓存的实际边界。"""
        if self._scene_bbox_min is None or self._scene_bbox_max is None:
            return True
        bbox_min = np.asarray(bbox_min, dtype=float)
        bbox_max = np.asarray(bbox_max, dtype=float)
        return bool(
            np.any(bbox_min < self._scene_bbox_min)
            or np.any(bbox_max > self._scene_bbox_max)
        )

    def _purge_old(self, unit: str, now_ts: float):
        """丢弃超过 purge_sec 的旧轨迹点。"""
        cutoff = now_ts - self.purge_sec
        traj = self._trajectory[unit]
        while traj and traj[0][0] < cutoff:
            traj.popleft()

    def _log_trajectory(self, unit: str):
        """重新 log 当前轨迹线。"""
        pts = [p[1] for p in self._trajectory[unit]]
        if len(pts) < 2:
            return
        self.rr.log(
            f"world/{unit}/trajectory",
            self.rr.LineStrips3D([pts], colors=[self._unit_color(unit)]),
        )

    def _log_pose_axes_history(self, unit: str):
        """按弧长自适应采样历史姿态轴, 避免轨迹变长后糊成一片。"""
        traj = list(self._trajectory[unit])
        if len(traj) < 2:
            return
        idx = _resample_by_arclength(traj, n_samples=60)
        axes_local = np.eye(3) * self._axis_len
        origins = []
        vectors = []
        colors = []
        for i in idx:
            _, t, q = traj[i]
            t = np.asarray(t)
            q = np.asarray(q)
            axes_world = _quat_rotate(axes_local, q)
            origins.extend([t.tolist()] * 3)
            vectors.extend(axes_world.tolist())
            colors.extend([[255, 60, 60], [60, 255, 60], [60, 120, 255]])
        self.rr.log(
            f"world/{unit}/pose_axes_history",
            self.rr.Arrows3D(vectors=vectors, origins=origins, colors=colors),
        )

    def _update_scene_aux(self):
        """根据所有单元轨迹包围盒, 动态更新三平面网格、坐标轴、刻度。"""
        all_pts = []
        for u in self.unit_names:
            all_pts.extend([p[1] for p in self._trajectory[u]])
        if not all_pts:
            bbox_min = np.array([-0.1, -0.1, -0.1])
            bbox_max = np.array([0.1, 0.1, 0.1])
        else:
            pts = np.asarray(all_pts)
            bbox_min = pts.min(axis=0)
            bbox_max = pts.max(axis=0)
        self._update_scene_aux_from_bbox(bbox_min, bbox_max)

    def _update_scene_aux_from_bbox(self, bbox_min: np.ndarray, bbox_max: np.ndarray):
        """由包围盒直接更新场景辅助元素(填充平面 + 网格 + 坐标轴 + 刻度)。"""
        bbox_min = np.asarray(bbox_min, dtype=float).copy()
        bbox_max = np.asarray(bbox_max, dtype=float).copy()
        trajectory_size = float((bbox_max - bbox_min).max())
        self._axis_len = max(
            trajectory_size * self.AXIS_LEN_RATIO,
            self.AXIS_LEN_MIN,
        )

        # 已显示的六个边界只能各自向外移动, 不重定中心、不整体加余量。
        if self._scene_bbox_min is not None:
            bbox_min = np.minimum(self._scene_bbox_min, bbox_min)
        if self._scene_bbox_max is not None:
            bbox_max = np.maximum(self._scene_bbox_max, bbox_max)

        verts, tris, grid_lines, axis_lines, labels, step = _bbox_scene(
            bbox_min, bbox_max, n_lines=16
        )
        self._scene_step = step

        self._scene_bbox_min = bbox_min
        self._scene_bbox_max = bbox_max

        # 相机跟踪目标只随场景扩展移动; radii=0 不渲染。
        center = (bbox_min + bbox_max) / 2.0
        self.rr.log(
            "world/camera_target",
            self.rr.Points3D([center.tolist()], radii=0.0, colors=[255, 255, 0]),
        )

        # 三个填充平面(浅灰/白色, 与背景区分)
        self.rr.log(
            "world/grid_planes",
            self.rr.Mesh3D(
                vertex_positions=verts,
                triangle_indices=tris,
                albedo_factor=[220, 220, 220, 255],
            ),
        )

        # 三平面网格线(深灰色, 在白色平面上清晰)
        self.rr.log(
            "world/grid",
            self.rr.LineStrips3D(grid_lines, colors=[[80, 80, 80]], radii=0.0015),
        )

        # 三坐标轴粗线
        self.rr.log(
            "world/axis_lines",
            self.rr.LineStrips3D(
                axis_lines,
                colors=[[255, 60, 60], [60, 255, 60], [60, 120, 255]],
                radii=0.004,
            ),
        )

        # 数字刻度和轴标题分开记录, 避免共角点处混成一团。
        tick_labels = [lab for lab in labels if "(m)" not in lab[0]]
        axis_titles = [lab for lab in labels if "(m)" in lab[0]]
        if tick_labels:
            self.rr.log(
                "world/axis_labels",
                self.rr.Points3D(
                    [lab[1] for lab in tick_labels],
                    labels=[lab[0] for lab in tick_labels],
                    radii=self.rr.Radius.ui_points(5.0),
                    colors=[20, 20, 20],
                    show_labels=True,
                ),
            )
        else:
            self.rr.log("world/axis_labels", self.rr.Clear(recursive=False))
        if axis_titles:
            self.rr.log(
                "world/axis_titles",
                self.rr.Points3D(
                    [lab[1] for lab in axis_titles],
                    labels=[lab[0] for lab in axis_titles],
                    radii=self.rr.Radius.ui_points(6.0),
                    colors=[lab[2] for lab in axis_titles],
                    show_labels=True,
                ),
            )
        else:
            self.rr.log("world/axis_titles", self.rr.Clear(recursive=False))

    def clear_unit(self, unit: str):
        """清空某单元的所有可视化实体。"""
        rr = self.rr
        for entity in (f"world/{unit}/axes", f"world/{unit}/trajectory",
                       f"world/{unit}/pose_axes_history", f"world/{unit}/frustum",
                       f"world/{unit}/image"):
            rr.log(entity, rr.Clear(recursive=False))
        self._trajectory[unit].clear()
        self._origin[unit] = None
        self._last_image_ts[unit] = 0.0
        self._last_axes_ts[unit] = 0.0
        self._last_traj_pt[unit] = 0.0
        self._last_traj_draw[unit] = 0.0
        self._last_history_draw[unit] = 0.0
        self._last_frustum_ts[unit] = 0.0
        all_pts = [
            p[1] for name in self.unit_names
            for p in self._trajectory[name]
        ]
        if all_pts:
            pts = np.asarray(all_pts)
            self._data_bbox_min = pts.min(axis=0)
            self._data_bbox_max = pts.max(axis=0)
        else:
            self._data_bbox_min = np.zeros(3)
            self._data_bbox_max = np.zeros(3)

    def _unit_color(self, unit: str, dim: float = 1.0):
        base = {
            "left_hand": [255, 80, 80],
            "right_hand": [80, 180, 255],
            "head": [120, 255, 120],
        }.get(unit, [200, 200, 200])
        return [int(c * dim) for c in base]

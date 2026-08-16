from collections import deque
from types import SimpleNamespace

import numpy as np

from ego_vio.visualizer.rerun_viz import RerunVisualizer, _bbox_scene


class _FakeRerun:
    class Radius:
        @staticmethod
        def ui_points(value):
            return ("ui_points", value)

    def __init__(self):
        self.logged = []

    def log(self, path, value):
        self.logged.append((path, value))

    @staticmethod
    def Arrows3D(**kwargs):
        return ("Arrows3D", kwargs)

    @staticmethod
    def LineStrips3D(*args, **kwargs):
        return ("LineStrips3D", args, kwargs)

    @staticmethod
    def Mesh3D(**kwargs):
        return ("Mesh3D", kwargs)

    @staticmethod
    def Transform3D(**kwargs):
        return ("Transform3D", kwargs)

    @staticmethod
    def Points3D(*args, **kwargs):
        return ("Points3D", args, kwargs)

    @staticmethod
    def Image(value):
        return ("Image", value)


def _make_visualizer():
    viz = RerunVisualizer.__new__(RerunVisualizer)
    viz.rr = _FakeRerun()
    viz.unit_names = ["dummy"]
    viz.purge_sec = None
    viz._trajectory = {"dummy": deque(maxlen=36000)}
    viz._origin = {"dummy": None}
    viz._last_image_ts = {"dummy": 0.0}
    viz._last_axes_ts = {"dummy": 0.0}
    viz._last_traj_pt = {"dummy": 0.0}
    viz._last_traj_draw = {"dummy": 0.0}
    viz._last_history_draw = {"dummy": 0.0}
    viz._last_frustum_ts = {"dummy": 0.0}
    viz._prev_pose_ts = {"dummy": None}
    viz._prev_pos = {"dummy": None}
    viz._vel = {"dummy": np.zeros(3)}
    viz._last_camera_ts = 0.0
    viz._last_scene_ts = 0.0
    viz._scene_step = 0.1
    viz._scene_bbox_min = np.array([-0.1, -0.1, -0.1])
    viz._scene_bbox_max = np.array([0.1, 0.1, 0.1])
    viz._rendered_bbox_min = None
    viz._rendered_bbox_max = None
    viz._planes_logged = False
    viz._axis_len = viz.AXIS_LEN_MIN
    viz._data_bbox_min = np.zeros(3)
    viz._data_bbox_max = np.zeros(3)
    viz._camera_center = np.zeros(3)
    viz._camera_distance = viz.CAMERA_DISTANCE_MIN
    viz._send_blueprint = lambda *_, **__: None
    return viz


def test_trajectory_redraw_is_smooth_but_history_stays_lightweight():
    viz = _make_visualizer()

    for i in range(41):
        ts = i * 0.025
        pose = SimpleNamespace(
            ts=ts,
            t=np.array([ts, 0.0, 0.0]),
            q=np.array([0.0, 0.0, 0.0, 1.0]),
            valid=True,
        )
        viz.log_pose("dummy", pose)

    paths = [path for path, _ in viz.rr.logged]
    axes_updates = paths.count("world/dummy/axes")
    trajectory_updates = paths.count("world/dummy/trajectory")
    history_updates = paths.count("world/dummy/pose_axes_history")

    assert axes_updates >= 28
    assert trajectory_updates >= 13
    assert history_updates <= 3


def test_image_rate_limit_keeps_requested_average_rate():
    viz = _make_visualizer()

    for i in range(31):
        viz.log_image("dummy", np.zeros((2, 2, 3)), ts=i / 30.0)

    paths = [path for path, _ in viz.rr.logged]
    assert paths.count("world/dummy/image") >= 14


def test_scene_expansion_compares_real_cached_bounds():
    viz = _make_visualizer()
    viz._update_scene_aux_from_bbox(
        np.array([-0.2, -0.2, -0.2]),
        np.array([0.2, 0.2, 0.2]),
    )

    assert not viz._bbox_expanded(
        np.array([-0.1, -0.1, -0.1]),
        np.array([0.1, 0.1, 0.1]),
    )
    assert viz._bbox_expanded(
        np.array([1.0, 1.0, 1.0]),
        np.array([1.1, 1.1, 1.1]),
    )


def test_bbox_scene_uses_exact_bounds_without_global_margin():
    bbox_min = np.array([-1.1, -2.2, -3.3])
    bbox_max = np.array([4.4, 5.5, 6.6])

    vertices, _, _, _, labels, _ = _bbox_scene(bbox_min, bbox_max)
    vertices = np.asarray(vertices)

    np.testing.assert_allclose(vertices.min(axis=0), bbox_min)
    np.testing.assert_allclose(vertices.max(axis=0), bbox_max)


def test_grid_labels_use_stable_nice_ticks_not_live_endpoints():
    _, _, _, _, labels, _ = _bbox_scene(
        np.array([-0.13, -0.1, -0.1]),
        np.array([0.28, 0.1, 0.1]),
    )
    texts = {text for text, *_ in labels}

    assert "-0.13" not in texts
    assert "0.28" not in texts
    assert {"-0.10", "0.00", "0.10", "0.20"} <= texts


def test_scene_expands_only_on_the_side_touched_by_trajectory():
    viz = _make_visualizer()
    viz._scene_bbox_min = np.array([-0.1, -0.1, -0.1])
    viz._scene_bbox_max = np.array([0.1, 0.1, 0.1])

    viz._update_scene_aux_from_bbox(
        np.array([0.0, 0.0, 0.0]),
        np.array([0.3, 0.05, 0.05]),
    )

    np.testing.assert_allclose(viz._scene_bbox_min, [-0.1, -0.1, -0.1])
    np.testing.assert_allclose(viz._scene_bbox_max, [0.3, 0.1, 0.1])


def test_live_scene_boundary_tracks_latest_positive_x_pose():
    """边界始终包住轨迹, 且沿运动方向有提前量(不紧贴也不甩开)。"""
    viz = _make_visualizer()
    viz._scene_bbox_min = np.array([-0.1, -0.1, -0.1])
    viz._scene_bbox_max = np.array([0.1, 0.1, 0.1])

    for i in range(41):
        ts = i * 0.025
        pose = SimpleNamespace(
            ts=ts,
            t=np.array([ts, 0.0, 0.0]),
            q=np.array([0.0, 0.0, 0.0, 1.0]),
            valid=True,
        )
        viz.log_pose("dummy", pose)

    # 未触及的一侧保持不动
    np.testing.assert_allclose(viz._scene_bbox_min, [-0.1, -0.1, -0.1])
    # 包住最后一个轨迹点 + 姿态轴, 提前量有限(不紧贴不甩开)
    assert viz._scene_bbox_max[0] >= 1.0 + viz.AXIS_LEN_MIN
    assert viz._scene_bbox_max[0] <= 1.0 + 0.3
    np.testing.assert_allclose(viz._scene_bbox_max[1:], [0.1, 0.1])


def test_rendered_box_always_encloses_trajectory():
    """防穿模: 任何时刻, 已渲染(旧)的包围盒都要包住当前轨迹点。

    渲染限频期间轨迹继续前进, 靠运动方向提前量保证不穿墙。
    """
    viz = _make_visualizer()
    viz._scene_bbox_min = np.array([-0.1, -0.1, -0.1])
    viz._scene_bbox_max = np.array([0.1, 0.1, 0.1])

    for i in range(1, 200):
        ts = i * 0.025
        # 1.2 m/s 步行速度, 绕一点弯, 三个方向都有运动
        pose = SimpleNamespace(
            ts=ts,
            t=np.array([ts * 1.2, 0.3 * np.sin(ts * 2.0), 0.1 * ts]),
            q=np.array([0.0, 0.0, 0.0, 1.0]),
            valid=True,
        )
        viz.log_pose("dummy", pose)
        if viz._rendered_bbox_min is not None:
            t = pose.t - viz._origin["dummy"]
            assert np.all(t >= viz._rendered_bbox_min), (
                f"frame {i}: {t} below {viz._rendered_bbox_min}"
            )
            assert np.all(t <= viz._rendered_bbox_max), (
                f"frame {i}: {t} above {viz._rendered_bbox_max}"
            )


def test_scene_lead_only_extends_in_direction_of_motion():
    """提前量只加在运动方向那一侧, 反方向不多扩。"""
    viz = _make_visualizer()
    viz._scene_bbox_min = None
    viz._scene_bbox_max = None
    viz._update_scene_bbox(
        np.array([0.2, 0.0, 0.0]),
        np.array([0.4, 0.1, 0.1]),
        vel=np.array([1.0, 0.0, 0.0]),
    )

    # +x 方向: 外推 velocity * LEAD_TIME + LEAD_MIN
    assert viz._scene_bbox_max[0] > 0.4 + 1.0 * viz.SCENE_LEAD_TIME
    # -x 方向: 不加提前量
    assert viz._scene_bbox_min[0] == 0.2
    # y/z 无运动: 两侧都不加提前量
    assert viz._scene_bbox_min[1] == 0.0
    assert viz._scene_bbox_max[1] == 0.1


def test_scene_state_tracks_pose_axis_even_when_grid_not_rendered():
    """网格渲染限频未到时, 边界状态(每帧更新)也要先包住姿态轴端点。"""
    viz = _make_visualizer()
    viz._origin["dummy"] = np.zeros(3)
    viz._last_scene_ts = 0.04  # 网格限频尚未到, 但姿态轴本帧需要更新。
    pose = SimpleNamespace(
        ts=0.04,
        t=np.array([0.1, 0.0, 0.0]),
        q=np.array([0.0, 0.0, 0.0, 1.0]),
        valid=True,
    )

    viz.log_pose("dummy", pose)

    assert viz._scene_bbox_max[0] >= 0.1 + viz.AXIS_LEN_MIN
    paths = [path for path, _ in viz.rr.logged]
    assert "world/dummy/axes" in paths


def test_camera_stays_in_three_plane_open_octant():
    viz = _make_visualizer()
    center = np.array([1.0, 2.0, 3.0])

    position = viz._camera_position(center, distance=2.0)
    offset = position - center

    assert offset[0] > 0.0
    assert offset[1] < 0.0
    assert offset[2] > 0.0


def test_initial_camera_distance_keeps_small_pose_scene_large():
    """初始小场景: 距离需求不超过当前距离时不重发 blueprint。"""
    viz = _make_visualizer()
    calls = []
    viz._send_blueprint = lambda center, distance, **_: calls.append(distance)

    viz._update_camera_view()

    # ±0.1m 初始场景距离需求 = CAMERA_DISTANCE_MIN, 与当前一致 → 不触发变焦
    assert not calls

    # 当前距离明显不足时(例如重启后从近景开始), 立即变焦并留提前量
    viz._camera_distance = 0.2
    viz._update_camera_view()
    assert calls and 0.3 <= calls[-1] <= 0.7


def test_camera_blueprint_updates_during_live_pose_stream():
    """变焦 blueprint 单调变远且远少于场景渲染次数; 跟随点高频更新。"""
    viz = _make_visualizer()
    calls = []
    viz._send_blueprint = lambda center, distance, **_: calls.append(
        (np.asarray(center).copy(), distance)
    )

    for i in range(41):
        ts = i * 0.025
        pose = SimpleNamespace(
            ts=ts,
            t=np.array([ts, ts * 0.5, ts * 0.25]),
            q=np.array([0.0, 0.0, 0.0, 1.0]),
            valid=True,
        )
        viz.log_pose("dummy", pose)

    paths = [path for path, _ in viz.rr.logged]
    scene_renders = paths.count("world/grid")
    target_updates = paths.count("world/camera_target")

    # 跟随点: 每次场景渲染都更新, 保证平移跟随丝滑
    assert target_updates == scene_renders >= 20
    # 变焦: 单调拉远, 次数是对数级(远少于渲染次数)
    assert 2 <= len(calls) < scene_renders // 2
    distances = [d for _, d in calls]
    assert all(b > a for a, b in zip(distances, distances[1:]))
    assert calls[-1][0][0] > calls[0][0][0]


def test_axis_labels_are_forced_visible_and_high_contrast():
    viz = _make_visualizer()
    viz._update_scene_aux_from_bbox(
        np.array([-0.2, -0.2, -0.2]),
        np.array([0.2, 0.2, 0.2]),
    )

    tick_labels = [
        value for path, value in viz.rr.logged
        if path == "world/axis_labels"
    ][-1]
    _, _, tick_kwargs = tick_labels
    axis_titles = [
        value for path, value in viz.rr.logged
        if path == "world/axis_titles"
    ][-1]
    _, _, title_kwargs = axis_titles

    assert tick_kwargs["show_labels"] is True
    assert tick_kwargs["colors"] == [20, 20, 20]
    assert not {"X (m)", "Y (m)", "Z (m)"} & set(tick_kwargs["labels"])
    assert "-0.00" not in tick_kwargs["labels"]
    assert {"X (m)", "Y (m)", "Z (m)"} == set(title_kwargs["labels"])

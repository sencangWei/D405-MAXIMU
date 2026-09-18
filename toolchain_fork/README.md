# MASt3R-SLAM D405 fork（算法本体备份）

这份目录是 **SLAM 前端算法本体**的异地备份。它平时不在本仓库里，住在：

```
/home/robot/ego_pipeline/work/toolchains/MASt3R-SLAM/
```

那是一个 `rmurai0610/MASt3R-SLAM` 的克隆，`origin` 指向上游 —— **我们的 fork 改动推不上去**。
在 2026-09-19 之前，这些改动只存在于工作区，**没有分支、没有 tag、没有 stash**；
一次 `git checkout .` 或重装工具链就会让它们永久消失，且没有任何地方能找回。

## 内容

| 文件 | 说明 |
|---|---|
| `mast3r_slam_d405_fork_20260919.patch` | 完整改动，`git format-patch` 格式（mbox），1961 行 |
| `stereo_depth.py` | 新增模块的独立副本，388 行，便于直接阅读 |

对应本地提交：工具链仓库 `d405-fork-20260919` 分支，`1484dd0`，基线为上游 `e6f4e3d`。
共 **13 个文件，+1395 / −34**。

## 怎么恢复

```bash
git clone https://github.com/rmurai0610/MASt3R-SLAM.git
cd MASt3R-SLAM
git checkout e6f4e3d
git am /path/to/mast3r_slam_d405_fork_20260919.patch
```

已实测该补丁可**干净应用**到 `e6f4e3d`（`git apply --check` 通过）。

## 改动都改了什么

- `mast3r_slam/stereo_depth.py`（新增）：D405 双红外立体深度与点图机制
- `main.py`：`--calib` 相机模型载入（**这是把 `config["use_calib"]` 置 `True` 的唯一途径**）、
  `allow_tf32`、关键帧与回环参数、标定与 IMU 先验入口
- `mast3r_slam/tracker.py`（+420）：立体点图位姿求解、IMU 旋转先验约束
- `mast3r_slam/global_opt.py`（+169）：多段关键帧图联合优化与修正量上限
- `mast3r_slam/frame.py` / `dataloader.py` / `evaluate.py`：立体帧、时间戳与评测支持
- `mast3r_slam/backend/src/{gn_kernels,matching_kernels}.cu`、`setup.py`：后端算子与构建调整
- `thirdparty/mast3r/`：`model.py` / `retrieval/processor.py` / `curope/kernels.cu` 兼容性修正

## 没纳入备份的东西

- `thirdparty/lietorch/`（237 MB 第三方依赖，可重新克隆）
- `.cuda/`（空目录）
- `*.so` 编译产物（`.gitignore` 已排除；可从上面的 `.cu` 与 `setup.py` 重建）

## 注意

补丁里含 `main.py` 的 `--calib` 载入逻辑。**这条链路要正确工作，启动器必须真的把 `--calib`
传进去** —— 见 `scripts/mast3r_slam_precision_workflow.sh` 里的注释：
拿 `config["use_calib"]` 当传参前提是循环条件，恒假，会让前端静默丢掉 D405 相机模型。

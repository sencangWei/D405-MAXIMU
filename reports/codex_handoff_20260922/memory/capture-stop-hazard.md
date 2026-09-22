---
name: capture-stop-hazard
description: "D405 采集中途停止: Ctrl-C 会静默毁掉整条 take(partial db3 留在内存盘);能优雅停止的 q 键只在预览开启时存在"
metadata: 
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-17T14:33:39.836Z
---

`capture_docker2_with_lighthouse.sh` 里 `trap cleanup EXIT INT TERM`，`cleanup()`
按 GUIDE→TRACKER→D405 顺序 `kill -TERM`。**容器内 Python 收到 SIGTERM 默认立即终止**：
`finally` 不跑，更关键的是紧随 `try/finally` **之后**的 db3 搬运（`shutil.move`
出 `/dev/shm/ego_vio_d405`）根本不会执行。

后果是一组**全静默**的症状，极易误判成硬件/算法故障：
- 采集日志停在 `[全流采集] 正式采集 N 秒` 之后，**没有 ERROR 行**
  （`except Exception` 会打印 ERROR，所以没有 ERROR ≠ 没有异常，
  而是进程被信号杀了 —— `KeyboardInterrupt` 继承自 `BaseException`，不被它捕获）
- 会话目录只有 `external_imu/`，没有 db3、没有验收报告、没有 `camera_ts.csv`
- partial db3 留在内存盘（可用 python sqlite3 只读打开，`quick_check` 仍 ok）
- `imu.bin` 长度 ≈ 实际录到的秒数（400 Hz × 40 B/sample = 16000 B/s）
- tracker 最后样本比容器死亡早 <1 s（这个顺序就是 `cleanup()` 的杀进程顺序，
  可与"容器自己崩"区分开）

**根因判别**：宿主脚本若走完 `wait "$D405_PID"` 会写 `d405_session.txt`。
该文件存在 ⇒ 脚本不是被打断的；不存在 ⇒ 脚本先退出、trap 杀了容器。

**How to apply**：
- **要在中途停止，只能点预览窗口按 `q`**。`q` 的处理在
  `if not args.no_preview` 分支内部 ⇒ **不开预览就没有优雅停止路径，只有 Ctrl-C**。
- 所以 `UMI_CAPTURE_PREVIEW=1` 不只是"能看画面"，它是可中断性的前提。
- 判读失败 take 时先看 tracker 与容器的**死亡先后**，再下结论。

2026-09-17 一次 848x480 采集即因此作废（会话
`d405_720p_rgb_stereo_ir_20260917_222416`，录到 29.75 s 被杀，
4.37 GB partial db3 留在 `/dev/shm`）。

相关：[[vins-runtime-intrinsics-source]]、[[capture-pipeline-ab-result]]

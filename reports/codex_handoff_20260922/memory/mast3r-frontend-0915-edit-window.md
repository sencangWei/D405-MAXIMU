---
name: mast3r-frontend-0915-edit-window
description: "【已作废】曾以为 MASt3R 前端回归的分界是 09-15 17:26 的一次代码改动、嫌疑锁定 main.py/tracker.py;2026-09-19 已破案,真凶是启动器脚本的 --calib 门控(该脚本不被 git 跟踪,所以查代码永远查不到)。保留价值仅剩两条排除结论"
metadata:
  node_type: memory
  type: project
  originSessionId: 616ba18d-61b7-4aba-830c-5738635103f2
  modified: 2026-09-18T17:08:33.794Z
---

**这条记忆的核心推论是错的,已作废。** 真相见 [[mast3r-frontend-regression-20260918]]:
根因是 `mast3r_slam_precision_workflow.sh` 的 `--calib` 循环门控,**那个脚本不被 git 跟踪**,
所以"在工具链代码里找 09-15 那次改动"这条路从方向上就不可能找到 —— 工具链代码确实一直没变。

下面两条结论**独立于本推论,仍然有效**,故保留:

1. **输入数据集逐字节相同(全量,非抽样)。** 两份已制备数据集:3603 个文件里只有 2 个不同
   (`dataset_manifest.json` 和 `imu_rotation_priors_report.json`,都只差 output 路径);
   3598 张 PNG(含 `stereo_right/`)+ `frames.csv` + `calibration.yaml` + `imu_rotation_priors.csv` 全同。
2. **同日内确定性。** 09-18 五次独立前端运行 `dataset_full.txt` **全部 `a18d7f87bb79826e`**;
   跨 take 同一 session 多次重跑也逐位一致 ⇒ 是确定性缺陷,不是随机性/双稳态运气。
   (注:那些"坏"的 sha 现在已知都是**没带 `--calib`** 造成的,确定性本身仍然成立且有用。)

**别再用 `toolchain_dirty_diff_sha256` 当回归分界。** 它比的是工作区 vs **暂存区**,
`git add` / `git reset` 就能改它而文件一个字节没动。

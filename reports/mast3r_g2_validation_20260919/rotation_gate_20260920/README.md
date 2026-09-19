# 旋转门（`max_rotation_rmse_deg = 2.0`）到底被什么顶出去 — 2026-09-20

用户的痛点：「旋转侧怎么把轨迹做精准」。此前只知道「融合不贡献姿态」，不知道
卡在 1.9–3.2° 的这道门由什么构成。

> ## ⚠ 本文档二稿，更正了初稿的两处错误
>
> 1. **初稿说「常量体轴姿态偏移表现为第三列（常量失配）」——错了。**
>    `alignment_conditioning.py` 的约定探针实测：在姿态分布铺满球面时，一个常量
>    体轴偏移 C **全部落进第二列（纯漂移）**，第三列 ≈ 0。
> 2. **初稿引用「手眼解出的相机-IMU 外参与配置差 0.86–2.19°」——该数字作废。**
>    那是 Nelder-Mead 在**退化**代价面上跑出来的假解（解离配置 ~89°，却只比最优差
>    0.94°）。`handeye_conditioning.py` 证明这个代价面在本数据上**根本不可辨识**。
>
> 更正后的结论见下。

## 一、口径（先钉死度量约定）

`scripts/evaluate_slam_ground_truth.py:173-292` 一次给出三个量：

| 量 | 对齐旋转来自 | 实测（18 cell） |
|---|---|---|
| `ate_rotation_rmse_deg` | **位置** `rigid_align` | **门指标**（`:316` 比对 2.0），1.76–3.20° |
| `attitude_aligned_ate_rotation_rmse_deg` | 姿态 `orientation_align` | 1.04–1.42° |
| `position_vs_attitude_alignment_rotation_deg` | 两者夹角 | 1.41–2.98° |

约定探针（`alignment_conditioning.py` ⓪，合成数据、已知答案）：

| 合成 | 门 | 第二列 | 第三列 |
|---|---|---|---|
| 只有世界旋转 M（21.4°） | **0.000** | 0.000 | 0.000 |
| 只有常量体轴偏移 C（1.5°） | 1.500 | **1.499** | 0.060 |
| M 与 C 都有 | 1.500 | 1.499 | 0.060 |

⇒ **世界旋转被完全吸收；常量体轴偏移全额进门，且落在第二列。**
⇒ 第三列非零意味着**估计的位置与估计的姿态不在同一个世界系里**。

## 二、逐项排除

| # | 假说 | 实验 | 结论 |
|---|---|---|---|
| 1 | 位置对齐病态（轨迹太小） | `alignment_conditioning` 缩放律 | **否**。161mm 轨迹上 5mm 位置误差只把对齐旋转推歪 **0.10°**；用真实残差整体旋转也只 0.21–1.38°（中位 0.78），只解释实测常量的 **39%** |
| 2 | 时间偏移 | 初稿 τ 扫 ±40ms | **否**。第三列纹丝不动（1.46–1.50） |
| 3 | 相机-IMU 外参可辨识 | `handeye_conditioning` 代价面形状 | **否（不可辨识）**。代价在**配置值处就是局部极小**（0.47–2.00 deg²），±2° 切向只改变 ≲0.5 deg²，而拟合噪声底就有 0.7°RMS；随机方向 10–65 deg²。**这段数据分辨不了外参** |
| 4 | 常量是「每次跑都变」的随机量 | `constant_transfer` 留一 | **否**。oracle 常量跨 take 两两夹角中位 **1.46°**；留一迁移吃掉可消量的 **55–86%**（6/8 个有可消量的 cell）；**全局单一常量**（\|C\|=1.42°）改善 7/8，把过门 cell 从 5/8 降到 4/8 |

## 三、更正后的结论

`constant_model_test.py` 用**真实 GT 的姿态分布**做合成，扫两个模型：

| 误差模型 | 门 | 第二列 | 第三列 |
|---|---|---|---|
| 恒定体轴偏移 \|C\| | **1.057·\|C\|** | 0.33·\|C\| | **1.004·\|C\|** |
| 随时间**漂移**的同幅度误差 | 0.61·幅度 | **0.61·幅度** | ≈0 |

真实数据（v10/g1：门 1.87 / 漂移 1.18 / 常量 1.46）**两个模型都凑不出来**——
漂移模型给不出 1.46 的第三列，常量模型给不出 1.18 的第二列。两者叠加
`门 ≈ sqrt(常量² + 漂移²)` 才对得上（v10/g1 预测 1.93 vs 实测 1.87；
v10/g2 预测 3.36 vs 3.20；batch5/g1 预测 1.82 vs 1.76）。

⇒ **门 = 一个恒定体轴姿态偏移（≈1.0–3.0°，占第三列）+ 一份真实的时变姿态误差
（≈1.0–2.0°，占第二列），二者正交叠加。**

`chain_rotation_trace.py` 沿链条逐环评测，看这个恒定项在哪一环出生：

| 环节 | 门 | 漂移 | 位置×姿态 | ATE mm |
|---|---|---|---|---|
| `vins_raw` | 2.30–13.09 | 1.04–1.42 | **1.97–13.02** | 15–127 |
| `vins_corrected` | 同上 | 同上 | 同上 | 同上 |
| `[7/8] graph` | ~90（相机系，另说） | — | ~83（相机系） | 13–22 |
| `[8/9] fused` | 1.76–3.20 | **1.04–1.42** | 1.41–2.98 | 3.9–9.1 |

⇒ **两项都已经是 `vins_raw` 的**（`vins_raw` 第三列中位 3.10°，融合后降到中位 2.02°，
融合是**改善**方不是肇事方）。第二列在**每一环都恒为 1.04–1.42°**——它 Inherited from VINS。

## 四、还没解决的那一步：常量在估计里还是在真值里

**度量在结构上分不开这两者**：`pose_errors` 只比较估计与 GT，一个恒定体轴偏移
放在估计侧还是 GT 侧，读数完全一样。

而 GT 这一侧有一个**具体的、值得怀疑的环节**：

- `lighthouse_ground_truth_provenance.json` 显示 GT 由 `tracker_T_body` 变换得到；
- `lighthouse_tracker_body_frozen_v2.json`（schema `lighthouse_d405_aprilgrid_joint_handeye_v1`）
  里 `method = "multi-capture robust relative-motion AX=XB **directly to Docker2 VINS body**"`
  —— **GT 的「body」系就是 VINS 的 body 系**（标定时的）；
- 该标定 `rotation_residual_deg`: median 0.216 / rmse 0.306 / p95 0.589 / max 1.247；
- 但它的两次训练捕获**彼此只差 `rotation_deg = 0.62`**（`training_pairwise_independent_differences`）
  —— 激励极小，AX=XB 在这种输入下对未激励方向基本没约束。

⇒ **GT 的姿态只保证到 ~0.3–0.6° 相对 VINS body 约定**，而这个约定本身又依赖
VINS 在标定那次的观测量。这条线索是当前最值得查的，但它需要一个**独立于 VINS 的
姿态参考**才能判定（陀螺积分判不了常量偏移——常量偏移不改变角速度）。

## 五、可动的杠杆（按把握排序）

1. ~~改相机-IMU 外参 / 开 `estimate_extrinsic`~~ —— **本轮证据不支持**：外参在本数据上
   不可辨识（二·3），`estimate_extrinsic: 0` 并不是已知的病根。要动它得先拿到
   **有足够旋转激励**的数据（大角度旋转的标定段），否则在线估计只会跟着噪声跑。
2. **第 8/9 步不再丢弃 MASt3R 姿态**（`attitude_ab.py`，18/18 全组一致改善、
   ATE 不变、免费）——幅度小（中位 −0.088°），压的是第二列。
3. **查 GT 侧**（第四节）：这是唯一能把「1.0–3.0° 恒定项」的归属定下来的方向。
   若归 GT，则这道门有一部分在量真值自己的误差，调算法永远救不回来。
4. **拉长轨迹 / 加大回路**：所有灵敏度都随轨迹尺度下降，这是唯一在所有模型下
   都单调有效的手段（与既有 [[vins-config-optimal]]「换更大回路」互印）。

## 文件

| 文件 | 内容 |
|---|---|
| `rotation_gate_decomposition.py` / `.txt` | 初稿：三项分解、度量地板、τ 扫描（**其中 ⑦ 手眼那节作废**） |
| `attitude_ab.py` / `.txt` | 第 8/9 步姿态开关的 18-cell A/B |
| `alignment_conditioning.py` / `.txt` | ⓪ 约定探针（更正初稿）；位置误差→对齐旋转的缩放律 |
| `chain_rotation_trace.py` / `.txt` | 沿 `vins_raw → … → fused` 逐环评测 |
| `constant_transfer.py` / `.txt` | oracle 常量的跨 take 一致性 + 留一迁移 |
| `handeye_conditioning.py` / `.txt` | 手眼外参的可辨识性（代价面形状） |
| `constant_model_test.py` / `.txt` | 恒常量 vs 漂移两个模型对三列的预测 |
| `attitude_ab/` | A/B 中间轨迹（8.7MB，未入库） |

## 复现

```
python3 alignment_conditioning.py    # 秒级
python3 constant_model_test.py       # 秒级
python3 constant_transfer.py         # 分钟级（每 cell 一次 Nelder-Mead）
python3 handeye_conditioning.py      # 分钟级
python3 chain_rotation_trace.py      # 分钟级
python3 attitude_ab.py               # 跑 44 次第 8/9 步 + 平滑，数十秒
```

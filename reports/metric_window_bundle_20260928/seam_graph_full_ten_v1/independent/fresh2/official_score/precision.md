# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 3.716 mm |
| ATE 平均 | 3.358 mm |
| ATE 最小 | 0.178 mm |
| ATE 中位 | 2.844 mm |
| ATE P95 | 7.036 mm |
| ATE 最大 | 8.154 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.570° |
| RPE 平移 RMSE | 3.073 mm |
| 终点漂移 | 8.742 mm |
| Sim(3)形状诊断 RMSE | 3.677 mm |
| Sim(3)最优尺度(gt/estimate) | 1.002511 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

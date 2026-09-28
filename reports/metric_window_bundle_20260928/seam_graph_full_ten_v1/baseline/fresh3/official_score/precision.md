# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 1.920 mm |
| ATE 平均 | 1.545 mm |
| ATE 最小 | 0.119 mm |
| ATE 中位 | 0.795 mm |
| ATE P95 | 3.670 mm |
| ATE 最大 | 6.310 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.056° |
| RPE 平移 RMSE | 2.498 mm |
| 终点漂移 | 2.370 mm |
| Sim(3)形状诊断 RMSE | 1.912 mm |
| Sim(3)最优尺度(gt/estimate) | 1.001052 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

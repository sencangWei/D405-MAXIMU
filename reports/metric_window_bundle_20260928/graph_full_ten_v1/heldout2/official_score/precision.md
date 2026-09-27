# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 3.516 mm |
| ATE 平均 | 3.388 mm |
| ATE 最小 | 0.299 mm |
| ATE 中位 | 3.633 mm |
| ATE P95 | 4.895 mm |
| ATE 最大 | 6.016 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 0.843° |
| RPE 平移 RMSE | 2.071 mm |
| 终点漂移 | 6.261 mm |
| Sim(3)形状诊断 RMSE | 2.876 mm |
| Sim(3)最优尺度(gt/estimate) | 0.989858 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

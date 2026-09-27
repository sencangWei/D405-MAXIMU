# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 5.138 mm |
| ATE 平均 | 4.675 mm |
| ATE 最小 | 0.771 mm |
| ATE 中位 | 3.900 mm |
| ATE P95 | 8.494 mm |
| ATE 最大 | 9.459 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.378° |
| RPE 平移 RMSE | 2.757 mm |
| 终点漂移 | 6.626 mm |
| Sim(3)形状诊断 RMSE | 3.280 mm |
| Sim(3)最优尺度(gt/estimate) | 0.979431 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

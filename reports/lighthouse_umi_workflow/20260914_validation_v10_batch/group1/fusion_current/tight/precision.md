# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 2.759 mm |
| ATE 平均 | 2.405 mm |
| ATE 最小 | 0.481 mm |
| ATE 中位 | 2.212 mm |
| ATE P95 | 4.855 mm |
| ATE 最大 | 13.737 mm |
| 10 mm 内比例 | 99.885% |
| 姿态 RMSE | 1.874° |
| RPE 平移 RMSE | 3.446 mm |
| 终点漂移 | 3.463 mm |
| Sim(3)形状诊断 RMSE | 2.758 mm |
| Sim(3)最优尺度(gt/estimate) | 1.000191 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

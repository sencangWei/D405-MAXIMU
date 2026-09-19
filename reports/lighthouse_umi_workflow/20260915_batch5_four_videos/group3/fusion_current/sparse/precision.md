# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 7.234 mm |
| ATE 平均 | 6.108 mm |
| ATE 最小 | 0.906 mm |
| ATE 中位 | 4.642 mm |
| ATE P95 | 14.097 mm |
| ATE 最大 | 22.301 mm |
| 10 mm 内比例 | 82.157% |
| 姿态 RMSE | 2.500° |
| RPE 平移 RMSE | 6.510 mm |
| 终点漂移 | 8.268 mm |
| Sim(3)形状诊断 RMSE | 7.136 mm |
| Sim(3)最优尺度(gt/estimate) | 0.993628 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

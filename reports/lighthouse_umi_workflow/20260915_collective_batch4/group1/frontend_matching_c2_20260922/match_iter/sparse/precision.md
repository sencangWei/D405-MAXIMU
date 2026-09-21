# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 6.172 mm |
| ATE 平均 | 4.993 mm |
| ATE 最小 | 0.597 mm |
| ATE 中位 | 4.283 mm |
| ATE P95 | 10.844 mm |
| ATE 最大 | 22.759 mm |
| 10 mm 内比例 | 93.173% |
| 姿态 RMSE | 1.844° |
| RPE 平移 RMSE | 7.989 mm |
| 终点漂移 | 7.670 mm |
| Sim(3)形状诊断 RMSE | 6.085 mm |
| Sim(3)最优尺度(gt/estimate) | 1.006477 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

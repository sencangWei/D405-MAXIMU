# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 7.420 mm |
| ATE 平均 | 7.118 mm |
| ATE 最小 | 0.876 mm |
| ATE 中位 | 7.388 mm |
| ATE P95 | 11.024 mm |
| ATE 最大 | 11.707 mm |
| 10 mm 内比例 | 90.901% |
| 姿态 RMSE | 0.886° |
| RPE 平移 RMSE | 2.855 mm |
| 终点漂移 | 6.540 mm |
| Sim(3)形状诊断 RMSE | 3.765 mm |
| Sim(3)最优尺度(gt/estimate) | 0.968622 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

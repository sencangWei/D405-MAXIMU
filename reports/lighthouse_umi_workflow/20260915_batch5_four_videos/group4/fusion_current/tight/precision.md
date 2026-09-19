# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 6.626 mm |
| ATE 平均 | 6.361 mm |
| ATE 最小 | 0.697 mm |
| ATE 中位 | 6.078 mm |
| ATE P95 | 9.628 mm |
| ATE 最大 | 13.185 mm |
| 10 mm 内比例 | 96.959% |
| 姿态 RMSE | 2.410° |
| RPE 平移 RMSE | 6.734 mm |
| 终点漂移 | 9.031 mm |
| Sim(3)形状诊断 RMSE | 6.397 mm |
| Sim(3)最优尺度(gt/estimate) | 0.992047 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

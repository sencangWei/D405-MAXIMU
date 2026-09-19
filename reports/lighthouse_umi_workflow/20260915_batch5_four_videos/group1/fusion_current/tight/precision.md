# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 4.906 mm |
| ATE 平均 | 3.944 mm |
| ATE 最小 | 0.294 mm |
| ATE 中位 | 3.034 mm |
| ATE P95 | 10.242 mm |
| ATE 最大 | 18.540 mm |
| 10 mm 内比例 | 94.779% |
| 姿态 RMSE | 1.757° |
| RPE 平移 RMSE | 4.556 mm |
| 终点漂移 | 4.034 mm |
| Sim(3)形状诊断 RMSE | 4.720 mm |
| Sim(3)最优尺度(gt/estimate) | 0.992452 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

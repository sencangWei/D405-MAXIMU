# Lighthouse 外部真值 SLAM 精度报告

判定：**FAIL**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 4.001 mm |
| ATE 平均 | 3.301 mm |
| ATE 最小 | 0.250 mm |
| ATE 中位 | 2.536 mm |
| ATE P95 | 8.193 mm |
| ATE 最大 | 12.435 mm |
| 10 mm 内比例 | 98.680% |
| 姿态 RMSE | 1.969° |
| RPE 平移 RMSE | 4.756 mm |
| 终点漂移 | 3.429 mm |
| Sim(3)形状诊断 RMSE | 3.014 mm |
| Sim(3)最优尺度(gt/estimate) | 1.014275 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

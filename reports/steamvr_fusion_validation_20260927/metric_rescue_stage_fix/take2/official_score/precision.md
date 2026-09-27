# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 5.319 mm |
| ATE 平均 | 4.838 mm |
| ATE 最小 | 0.825 mm |
| ATE 中位 | 4.836 mm |
| ATE P95 | 8.703 mm |
| ATE 最大 | 9.607 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.415° |
| RPE 平移 RMSE | 2.821 mm |
| 终点漂移 | 7.565 mm |
| Sim(3)形状诊断 RMSE | 3.491 mm |
| Sim(3)最优尺度(gt/estimate) | 0.979138 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

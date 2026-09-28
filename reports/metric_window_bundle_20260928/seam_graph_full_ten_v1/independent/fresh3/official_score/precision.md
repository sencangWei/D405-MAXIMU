# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 1.843 mm |
| ATE 平均 | 1.448 mm |
| ATE 最小 | 0.105 mm |
| ATE 中位 | 0.700 mm |
| ATE P95 | 3.509 mm |
| ATE 最大 | 6.253 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.047° |
| RPE 平移 RMSE | 2.438 mm |
| 终点漂移 | 2.270 mm |
| Sim(3)形状诊断 RMSE | 1.839 mm |
| Sim(3)最优尺度(gt/estimate) | 1.000677 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。

# Lighthouse 外部真值 SLAM 精度报告

判定：**PASS**

| 指标 | 结果 |
| --- | ---: |
| ATE RMSE | 7.010 mm |
| ATE 平均 | 6.681 mm |
| ATE 最小 | 1.974 mm |
| ATE 中位 | 7.738 mm |
| ATE P95 | 9.295 mm |
| ATE 最大 | 9.952 mm |
| 10 mm 内比例 | 100.000% |
| 姿态 RMSE | 1.127° |
| RPE 平移 RMSE | 2.587 mm |
| 终点漂移 | 14.721 mm |
| Sim(3)形状诊断 RMSE | 4.709 mm |
| Sim(3)最优尺度(gt/estimate) | 0.976456 |

主判定仅使用刚体 SE(3)，不允许缩放。Sim(3)仅诊断单目尺度与形状，不参与通过判定。Lighthouse 只用于评分，不输入 SLAM。
